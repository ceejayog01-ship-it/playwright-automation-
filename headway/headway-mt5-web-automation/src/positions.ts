/**
 * Live position monitoring + time-exit / SL close actions.
 *
 * This module is the automated counterpart to you watching the Trade tab.
 * It runs every cycle (and can be invoked standalone) so positions are
 * managed even when you are not at the terminal.
 *
 * Responsibilities
 * 1. Read open positions from the Trade tab (symbol, side, volume, ticket,
 *    open price, SL, profit if visible).
 * 2. For each managed position (matched by ticket_id or signal metadata):
 *    - If current time >= deadline  → close by market (TIME exit).
 *    - If price has crossed SL      → close by market (SL exit) when the
 *      broker has not already stopped it out.
 * 3. Write a machine-readable report so the bridge can update its local hash-chained ledger
 *    (status, exit_reason, exit_price, exit_time, pips, pnl).
 *
 * Safety
 * - Never opens new positions.
 * - Only closes positions that appear in the supplied "managed" list
 *   (or, when --close-all-managed is set, any EURUSD position that matches
 *   the strategy lot size). Dry-run is the default unless --execute is passed.
 * - All actions are logged with stages for audit.
 *
 * Selectors are best-effort; refine them from live screenshots / probe
 * the same way the order ticket selectors were refined.
 */

import { Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { Selectors } from '../config/selectors';
import { findFirstVisible, safeClick, sleep, screenshotOnFailure } from './utils';
import { readMargin, waitForQuote, parseMargin } from './quotes';
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
  exit_price?: number;
  exit_time?: string;
  pips?: number;
  pnl_usd?: number;
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
    if (!stillThere) return { closed: true, detail: 'position_gone_from_trade_tab' };
    return { closed: false, detail: 'close_clicked_but_position_still_visible' };
  } catch (e) {
    return { closed: false, detail: `close_exception:${String(e).slice(0, 120)}` };
  }
}

function pipsFrom(side: 'buy' | 'sell', entry: number, exit: number): number {
  const diff = side === 'buy' ? exit - entry : entry - exit;
  return Math.round((diff / 0.0001) * 10) / 10; // 1 decimal pip
}

// --------------------------------------------------------------------------- main monitor

export interface MonitorOptions {
  managed: ManagedPosition[];
  execute: boolean;                 // false = dry-run (report only)
  symbol?: string;
  now?: Date;                       // injectable for tests
  closeOnSl?: boolean;              // default true
  closeOnTime?: boolean;            // default true
  outDir?: string;
}

/**
 * Run one monitoring pass.
 * - Reads open positions from the terminal.
 * - For each managed position that is still open and has breached SL or deadline,
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
  const closeOnSl = opts.closeOnSl !== false;
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

    const quote = await waitForQuote(page, symbol, 15000);
    st('live_quote', !!quote, quote ? `bid ${quote.bid} ask ${quote.ask}` : 'missing');

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

      const deadline = new Date(m.deadline);
      const pastDeadline = now.getTime() >= deadline.getTime();

      // SL check using live quote (bid for BUY stop, ask for SELL stop approximation)
      let hitSl = false;
      if (quote && m.sl > 0) {
        if (m.side === 'buy') hitSl = quote.bid <= m.sl;
        else hitSl = quote.ask >= m.sl;
      }
      // Also trust the position's own SL if the terminal already shows it hit
      if (match.sl && quote) {
        if (m.side === 'buy' && quote.bid <= match.sl) hitSl = true;
        if (m.side === 'sell' && quote.ask >= match.sl) hitSl = true;
      }

      let reason: CloseAction['reason'] | null = null;
      if (closeOnTime && pastDeadline) reason = 'TIME';
      else if (closeOnSl && hitSl) reason = 'SL';

      if (!reason) {
        action.reason = 'SKIPPED';
        action.detail = `still open; deadline ${m.deadline}; sl ${m.sl}; quote ${quote ? (m.side === 'buy' ? quote.bid : quote.ask) : 'n/a'}`;
        report.actions.push(action);
        continue;
      }

      action.reason = reason;
      action.attempted = true;
      st(`close_${reason.toLowerCase()}_${m.signal_id.slice(0, 12)}`, true, `ticket=${match.ticket || '?'}`);

      const result = await closePositionByTicket(page, match, execute);
      action.closed = result.closed;
      action.detail = result.detail;
      action.exit_time = new Date().toISOString();

      // Approximate exit price from the quote we just used
      if (quote) {
        action.exit_price = m.side === 'buy' ? quote.bid : quote.ask;
        action.pips = pipsFrom(m.side, m.entry, action.exit_price);
        // Rough USD P&L: pips * $10 per pip per 1.00 lot for EURUSD
        action.pnl_usd = Math.round(action.pips * 10 * m.lots * 100) / 100;
      }

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
