import { validateNativeM30Rates } from '../src/nativeRates';
const rows = Array.from({ length: 500 }, (_, i) => ({ time: 1800000000 + i * 1800, open: 1.1, high: 1.101, low: 1.099, close: 1.1005 }));
if (!validateNativeM30Rates(rows).valid) throw new Error('valid 500-row M30 fixture rejected');
if (validateNativeM30Rates(rows.slice(0, 499)).valid) throw new Error('499-row fixture must fail');
const duplicate = rows.slice(); duplicate[10] = { ...duplicate[10], time: duplicate[9].time };
if (validateNativeM30Rates(duplicate).valid) throw new Error('duplicate timestamp fixture must fail');
console.log('Native M30 rate validation tests passed');
