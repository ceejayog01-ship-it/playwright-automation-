/**
 * Account policy: replaces the former hard-coded "demo only" gates.
 *
 * ACCOUNT_MODE=demo (default) keeps the exact checks the verified login was built on.
 * ACCOUNT_MODE=real is an explicit opt-in and is stricter about what it will accept:
 *   - the declared mode must AGREE with the URL and with the visible server label (a demo label in real mode,
 *     or a real label in demo mode, aborts before any credential is typed);
 *   - EXPECTED_SERVER_LABEL (e.g. your real server's name as shown on the login form) is mandatory.
 * Pure functions only, so the whole policy is unit-tested without a browser.
 */
export type AccountMode = 'demo' | 'real';

export interface Policy {
  mode: AccountMode;
  entryHosts: string[];
  serverLabel: string;                 // canonical label recorded in auth_result.json
  terminalHost: (host: string) => boolean;
  blockedKeywords: string[];           // demo mode only
}

export class PolicyError extends Error {}

type Env = Record<string, string | undefined>;

export function loadPolicy(env: Env = process.env): Policy {
  const mode = (env.ACCOUNT_MODE || 'demo').trim().toLowerCase();
  if (mode !== 'demo' && mode !== 'real') throw new PolicyError(`ACCOUNT_MODE must be "demo" or "real", got "${mode}"`);
  const entryHosts = (env.ALLOWED_ENTRY_HOSTS || 'hw.online').split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
  const pinned = (env.EXPECTED_TERMINAL_HOST || '').trim().toLowerCase();

  if (mode === 'demo') {
    return {
      mode, entryHosts,
      serverLabel: 'Headway-Demo',
      terminalHost: h => (pinned ? h === pinned : h === 'demo-mt5.hw-terminal.com'),
      blockedKeywords: (env.BLOCKED_SERVER_KEYWORDS || 'Real,Live').split(',').map(s => s.trim()).filter(Boolean),
    };
  }
  const label = (env.EXPECTED_SERVER_LABEL || '').trim();
  if (!label) throw new PolicyError('ACCOUNT_MODE=real requires EXPECTED_SERVER_LABEL (the server name shown on the login form)');
  if (/demo/i.test(label)) throw new PolicyError('EXPECTED_SERVER_LABEL contains "demo" but ACCOUNT_MODE=real');
  return {
    mode, entryHosts,
    serverLabel: label,
    terminalHost: h => (pinned ? h === pinned : /(^|\.)hw-terminal\.com$/.test(h) && !/^demo/.test(h)),
    blockedKeywords: [],
  };
}

export function hostOf(value: string): string {
  try { return new URL(value).hostname.toLowerCase(); } catch { return ''; }
}

/** Entry URL must be an allowed host, and its path must agree with the declared mode. */
export function checkEntry(p: Policy, url: string): { ok: boolean; reason: string } {
  if (!p.entryHosts.includes(hostOf(url))) return { ok: false, reason: 'entry_host_not_allowed' };
  const demoPath = /mt5-demo/i.test(url);
  if (p.mode === 'demo' && !demoPath) return { ok: false, reason: 'demo_mode_but_url_not_demo' };
  if (p.mode === 'real' && demoPath) return { ok: false, reason: 'real_mode_but_url_is_demo' };
  return { ok: true, reason: 'ok' };
}

/** Visible server label on the login form must agree with the declared mode. */
export function checkServerLabel(p: Policy, visible: string): { ok: boolean; reason: string } {
  const text = visible.replace(/\s+/g, ' ').trim().toLowerCase();
  if (p.mode === 'demo') {
    if (p.blockedKeywords.some(k => text.includes(k.toLowerCase()))) return { ok: false, reason: 'demo_mode_but_label_is_real' };
    return /demo/.test(text) ? { ok: true, reason: 'ok' } : { ok: false, reason: 'label_not_explicitly_demo' };
  }
  if (/demo/.test(text)) return { ok: false, reason: 'real_mode_but_label_is_demo' };
  return text.includes(p.serverLabel.toLowerCase()) ? { ok: true, reason: 'ok' } : { ok: false, reason: 'label_does_not_match_expected' };
}
