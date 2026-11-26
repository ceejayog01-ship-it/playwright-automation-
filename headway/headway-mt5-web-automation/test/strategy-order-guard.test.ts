import { assertStrategyOrderAllowed, MAX_STRATEGY_LOTS } from '../src/strategyOrder';
import { OrderGuardError } from '../src/order';
import { loadPolicy } from '../src/policy';

process.env.ACCOUNT_MODE = 'demo';
const policy = loadPolicy();
const realPolicy = loadPolicy({ ACCOUNT_MODE: 'real' });
const base = { signal_id: 's1', symbol: 'EURUSD', side: 'buy' as const, lots: 0.01, execute: false };

assertStrategyOrderAllowed(policy, base, {});
try {
  assertStrategyOrderAllowed(policy, { ...base, lots: 1.0 }, {});
  throw new Error('should reject large lots');
} catch (e) { if (!(e instanceof OrderGuardError)) throw e; }

try {
  assertStrategyOrderAllowed(policy, { ...base, execute: true }, { ALLOW_ORDERS: 'no' });
  throw new Error('should reject execute without ALLOW_ORDERS');
} catch (e) { if (!(e instanceof OrderGuardError)) throw e; }

assertStrategyOrderAllowed(policy, { ...base, lots: MAX_STRATEGY_LOTS, execute: true }, { ALLOW_ORDERS: 'yes' });
try {
  assertStrategyOrderAllowed(realPolicy, { ...base, execute: true }, { ALLOW_ORDERS: 'yes', MT5_LOGIN: '9876543', REAL_ORDER_CONFIRM: 'REAL-6543' });
  throw new Error('real-account execution must be blocked');
} catch (e) { if (!(e instanceof OrderGuardError) || !e.message.startsWith('automated_execution_demo_only')) throw e; }
assertStrategyOrderAllowed(policy, { ...base, signal_id: 'x1', lots: 0.50, sl: 1.05, execute: false }, {});
console.log('strategy-order guard tests passed');
