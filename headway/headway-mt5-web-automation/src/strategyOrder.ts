/**
 * Strategy market-order path for Headway web terminal.
 *
 * Places ONE market order for an accepted TSE signal, optionally sets SL,
 * then reads the Trade tab for a real ticket_id.
 *
 * Safety (same philosophy as dummy order):
 *  1. Pure guards run before any click.
 *  2. Default is dry-run (open ticket, set volume/SL, screenshot, Escape — no Buy/Sell).
 *  3. Live click requires --execute AND ALLOW_ORDERS=yes
 *     (real account also needs REAL_ORDER_CONFIRM=REAL-<last4 of login>).
 *  4. Buy/Sell is clicked at most ONCE; no retry that could double-fire.
 *  5. Confirmation uses margin increase + Trade-tab row (ticket preferred).
 *
 * Does NOT invent ticket IDs. If the Trade tab cannot be parsed, outcome is
 * executed_unconfirmed and the operator must check the account.
 */
import { Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { Selectors } from '../config/selectors';
import { findFirstVisible, safeClick, screenshotOnFailure, sleep } from './utils';
import { Policy } from './policy';
import { readMargin, waitForQuote } from './quotes';
import { readOpenPositions, OpenPosition } from './positions';
import { OrderGuardError, OrderEnv } from './order';

export const MAX_STRATEGY_LOTS = 0.50;
export const MIN_STRATEGY_LOTS = 0.01;

export interface StrategyOrderRequest {
  signal_id: string;
  symbol: string;
  side: 'buy' | 'sell';
  lots: number;
  entry?: number;          // expected entry (informational)
  sl?: number;             // stop-loss price to set on the ticket if the UI exposes it
  execute: boolean;
  closeAfter?: boolean;    // optional immediate close (connectivity only)
}

export interface StrategyOrderReport {
  schema: 1;
  kind: 'STRATEGY_ORDER';
  account_mode: string;
  signal_id: string;
  symbol: string;
  side: string;
  lots: number;
  sl: number | null;
  execute: boolean;
  dry_run: boolean;
  stages: Array<{ name: string; ok: boolean; detail: string }>;
  margin_before: number | null;
  margin_after_open: number | null;
  ticket_id: string | null;
  open_price: number | null;
  opened_confirmed: boolean;
  closed_confirmed: boolean | null;
  outcome: string;
  open_positions_snapshot: OpenPosition[];
  started_at: string;
  finished_at: string;
  run_id: string;
}

const REJECT = /(rejected|invalid (volume|stops|price|request)|not enough money|no money|market (is )?closed|trade (is )?disabled|requote|off quotes)/i;
const EPS = 0.005;

export function assertStrategyOrderAllowed(policy: Policy, req: StrategyOrderRequest, env: OrderEnv): void {
  const allowed = (env.ORDER_SYMBOLS || 'EURUSD').split(',').map(s => s.trim().toUpperCase()).filter(Boolean);
  if (!req.signal_id || String(req.signal_id).length < 2) throw new OrderGuardError('missing_signal_id');
  if (!allowed.includes(req.symbol.toUpperCase())) throw new OrderGuardError(`symbol_not_allowed:${req.symbol}`);
  if (req.side !== 'buy' && req.side !== 'sell') throw new OrderGuardError('bad_side');
  if (!Number.isFinite(req.lots) || req.lots < MIN_STRATEGY_LOTS - 1e-9 || req.lots > MAX_STRATEGY_LOTS + 1e-9) {
    throw new OrderGuardError(`lots_out_of_range(${MIN_STRATEGY_LOTS}<=lots<=${MAX_STRATEGY_LOTS})`);
  }
  if (Math.abs(req.lots * 100 - Math.round(req.lots * 100)) > 1e-9) throw new OrderGuardError('lots_not_multiple_of_0.01');
  if (req.sl != null) {
    if (!Number.isFinite(req.sl) || req.sl <= 0) throw new OrderGuardError('bad_sl');
  }
  if (req.closeAfter && !req.execute) throw new OrderGuardError('close_requires_execute');
  if (!req.execute) return;
  if (env.ALLOW_ORDERS !== 'yes') throw new OrderGuardError('orders_disabled:set ALLOW_ORDERS=yes');
  if (policy.mode === 'real') {
    const login = (env.MT5_LOGIN || '').trim();
    if (login.length < 4) throw new OrderGuardError('real_mode_needs_MT5_LOGIN');
    if (env.REAL_ORDER_CONFIRM !== `REAL-${login.slice(-4)}`) throw new OrderGuardError('real_order_confirmation_missing_or_wrong');
  }
}

async function anyText(page: Page): Promise<string> {
  const parts: string[] = [];
  for (const f of page.frames()) parts.push(await f.locator('body').innerText({ timeout: 2000 }).catch(() => ''));
  return parts.join('\n');
}

async function openTicket(page: Page, symbol: string): Promise<boolean> {
  for (const f of page.frames()) {
    const row = f.getByText(symbol, { exact: true }).first();
    if (await row.isVisible({ timeout: 800 }).catch(() => false)) {
      await row.dblclick({ timeout: 5000 }).catch(() => {});
      break;
    }
  }
  if (await findFirstVisible(page, Selectors.order.volumeInput, 6000)) return true;
  await page.keyboard.press('F9').catch(() => {});
  return !!(await findFirstVisible(page, Selectors.order.volumeInput, 6000));
}

async function ticketHasSymbol(page: Page, symbol: string): Promise<boolean> {
  for (const f of page.frames()) {
    const values: string[] = await f.locator('input, select').evaluateAll(els => els.map(e => (e as HTMLInputElement).value || '')).catch(() => []);
    if (values.some(v => v.toUpperCase().includes(symbol.toUpperCase()))) return true;
  }
  return false;
}

async function trySetStopLoss(page: Page, sl: number): Promise<{ ok: boolean; detail: string }> {
  const el = await findFirstVisible(page, Selectors.order.stopLossInput, 2500);
  if (!el) return { ok: false, detail: 'sl_input_not_found' };
  try {
    await el.fill('', { timeout: 2000 }).catch(() => {});
    await el.fill(String(sl), { timeout: 3000 });
    const shown = Number(await el.inputValue().catch(() => 'NaN'));
    if (Number.isFinite(shown) && Math.abs(shown - sl) < 0.00015) return { ok: true, detail: `sl_set=${shown}` };
    return { ok: false, detail: `sl_field_shows=${shown}` };
  } catch (e) {
    return { ok: false, detail: String(e).slice(0, 80) };
  }
}

function matchNewPosition(
  before: OpenPosition[],
  after: OpenPosition[],
  symbol: string,
  side: 'buy' | 'sell',
  lots: number,
): OpenPosition | null {
  const key = (p: OpenPosition) => `${p.ticket || ''}|${p.side}|${p.lots}|${p.openPrice}`;
  const beforeKeys = new Set(before.map(key));
  const candidates = after.filter(p =>
    p.symbol.toUpperCase() === symbol.toUpperCase()
    && p.side === side
    && Math.abs(p.lots - lots) < 0.011
    && !beforeKeys.has(key(p)),
  );
  if (candidates.length === 1) return candidates[0];
  // Fallback: any matching side/lots not present before by ticket
  const beforeTickets = new Set(before.map(p => p.ticket).filter(Boolean));
  const byTicket = after.filter(p =>
    p.symbol.toUpperCase() === symbol.toUpperCase()
    && p.side === side
    && p.ticket
    && !beforeTickets.has(p.ticket),
  );
  if (byTicket.length === 1) return byTicket[0];
  return candidates[0] || byTicket[0] || null;
}

export async function runStrategyOrder(
  page: Page,
  policy: Policy,
  req: StrategyOrderRequest,
  outDir = path.join(process.cwd(), 'artifacts'),
): Promise<StrategyOrderReport> {
  const stages: StrategyOrderReport['stages'] = [];
  const started = new Date().toISOString();
  const rep: StrategyOrderReport = {
    schema: 1,
    kind: 'STRATEGY_ORDER',
    account_mode: policy.mode,
    signal_id: req.signal_id,
    symbol: req.symbol.toUpperCase(),
    side: req.side,
    lots: req.lots,
    sl: req.sl ?? null,
    execute: req.execute,
    dry_run: !req.execute,
    stages,
    margin_before: null,
    margin_after_open: null,
    ticket_id: null,
    open_price: null,
    opened_confirmed: false,
    closed_confirmed: null,
    outcome: 'not_started',
    open_positions_snapshot: [],
    started_at: started,
    finished_at: started,
    run_id: process.env.GITHUB_RUN_ID || 'local',
  };
  const st = (name: string, ok: boolean, detail = '') => {
    stages.push({ name, ok, detail });
    console.log(`[strategy-order] ${ok ? 'OK  ' : 'FAIL'} ${name}${detail ? ' – ' + detail : ''}`);
    return ok;
  };
  const abort = (why: string) => { rep.outcome = `failed:${why}`; };

  try {
    const beforePositions = await readOpenPositions(page, req.symbol);
    rep.margin_before = await readMargin(page);
    if (!st('margin_readable', rep.margin_before !== null, String(rep.margin_before))) {
      if (req.execute) return abort('margin_unreadable'), rep;
    }
    const q = await waitForQuote(page, req.symbol, 20000);
    if (!st('live_quote', !!q, q ? `bid ${q.bid} ask ${q.ask}` : 'not found')) {
      if (req.execute) return abort('quote_unreadable'), rep;
    }

    if (!st('ticket_opened', await openTicket(page, req.symbol))) {
      await screenshotOnFailure(page, 'strategy-order-ticket-not-found');
      return abort('ticket_not_opened'), rep;
    }
    const vol = await findFirstVisible(page, Selectors.order.volumeInput, 4000);
    if (!vol) {
      await screenshotOnFailure(page, 'strategy-order-volume-missing');
      return abort('volume_input_missing'), rep;
    }
    await vol.fill(String(req.lots), { timeout: 5000 });
    const applied = Number(await vol.inputValue().catch(() => 'NaN'));
    if (!st('volume_applied', Math.abs(applied - req.lots) < 1e-9, `wanted ${req.lots}, field shows ${applied}`)) {
      await screenshotOnFailure(page, 'strategy-order-volume-not-applied');
      return abort('volume_not_applied'), rep;
    }
    if (!st('symbol_in_ticket', await ticketHasSymbol(page, req.symbol))) {
      await screenshotOnFailure(page, 'strategy-order-wrong-symbol');
      return abort('symbol_not_in_ticket'), rep;
    }

    if (req.sl != null && Number.isFinite(req.sl)) {
      const slRes = await trySetStopLoss(page, req.sl);
      const requireSl = (process.env.REQUIRE_BROKER_SL || 'yes').toLowerCase() !== 'no';
      st('sl_applied', slRes.ok, slRes.detail);
      if (!slRes.ok && requireSl) {
        await page.keyboard.press('Escape').catch(() => {});
        await screenshotOnFailure(page, 'strategy-order-sl-required-missing');
        return abort(`sl_not_applied:${slRes.detail}`), rep;
      }
    } else {
      st('sl_skipped', true, 'no_sl_provided');
      // Strategy contract expects SL; refuse live execute without SL unless explicitly allowed.
      if (req.execute && (process.env.ALLOW_ORDER_WITHOUT_SL || '').toLowerCase() !== 'yes') {
        await page.keyboard.press('Escape').catch(() => {});
        return abort('sl_required_for_strategy_order'), rep;
      }
    }

    await screenshotOnFailure(page, 'strategy-order-ticket-ready');

    if (!req.execute) {
      await page.keyboard.press('Escape').catch(() => {});
      const m = await readMargin(page);
      st('dry_run_no_position', m === null || rep.margin_before === null || Math.abs(m - rep.margin_before) < EPS, `margin ${m}`);
      rep.outcome = stages.filter(s => s.name !== 'sl_applied' || s.ok).every(s => s.ok || s.name === 'sl_applied')
        ? 'dry_run_ok'
        : 'failed:dry_run_checks';
      // dry-run ok even if SL input missing
      if (stages.find(s => s.name === 'volume_applied')?.ok && stages.find(s => s.name === 'ticket_opened')?.ok) {
        rep.outcome = 'dry_run_ok';
      }
      return rep;
    }

    // ---- single click ----
    const clicked = await safeClick(
      page,
      req.side === 'buy' ? Selectors.order.buy : Selectors.order.sell,
      `${req.side.toUpperCase()} (strategy ${req.lots} lots)`,
      8000,
    );
    if (!st('side_clicked_once', clicked)) {
      await screenshotOnFailure(page, 'strategy-order-side-button-missing');
      return abort('side_button_missing'), rep;
    }

    const before = rep.margin_before ?? 0;
    const end = Date.now() + 25000;
    let rejected = '';
    while (Date.now() < end && !rep.opened_confirmed && !rejected) {
      const m = await readMargin(page);
      if (m !== null && m > before + EPS) {
        rep.opened_confirmed = true;
        rep.margin_after_open = m;
        break;
      }
      const hit = (await anyText(page)).match(REJECT);
      if (hit) rejected = hit[0];
      await sleep(1000);
    }
    await screenshotOnFailure(page, 'strategy-order-after-click');
    if (rejected) {
      st('order_rejected', false, rejected);
      return abort(`order_rejected:${rejected}`), rep;
    }

    // Read Trade tab for ticket_id
    await sleep(1500);
    const afterPositions = await readOpenPositions(page, req.symbol);
    rep.open_positions_snapshot = afterPositions;
    const matched = matchNewPosition(beforePositions, afterPositions, req.symbol, req.side, req.lots);
    if (matched) {
      rep.ticket_id = matched.ticket || null;
      rep.open_price = matched.openPrice || null;
      st('ticket_captured', !!rep.ticket_id, rep.ticket_id ? `ticket=${rep.ticket_id} open=${rep.open_price}` : `open=${rep.open_price} (no ticket text)`);
      if (!rep.opened_confirmed) {
        rep.opened_confirmed = true;
        rep.margin_after_open = await readMargin(page);
      }
    } else {
      st('ticket_captured', false, `no matching new row (before=${beforePositions.length} after=${afterPositions.length})`);
    }

    if (!st('position_confirmed', rep.opened_confirmed, `margin ${rep.margin_before} -> ${rep.margin_after_open}; ticket=${rep.ticket_id}`)) {
      rep.outcome = 'executed_unconfirmed';
      return rep;
    }
    rep.outcome = rep.ticket_id ? 'executed_confirmed_with_ticket' : 'executed_confirmed';

    if (req.closeAfter) {
      // Optional immediate close for connectivity tests only.
      let closed = false;
      for (const f of page.frames()) {
        const needle = rep.ticket_id || req.symbol;
        const rows = f.locator('tr, [role="row"]').filter({ hasText: new RegExp(needle, 'i') });
        const n = await rows.count().catch(() => 0);
        if (n === 0) continue;
        await rows.first().click({ button: 'right', timeout: 5000 }).catch(() => {});
        if (await safeClick(page, Selectors.order.closeMenuItem, 'Close position menu item', 5000)) {
          await safeClick(page, Selectors.order.closeConfirm, 'Close confirmation', 4000).catch(() => false);
          closed = true;
        }
        break;
      }
      if (closed) {
        const endM = Date.now() + 20000;
        while (Date.now() < endM) {
          const m = await readMargin(page);
          if (m !== null && m <= before + EPS) {
            rep.closed_confirmed = true;
            break;
          }
          await sleep(1000);
        }
        st('position_closed_by_margin', !!rep.closed_confirmed, '');
        if (!rep.closed_confirmed) rep.outcome = 'executed_confirmed_close_failed';
      } else {
        rep.closed_confirmed = false;
        st('close_attempted', false, 'could not close via UI');
        rep.outcome = 'executed_confirmed_close_failed';
      }
    }
    return rep;
  } catch (e) {
    st('exception', false, String(e).slice(0, 200));
    await screenshotOnFailure(page, 'strategy-order-exception');
    if (rep.outcome === 'not_started') abort('exception');
    return rep;
  } finally {
    rep.finished_at = new Date().toISOString();
    try {
      fs.mkdirSync(outDir, { recursive: true });
      fs.writeFileSync(path.join(outDir, 'strategy_order_result.json'), JSON.stringify(rep, null, 2) + '\n');
      // Also write a bridge-friendly managed position stub when we have a ticket
      if (rep.ticket_id && rep.opened_confirmed && !req.closeAfter) {
        const managed = [{
          signal_id: req.signal_id,
          ticket_id: rep.ticket_id,
          symbol: req.symbol.toUpperCase(),
          side: req.side,
          lots: req.lots,
          entry: rep.open_price ?? req.entry ?? 0,
          sl: req.sl ?? 0,
          deadline: new Date(Date.now() + 6 * 3600 * 1000).toISOString(),
          opened_at: rep.finished_at,
        }];
        fs.writeFileSync(path.join(outDir, 'managed_positions.json'), JSON.stringify(managed, null, 2) + '\n');
      }
    } catch { /* reporting must not mask the outcome */ }
  }
}

export function strategyOrderExitCode(outcome: string): number {
  if (outcome === 'dry_run_ok' || outcome === 'executed_confirmed' || outcome === 'executed_confirmed_with_ticket') return 0;
  if (outcome === 'executed_confirmed_close_failed' || outcome === 'executed_unconfirmed') return 3;
  return 1;
}
