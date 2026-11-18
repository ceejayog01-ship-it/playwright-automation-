import { Frame, Locator, Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Headway's MetaTrader web terminal can render its connection form inside an
 * iframe. All UI lookup helpers therefore inspect the main document and every
 * attached frame instead of assuming the controls belong to the top page.
 */
function searchableFrames(page: Page): Frame[] {
  return page.frames().filter((frame) => !frame.isDetached());
}

export async function findFirstVisible(
  page: Page,
  selectors: readonly string[],
  timeout = 10000
): Promise<Locator | null> {
  const deadline = Date.now() + timeout;

  while (Date.now() < deadline) {
    for (const frame of searchableFrames(page)) {
      for (const selector of selectors) {
        try {
          const locator = frame.locator(selector).first();
          if (await locator.isVisible()) return locator;
        } catch {
          // A frame can navigate or detach while the terminal is loading.
        }
      }
    }
    await page.waitForTimeout(250);
  }

  return null;
}

export async function findVisibleText(
  page: Page,
  text: string,
  timeout = 5000
): Promise<{ frame: Frame; locator: Locator } | null> {
  const deadline = Date.now() + timeout;
  const escaped = text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const selectors = [`text=/${escaped}/i`, `*:text-is("${text}")`];

  while (Date.now() < deadline) {
    for (const frame of searchableFrames(page)) {
      for (const selector of selectors) {
        try {
          const locator = frame.locator(selector).first();
          if (await locator.isVisible()) return { frame, locator };
        } catch {
          // Continue through remaining selectors/frames.
        }
      }
    }
    await page.waitForTimeout(200);
  }
  return null;
}

export async function safeClick(
  page: Page,
  selectors: readonly string[],
  description: string,
  timeout = 15000
): Promise<boolean> {
  const element = await findFirstVisible(page, selectors, timeout);
  if (!element) {
    console.error(`[safeClick] Could not find visible control: ${description}`);
    return false;
  }

  try {
    await element.click({ timeout: 7000 });
    console.log(`[safeClick] Clicked: ${description}`);
    return true;
  } catch (error) {
    console.error(`[safeClick] Click failed for ${description}:`, error);
    return false;
  }
}

export async function safeFill(
  page: Page,
  selectors: readonly string[],
  value: string,
  description: string,
  timeout = 15000
): Promise<boolean> {
  const element = await findFirstVisible(page, selectors, timeout);
  if (!element) {
    console.error(`[safeFill] Could not find visible field: ${description}`);
    return false;
  }

  try {
    await element.fill(value, { timeout: 7000 });
    console.log(`[safeFill] Filled: ${description}`);
    return true;
  } catch (error) {
    console.error(`[safeFill] Fill failed for ${description}:`, error);
    return false;
  }
}

export async function logFrameDiagnostics(page: Page): Promise<void> {
  const frames = searchableFrames(page);
  console.log(`[diagnostics] Attached frames: ${frames.length}`);

  for (let index = 0; index < frames.length; index += 1) {
    const frame = frames[index];
    try {
      const fields = await frame.locator('input').evaluateAll((nodes) =>
        nodes.map((node) => {
          const input = node as HTMLInputElement;
          return {
            type: input.type || '',
            name: input.name || '',
            id: input.id || '',
            placeholder: input.placeholder || '',
            ariaLabel: input.getAttribute('aria-label') || '',
            autocomplete: input.autocomplete || '',
          };
        })
      );
      console.log(`[diagnostics] frame[${index}] url=${frame.url()} inputs=${JSON.stringify(fields)}`);
    } catch (error) {
      console.log(`[diagnostics] frame[${index}] inspection failed: ${String(error)}`);
    }
  }
}

export async function screenshotOnFailure(page: Page, name: string): Promise<void> {
  const dir = path.join(process.cwd(), 'artifacts');
  try {
    fs.mkdirSync(dir, { recursive: true });
    const stamp = Date.now();
    const screenshot = path.join(dir, `${name}-${stamp}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    console.log(`[screenshot] Saved: ${screenshot}`);

    const frameInfo = searchableFrames(page).map((frame, index) => ({
      index,
      url: frame.url(),
    }));
    fs.writeFileSync(
      path.join(dir, `${name}-${stamp}-frames.json`),
      JSON.stringify(frameInfo, null, 2)
    );
  } catch (error) {
    console.warn('[screenshot] Failure diagnostics could not be saved:', error);
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** A positive demo match is required; absence of a live keyword is not enough. */
export function isDemoServer(serverText: string, blockedKeywords: string[]): boolean {
  const normalized = serverText.trim().toLowerCase();
  const blocked = blockedKeywords.some((keyword) =>
    normalized.includes(keyword.trim().toLowerCase())
  );
  return normalized.includes('demo') && !blocked;
}
