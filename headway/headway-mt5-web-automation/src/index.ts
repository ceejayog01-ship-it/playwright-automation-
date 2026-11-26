#!/usr/bin/env node
/**
 * Headway MT5 Web Terminal automation — integrated entry point.
 *
 * Headway is used for login, account access, order execution and broker confirmations ONLY.
 * It collects no market candles and supplies no market prices (Yahoo Finance is the sole price provider).
 *
 * Modes:
 *   login | order | strategy-order | monitor
 *
 * strategy-order — one guarded market order for an accepted signal (SL attached; ticket captured)
 * monitor        — broker-confirmed time-exit closes and position reconciliation, driven by the bridge
 */
import { Command } from 'commander';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import * as path from 'path';
import { launchBrowser, closeBrowser } from './browser';
import { performLogin, getLastLoginReport } from './login';
import { loadPolicy, PolicyError } from './policy';
import { writeAuthResult } from './authResult';
import { assertOrderAllowed, OrderGuardError, runDummyOrder, orderExitCode } from './order';
import { monitorAndClose, monitorExitCode, ManagedPosition } from './positions';
import { assertStrategyOrderAllowed, runStrategyOrder, strategyOrderExitCode } from './strategyOrder';
import { screenshotOnFailure } from './utils';

dotenv.config();

const program = new Command();
program
  .option('--mode <mode>', 'login | order | strategy-order | monitor', 'login')
  .option('--side <side>', 'buy | sell (order mode)', 'buy')
  .option('--symbol <symbol>', 'instrument', 'EURUSD')
  .option('--lots <n>', 'dummy order volume', '0.01')
  .option('--execute', 'order mode: really click Buy/Sell (default is a dry run)', false)
  .option('--close', 'order mode: close the dummy position afterwards', false)
  .option('--managed <path>', 'monitor mode: JSON file of managed positions (from bridge)')
  .option('--close-on-time', 'monitor: close when deadline is reached', true)
  .option('--signal-id <id>', 'strategy-order: signal_id for audit/dedup')
  .option('--sl <price>', 'strategy-order: stop-loss price')
  .option('--signal-file <path>', 'strategy-order: JSON file with signal fields')
  .parse(process.argv);
const o = program.opts();

const artifacts = () => { const d = path.join(process.cwd(), 'artifacts'); fs.mkdirSync(d, { recursive: true }); return d; };

async function main() {
  let policy;
  try { policy = loadPolicy(); } catch (e) {
    console.error(`[policy] ${(e as Error).message}`);
    writeAuthResult('failed', 'policy_error', 'demo', null, null, '');
    process.exitCode = 2; return;
  }
  const login = process.env.MT5_LOGIN;
  const password = process.env.MT5_PASSWORD;
  const url = process.env.WEB_TERMINAL_URL || (policy.mode === 'demo' ? 'https://hw.online/webterminal/mt5-demo/' : '');

  if (!login || !password || !url) {
    console.error('Missing MT5_LOGIN / MT5_PASSWORD' + (url ? '' : ' / WEB_TERMINAL_URL (required in real mode)'));
    writeAuthResult('failed', 'missing_credentials_or_url', policy.mode, null, null, url);
    process.exitCode = 1; return;
  }

  if (!['login', 'order', 'strategy-order', 'monitor'].includes(o.mode)) {
    console.error(`Unknown or removed mode: ${o.mode}. Headway collects no candles and supplies no prices; use login | order | strategy-order | monitor.`);
    process.exitCode = 2; return;
  }
  const symbol = String(o.symbol).toUpperCase();
  const orderReq = { symbol, side: (o.side === 'sell' ? 'sell' : 'buy') as 'buy' | 'sell', lots: Number(o.lots), execute: !!o.execute, closeAfter: !!o.close };
  if (!['login', 'order', 'strategy-order', 'monitor'].includes(o.mode)) {
    console.error(`Unknown or removed mode: ${o.mode}. Headway collects no candles or prices; allowed: login | order | strategy-order | monitor`);
    process.exitCode = 2; return;
  }
  if (o.mode === 'order') {
    try { assertOrderAllowed(policy, orderReq, process.env); }
    catch (e) { if (e instanceof OrderGuardError) { console.error(`[order] GUARD: ${e.message}. Nothing was sent; browser not launched.`); process.exitCode = 2; return; } throw e; }
  }
  let strategyReq: any = null;
  if (o.mode === 'strategy-order') {
    let fromFile: any = {};
    if (o.signalFile && fs.existsSync(o.signalFile)) {
      try { fromFile = JSON.parse(fs.readFileSync(o.signalFile, 'utf8')); if (Array.isArray(fromFile)) fromFile = fromFile[0] || {}; } catch { /* ignore */ }
    }
    strategyReq = {
      signal_id: String(o.signalId || fromFile.signal_id || fromFile.id || `manual-${Date.now()}`),
      symbol: String(fromFile.symbol || o.symbol || 'EURUSD').toUpperCase(),
      side: (String(fromFile.side || o.side || 'buy').toLowerCase() === 'sell' ? 'sell' : 'buy') as 'buy' | 'sell',
      lots: Number(fromFile.lots != null ? fromFile.lots : o.lots),
      entry: fromFile.entry != null ? Number(fromFile.entry) : undefined,
      sl: o.sl != null ? Number(o.sl) : (fromFile.sl != null ? Number(fromFile.sl) : undefined),
      execute: !!o.execute,
      closeAfter: !!o.close,
    };
    try { assertStrategyOrderAllowed(policy, strategyReq, process.env); }
    catch (e) { if (e instanceof OrderGuardError) { console.error(`[strategy-order] GUARD: ${e.message}. Nothing was sent; browser not launched.`); process.exitCode = 2; return; } throw e; }
  }

  console.log(`=== Headway MT5 Web Terminal Automation (${policy.mode.toUpperCase()} account) ===`);
  console.log(`Mode: ${o.mode}${o.mode === 'order' ? (o.execute ? ' (EXECUTE)' : ' (dry run)') : ''}`);
  console.log(`URL:  ${url.split('?')[0]}`);
  console.log('-------------------------------------------------------');

  const { browser, context, page } = await launchBrowser();

  try {
    const loggedIn = await performLogin(page, { login, password, url });
    const rep = getLastLoginReport();
    if (!loggedIn) {
      writeAuthResult('failed', rep?.reason ?? 'login_failed', policy.mode, null, null, url);
      console.error('Login failed. Aborting.');
      process.exitCode = 1; return;
    }
    writeAuthResult('verified', 'ok', policy.mode, rep?.server ?? policy.serverLabel, rep?.terminalHost ?? null, url);

    if (o.mode === 'strategy-order') {
      const report = await runStrategyOrder(page, policy, strategyReq!);
      console.log(`[strategy-order] outcome: ${report.outcome}; ticket=${report.ticket_id}`);
      if (report.outcome === 'executed_unconfirmed') console.error('[strategy-order] Clicked but ticket/margin not confirmed. CHECK THE ACCOUNT MANUALLY.');
      if (report.outcome === 'executed_confirmed_close_failed') console.error('[strategy-order] !!! CLOSE THE POSITION MANUALLY !!!');
      process.exitCode = strategyOrderExitCode(report.outcome);
    } else if (o.mode === 'order') {
      const report = await runDummyOrder(page, policy, orderReq);
      console.log(`[order] outcome: ${report.outcome}`);
      if (report.outcome === 'executed_confirmed_close_failed') console.error('[order] !!! CLOSE THE DUMMY POSITION MANUALLY !!!');
      if (report.outcome === 'executed_unconfirmed') console.error('[order] Buy/Sell was clicked but the position could not be verified. CHECK THE ACCOUNT MANUALLY.');
      process.exitCode = orderExitCode(report.outcome);
    } else if (o.mode === 'monitor') {
      let managed: ManagedPosition[] = [];
      if (o.managed && fs.existsSync(o.managed)) {
        try { managed = JSON.parse(fs.readFileSync(o.managed, 'utf8')); } catch (e) {
          console.error('[monitor] cannot parse --managed file:', e);
          process.exitCode = 1; return;
        }
      } else {
        console.log('[monitor] no --managed file; will only list open positions (no closes)');
      }
      const report = await monitorAndClose(page, policy, {
        managed,
        execute: !!o.execute,
        symbol,
        closeOnTime: o.closeOnTime !== false,
      });
      console.log(`[monitor] outcome: ${report.outcome}`);
      process.exitCode = monitorExitCode(report);
    } else {
      console.log('Login-only mode finished successfully.');
    }

    if (process.env.HEADLESS !== 'true') {
      console.log('Keeping authenticated terminal session open briefly...');
      await page.waitForTimeout(20000);
    }
  } catch (err) {
    console.error('Unexpected error:', err);
    await screenshotOnFailure(page, 'unexpected-error');
    process.exitCode = 1;
  } finally {
    await closeBrowser(browser, context);
  }
}

main().catch((e) => { console.error(e instanceof PolicyError ? e.message : e); process.exit(1); });
