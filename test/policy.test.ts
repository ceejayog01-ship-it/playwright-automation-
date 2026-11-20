import * as assert from 'assert';
import { loadPolicy, checkEntry, checkServerLabel, PolicyError } from '../src/policy';

const DEMO_URL = 'https://hw.online/webterminal/mt5-demo/';
const REAL_URL = 'https://hw.online/webterminal/mt5-real/';

// ---- demo (default) keeps the verified behaviour ----
const demo = loadPolicy({});
assert.strictEqual(demo.mode, 'demo');
assert.ok(checkEntry(demo, DEMO_URL).ok);
assert.strictEqual(checkEntry(demo, REAL_URL).reason, 'demo_mode_but_url_not_demo');
assert.strictEqual(checkEntry(demo, 'https://evil.example/mt5-demo/').reason, 'entry_host_not_allowed');
assert.ok(checkServerLabel(demo, 'Headway-Demo').ok);
assert.strictEqual(checkServerLabel(demo, 'Headway-Real').reason, 'demo_mode_but_label_is_real');
assert.strictEqual(checkServerLabel(demo, 'Headway-Live').reason, 'demo_mode_but_label_is_real');
assert.strictEqual(checkServerLabel(demo, 'Headway-Foo').reason, 'label_not_explicitly_demo');
assert.ok(demo.terminalHost('demo-mt5.hw-terminal.com'));
assert.ok(!demo.terminalHost('real-mt5.hw-terminal.com'));

// ---- real is opt-in and strict ----
assert.throws(() => loadPolicy({ ACCOUNT_MODE: 'real' }), PolicyError, 'real needs EXPECTED_SERVER_LABEL');
assert.throws(() => loadPolicy({ ACCOUNT_MODE: 'real', EXPECTED_SERVER_LABEL: 'Headway-Demo' }), PolicyError);
assert.throws(() => loadPolicy({ ACCOUNT_MODE: 'paper' }), PolicyError);
const real = loadPolicy({ ACCOUNT_MODE: 'real', EXPECTED_SERVER_LABEL: 'Headway-Real' });
assert.ok(checkEntry(real, REAL_URL).ok);
assert.strictEqual(checkEntry(real, DEMO_URL).reason, 'real_mode_but_url_is_demo');
assert.ok(checkServerLabel(real, 'Server  Headway-Real').ok);
assert.strictEqual(checkServerLabel(real, 'Headway-Demo').reason, 'real_mode_but_label_is_demo');
assert.strictEqual(checkServerLabel(real, 'Headway-Other').reason, 'label_does_not_match_expected');
assert.ok(real.terminalHost('real-mt5.hw-terminal.com'));
assert.ok(!real.terminalHost('demo-mt5.hw-terminal.com'), 'a demo terminal host can never satisfy real mode');
assert.ok(!real.terminalHost('evil.example.com'));
const pinned = loadPolicy({ ACCOUNT_MODE: 'real', EXPECTED_SERVER_LABEL: 'Headway-Real', EXPECTED_TERMINAL_HOST: 'x.hw-terminal.com' });
assert.ok(pinned.terminalHost('x.hw-terminal.com') && !pinned.terminalHost('real-mt5.hw-terminal.com'));
console.log('policy: all assertions passed');
