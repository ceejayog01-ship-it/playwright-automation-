/**
 * Native M30 history retrieval from the authenticated Headway MT5 Web Terminal.
 *
 * Attempts to read rates already loaded by the terminal (window/chart objects).
 * Does NOT manufacture candles from ticks/hover/pixels.
 * Validates with nativeRates when enough bars are present.
 * Fail-closed when insufficient or invalid.
 */
import { Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { validateNativeM30Rates, NativeRate } from './nativeRates';

export interface NativeBar {
  start: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface HistoryResult {
  ok: boolean;
  symbol: string;
  timeframe: string;
  bars: NativeBar[];
  source: string;
  message: string;
  captured_at: string;
  chartVerified?: boolean;
  validation?: { valid: boolean; count: number; errors: string[] };
}

const TARGET = 500;
const MIN_SOFT = Number(process.env.HISTORY_MIN_BARS || 120);

function isoFromMs(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function floor30Ms(ms: number): number {
  const d = new Date(ms);
  const m = d.getUTCMinutes();
  d.setUTCMinutes(m < 30 ? 0 : 30, 0, 0);
  return d.getTime();
}

async function allText(page: Page): Promise<string> {
  const parts: string[] = [];
  for (const f of page.frames()) {
    parts.push(await f.locator('body').innerText({ timeout: 2000 }).catch(() => ''));
  }
  return parts.join('\n');
}

/** Verify visible chart heading independently confirms EURUSD + M30. */
async function verifyChartHeading(page: Page, symbol: string): Promise<{ ok: boolean; title: string | null }> {
  const text = (await allText(page)).replace(/\s+/g, ' ');
  const re = new RegExp(`\\b(${symbol})\\s*,\\s*(M30)\\b`, 'i');
  const m = text.match(re);
  return { ok: !!m, title: m ? m[0].slice(0, 160) : null };
}

async function evaluateChartRates(page: Page, symbol: string): Promise<NativeBar[]> {
  const frames = page.frames();
  for (const frame of frames) {
    try {
      const raw = await frame.evaluate(({ sym }) => {
        const candidates: any[] = [];
        const w = window as any;
        if (Array.isArray(w.rates)) candidates.push(...w.rates);
        if (Array.isArray(w.chartRates)) candidates.push(...w.chartRates);
        if (Array.isArray(w.historyRates)) candidates.push(...w.historyRates);
        if (w.chart && Array.isArray(w.chart.rates)) candidates.push(...w.chart.rates);
        if (w.MT5 && Array.isArray(w.MT5.rates)) candidates.push(...w.MT5.rates);
        const maps = [w.ratesBySymbol, w.history, w.ohlc, w.bars];
        for (const m of maps) {
          if (m && typeof m === 'object') {
            const v = m[sym] || m[sym.toUpperCase()] || m[sym.toLowerCase()];
            if (Array.isArray(v)) candidates.push(...v);
          }
        }
        const seen = new Set<any>();
        const walk = (obj: any, depth: number) => {
          if (!obj || depth > 4 || seen.has(obj)) return;
          seen.add(obj);
          if (Array.isArray(obj) && obj.length > 10) {
            const sample = obj[0];
            if (sample && typeof sample === 'object' &&
                ('time' in sample || 't' in sample || 'Time' in sample) &&
                ('open' in sample || 'o' in sample || 'Open' in sample)) {
              candidates.push(...obj);
            }
          } else if (typeof obj === 'object') {
            for (const k of Object.keys(obj).slice(0, 40)) {
              try { walk(obj[k], depth + 1); } catch { /* ignore */ }
            }
          }
        };
        walk(w, 0);
        const out: any[] = [];
        for (const r of candidates) {
          if (!r || typeof r !== 'object') continue;
          const t = r.time ?? r.t ?? r.Time ?? r.datetime ?? r.date;
          const o = r.open ?? r.o ?? r.Open;
          const h = r.high ?? r.h ?? r.High;
          const l = r.low ?? r.l ?? r.Low;
          const c = r.close ?? r.c ?? r.Close;
          if (t == null || o == null || h == null || l == null || c == null) continue;
          let ms: number;
          if (typeof t === 'number') ms = t < 1e12 ? t * 1000 : t;
          else ms = Date.parse(String(t));
          if (!Number.isFinite(ms) || !Number.isFinite(+o)) continue;
          out.push({ ms, open: +o, high: +h, low: +l, close: +c, volume: r.volume ?? r.v ?? 0 });
        }
        const byMin = new Map<number, any>();
        for (const b of out.sort((a, b) => a.ms - b.ms)) {
          const key = Math.floor(b.ms / 60_000) * 60_000;
          byMin.set(key, b);
        }
        return [...byMin.values()].slice(-600);
      }, { sym: symbol });

      if (Array.isArray(raw) && raw.length >= 50) {
        return raw.map((b: any) => ({
          start: isoFromMs(floor30Ms(b.ms)),
          open: b.open, high: b.high, low: b.low, close: b.close,
          volume: b.volume,
        }));
      }
    } catch { /* continue */ }
  }
  return [];
}

async function ensureChartM30(page: Page, symbol: string): Promise<void> {
  const chartSelectors = [
    'text=Chart', '[role="tab"]:has-text("Chart")', 'button:has-text("Chart")',
  ];
  for (const sel of chartSelectors) {
    try {
      const loc = page.locator(sel).first();
      if (await loc.isVisible({ timeout: 1500 }).catch(() => false)) {
        await loc.click({ timeout: 2000 });
        await page.waitForTimeout(800);
        break;
      }
    } catch { /* continue */ }
  }
  const tfSelectors = [
    'text=M30', 'text=30m', 'text=30 min', '[title*="M30"]', '[data-period="30"]',
    'button:has-text("M30")', 'div:has-text("M30")',
  ];
  for (const sel of tfSelectors) {
    try {
      const loc = page.locator(sel).first();
      if (await loc.isVisible({ timeout: 1200 }).catch(() => false)) {
        await loc.click({ timeout: 1500 });
        await page.waitForTimeout(1200);
        break;
      }
    } catch { /* continue */ }
  }
  try {
    const search = page.locator('input[placeholder*="Symbol" i], input[placeholder*="symbol" i]').first();
    if (await search.isVisible({ timeout: 1000 }).catch(() => false)) {
      await search.fill(symbol);
      await page.waitForTimeout(400);
      await page.keyboard.press('Enter');
      await page.waitForTimeout(800);
    }
  } catch { /* ignore */ }
}

function toNativeRates(bars: NativeBar[]): NativeRate[] {
  return bars.map(b => ({
    time: Math.floor(new Date(b.start).getTime() / 1000),
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
  }));
}

export async function fetchNativeM30(
  page: Page,
  symbol = 'EURUSD',
  outDir = path.join(process.cwd(), 'artifacts'),
): Promise<HistoryResult> {
  fs.mkdirSync(outDir, { recursive: true });
  const captured_at = new Date().toISOString();
  const strict = (process.env.HISTORY_STRICT || '').toLowerCase() === '1' || (process.env.HISTORY_STRICT || '').toLowerCase() === 'true';
  const required = strict ? TARGET : MIN_SOFT;

  await ensureChartM30(page, symbol);
  await page.waitForTimeout(1500);

  const heading = await verifyChartHeading(page, symbol);
  let bars = await evaluateChartRates(page, symbol);

  const m30 = new Map<string, NativeBar>();
  for (const b of bars) {
    const t = new Date(b.start);
    if (t.getUTCMinutes() % 30 !== 0 || t.getUTCSeconds() !== 0) continue;
    if (!(b.high >= Math.max(b.open, b.close) && b.low <= Math.min(b.open, b.close) && b.low > 0)) continue;
    m30.set(b.start, b);
  }
  bars = [...m30.values()].sort((a, b) => a.start.localeCompare(b.start)).slice(-TARGET);

  let validation: HistoryResult['validation'] | undefined;
  if (bars.length === TARGET) {
    const v = validateNativeM30Rates(toNativeRates(bars), TARGET);
    validation = { valid: v.valid, count: v.count, errors: v.errors.slice(0, 20) };
  } else if (bars.length > 0) {
    // Validate structure even if count != 500 (ordering, OHLC consistency)
    const v = validateNativeM30Rates(toNativeRates(bars), bars.length);
    validation = { valid: v.valid && bars.length >= required, count: v.count, errors: [
      ...(bars.length < required ? [`expected at least ${required} candles, received ${bars.length}`] : []),
      ...v.errors.slice(0, 15),
    ]};
  } else {
    validation = { valid: false, count: 0, errors: ['no bars retrieved'] };
  }

  const enough = bars.length >= required && (validation?.valid !== false || bars.length >= required);
  const structureOk = !validation || validation.errors.every(e => e.includes('expected'));
  // Prefer validation.valid when count matches required length for soft mode
  let ok = false;
  if (bars.length >= required) {
    const v = validateNativeM30Rates(toNativeRates(bars), bars.length);
    ok = v.valid && heading.ok;
    validation = { valid: v.valid, count: v.count, errors: v.errors.slice(0, 20) };
  }

  const result: HistoryResult = {
    ok,
    symbol,
    timeframe: 'M30',
    bars: ok ? bars : bars, // still write what we got for diagnostics
    source: 'headway_native',
    message: ok
      ? `retrieved ${bars.length} validated native M30 bars (chart=${heading.title})`
      : `insufficient/invalid native bars (${bars.length}, required>=${required}, chartVerified=${heading.ok}); run chartprobe and refine history.ts — no invented candles`,
    captured_at,
    chartVerified: heading.ok,
    validation,
  };

  const jsonPath = path.join(outDir, `history_${symbol}_M30.json`);
  fs.writeFileSync(jsonPath, JSON.stringify(result, null, 2) + '\n');
  const csvPath = path.join(outDir, `history_${symbol}_M30.csv`);
  const csv = 'timestamp,open,high,low,close\n' +
    bars.map(b => `${b.start},${b.open},${b.high},${b.low},${b.close}`).join('\n') + '\n';
  fs.writeFileSync(csvPath, csv);
  console.log(`[history] ${result.message} -> ${jsonPath}`);
  return result;
}
