import { Page } from 'playwright';
import { Selectors } from '../config/selectors';
import { safeFill, safeClick, findFirstVisible, screenshotOnFailure, sleep, isDemoServer } from './utils';

export interface LoginCredentials {
  login: string;
  password: string;
  url: string;
}

export async function performLogin(page: Page, creds: LoginCredentials): Promise<boolean> {
  console.log('[login] Navigating to', creds.url);

  try {
    await page.goto(creds.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  } catch (e) {
    console.error('[login] Navigation failed (possible Cloudflare):', e);
    await screenshotOnFailure(page, 'nav-failed');
    return false;
  }

  // Give Cloudflare a chance to finish if it appears
  console.log('[login] Waiting for possible Cloudflare challenge...');
  await sleep(4000);

  // Check if we are still on a challenge page
  const bodyText = await page.locator('body').innerText().catch(() => '');
  if (bodyText.toLowerCase().includes('verifying') || bodyText.toLowerCase().includes('cloudflare')) {
    console.warn('[login] Cloudflare challenge detected. Waiting longer...');
    await sleep(8000);
  }

  // Safety: check server text if visible
  const blocked = (process.env.BLOCKED_SERVER_KEYWORDS || 'Real,Live').split(',');
  const serverEl = await findFirstVisible(page, Selectors.login.serverText, 5000);
  if (serverEl) {
    const serverText = (await serverEl.innerText().catch(() => '')) || '';
    console.log('[login] Detected server text:', serverText);
    if (!isDemoServer(serverText, blocked)) {
      console.error('[login] SAFETY ABORT: Server appears to be REAL/LIVE. Refusing to continue.');
      await screenshotOnFailure(page, 'real-server-abort');
      return false;
    }
  }

  // Fill credentials
  const loginOk = await safeFill(page, Selectors.login.loginInput, creds.login, 'Login field');
  if (!loginOk) {
    await screenshotOnFailure(page, 'login-field-missing');
    return false;
  }

  const passOk = await safeFill(page, Selectors.login.passwordInput, creds.password, 'Password field');
  if (!passOk) {
    await screenshotOnFailure(page, 'password-field-missing');
    return false;
  }

  // Optional: uncheck "Save password" for cleanliness
  // (we leave it alone for now)

  // Click Connect
  const clicked = await safeClick(page, Selectors.login.connectButton, 'Connect to account button');
  if (!clicked) {
    await screenshotOnFailure(page, 'connect-button-missing');
    return false;
  }

  // Wait for post-login UI
  console.log('[login] Waiting for trading UI...');
  await sleep(5000);

  // Success heuristic: look for SELL/BUY or Trade tab
  const successEl = await findFirstVisible(
    page,
    [...Selectors.trading.sellButton, ...Selectors.trading.buyButton, ...Selectors.trading.tradeTab],
    20000
  );

  if (successEl) {
    console.log('[login] SUCCESS – trading interface detected');
    await screenshotOnFailure(page, 'login-success');
    return true;
  }

  console.error('[login] Login appears to have failed – trading UI not found');
  await screenshotOnFailure(page, 'login-failed');
  return false;
}
