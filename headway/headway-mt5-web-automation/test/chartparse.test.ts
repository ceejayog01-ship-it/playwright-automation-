import * as assert from 'assert';
import { parseOhlcText, scanJsonForOhlc, analyzeBinary, redactBuffer, classifyChartProbe } from '../src/chartparse';

// ---------- hover text
let r = parseOhlcText('EURUSD,M30  O: 1.15432  H: 1.15500  L: 1.15400  C: 1.15450');
assert.deepStrictEqual([r!.o, r!.h, r!.l, r!.c, r!.time], [1.15432, 1.155, 1.154, 1.1545, null]);
r = parseOhlcText('2026.11.19 10:30\nOpen\n1.15432\nHigh\n1.15500\nLow\n1.15400\nClose\n1.15450');
assert.strictEqual(r!.time, '2026-11-19 10:30'); assert.strictEqual(r!.h, 1.155);
r = parseOhlcText('Date 2026-11-19 14:00  O1.1000 H1.1010 L1.0995 C1.1005');
assert.strictEqual(r!.time, '2026-11-19 14:00');
assert.strictEqual(parseOhlcText('O: 1.2 H: 1.1 L: 1.0 C: 1.15'), null, 'high below open is not a candle');
assert.strictEqual(parseOhlcText('Balance: 7,125.38 Equity: 7,125.38'), null);

// ---------- JSON
assert.deepStrictEqual(scanJsonForOhlc({ rates: Array.from({ length: 12 }, (_, i) => ({ o: 1.1, h: 1.11, l: 1.09, c: 1.1, t: i })) }), { count: 12, shape: 'objects{o,h,l,c}' });
assert.strictEqual(scanJsonForOhlc({ d: [[1700000000, 1.1, 1.11, 1.09, 1.1, 99]] }).shape, 'rows[t,o,h,l,c,...]');
assert.strictEqual(scanJsonForOhlc({ a: [1, 2, 3], b: 'x' }).count, 0);

// ---------- binary: MqlRates layout (time i64, O,H,L,C f64, tick_volume i64, spread i32, pad, real_volume i64 = 64 bytes)
function rates(n: number, ms = false): Buffer {
  const b = Buffer.alloc(n * 64);
  const t0 = 1_763_546_400;                                   // 2025-11-19 10:00:00 UTC, a multiple of 1800
  for (let i = 0; i < n; i++) {
    const o = 64 * i, p = 1.15 + (i % 7) * 0.0003;
    const ts = t0 + i * 1800;
    b.writeBigInt64LE(BigInt(ms ? ts * 1000 : ts), o);
    b.writeDoubleLE(p, o + 8); b.writeDoubleLE(p + 0.0008, o + 16); b.writeDoubleLE(p - 0.0006, o + 24); b.writeDoubleLE(p + 0.0002, o + 32);
    b.writeBigInt64LE(BigInt(500 + i), o + 40); b.writeInt32LE(12, o + 48); b.writeBigInt64LE(0n, o + 56);
  }
  return b;
}
let a = analyzeBinary(Buffer.concat([Buffer.from('HDR-junk-bytes!!'), rates(50)]));       // unaligned start on purpose
assert.strictEqual(a.ratesLikeRecords, 50); assert.strictEqual(a.recordStride, 64);
assert.strictEqual(a.firstTimestamp, '2025-11-19T10:00:00.000Z'); assert.strictEqual(a.lastTimestamp, '2025-11-20T10:30:00.000Z');
a = analyzeBinary(rates(30, true)); assert.strictEqual(a.ratesLikeRecords, 30, 'millisecond timestamps are recognised too');
const noise = Buffer.alloc(8000); for (let i = 0; i < noise.length; i++) noise[i] = (i * 2654435761) >>> 24;
a = analyzeBinary(noise); assert.strictEqual(a.ratesLikeRecords, 0); assert.strictEqual(a.ohlcRuns, 0);
assert.strictEqual(analyzeBinary(Buffer.alloc(0)).ratesLikeRecords, 0);

// ---------- redaction of saved frames
const secret = Buffer.concat([Buffer.from('xx12345678yy'), Buffer.from('S3cretPw', 'utf16le'), Buffer.from('zz')]);
const clean = redactBuffer(secret, ['12345678', 'S3cretPw']);
assert.ok(clean.indexOf(Buffer.from('12345678')) === -1 && clean.indexOf(Buffer.from('S3cretPw', 'utf16le')) === -1);
assert.strictEqual(clean.length, secret.length);

// ---------- verdict
const base = { chartFound: true, hoverDistinct: 0, hoverWithTime: 0, wsJsonOhlcMax: 0, wsRatesLikeMax: 0, wsBinaryFramesAfterScroll: 0, wsBytesAfterScroll: 0 };
assert.strictEqual(classifyChartProbe({ ...base, wsJsonOhlcMax: 300 }).verdict, 'ws_json');
assert.strictEqual(classifyChartProbe({ ...base, wsRatesLikeMax: 500 }).verdict, 'ws_binary_rates');
assert.strictEqual(classifyChartProbe({ ...base, hoverDistinct: 30, hoverWithTime: 30 }).verdict, 'hover_text');
assert.strictEqual(classifyChartProbe({ ...base, hoverDistinct: 30, hoverWithTime: 0 }).verdict, 'none', 'prices without times cannot be placed on the timeline');
assert.strictEqual(classifyChartProbe({ ...base, wsBinaryFramesAfterScroll: 4, wsBytesAfterScroll: 90000 }).verdict, 'ws_binary_unknown');
assert.match(classifyChartProbe({ ...base, chartFound: false }).advice, /No chart canvas/);
console.log('chartparse: all assertions passed');
