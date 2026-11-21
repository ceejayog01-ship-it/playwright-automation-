import { Page } from 'playwright';

export interface Quote { symbol: string; bid: number; ask: number; }

const SYMBOL = /^[A-Z]{6}(?:[.#_][A-Za-z0-9]*|[a-z]{1,2})?$/;   // EURUSD, EURUSDm, EURUSD.pro
const NUM = /^\d+\.\d+$/;

/**
 * Parse Market Watch text. Works for both a table ("EURUSD 1.15432 1.15441 ...") and a grid that
 * yields one cell per line, by scanning tokens: a symbol-like token starts a record, its next two
 * decimals are bid and ask. Split-digit "superscript pip" renderings are NOT handled (the probe will show if needed).
 */
export function parseQuotes(text: string): Quote[] {
  const tokens = text.split(/\s+/).filter(Boolean);
  const out: Quote[] = [];
  let cur: { symbol: string; nums: number[] } | null = null;
  const flush = () => {
    if (cur && cur.nums.length >= 2) {
      const [bid, ask] = cur.nums;
      if (bid > 0 && ask >= bid) out.push({ symbol: cur.symbol, bid, ask });
    }
    cur = null;
  };
  for (const t of tokens) {
    if (SYMBOL.test(t)) { flush(); cur = { symbol: t, nums: [] }; continue; }
    if (cur && cur.nums.length < 2 && NUM.test(t)) cur.nums.push(Number(t));
  }
  flush();
  return out;
}

export async function readQuotes(page: Page, symbols?: string[]): Promise<Quote[]> {
  const seen = new Map<string, Quote>();
  for (const frame of page.frames()) {
    const text = await frame.locator('body').innerText({ timeout: 2500 }).catch(() => '');
    for (const q of parseQuotes(text)) if (!seen.has(q.symbol)) seen.set(q.symbol, q);
  }
  const all = [...seen.values()];
  return symbols ? all.filter(q => symbols.map(s => s.toUpperCase()).includes(q.symbol.toUpperCase())) : all;
}

export async function waitForQuote(page: Page, symbol: string, timeoutMs = 20000): Promise<Quote | null> {
  const end = Date.now() + timeoutMs;
  while (Date.now() < end) {
    const q = (await readQuotes(page, [symbol]))[0];
    if (q) return q;
    await page.waitForTimeout(1000);
  }
  return null;
}

/** Account "Margin: $x" value (not "Free Margin"). Used as hard evidence that a position opened/closed. */
export function parseMargin(text: string): number | null {
  const m = text.match(/(?<!Free\s)\bMargin\s*:\s*\$?\s*([\d,]+(?:\.\d+)?)/i);
  return m ? Number(m[1].replace(/,/g, '')) : null;
}

export async function readMargin(page: Page): Promise<number | null> {
  for (const frame of page.frames()) {
    const text = await frame.locator('body').innerText({ timeout: 2500 }).catch(() => '');
    const v = parseMargin(text.replace(/\s+/g, ' '));
    if (v !== null) return v;
  }
  return null;
}
