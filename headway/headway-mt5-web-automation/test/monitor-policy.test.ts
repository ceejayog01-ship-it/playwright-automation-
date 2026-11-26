import * as assert from 'assert';
import { decideClose, parseTradeTab } from '../src/positions';
import { strategyOrderExitCode } from '../src/strategyOrder';

const dl = '2026-11-19T16:30:00Z';
assert.strictEqual(decideClose(dl, new Date('2026-11-19T16:29:59Z')), null);          // before the original time exit
assert.strictEqual(decideClose(dl, new Date('2026-11-19T16:30:00Z')), 'TIME');
assert.strictEqual(decideClose(dl, new Date('2026-11-19T20:00:00Z')), 'TIME');
assert.strictEqual(decideClose(dl, new Date('2026-11-19T20:00:00Z'), false), null);
assert.strictEqual(decideClose('garbage', new Date()), null);                          // never guess
// the monitor has no price-based (SL) close path any more: SL is broker-side
assert.strictEqual(decideClose(dl, new Date('2026-11-19T10:00:00Z')), null);
// trade-tab parsing still works for reconciliation
const rows = parseTradeTab('555001 EURUSD buy 0.50 1.10010 1.09800 12.5');
assert.strictEqual(rows.length, 1); assert.strictEqual(rows[0].ticket, '555001'); assert.strictEqual(rows[0].side, 'buy');
// unconfirmed / half-done outcomes are never exit code 0
assert.deepStrictEqual(['executed_confirmed_with_ticket', 'executed_unconfirmed', 'failed:x'].map(strategyOrderExitCode), [0, 3, 1]);
console.log('monitor-policy: all assertions passed');
