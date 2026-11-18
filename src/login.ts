import { Page, Frame } from 'playwright';
import { Selectors } from '../config/selectors';
import { safeFill, safeClick, findFirstVisible, screenshotOnFailure, sleep, isDemoServer, savePostLoginDiagnostics } from './utils';

export interface LoginCredentials {
  login: string;
  password: string;
  url: string;
}

const DEMO_ENTRY_HOSTS = ['hw.online'];
const DEMO_TERMINAL_HOSTS = ['demo-mt5.hw-terminal.com'];

function hostOf(value: string): string {
  try { return new URL(value).hostname.toLowerCase(); } catch { return ''; }
}

async function visibleText(frame: Frame): Promise<string> {
  return frame.locator('body').innerText({ timeout: 2500 }).catch(() => '');
}

async function inspectConnectedTerminal(page: Page, expectedDemoUrl: string) {
  const frames = page.frames();
  const frameReports: Array<Record<string, unknown>> = [];
  let accountInfo = false;
  let marketWatch = false;
  let chart = false;
  let terminalHost = false;
  let authError = false;
  let accountSummary = '';

  const expectedHost = hostOf(expectedDemoUrl);
  const entryIsDemo = DEMO_ENTRY_HOSTS.includes(expectedHost) && /mt5-demo/i.test(expectedDemoUrl);

  for (const [index, frame] of frames.entries()) {
    const url = frame.url();
    const host = hostOf(url);
    const text = await visibleText(frame);
    const normalized = text.replace(/\s+/g, ' ').trim();
    const lower = normalized.toLowerCase();

    const hasBalance = /\bbalance\s*:/i.test(normalized);
    const hasEquity = /\bequity\s*:/i.test(normalized);
    const hasMargin = /\b(?:free\s+)?margin\s*:/i.test(normalized);
    if (hasBalance && (hasEquity || hasMargin)) {
      accountInfo = true;
      accountSummary = normalized.slice(Math.max(0, normalized.search(/\bbalance\s*:/i) - 20), 500);
    }

    const hasMarketHeaders = /\bsymbol\b/i.test(normalized) && /\bbid\b/i.test(normalized) && /\bask\b/i.test(normalized);
    const marketRows = await frame.locator('table tr, [role="row"]').count().catch(() => 0);
    const hasSymbolRows = await frame.locator('text=/^[A-Z]{6}\b/').count().catch(() => 0);
    if (hasMarketHeaders && (marketRows >= 2 || hasSymbolRows >= 2)) marketWatch = true;

    const canvasCount = await frame.locator('canvas').count().catch(() => 0);
    const chartText = /candlestick|chart|timeframe/i.test(lower);
    if (canvasCount > 0 || chartText) chart = true;

    if (DEMO_TERMINAL_HOSTS.includes(host)) terminalHost = true;
    if (/invalid (login|password)|authorization failed|account is disabled|connection failed|wrong password|login failed/i.test(lower)) authError = true;

    frameReports.push({
      index,
      url: url.split('?')[0],
      host,
      visibleTextLength: normalized.length,
      hasBalance,
      hasEquity,
      hasMargin,
      hasMarketHeaders,
      marketRows,
      hasSymbolRows,
      canvasCount,
      authError,
    });
  }

  const demoIdentityVerified = entryIsDemo && terminalHost;
  const terminalUiVerified = accountInfo && marketWatch && chart;
  return {
    success: demoIdentityVerified && terminalUiVerified && !authError,
    demoIdentityVerified,
    terminalUiVerified,
    accountInfo,
    marketWatch,
    chart,
    authError,
    accountSummary,
    frameReports,
  };
}

export async function performLogin(page: Page, creds: LoginCredentials): Promise<boolean> {
  console.log('[login] Navigating to', creds.url);
  try {
    await page.goto(creds.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  } catch (e) {
    console.error('[login] Navigation failed:', e);
    await screenshotOnFailure(page, 'nav-failed');
    return false;
  }

  await sleep(4000);
  const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
  if (bodyText.includes('verifying') || bodyText.includes('cloudflare')) await sleep(8000);

  // Require the intended Headway demo entry point and visible demo-server label.
  const entryHost = hostOf(creds.url);
  if (!DEMO_ENTRY_HOSTS.includes(entryHost) || !/mt5-demo/i.test(creds.url)) {
    console.error('[login] SAFETY ABORT: URL is not the configured Headway demo endpoint.');
    await screenshotOnFailure(page, 'non-demo-entry-abort');
    return false;
  }

  const serverEl = await findFirstVisible(page, Selectors.login.serverText, 10000);
  if (!serverEl) {
    console.error('[login] Demo server label not visible; refusing to submit credentials.');
    await screenshotOnFailure(page, 'demo-server-label-missing');
    return false;
  }
  const serverText = (await serverEl.innerText().catch(() => '')).trim();
  if (!isDemoServer(serverText, (process.env.BLOCKED_SERVER_KEYWORDS || 'Real,Live').split(',')) || !/demo/i.test(serverText)) {
    console.error('[login] SAFETY ABORT: visible server label is not explicitly a demo server.');
    await screenshotOnFailure(page, 'non-demo-server-abort');
    return false;
  }
  console.log('[login] Demo server label verified:', serverText);

  if (!await safeFill(page, Selectors.login.loginInput, creds.login, 'Login field')) {
    await screenshotOnFailure(page, 'login-field-missing'); return false;
  }
  if (!await safeFill(page, Selectors.login.passwordInput, creds.password, 'Password field')) {
    await screenshotOnFailure(page, 'password-field-missing'); return false;
  }
  if (!await safeClick(page, Selectors.login.connectButton, 'Connect to account button')) {
    await screenshotOnFailure(page, 'connect-button-missing'); return false;
  }

  console.log('[login] Credentials submitted to the confirmed demo endpoint; waiting for terminal state.');
  const deadline = Date.now() + 45000;
  let result = await inspectConnectedTerminal(page, creds.url);
  while (Date.now() < deadline && !result.success && !result.authError) {
    await sleep(1500);
    result = await inspectConnectedTerminal(page, creds.url);
  }

  if (result.success) {
    console.log('[login] VERIFIED: demo endpoint, account summary, Market Watch and chart are visible.');
    await savePostLoginDiagnostics(page, 'login-verified', result);
    return true;
  }

  console.error('[login] NOT VERIFIED:', {
    demoIdentityVerified: result.demoIdentityVerified,
    accountInfo: result.accountInfo,
    marketWatch: result.marketWatch,
    chart: result.chart,
    authError: result.authError,
  });
  await savePostLoginDiagnostics(page, 'login-unverified', result);
  return false;
}
