#!/usr/bin/env node
import { Command } from 'commander';
import * as dotenv from 'dotenv';
import * as fs from 'fs';
import * as path from 'path';
import { launchBrowser, closeBrowser } from './browser';
import { performLogin, getLastLoginReport } from './login';
import { loadPolicy, PolicyError } from './policy';
import { writeAuthResult } from './authResult';
import { assertOrderAllowed, OrderGuardError, runDummyOrder, orderExitCode } from './order';
import { waitForQuote, readQuotes } from './quotes';
import { collectSamples, aggregateTicks, ticksToCsv, barsToCsv, Tick } from './ticks';
import { attachProbe } from './probe';
import { runChartProbe } from './chartprobe';
import { screenshotOnFailure } from './utils';

dotenv.config();

const program = new Command();
program
  .option('--mode <mode>', 'login | quotes | sample | ticks | probe | chartprobe | order', 'login')
  .option('--side <side>', 'buy | sell (order mode)', 'buy')
  .option('--symbol <symbol>', 'instrument', 'EURUSD')
  .option('--lots <n>', 'dummy order volume', '0.01')
  .option('--execute', 'order mode: really click Buy/Sell (default is a dry run)', false)
  .option('--close', 'order mode: close the dummy position afterwards', false)
  .option('--seconds <n>', 'ticks / probe / sample duration', '60')
  .option('--until <epoch>', 'sample mode: stop at this unix time (seconds), overrides --seconds')
  .option('--bar-minutes <n>', 'ticks mode bar size', '1')
  .option('--require', 'quotes mode: exit 1 if no quote is readable', false)
  .parse(process.argv);
const o = program.opts();

async function writeQuotesFile(page: any, symbol: string, mode: string, timeoutMs: number) {
  const q = await waitForQuote(page, symbol, timeoutMs);
  const all = await readQuotes(page);
  fs.writeFileSync(path.join(artifacts(), 'quotes.json'), JSON.stringify({
    schema: 1, captured_at: new Date().toISOString(), account_mode: mode,
    status: q ? 'ok' : 'symbol_not_found', quotes: all.filter((x: any) => x.symbol.toUpperCase() === symbol), visible_symbols: all.map((x: any) => x.symbol),
  }, null, 2) + '\n');
  return q;
}

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

  const symbol = String(o.symbol).toUpperCase();
  const orderReq = { symbol, side: (o.side === 'sell' ? 'sell' : 'buy') as 'buy' | 'sell', lots: Number(o.lots), execute: !!o.execute, closeAfter: !!o.close };
  if (o.mode === 'order') {
    try { assertOrderAllowed(policy, orderReq, process.env); }
    catch (e) { if (e instanceof OrderGuardError) { console.error(`[order] GUARD: ${e.message}. Nothing was sent; browser not launched.`); process.exitCode = 2; return; } throw e; }
  }

  console.log(`=== Headway MT5 Web Terminal Automation (${policy.mode.toUpperCase()} account) ===`);
  console.log(`Mode: ${o.mode}${o.mode === 'order' ? (o.execute ? ' (EXECUTE)' : ' (dry run)') : ''}`);
  console.log(`URL:  ${url.split('?')[0]}`);
  console.log('-------------------------------------------------------');

  const { browser, context, page } = await launchBrowser();
  const probe = (o.mode === 'probe' || o.mode === 'chartprobe') ? attachProbe(page, [login, password]) : null;   // attach BEFORE login to see the handshake
  if (o.mode === 'chartprobe') probe!.beginCapture('login_and_initial_load');

  try {
    const loggedIn = await performLogin(page, { login, password, url });
    const rep = getLastLoginReport();
    if (!loggedIn) {
      writeAuthResult('failed', rep?.reason ?? 'login_failed', policy.mode, null, null, url);
      console.error('Login failed. Aborting.');
      process.exitCode = 1; return;
    }
    writeAuthResult('verified', 'ok', policy.mode, rep?.server ?? policy.serverLabel, rep?.terminalHost ?? null, url);

    if (o.mode === 'quotes') {
      const q = await writeQuotesFile(page, symbol, policy.mode, 25000);
      console.log(q ? `[quotes] ${symbol} bid ${q.bid} ask ${q.ask}` : `[quotes] ${symbol} not readable`);
      if (!q && o.require) process.exitCode = 1;
    } else if (o.mode === 'sample') {
      // Scheduled-run sampler: the ONLY price source for the strategy and the lifecycle simulation.
      const first = await writeQuotesFile(page, symbol, policy.mode, 30000);
      if (!first) { console.error('[sample] no readable quote after login; aborting'); process.exitCode = 1; return; }
      const barMin = 1;
      const writeBars = (s: Tick[]) => fs.writeFileSync(path.join(artifacts(), `headway_bars_${symbol}_M${barMin}.csv`), barsToCsv(aggregateTicks(s, barMin)));
      const secs = o.until ? Math.max(30, Number(o.until) - Date.now() / 1000) : Number(o.seconds);
      console.log(`[sample] sampling for ${Math.round(secs)}s`);
      const res = await collectSamples(page, symbol, secs, { flushEverySec: 300, onFlush: writeBars });
      writeBars(res.samples);
      await writeQuotesFile(page, symbol, policy.mode, 5000);
      const status = {
        schema: 1, symbol, requested_seconds: Math.round(secs), samples: res.samples.length, failed_polls: res.failedPolls, aborted: res.aborted,
        first_sample: res.samples.length ? new Date(res.samples[0].t).toISOString() : null,
        last_sample: res.samples.length ? new Date(res.samples[res.samples.length - 1].t).toISOString() : null,
      };
      fs.writeFileSync(path.join(artifacts(), 'sample_status.json'), JSON.stringify(status, null, 2) + '\n');
      console.log(`[sample] ${res.samples.length} samples, ${res.failedPolls} failed polls${res.aborted ? ' (ABORTED: quote unreadable)' : ''}`);
      if (res.aborted || res.samples.length === 0) process.exitCode = 1;
    } else if (o.mode === 'ticks') {
      const res = await collectSamples(page, symbol, Number(o.seconds));
      const bars = aggregateTicks(res.samples, Number(o.barMinutes));
      fs.writeFileSync(path.join(artifacts(), `ticks_${symbol}.csv`), ticksToCsv(res.samples));
      fs.writeFileSync(path.join(artifacts(), `headway_bars_${symbol}_M${o.barMinutes}.csv`), barsToCsv(bars));
      console.log(`[ticks] ${res.samples.length} samples -> ${bars.length} bar(s)`);
      if (res.samples.length === 0) process.exitCode = 1;
    } else if (o.mode === 'chartprobe') {
      await runChartProbe(page, probe!, [login, password]);
    } else if (o.mode === 'probe') {
      await page.waitForTimeout(Number(o.seconds) * 1000);
      await probe!.finish();
    } else if (o.mode === 'order') {
      const report = await runDummyOrder(page, policy, orderReq);
      console.log(`[order] outcome: ${report.outcome}`);
      if (report.outcome === 'executed_confirmed_close_failed') console.error('[order] !!! CLOSE THE DUMMY POSITION MANUALLY !!!');
      if (report.outcome === 'executed_unconfirmed') console.error('[order] Buy/Sell was clicked but the position could not be verified. CHECK THE ACCOUNT MANUALLY.');
      process.exitCode = orderExitCode(report.outcome);
    } else {
      console.log('Login-only mode finished successfully.');
    }

    if (process.env.HEADLESS !== 'true') {
      console.log('Keeping browser open for 20 seconds for visual inspection...');
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
