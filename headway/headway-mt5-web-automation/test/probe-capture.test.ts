import * as assert from 'assert';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { EventEmitter } from 'events';

// rawRoot is derived from process.cwd() when attachProbe() runs, so work in a scratch directory.
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'probe-capture-'));
process.chdir(scratch);
import { attachProbe } from '../src/probe';

class FakeWS extends EventEmitter { url() { return 'wss://demo-mt5.hw-terminal.com/terminal?token=abc'; } }
const LOGIN = '12345678', PASS = 'S3cretPw';

(async () => {
  const page: any = new EventEmitter();
  page.frames = () => [];
  const probe = attachProbe(page, [LOGIN, PASS]);
  const ws = new FakeWS();
  page.emit('websocket', ws);

  // 36 frames like the real startup (sizes copied from the failed-run manifest), incl. a login handshake with UTF-16 secrets
  const handshake = Buffer.concat([Buffer.from('HELLO'), Buffer.from(LOGIN, 'utf16le'), Buffer.from(PASS, 'utf16le'), Buffer.from('TAIL')]);
  const sizes = [120, 184, 24, 24, 10024, 4376, 9048, 158360, 24, 30712, 33496, 30712, 2760, 41640, 21432];
  ws.emit('framesent', { payload: handshake });
  const incoming: Buffer[] = [];
  sizes.forEach((n, i) => { const b = Buffer.alloc(n, (i % 250) + 1); incoming.push(b); ws.emit('framereceived', { payload: b }); ws.emit('framesent', { payload: Buffer.alloc(24, 9) }); });
  ws.emit('framereceived', { payload: JSON.stringify({ ok: true, note: `password=${PASS}` }) });   // a text frame with a credential field
  const total = 1 + sizes.length * 2 + 1;

  const out = path.join(scratch, 'out');
  await probe.finish(out);

  const raw = path.join(scratch, 'artifacts', 'chart-probe', 'websocket-raw');
  const files = fs.readdirSync(raw).filter(f => f !== 'websocket-frames.json');
  const manifest = JSON.parse(fs.readFileSync(path.join(raw, 'websocket-frames.json'), 'utf8')).frames as any[];

  // 1) the previous run's failure: names must be interpolated and unique (every frame kept, none overwritten)
  assert.strictEqual(manifest.length, total, 'every frame is in the manifest');
  assert.ok(files.every(f => /^ws-\d{3}-(incoming|outgoing)-\d{6}\.(bin|txt|sanitized\.bin)$/.test(f)), 'file names are fully interpolated: ' + files.filter(f => !/^ws-\d{3}-/.test(f)).join(','));
  assert.strictEqual(new Set(manifest.map(m => m.file)).size, total, 'manifest file names are unique');
  manifest.forEach(m => assert.ok(fs.existsSync(path.join(raw, m.file)), `manifest file exists on disk: ${m.file}`));
  assert.ok(manifest.filter(m => m.rawStored === false).every(m => m.direction === 'outgoing' && m.file === m.sanitizedFile), 'only outgoing binary frames skip the raw copy');

  // 2) incoming binary frames are stored byte-exact (offsets preserved for decoding)
  const inc = manifest.filter(m => m.direction === 'incoming' && m.kind === 'binary');
  assert.strictEqual(inc.length, sizes.length);
  inc.forEach((m, i) => assert.ok(fs.readFileSync(path.join(raw, m.file)).equals(incoming[i]), `incoming frame ${i} byte-exact`));

  // 3) credentials: outgoing raw binary is NOT stored; the sanitized sidecar has identical length with the secrets zeroed
  const hs = manifest.find(m => m.direction === 'outgoing' && m.length === handshake.length)!;
  assert.ok(!fs.existsSync(path.join(raw, `ws-001-outgoing-000001.bin`)), 'raw outgoing handshake not persisted');
  assert.ok(hs.sanitizedFile && fs.existsSync(path.join(raw, hs.sanitizedFile)), 'sanitized sidecar exists');
  const san = fs.readFileSync(path.join(raw, hs.sanitizedFile));
  assert.strictEqual(san.length, handshake.length, 'same length, offsets preserved');
  for (const s of [LOGIN, PASS]) for (const enc of ['utf8', 'utf16le'] as const) assert.strictEqual(san.indexOf(Buffer.from(s, enc)), -1, `${s} (${enc}) zeroed`);
  assert.ok(san.indexOf(Buffer.from('HELLO')) === 0 && san.indexOf(Buffer.from('TAIL')) > 0, 'non-secret bytes untouched');

  // 4) nothing anywhere on disk contains the credentials
  const walk = (d: string): string[] => fs.readdirSync(d, { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]);
  for (const f of walk(scratch)) {
    const b = fs.readFileSync(f);
    for (const s of [LOGIN, PASS]) for (const enc of ['utf8', 'utf16le'] as const) assert.strictEqual(b.indexOf(Buffer.from(s, enc)), -1, `${s}/${enc} found in ${path.relative(scratch, f)}`);
  }
  console.log(`probe-capture: all assertions passed (${total} frames, ${files.length} files, no credentials on disk)`);
  process.chdir(os.tmpdir());
})().catch(e => { console.error(e); process.exit(1); });
