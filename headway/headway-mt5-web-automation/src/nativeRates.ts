/** Validation gate for candles decoded from Headway's native historical-rate response.
 * This module validates records only; it intentionally does not guess/decode unknown binary frames.
 */
export interface NativeRate { time: number; open: number; high: number; low: number; close: number; }
export interface RateValidation { valid: boolean; count: number; errors: string[]; firstTime: string | null; lastTime: string | null; }
export function validateNativeM30Rates(rows: NativeRate[], required = 500): RateValidation {
  const errors: string[] = [];
  if (rows.length !== required) errors.push(`expected exactly ${required} candles, received ${rows.length}`);
  let previous = -Infinity;
  const seen = new Set<number>();
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    if (!Number.isInteger(r.time) || r.time <= 0) errors.push(`row ${i}: timestamp is not a positive integer epoch time`);
    if (seen.has(r.time)) errors.push(`row ${i}: duplicate timestamp ${r.time}`);
    seen.add(r.time);
    if (r.time <= previous) errors.push(`row ${i}: timestamps are not strictly ascending`);
    previous = r.time;
    const values = [r.open, r.high, r.low, r.close];
    if (!values.every(Number.isFinite) || values.some(v => v <= 0)) errors.push(`row ${i}: OHLC contains a non-finite or non-positive price`);
    if (Number.isFinite(r.high) && Number.isFinite(r.low) && (r.high < Math.max(r.open, r.close) || r.low > Math.min(r.open, r.close) || r.high < r.low)) errors.push(`row ${i}: inconsistent OHLC range`);
  }
  return { valid: errors.length === 0, count: rows.length, errors, firstTime: rows.length ? new Date(rows[0].time * 1000).toISOString() : null, lastTime: rows.length ? new Date(rows[rows.length - 1].time * 1000).toISOString() : null };
}
