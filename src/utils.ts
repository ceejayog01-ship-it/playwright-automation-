import { Page, Locator } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';

export async function findFirstVisible(
  page: Page,
  selectors: string[],
  timeout = 10000
): Promise<Locator | null> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    for (const sel of selectors) {
      try {
        const loc = page.locator(sel).first();
        if (await loc.isVisible({ timeout: 800 }).catch(() => false)) {
          return loc;
        }
      } catch {
        // continue
      }
    }
    await page.waitForTimeout(400);
  }
  return null;
}

export async function safeClick(
  page: Page,
  selectors: string[],
  description: string,
  timeout = 15000
): Promise<boolean> {
  const el = await findFirstVisible(page, selectors, timeout);
  if (!el) {
    console.error(`[safeClick] Could not find: ${description}`);
    return false;
  }
  await el.click({ timeout: 5000 });
  console.log(`[safeClick] Clicked: ${description}`);
  return true;
}

export async function safeFill(
  page: Page,
  selectors: string[],
  value: string,
  description: string,
  timeout = 15000
): Promise<boolean> {
  const el = await findFirstVisible(page, selectors, timeout);
  if (!el) {
    console.error(`[safeFill] Could not find: ${description}`);
    return false;
  }
  await el.fill(value, { timeout: 5000 });
  console.log(`[safeFill] Filled: ${description}`);
  return true;
}

export async function screenshotOnFailure(page: Page, name: string) {
  try {
    const dir = path.join(process.cwd(), 'artifacts');
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${name}-${Date.now()}.png`);
    await page.screenshot({ path: file, fullPage: true });
    console.log(`[screenshot] Saved → ${file}`);
  } catch (e) {
    console.warn('[screenshot] Failed to capture', e);
  }
}

export function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export function isDemoServer(serverText: string, blockedKeywords: string[]): boolean {
  const lower = serverText.toLowerCase();
  return !blockedKeywords.some((k) => lower.includes(k.toLowerCase()));
}
