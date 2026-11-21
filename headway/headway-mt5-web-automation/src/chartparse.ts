/**
 * Pure analysis helpers for the chart probe (no browser needed, fully unit-tested).
 * They answer: "does this text / JSON / binary frame contain EURUSD candles, and in what shape?"
 */

export interface Ohlc { o: number; h: number; l: number; c: number; time: string | null; }

const NUM = '(\\d{1,5}\\.\\d{2,6})';
const RE_COMPACT = new RegExp(`\\bO[:\\s=]*${NUM}\\s*,?\\s*H[:\\s=]*${NUM}\\s*,?\\s*L[:\\s=]*${NUM}\\s*,?\\s*C[:\\s=]*${NUM}`, 'i');
const RE_WORDS = new RegExp(`\\bOpen\\b[:\\s=]*${NUM}[\\s\\S]{0,40}?\\bHigh\\b[:\\s=]*${NUM}[\\s\\S]{0,40}?\\bLow\\b[:\\s=]*${NUM}[\\s\\S]{0,40}?\\bClose\\b[:\\s=]*${NUM}`, 'i');
const RE_TIME = /(\d{4})[.\-/](\d{2})[.\-/](\d{2})[ T,]+(\d{2}):(\d{2})/;

const plausible = (o: number, h: number, l: number, c: number) =>
  [o, h, l, c].every(v => Number.isFinite(v) && v > 0) && l <= Math.min(o, c) && h >= Math.max(o, c);

/** Extract one OHLC reading (and a date/time if shown) from on-screen text. Returns null if nothing consistent is found. */
export function parseOhlcText(text: string): Ohlc | null {
  const m = text.match(RE_COMPACT) || text.match(RE_WORDS);
  if (!m) return null;
  const [o, h, l, c] = [m[1], m[2], m[3], m[4]].map(Number);
  if (!plausible(o, h, l, c)) return null;
  const t = text.match(RE_TIME);
  return { o, h, l, c, time: t ? `${t[1]}-${t[2]}-${t[3]} ${t[4]}:${t[5]}` : null };
}

// ---------------------------------------------------------------------------------------------- JSON
export interface JsonOhlcScan { count: number; shape: string | null; }

/** Look for arrays of candles: objects {o,h,l,c} / {open,high,low,close} or numeric rows [t,o,h,l,c,...]. */
export function scanJsonForOhlc(value: unknown): JsonOhlcScan {
  let best: JsonOhlcScan = { count: 0, shape: null };
  const consider = (count: number, shape: string) => { if (count > best.count) best = { count, shape }; };
  const walk = (v: unknown, depth: number): void => {
    if (depth > 6 || v === null || typeof v !== 'object') return;
    if (Array.isArray(v)) {
      const objs = v.filter(x => x && typeof x === 'object' && !Array.isArray(x)) as Record<string, any>[];
      const short = objs.filter(x => ['o', 'h', 'l', 'c'].every(k => typeof x[k] === 'number'));
      const long = objs.filter(x => ['open', 'high', 'low', 'close'].every(k => typeof x[k] === 'number'));
      if (short.length) consider(short.length, 'objects{o,h,l,c}');
      if (long.length) consider(long.length, 'objects{open,high,low,close}');
      const rows = v.filter(x => Array.isArray(x) && x.length >= 5 && x.slice(0, 6).every(n => typeof n === 'number')) as number[][];
      const t5 = rows.filter(r => plausible(r[1], r[2], r[3], r[4]));
      if (t5.length) consider(t5.length, 'rows[t,o,h,l,c,...]');
      const t4 = rows.filter(r => plausible(r[0], r[1], r[2], r[3]));
      if (t4.length) consider(t4.length, 'rows[o,h,l,c,...]');
    }
    for (const x of Object.values(v as object)) walk(x, depth + 1);
  };
  walk(value, 0);
  return best;
}

// ---------------------------------------------------------------------------------------------- binary
export interface BinaryAnalysis {
  length: number;
  doublesInRange: number;          // 8-byte LE doubles that look like an EURUSD price
  ohlcRuns: number;                // 4 consecutive valid doubles (open,high,low,close)
  m30Timestamps: number;           // int64 values that are unix seconds/ms aligned to 30 minutes
  ratesLikeRecords: number;        // time(int64, M30-aligned) immediately followed by valid O,H,L,C doubles (MqlRates layout)
  recordStride: number | null;     // most common distance between rates-like records
  firstTimestamp: string | null;
  lastTimestamp: string | null;
}

export function analyzeBinary(buf: Buffer, lo = 0.8, hi = 1.6): BinaryAnalysis {
  const out: BinaryAnalysis = { length: buf.length, doublesInRange: 0, ohlcRuns: 0, m30Timestamps: 0, ratesLikeRecords: 0, recordStride: null, firstTimestamp: null, lastTimestamp: null };
  const inRange = (v: number) => Number.isFinite(v) && v >= lo && v <= hi;
  const dbl = (i: number) => (i >= 0 && i + 8 <= buf.length ? buf.readDoubleLE(i) : NaN);
  const validOhlcAt = (i: number) => {
    const [o, h, l, c] = [dbl(i), dbl(i + 8), dbl(i + 16), dbl(i + 24)];
    return [o, h, l, c].every(inRange) && plausible(o, h, l, c) && h - l < 0.05;
  };
  const rates: number[] = [];
  const times: number[] = [];
  for (let i = 0; i + 8 <= buf.length; i++) {
    if (inRange(dbl(i))) out.doublesInRange++;
    if (i + 32 <= buf.length && validOhlcAt(i)) { out.ohlcRuns++; i += 31; continue; }
  }
  for (let i = 0; i + 40 <= buf.length; i++) {
    const raw = Number(buf.readBigInt64LE(i));
    const secs = raw > 1.6e12 && raw < 1.95e12 ? raw / 1000 : raw;
    if (secs > 1.6e9 && secs < 1.95e9 && secs % 1800 === 0) {
      out.m30Timestamps++;
      if (validOhlcAt(i + 8)) { rates.push(i); times.push(secs); }
    }
  }
  out.ratesLikeRecords = rates.length;
  if (rates.length >= 3) {
    const diffs = new Map<number, number>();
    for (let k = 1; k < rates.length; k++) diffs.set(rates[k] - rates[k - 1], (diffs.get(rates[k] - rates[k - 1]) || 0) + 1);
    const [d, n] = [...diffs.entries()].sort((a, b) => b[1] - a[1])[0];
    if (n >= 2 && d >= 40 && d <= 256) out.recordStride = d;
    out.firstTimestamp = new Date(Math.min(...times) * 1000).toISOString();
    out.lastTimestamp = new Date(Math.max(...times) * 1000).toISOString();
  }
  return out;
}

/** Overwrite any occurrence of the secrets (UTF-8 and UTF-16LE) so a saved raw frame never contains credentials. */
export function redactBuffer(buf: Buffer, secrets: string[]): Buffer {
  const out = Buffer.from(buf);
  for (const s of secrets.filter(x => x && x.length >= 3)) {
    for (const enc of ['utf8', 'utf16le'] as const) {
      const needle = Buffer.from(s, enc);
      let at = out.indexOf(needle);
      while (at !== -1) { out.fill(0, at, at + needle.length); at = out.indexOf(needle, at + needle.length); }
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------- verdict
export interface ChartProbeSummary {
  chartFound: boolean;
  hoverDistinct: number;           // distinct OHLC readouts seen while hovering
  hoverWithTime: number;           // of those, how many also showed a date/time
  wsJsonOhlcMax: number;           // most candles found in any JSON frame
  wsRatesLikeMax: number;          // most MqlRates-like records found in any binary frame
  wsBinaryFramesAfterScroll: number;
  wsBytesAfterScroll: number;
}

export type Verdict = 'ws_json' | 'ws_binary_rates' | 'hover_text' | 'ws_binary_unknown' | 'none';

export function classifyChartProbe(s: ChartProbeSummary): { verdict: Verdict; advice: string } {
  if (s.wsJsonOhlcMax >= 10) return { verdict: 'ws_json', advice: 'History arrives as JSON over the WebSocket: parse it directly (easiest and exact).' };
  if (s.wsRatesLikeMax >= 10) return { verdict: 'ws_binary_rates', advice: 'History arrives as binary records shaped like MqlRates (int64 time + open/high/low/close doubles): a decoder is straightforward. Send the saved frames/*.bin.' };
  if (s.hoverDistinct >= 5 && s.hoverWithTime >= 3) return { verdict: 'hover_text', advice: 'OHLC and time are readable as text when hovering: extract bar by bar (slower, but exact and read-only).' };
  if (s.wsBinaryFramesAfterScroll >= 1 && s.wsBytesAfterScroll > 2000) return { verdict: 'ws_binary_unknown', advice: 'Scrolling loads data over a binary WebSocket, but the layout is not the simple MqlRates one. Send frames/*.bin and the report; a decoder may still be possible unless the stream is encrypted.' };
  return { verdict: 'none', advice: s.chartFound ? 'No readable candle source found (no hover text, no history frames). Native MT5 history (EA) is then the only reliable route.' : 'No chart canvas was found: check the screenshot, the terminal may not have loaded a chart.' };
}
