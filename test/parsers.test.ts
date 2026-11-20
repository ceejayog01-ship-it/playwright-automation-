import * as assert from 'assert';
import { parseQuotes, parseMargin } from '../src/quotes';
import { aggregateTicks, barsToCsv } from '../src/ticks';
import { buildRedactor } from '../src/probe';

// table layout
let q = parseQuotes('Symbol Bid Ask\nEURUSD 1.15432 1.15441 9\nGBPUSD 1.31000 1.31012 12');
assert.deepStrictEqual(q.map(x => x.symbol), ['EURUSD', 'GBPUSD']);
assert.strictEqual(q[0].bid, 1.15432); assert.strictEqual(q[0].ask, 1.15441);
// one-cell-per-line grid, broker suffix, extra numeric columns ignored
q = parseQuotes('SYMBOL\nBID\nASK\nEURUSDm\n1.15432\n1.15441\n0.5\n1.16\nXAUUSD\n2650.12\n2650.45');
assert.deepStrictEqual(q.map(x => x.symbol), ['EURUSDm', 'XAUUSD']);
assert.strictEqual(q[1].ask, 2650.45);
// garbage / crossed quotes are dropped, never guessed
assert.deepStrictEqual(parseQuotes('EURUSD 1.2 1.1'), []);
assert.deepStrictEqual(parseQuotes('Balance: 7,125.38 Equity: 7,125.38'), []);

// margin: not "Free Margin"
assert.strictEqual(parseMargin('Balance: $7,125.38 Equity: $7,125.38 Margin: $0.00 Free Margin: $7,125.38'), 0);
assert.strictEqual(parseMargin('Free Margin: $9.00 Margin: $1,234.50'), 1234.5);
assert.strictEqual(parseMargin('Free Margin: $9.00'), null);

// ticks -> bars (bid based, UTC aligned)
const base = Date.UTC(2026, 10, 19, 10, 0, 0);
const bars = aggregateTicks([
  { t: base + 5_000, bid: 1.1, ask: 1.1001 }, { t: base + 20_000, bid: 1.102, ask: 1.1021 },
  { t: base + 40_000, bid: 1.099, ask: 1.0991 }, { t: base + 70_000, bid: 1.1005, ask: 1.1006 },
], 1);
assert.strictEqual(bars.length, 2);
assert.deepStrictEqual([bars[0].open, bars[0].high, bars[0].low, bars[0].close, bars[0].ticks], [1.1, 1.102, 1.099, 1.099, 3]);
assert.strictEqual(bars[1].start, '2026-11-19T10:01:00.000Z');
assert.ok(barsToCsv(bars).startsWith('timestamp,open,high,low,close,ticks\n2026-11-19T10:00:00.000Z,1.1,'));

// probe redaction incl. UTF-16LE hex (MT5 encodes strings as UTF-16)
const r = buildRedactor(['12345678', 'S3cretPw']);
assert.strictEqual(r.text('login=12345678 pw=S3cretPw'), 'login=<redacted> pw=<redacted>');
const hex16 = Buffer.from('S3cretPw', 'utf16le').toString('hex');
assert.ok(!r.hex('aabb' + hex16 + 'ccdd').includes(hex16));
console.log('parsers: all assertions passed');
