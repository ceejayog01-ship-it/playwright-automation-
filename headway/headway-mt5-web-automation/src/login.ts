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

async function visibleText(frame: Frame): Promise<string> {
  return frame.locator('body').innerText({ timeout: 2500 }).catch(() => '');
}

/** Read only explicit, visible labels from the authenticated terminal origin.
 * Supports both inline labels ("Login: 123456") and stacked UI labels where
 * the value is rendered on the following line. Never infers identity from
 * credentials, URLs, form values, balance, or chart contents. */
function labelledValues(text: string, kind: 'login' | 'server'): string[] {
  const lines = text.split(/\r?\n/).map((v) => v.replace(/\s+/g, ' ').trim()).filter(Boolean);
  const values: string[] = [];
  const label = kind === 'login'
    ? /^(?:login|account(?:\s*(?:number|no\.?|id))?|account\s*#)\s*[:#-]?\s*(.*)$/i
    : /^(?:server|trade\s*server)\s*[:#-]?\s*(.*)$/i;
  for (let i = 0; i < lines.length; i++) {
    const match = lines[i].match(label);
    if (!match) continue;
    let value = (match[1] || '').trim();
    if (!value && lines[i + 1]) value = lines[i + 1].trim();
    if (kind === 'login') {
      const numeric = value.match(/(?:^|\D)(\d{5,})(?:\D|$)/);
      if (numeric) values.push(numeric[1]);
    } else {
      // Keep the server token only; do not accept arbitrary neighbouring UI text.
      const server = value.match(/^([A-Za-z0-9][A-Za-z0-9._ -]{2,59})$/);
      if (server) values.push(server[1].trim());
    }
  }
  return [...new Set(values)];
}

async function inspectConnectedTerminal(page: Page, policy: Policy, entryUrl: string) {
  const frames = page.frames();
  const frameReports: Array<Record<string, unknown>> = [];
  const loginCandidates = new Set<string>();
  const serverCandidates = new Set<string>();
  let accountInfo = false;
  let marketWatch = false;
  let chart = false;
  let terminalHost = false;
  let terminalHostName = '';
  let authError = false;
  let accountSummary = '';
  const identityFrames: number[] = [];

  const entryOk = checkEntry(policy, entryUrl).ok;
  for (const [index, frame] of frames.entries()) {
    const url = frame.url();
    const host = hostOf(url);
    const approvedTerminalFrame = policy.terminalHost(host);
    const text = await visibleText(frame);
    const normalized = text.replace(/\s+/g, ' ').trim();
    const lower = normalized.toLowerCase();

    // The web terminal may render account identity and server in separate
    // same-origin subframes. Aggregate explicit labels only from approved
    // terminal frames, then reject conflicting/multiple candidates below.
    const frameLogins = approvedTerminalFrame ? labelledValues(text, 'login') : [];
    const frameServers = approvedTerminalFrame ? labelledValues(text, 'server') : [];
    frameLogins.forEach((v) => loginCandidates.add(v));
    frameServers.forEach((v) => serverCandidates.add(v));
    if (frameLogins.length || frameServers.length) identityFrames.push(index);

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

    if (approvedTerminalFrame) { terminalHost = true; terminalHostName = host; }
    if (/invalid (login|password)|authorization failed|account is disabled|connection failed|wrong password|login failed/i.test(lower)) authError = true;

    frameReports.push({
      index, url: url.split('?')[0], host, approvedTerminalFrame,
      loginCandidateCount: frameLogins.length, serverCandidateCount: frameServers.length,
      visibleTextLength: normalized.length, hasBalance, hasEquity, hasMargin,
      hasMarketHeaders, marketRows, hasSymbolRows, canvasCount, authError,
    });
  }

  // Ambiguous identity is a hard failure. The exact numeric login and exact
  // expected server must each be uniquely visible on approved terminal frames.
  const observedLogin = loginCandidates.size === 1 ? [...loginCandidates][0] : null;
  const observedServer = serverCandidates.size === 1 ? [...serverCandidates][0] : null;
  const expectedLogin = (process.env.MT5_LOGIN || '').trim();
  const accountIdentityVerified = /^\d{5,}$/.test(expectedLogin) && observedLogin === expectedLogin;
  const expectedServer = (process.env.EXPECTED_SERVER_LABEL || policy.serverLabel).trim();
  const serverIdentityVerified = Boolean(observedServer) && observedServer!.toLowerCase() === expectedServer.toLowerCase();
  const identityVerified = entryOk && terminalHost && identityFrames.length > 0 && accountIdentityVerified && serverIdentityVerified;
  const terminalUiVerified = accountInfo && marketWatch && chart;
  return {
    success: identityVerified && terminalUiVerified && !authError,
    accountMode: policy.mode, identityVerified,
    expectedLogin, observedLogin, observedServer, expectedServer,
    loginCandidateCount: loginCandidates.size, serverCandidateCount: serverCandidates.size,
    accountIdentityVerified, serverIdentityVerified, identityEvidenceFrames: identityFrames,
    terminalHostName, terminalUiVerified, accountInfo, marketWatch, chart,
    authError, accountSummary, frameReports,
  };
}

export async function performLogin(page: Page, creds: LoginCredentials): Promise<boolean> {
  const policy = loadPolicy();
  const fail = (reason: string): false => {
    lastLoginReport = { mode: policy.mode, verified: false, reason, server: null, terminalHost: null, expectedLogin: creds.login || null, observedLogin: null, observedServer: null, accountIdentityVerified: false, serverIdentityVerified: false };
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

  const serverEl = await findFirstVisible(page, Selectors.login.serverText, 10000);
  if (!serverEl) {
    console.error('[login] Server label not visible; refusing to submit credentials.');
    await screenshotOnFailure(page, 'server-label-missing');
    return fail('server_label_missing');
  }
  const serverText = (await serverEl.innerText().catch(() => '')).trim();
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
    console.log(`[login] VERIFIED: ${policy.mode} endpoint, account summary, Market Watch and chart are visible.`);
    // Balances are not written to the machine-readable report; the screenshot artifact does show them (keep it private).
    const { accountSummary: _omit, ...safeReport } = result;
    await savePostLoginDiagnostics(page, 'login-verified', { ...safeReport, accountSummaryCaptured: Boolean(_omit) });
    lastLoginReport = { mode: policy.mode, verified: true, reason: 'ok', server: result.observedServer, terminalHost: result.terminalHostName, expectedLogin: result.expectedLogin, observedLogin: result.observedLogin, observedServer: result.observedServer, accountIdentityVerified: result.accountIdentityVerified, serverIdentityVerified: result.serverIdentityVerified };
    return true;
  }

  console.error('[login] NOT VERIFIED:', {
    identityVerified: result.identityVerified,
    accountIdentityVerified: result.accountIdentityVerified, serverIdentityVerified: result.serverIdentityVerified,
    accountInfo: result.accountInfo,
    marketWatch: result.marketWatch,
    chart: result.chart,
    authError: result.authError,
  });
  await savePostLoginDiagnostics(page, 'login-unverified', result);
  const reason = result.authError ? 'auth_error' : (!result.accountIdentityVerified ? 'numeric_account_identity_unverified' : (!result.serverIdentityVerified ? 'connected_server_unverified' : 'terminal_not_verified'));
  lastLoginReport = {
    mode: policy.mode, verified: false, reason, server: result.observedServer || null,
    terminalHost: result.terminalHostName || null, expectedLogin: result.expectedLogin || null,
    observedLogin: result.observedLogin || null, observedServer: result.observedServer || null,
    accountIdentityVerified: result.accountIdentityVerified, serverIdentityVerified: result.serverIdentityVerified,
  };
  return false;
}
