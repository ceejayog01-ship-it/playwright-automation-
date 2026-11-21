import { Page, Frame } from 'playwright';
import { Selectors } from '../config/selectors';
import { safeFill, safeClick, findFirstVisible, screenshotOnFailure, sleep, savePostLoginDiagnostics } from './utils';
import { Policy, loadPolicy, hostOf, checkEntry, checkServerLabel } from './policy';

export interface LoginCredentials {
  login: string;
  password: string;
  url: string;
}

/** Outcome of the most recent performLogin(), for the caller (index.ts) to record. Contains no credentials. */
export interface LoginReport { mode: 'demo' | 'real'; verified: boolean; reason: string; server: string | null; terminalHost: string | null; expectedLogin: string | null; observedLogin: string | null; observedServer: string | null; accountIdentityVerified: boolean; serverIdentityVerified: boolean; }
let lastLoginReport: LoginReport | null = null;
export function getLastLoginReport(): LoginReport | null { return lastLoginReport; }

async function inspectConnectedTerminal(page: Page, policy: Policy, entryUrl: string) {
  const frames = page.frames();
  let terminalHostName = '';
  let authError = false;
  let loginFormVisible = false;
  let terminalEvidence = false;
  const frameReports: Array<Record<string, unknown>> = [];

  for (const [index, frame] of frames.entries()) {
    const url = frame.url();
    const host = hostOf(url);
    const approvedTerminalFrame = policy.terminalHost(host);
    const text = await frame.locator('body').innerText({ timeout: 2500 }).catch(() => '');
    const normalized = text.replace(/\s+/g, ' ').trim();
    const lower = normalized.toLowerCase();
    const hasLoginInput = await frame.locator('input[type="password"], input[name*="login" i], input[placeholder*="login" i]').count().catch(() => 0);
    const visibleLoginInput = hasLoginInput > 0 && await frame.locator('input[type="password"], input[name*="login" i], input[placeholder*="login" i]').first().isVisible().catch(() => false);
    if (visibleLoginInput) loginFormVisible = true;
    if (approvedTerminalFrame) terminalHostName = host;
    if (/invalid (login|password)|authorization failed|account is disabled|connection failed|wrong password|login failed/i.test(lower)) authError = true;

    // Authentication evidence is a live terminal workspace, not account-number
    // matching, balances, Market Watch layout, or a visible price chart.
    const workspaceMarker = /market watch|toolbox|navigator|positions|orders|trade|quotes|disconnect|log out|logout/i.test(lower);
    if (approvedTerminalFrame && workspaceMarker) terminalEvidence = true;
    frameReports.push({ index, host, approvedTerminalFrame, visibleLoginInput, workspaceMarker, authError });
  }

  const entryOk = checkEntry(policy, entryUrl).ok;
  const success = entryOk && Boolean(terminalHostName) && terminalEvidence && !loginFormVisible && !authError;
  return { success, accountMode: policy.mode, terminalHostName, terminalEvidence, loginFormVisible, authError, frameReports };
}

export async function performLogin(page: Page, creds: LoginCredentials): Promise<boolean> {
  const policy = loadPolicy();
  const fail = (reason: string): false => {
    lastLoginReport = { mode: policy.mode, verified: false, reason, server: policy.serverLabel, terminalHost: null, expectedLogin: null, observedLogin: null, observedServer: null, accountIdentityVerified: false, serverIdentityVerified: false };
    return false;
  };
  console.log(`[login] Account mode: ${policy.mode.toUpperCase()}`);
  console.log('[login] Navigating to', creds.url);
  try {
    await page.goto(creds.url, { waitUntil: 'domcontentloaded', timeout: 60000 });
  } catch (e) {
    console.error('[login] Navigation failed:', e);
    await screenshotOnFailure(page, 'nav-failed');
    return fail('navigation_failed');
  }

  await sleep(4000);
  const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
  if (bodyText.includes('verifying') || bodyText.includes('cloudflare')) await sleep(8000);

  // Require an allowed Headway entry point whose path agrees with the declared mode.
  const entry = checkEntry(policy, creds.url);
  if (!entry.ok) {
    console.error(`[login] SAFETY ABORT: URL rejected for ${policy.mode} mode (${entry.reason}).`);
    await screenshotOnFailure(page, 'entry-url-abort');
    return fail(entry.reason);
  }

  // Read the exact visible server-name text, not a broad container such as
  // `div:has-text("Server")`. The latter matched the entire login panel in
  // the 2026-11-21 evidence screenshot and falsely classified Headway-Demo.
  let serverText = '';
  const expectedVisibleServer = policy.mode === 'demo' ? 'Headway-Demo' : policy.serverLabel;
  for (const frame of page.frames()) {
    const exact = frame.getByText(new RegExp(`^${expectedVisibleServer.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`, 'i')).first();
    if (await exact.isVisible({ timeout: 500 }).catch(() => false)) {
      serverText = (await exact.innerText().catch(() => '')).trim();
      if (serverText) break;
    }
  }
  if (!serverText) {
    console.error(`[login] Exact server name not visible (${expectedVisibleServer}); refusing to submit credentials.`);
    await screenshotOnFailure(page, 'server-label-missing');
    return fail('server_label_missing');
  }
  const label = checkServerLabel(policy, serverText);
  if (!label.ok) {
    console.error(`[login] SAFETY ABORT: visible server label rejected for ${policy.mode} mode (${label.reason}).`);
    await screenshotOnFailure(page, 'server-label-abort');
    return fail(label.reason);
  }
  console.log(`[login] ${policy.mode === 'demo' ? 'Demo' : 'Real'} server label verified:`, serverText.replace(/\s+/g, ' ').slice(0, 80));

  if (!await safeFill(page, Selectors.login.loginInput, creds.login, 'Login field')) {
    await screenshotOnFailure(page, 'login-field-missing'); return fail('login_field_missing');
  }
  if (!await safeFill(page, Selectors.login.passwordInput, creds.password, 'Password field')) {
    await screenshotOnFailure(page, 'password-field-missing'); return fail('password_field_missing');
  }
  if (!await safeClick(page, Selectors.login.connectButton, 'Connect to account button')) {
    await screenshotOnFailure(page, 'connect-button-missing'); return fail('connect_button_missing');
  }

  console.log(`[login] Credentials submitted to the confirmed ${policy.mode} endpoint; waiting for terminal state.`);
  const deadline = Date.now() + 45000;
  let result = await inspectConnectedTerminal(page, policy, creds.url);
  while (Date.now() < deadline && !result.success && !result.authError) {
    await sleep(1500);
    result = await inspectConnectedTerminal(page, policy, creds.url);
  }

  if (result.success) {
    console.log(`[login] AUTHENTICATED: ${policy.mode} terminal session established; no account-number or chart-display verification required.`);
    await savePostLoginDiagnostics(page, 'login-verified', result);
    lastLoginReport = { mode: policy.mode, verified: true, reason: 'authenticated_terminal_session', server: policy.serverLabel, terminalHost: result.terminalHostName, expectedLogin: null, observedLogin: null, observedServer: null, accountIdentityVerified: false, serverIdentityVerified: false };
    return true;
  }

  console.error('[login] AUTHENTICATION NOT CONFIRMED:', {
    terminalEvidence: result.terminalEvidence, loginFormVisible: result.loginFormVisible, authError: result.authError,
  });
  await savePostLoginDiagnostics(page, 'login-unverified', result);
  const reason = result.authError ? 'auth_error' : (result.loginFormVisible ? 'login_form_still_visible' : 'terminal_session_not_confirmed');
  lastLoginReport = {
    mode: policy.mode, verified: false, reason, server: policy.serverLabel,
    terminalHost: result.terminalHostName || null, expectedLogin: null,
    observedLogin: null, observedServer: null, accountIdentityVerified: false, serverIdentityVerified: false,
  };
  return false;
}
