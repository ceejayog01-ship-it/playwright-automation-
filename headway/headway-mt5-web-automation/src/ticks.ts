import { Page } from 'playwright';
import { readQuotes } from './quotes';

export interface Tick { t: number; bid: number; ask: number; }       // t = epoch ms (UTC)
export interface Bar { start: string; open: number; high: number; low: number; close: number; ticks: number; }

/** Aggregate bid ticks into UTC-aligned bars of `minutes`. Pure; used to export candles from the live session. */
export function aggregateTicks(ticks: Tick[], minutes: number): Bar[] {
  const span = minutes * 60_000;
  const bars = new Map<number, Bar>();
  for (const k of [...ticks].sort((a, b) => a.t - b.t)) {
    const s = Math.floor(k.t / span) * span;
    const b = bars.get(s);
    if (!b) bars.set(s, { start: new Date(s).toISOString(), open: k.bid, high: k.bid, low: k.bid, close: k.bid, ticks: 1 });
    else { b.high = Math.max(b.high, k.bid); b.low = Math.min(b.low, k.bid); b.close = k.bid; b.ticks++; }
  }
  return [...bars.entries()].sort((a, b) => a[0] - b[0]).map(e => e[1]);
}

export const ticksToCsv = (t: Tick[]) => 'timestamp,bid,ask\n' + t.map(k => `${new Date(k.t).toISOString()},${k.bid},${k.ask}`).join('\n') + '\n';
export const barsToCsv = (b: Bar[]) => 'timestamp,open,high,low,close,ticks\n' + b.map(x => `${x.start},${x.open},${x.high},${x.low},${x.close},${x.ticks}`).join('\n') + '\n';

export interface SampleResult { samples: Tick[]; failedPolls: number; aborted: boolean; }

/**
 * Poll Market Watch about once a second for `seconds`. EVERY successful poll is a sample (even if the price did not
 * change), so a quiet minute still counts as observed: M1/M30 coverage then measures what we actually saw.
 * Flushes periodically so a runner timeout/cancel never loses the data collected so far, and aborts if the quote
 * becomes unreadable for `maxConsecutiveFailures` polls (session dropped / logged out). Read-only.
 */
export async function collectSamples(
  page: Page, symbol: string, seconds: number,
  opts: { flushEverySec?: number; onFlush?: (s: Tick[]) => void; maxConsecutiveFailures?: number } = {}
): Promise<SampleResult> {
  const { flushEverySec = 300, onFlush, maxConsecutiveFailures = 60 } = opts;
  const samples: Tick[] = [];
  const end = Date.now() + seconds * 1000;
  let failedPolls = 0, streak = 0, aborted = false, lastFlush = Date.now();
  while (Date.now() < end) {
    const q = (await readQuotes(page, [symbol]))[0];
    if (q) { samples.push({ t: Date.now(), bid: q.bid, ask: q.ask }); streak = 0; }
    else { failedPolls++; if (++streak >= maxConsecutiveFailures) { aborted = true; break; } }
    if (onFlush && Date.now() - lastFlush >= flushEverySec * 1000) { onFlush(samples); lastFlush = Date.now(); }
    await page.waitForTimeout(1000);
  }
  return { samples, failedPolls, aborted };
}
