/**
 * Live position monitoring: broker-confirmed TIME exits and reconciliation.
 *
 * Market prices are NOT read here. Yahoo Finance is the sole market-price provider and Headway supplies no prices.
 *  - The stop-loss is enforced by the broker: the strategy order is refused unless the SL is attached to the ticket.
 *  - This monitor closes a managed position by market only when its original time-exit deadline has passed.
 *  - A close is reported as confirmed only when the position left the Trade tab AND the account margin fell.
 *  - A position that is simply no longer visible (e.g. stopped out by the broker) is reported NOT_FOUND, never as a confirmed close.
 *  - No exit price or P&L is invented: they are not reported because they are not read from the broker.
 *
 * Safety: never opens positions; only closes positions in the supplied managed list; dry-run unless --execute.
 */

import { Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { Selectors } from '../config/selectors';
import { findFirstVisible, safeClick, sleep, screenshotOnFailure } from './utils';
import { readMargin } from './account';
import { Policy } from './policy';

// --------------------------------------------------------------------------- types

export interface OpenPosition {
  ticket?: string;
  symbol: string;
  side: 'buy' | 'sell';
  lots: number;
  openPrice: number;
  sl?: number;
  tp?: number;
  profit?: number;
  openTime?: string;
  raw?: string;
}

export interface ManagedPosition {
  signal_id: string;
  ticket_id?: string;
  symbol: string;
  side: 'buy' | 'sell';
  lots: number;
  entry: number;
  sl: number;
  deadline: string;          // ISO UTC
  opened_at: string;
  strategy_id?: string;
}

export interface CloseAction {
  signal_id: string;
  ticket_id?: string;
  reason: 'TIME' | 'SL' | 'MANUAL' | 'NOT_FOUND' | 'SKIPPED';
  attempted: boolean;
  closed: boolean;
  exit_time?: string;        // when the close was confirmed (terminal clock), not a price
  detail: string;
}

export interface MonitorReport {
  schema: 1;
  kind: 'POSITION_MONITOR';
  account_mode: string;
  captured_at: string;
  open_positions: OpenPosition[];
  managed: ManagedPosition[];
  actions: CloseAction[];
  margin_before: number | null;
  margin_after: number | null;
  execute: boolean;
  dry_run: boolean;
  outcome: string;
  stages: Array<{ name: string; ok: boolean; detail?: string }>;
  run_id: string;
}

// --------------------------------------------------------------------------- parsing

const SIDE_RE = /\b(buy|sell)\b/i;
const NUM_RE = /(\d+\.\d+)/g;

/**
 * Best-effort parse of the Trade tab text into open positions.
 * MT5 web terminals typically show rows like:
 *   123456789  EURUSD  buy  0.50  1.08520  1.08200  ...  12.50
 * Exact column order varies; we look for a 6-letter symbol + side + volume + prices.
 */
export function parseTradeTab(text: string, symbolFilter = 'EURUSD'): OpenPosition[] {
  const lines = text.split(/\n+/).map(l => l.trim()).filter(Boolean);
  const out: OpenPosition[] = [];
  const sym = symbolFilter.toUpperCase();

  for (const line of lines) {
    if (!line.toUpperCase().includes(sym)) continue;
    const sideM = line.match(SIDE_RE);
    if (!sideM) continue;
    const side = sideM[1].toLowerCase() as 'buy' | 'sell';

    // Collect all decimal numbers on the line
    const nums = [...line.matchAll(NUM_RE)].map(m => Number(m[1]));
    if (nums.length < 2) continue;

    // Heuristic: volume is usually a small number (0.01–10), open price ~1.0x for EURUSD
    let lots = 0.5;
    let openPrice = 0;
    let sl: number | undefined;
    let profit: number | undefined;

    for (const n of nums) {
      if (n >= 0.01 && n <= 10 && Math.abs(n * 100 - Math.round(n * 100)) < 1e-6) {
        // plausible lot size
        if (lots === 0.5 || n !== 0.5) lots = n;
      } else if (n > 0.5 && n < 2.5) {
        // EURUSD price region
        if (!openPrice) openPrice = n;
        else if (sl === undefined) sl = n;
      } else if (Math.abs(n) < 5000 && openPrice) {
        // residual could be profit
        profit = n;
      }
    }
    if (!openPrice) continue;

    // Ticket: first long integer on the line
    const ticketM = line.match(/\b(\d{6,})\b/);
    out.push({
      ticket: ticketM ? ticketM[1] : undefined,
      symbol: sym,
      side,
      lots,
      openPrice,
      sl,
      profit,
      raw: line.slice(0, 200),
    });
  }
  return out;
}

export async function readOpenPositions(page: Page, symbol = 'EURUSD'): Promise<OpenPosition[]> {
  // Prefer the Trade tab
  for (const sel of Selectors.trading.tradeTab) {
    try {
      const loc = page.locator(sel).first();
      if (await loc.isVisible({ timeout: 1200 }).catch(() => false)) {
        await loc.click({ timeout: 2000 });
        await sleep(600);
        break;
      }
    } catch { /* continue */ }
  }

  const parts: string[] = [];
  for (const frame of page.frames()) {
    const t = await frame.locator('body').innerText({ timeout: 2500 }).catch(() => '');
    if (t) parts.push(t);
  }
  const text = parts.join('\n');
  return parseTradeTab(text, symbol);
}

// --------------------------------------------------------------------------- close helpers

async function closePositionByTicket(
  page: Page,
  pos: OpenPosition,
  execute: boolean,
  marginBefore: number | null,
): Promise<{ closed: boolean; detail: string }> {
  if (!execute) {
    return { closed: false, detail: 'dry_run: would close' };
  }

  // Strategy: right-click / context menu on the row, or double-click + close button.
  // Fall back to selecting the row and using the close menu item from selectors.
  try {
    // Try to find the ticket or symbol+side text and open context menu
    const needle = pos.ticket || `${pos.symbol}`;
    for (const frame of page.frames()) {
      const row = frame.getByText(needle, { exact: false }).first();
      if (await row.isVisible({ timeout: 1500 }).catch(() => false)) {
        await row.click({ button: 'right', timeout: 3000 }).catch(() => row.dblclick({ timeout: 3000 }));
        await sleep(400);
        break;
      }
    }

    // Click Close menu item
    const closedMenu = await safeClick(page, Selectors.order.closeMenuItem, 'Close menu item', 4000);
    if (!closedMenu) {
      // Some terminals have a Close button directly
      const closeBtn = await findFirstVisible(page, [
        'button:has-text("Close")',
        'button:has-text("Close Position")',
        '[class*="close" i][role="button"]',
      ], 3000);
      if (closeBtn) await closeBtn.click({ timeout: 3000 });
      else return { closed: false, detail: 'close_control_not_found' };
    }

    // Confirm if a dialog appears
    await sleep(500);
    await safeClick(page, Selectors.order.closeConfirm, 'Close confirm', 3000);
    await sleep(1200);

    // Strong verification: position must disappear from Trade tab OR margin must drop.
    await sleep(1500);
    const after = await readOpenPositions(page, pos.symbol);
    const stillThere = after.some(p =>
      (pos.ticket && p.ticket === pos.ticket) ||
      (!pos.ticket && p.side === pos.side && Math.abs(p.lots - pos.lots) < 0.011 && Math.abs(p.openPrice - pos.openPrice) < 0.0002)
    );
    if (stillThere) return { closed: false, detail: 'close_clicked_but_position_still_visible' };
    // Second, independent piece of broker evidence: the account margin must have fallen.
    const marginAfter = await readMargin(page);
    if (marginBefore === null || marginAfter === null) return { closed: false, detail: 'gone_from_trade_tab_but_margin_unreadable' };
    if (!(marginAfter < marginBefore - 0.005)) return { closed: false, detail: `gone_from_trade_tab_but_margin_not_lower(${marginBefore}->${marginAfter})` };
    return { closed: true, detail: `position_gone_from_trade_tab_and_margin_fell(${marginBefore}->${marginAfter})` };
  } catch (e) {
    return { closed: false, detail: `close_exception:${String(e).slice(0, 120)}` };
  }
}

/** Pure: should this managed position be closed now? Only the original time exit closes by market. */
export function decideClose(deadlineIso: string, now: Date, closeOnTime = true): 'TIME' | null {
  const deadline = new Date(deadlineIso).getTime();
  if (!Number.isFinite(deadline)) return null;          // unparseable deadline: never guess, never close
  return closeOnTime && now.getTime() >= deadline ? 'TIME' : null;
}

// --------------------------------------------------------------------------- main monitor

export interface MonitorOptions {
  managed: ManagedPosition[];
  execute: boolean;                 // false = dry-run (report only)
  symbol?: string;
  now?: Date;                       // injectable for tests
  closeOnTime?: boolean;            // default true
  outDir?: string;
}

/**
 * Run one monitoring pass.
 * - Reads open positions from the terminal.
 * - For each managed position that is still open and past its time-exit deadline,
 *   attempts a market close (or records the intention in dry-run).
 * - Writes artifacts/position_monitor.json for the bridge.
 */
export async function monitorAndClose(
  page: Page,
  policy: Policy,
  opts: MonitorOptions,
): Promise<MonitorReport> {
  const symbol = (opts.symbol || 'EURUSD').toUpperCase();
  const now = opts.now || new Date();
  const execute = !!opts.execute;
  const closeOnTime = opts.closeOnTime !== false;
  const outDir = opts.outDir || path.join(process.cwd(), 'artifacts');
  const stages: MonitorReport['stages'] = [];
  const st = (name: string, ok: boolean, detail = '') => {
    stages.push({ name, ok, detail });
    console.log(`[monitor] ${ok ? 'OK  ' : 'FAIL'} ${name}${detail ? ' – ' + detail : ''}`);
  };

  const report: MonitorReport = {
    schema: 1,
    kind: 'POSITION_MONITOR',
    account_mode: policy.mode,
    captured_at: now.toISOString(),
    open_positions: [],
    managed: opts.managed,
    actions: [],
    margin_before: null,
    margin_after: null,
    execute,
    dry_run: !execute,
    outcome: 'ok',
    stages,
    run_id: process.env.GITHUB_RUN_ID || 'local',
  };

  try {
    report.margin_before = await readMargin(page);
    st('margin_readable', report.margin_before !== null, String(report.margin_before));

    const open = await readOpenPositions(page, symbol);
    report.open_positions = open;
    st('trade_tab_read', true, `${open.length} open position(s) visible`);

    for (const m of opts.managed) {
      const action: CloseAction = {
        signal_id: m.signal_id,
        ticket_id: m.ticket_id,
        reason: 'SKIPPED',
        attempted: false,
        closed: false,
        detail: '',
      };

      // Match open position by ticket, or by symbol+side+lots+entry proximity
      let match = open.find(p => m.ticket_id && p.ticket === m.ticket_id);
      if (!match) {
        match = open.find(p =>
          p.symbol === m.symbol.toUpperCase() &&
          p.side === m.side &&
          Math.abs(p.lots - m.lots) < 1e-6 &&
          Math.abs(p.openPrice - m.entry) < 0.0010
        );
      }

      if (!match) {
        action.reason = 'NOT_FOUND';
        action.detail = 'no matching open position (already closed by SL/broker or never filled)';
        report.actions.push(action);
        continue;
      }

      const reason = decideClose(m.deadline, now, closeOnTime);
      if (match.sl === undefined) st(`broker_sl_visible_${m.signal_id.slice(0, 12)}`, false, 'stop-loss not visible on the Trade tab row (informational; verify manually)');

      if (!reason) {
        action.reason = 'SKIPPED';
        action.detail = `still open; time exit at ${m.deadline}; stop-loss is enforced by the broker`;
        report.actions.push(action);
        continue;
      }

      action.reason = reason;
      action.attempted = true;
      st(`close_${reason.toLowerCase()}_${m.signal_id.slice(0, 12)}`, true, `ticket=${match.ticket || '?'}`);

      const result = await closePositionByTicket(page, match, execute, report.margin_before);
      action.closed = result.closed;
      action.detail = result.detail;
      if (result.closed) action.exit_time = new Date().toISOString();

      if (execute && !result.closed) {
        report.outcome = 'close_failed';
        await screenshotOnFailure(page, `monitor-close-failed-${m.signal_id.slice(0, 8)}`);
      }

      report.actions.push(action);
    }

    report.margin_after = await readMargin(page);
    st('margin_after', report.margin_after !== null, String(report.margin_after));

    const attempted = report.actions.filter(a => a.attempted).length;
    const closed = report.actions.filter(a => a.closed).length;
    if (attempted && execute && closed < attempted) report.outcome = 'partial_close_failed';
    else if (attempted && !execute) report.outcome = 'dry_run_ok';
    else report.outcome = 'ok';

  } catch (e) {
    st('exception', false, String(e).slice(0, 200));
    report.outcome = 'exception';
    await screenshotOnFailure(page, 'monitor-exception');
  }

  try {
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(path.join(outDir, 'position_monitor.json'), JSON.stringify(report, null, 2) + '\n');
  } catch { /* reporting must not mask outcome */ }

  console.log(`[monitor] outcome=${report.outcome} actions=${report.actions.length} open=${report.open_positions.length}`);
  return report;
}

/** Exit code for CI / scripts */
export function monitorExitCode(report: MonitorReport): number {
  if (report.outcome === 'ok' || report.outcome === 'dry_run_ok') return 0;
  if (report.outcome === 'partial_close_failed' || report.outcome === 'close_failed') return 3;
  return 1;
}
