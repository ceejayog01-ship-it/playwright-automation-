import {
  assertStrategyOrderAllowed,
  MAX_STRATEGY_LOTS
} from '../src/strategyOrder';

import { OrderGuardError } from '../src/order';
import { loadPolicy } from '../src/policy';

process.env.ACCOUNT_MODE = 'demo';

const policy = loadPolicy();

const base = {
  signal_id: 's1',
  symbol: 'EURUSD',
  side: 'buy' as const,
  lots: 0.01,
  execute: false
};

// Test 1: A valid strategy order must be accepted.
assertStrategyOrderAllowed(policy, base, {});

// Test 2: An order exceeding the permitted lot size must be rejected.
try {
  assertStrategyOrderAllowed(
    policy,
    { ...base, lots: 1.0 },
    {}
  );

  throw new Error('should reject large lots');
} catch (e) {
  if (!(e instanceof OrderGuardError)) throw e;
}

// Test 3: Order execution must be rejected when ALLOW_ORDERS is not enabled.
try {
  assertStrategyOrderAllowed(
    policy,
    { ...base, execute: true },
    { ALLOW_ORDERS: 'no' }
  );

  throw new Error('should reject execute without ALLOW_ORDERS');
} catch (e) {
  if (!(e instanceof OrderGuardError)) throw e;
}

// Test 4: An order within the configured maximum must pass
// when order execution is explicitly allowed.
assertStrategyOrderAllowed(
  policy,
  {
    ...base,
    lots: MAX_STRATEGY_LOTS,
    execute: true
  },
  { ALLOW_ORDERS: 'yes' }
);

// Test 5: A valid signal ID must be accepted.
// Signal IDs must contain at least two characters.
assertStrategyOrderAllowed(
  policy,
  {
    ...base,
    signal_id: 'x1',
    lots: 0.50,
    sl: 1.05,
    execute: false
  },
  {}
);

console.log('strategy-order guard tests passed');
