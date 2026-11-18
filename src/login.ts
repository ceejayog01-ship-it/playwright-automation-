import { Page } from 'playwright';
import { Selectors } from '../config/selectors';
import {
  safeFill,
  safeClick,
  findFirstVisible,
  findVisibleText,
  screenshotOnFailure,
  logFrameDiagnostics,
  sleep,
  isDemoServer,
} from './utils';

export interface LoginCredentials {
  login: string;
  password: string;
  url: string;
}

export async function performLogin(page: Page, creds: LoginCredentials): Promise<boolean> {
  console.log('[login] Navigating to Headway demo terminal:', creds.url);

  try {
    await page.goto(creds.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForLoadState('networkidle', { timeout: 12000 }).catch(() => {
      console.log('[login] Network remains active; continuing with frame inspection.');
    });
  } catch (error) {
    console.error('[login] Navigation failed:', error);
    await screenshotOnFailure(page, 'navigation-failed');
    return false;
  }

  // The MetaTrader connection form can appear after the outer page loads.
  await sleep(3500);
  await logFrameDiagnostics(page);

  const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
  if (bodyText.includes('cloudflare') || bodyText.includes('verify you are human')) {
    console.error('[login] Anti-bot verification page detected; refusing to bypass it.');
    await screenshotOnFailure(page, 'verification-challenge');
    return false;
  }

  // Require a positive demo-server label before entering credentials.
  const demoServer = await findVisibleText(page, 'Headway-Demo', 10000);
  if (!demoServer) {
    console.error('[login] DEMO SAFETY ABORT: visible Headway-Demo server label not found.');
    await screenshotOnFailure(page, 'demo-server-not-confirmed');
    return false;
  }

  const serverText = await demoServer.locator.innerText().catch(() => '');
  const blocked = (process.env.BLOCKED_SERVER_KEYWORDS || 'Real,Live').split(',');
  if (!isDemoServer(serverText || 'Headway-Demo', blocked)) {
    console.error('[login] DEMO SAFETY ABORT: server label is not positively identified as demo.');
    await screenshotOnFailure(page, 'non-demo-server-abort');
    return false;
  }
  console.log('[login] Demo server positively identified before authentication.');

  const loginOk = await safeFill(
    page,
    Selectors.login.loginInput,
    creds.login,
    'Login field',
    20000
  );
  if (!loginOk) {
    await logFrameDiagnostics(page);
    await screenshotOnFailure(page, 'login-field-missing');
    return false;
  }

  const passwordOk = await safeFill(
    page,
    Selectors.login.passwordInput,
    creds.password,
    'Password field',
    12000
  );
  if (!passwordOk) {
    await screenshotOnFailure(page, 'password-field-missing');
    return false;
  }

  const connectOk = await safeClick(
    page,
    Selectors.login.connectButton,
    'Connect to account button',
    12000
  );
  if (!connectOk) {
    await screenshotOnFailure(page, 'connect-button-missing');
    return false;
  }

  console.log('[login] Credentials submitted to the confirmed demo server; checking terminal response.');
  await sleep(7000);
  await logFrameDiagnostics(page);

  const errorText = await page.locator('body').innerText().catch(() => '');
  if (/invalid login|authorization failed|incorrect password|account disabled/i.test(errorText)) {
    console.error('[login] Headway rejected the demo credentials.');
    await screenshotOnFailure(page, 'credentials-rejected');
    return false;
  }

  const tradingUi = await findFirstVisible(
    page,
    [...Selectors.trading.sellButton, ...Selectors.trading.buyButton, ...Selectors.trading.tradeTab],
    20000
  );

  if (!tradingUi) {
    console.error('[login] Login not confirmed: trading interface controls were not detected.');
    await screenshotOnFailure(page, 'trading-interface-not-detected');
    return false;
  }

  console.log('[login] Trading interface detected after submission.');
  await screenshotOnFailure(page, 'login-success');
  return true;
}
