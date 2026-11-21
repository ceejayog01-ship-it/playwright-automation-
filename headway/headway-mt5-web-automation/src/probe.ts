import { Page, BrowserContext } from 'playwright';
import { analyzeBinary, scanJsonForOhlc } from './chartparse';
import * as fs from 'fs';
import * as path from 'path';
import * as crypto from 'crypto';

/**
 * READ-ONLY probe: records WHERE price/candle data flows in the authenticated terminal (HTTP responses,
 * WebSocket frames, window globals) so the native-history adapter can be built from evidence.
 * Never clicks, types or sends anything. Credentials are redacted (incl. their UTF-8/UTF-16LE hex forms);
 * query strings are dropped; no cookies or headers are stored.
 */
const HINT = /chart|candle|bar|history|rates|ohlc|quote|tick|symbol|market/i;

export function buildRedactor(secrets: string[]) {
  const plain = secrets.filter(s => s && s.length >= 3);
  const hexForms = plain.flatMap(s => [Buffer.from(s, 'utf8').toString('hex'), Buffer.from(s, 'utf16le').toString('hex')]);
  return {
    text: (s: string) => {
      let out = plain.reduce((acc, p) => acc.split(p).join('<redacted>'), s);
      // Scrub common credential-bearing fields and URL-style tokens without retaining their values.
      out = out.replace(/(password|passwd|access[_-]?token|refresh[_-]?token|authorization|bearer|session[_-]?id|sid|jwt|api[_-]?key)(\s*[=:]\s*|\s+)([^\s,;\"'&}]+)/ig, '$1$2<redacted>');
      return out;
    },
    hex: (h: string) => hexForms.reduce((acc, f) => acc.split(f).join('00'.repeat(f.length / 2)), h),
  };
}

const noQuery = (u: string) => u.split('?')[0].split('#')[0];

function printable(buf: Buffer, utf16: boolean, max = 120): string {
  const s = buf.toString(utf16 ? 'utf16le' : 'latin1');
  return s.replace(/[^\x20-\x7e]+/g, '·').slice(0, max);
}

export function attachProbe(page: Page, secrets: string[], context?: BrowserContext) {
  const red = buildRedactor(secrets);
  const http = new Map<string, { method: string; type: string; status: number; contentType: string; count: number; bytes: number; sample?: string }>();
  const sockets: Array<Record<string, any>> = [];
  const rawFrames: Array<{ socketId: number; direction: 'incoming' | 'outgoing'; kind: 'text' | 'binary'; timestamp: string; length: number; sha256: string; file: string; redactions: string[] }> = [];
  const attachedPages = new WeakSet<Page>();
  let socketSequence = 0;
  const rawRoot = path.join(process.cwd(), 'artifacts', 'chart-probe', 'websocket-raw');
  fs.mkdirSync(rawRoot, { recursive: true });
  // Capture windows (used by the chart probe): analyse EVERY frame received while a window is open.
  type Cap = { label: string; frames: any[]; bufs: Buffer[] };
  let cap: Cap | null = null;
  const captures: Cap[] = [];
  const record = (c: Cap, p: string | Buffer) => {
    const e: any = { len: p.length, kind: typeof p === 'string' ? 'text' : 'binary' };
    if (typeof p === 'string') { try { e.ohlcJson = scanJsonForOhlc(JSON.parse(p)); } catch { /* not JSON */ } }
    else {
      if (p.length <= 600_000) e.analysis = analyzeBinary(p);
      c.bufs.push(p); c.bufs.sort((a, b) => b.length - a.length); c.bufs.length = Math.min(c.bufs.length, 3);
    }
    if (c.frames.length < 200) c.frames.push(e);
  };

  const attachPage = (target: Page) => {
    if (attachedPages.has(target)) return;
    attachedPages.add(target);
    target.on('response', async r => {
      try {
        const url = noQuery(r.url());
        const type = r.request().resourceType();
        if (!['xhr', 'fetch', 'websocket', 'other', 'script'].includes(type) && !HINT.test(url)) return;
        const key = `${r.request().method()} ${url}`;
        const ct = r.headers()['content-type'] || '';
        const prev = http.get(key);
        const bytes = Number(r.headers()['content-length'] || 0);
        if (prev) { prev.count++; prev.bytes += bytes; return; }
        const entry: any = { method: r.request().method(), type, status: r.status(), contentType: ct, count: 1, bytes };
        if (/json|text/.test(ct) && HINT.test(url) && bytes < 200_000) entry.sample = red.text((await r.text().catch(() => '')).slice(0, 600));
        http.set(key, entry);
      } catch { /* diagnostic capture must not interrupt browser activity */ }
    });
    target.on('websocket', ws => {
      const socketId = ++socketSequence;
      const safeUrl = (() => { try { const u = new URL(ws.url()); u.username = ''; u.password = ''; u.search = ''; u.hash = ''; return red.text(u.toString()); } catch { return '<unparseable-websocket-url>'; } })();
      const info: Record<string, any> = { socketId, url: safeUrl, openedAt: new Date().toISOString(), framesRx: 0, framesTx: 0, bytesRx: 0, bytesTx: 0, textRx: 0, textTx: 0, binRx: 0, binTx: 0, samples: [], closed: false };
      sockets.push(info);
      const saveFrame = (direction: 'incoming' | 'outgoing', payload: string | Buffer) => {
        const timestamp = new Date().toISOString();
        const isText = typeof payload === 'string';
        let bytes: Buffer;
        const redactions: string[] = [];
        if (isText) {
          let safe = payload as string;
          for (const secret of secrets.filter(x => x && x.length >= 3)) {
            let at = safe.indexOf(secret);
            while (at !== -1) { safe = safe.slice(0, at) + '*'.repeat(secret.length) + safe.slice(at + secret.length); redactions.push(`known-secret@${at}:${secret.length}`); at = safe.indexOf(secret, at + secret.length); }
          }
          safe = safe.replace(/(password|passwd|access[_-]?token|refresh[_-]?token|authorization|bearer|session[_-]?id|sid|jwt|api[_-]?key)(\s*[=:]\s*|\s+)([^\s,;\"'&}]+)/ig, (_m, key: string, sep: string, value: string) => { redactions.push(`credential-field:${key}`); return key + sep + '*'.repeat(value.length); });
          bytes = Buffer.from(safe, 'utf8');
        } else {
          // Keep the exact protocol payload in a raw evidence file. Because the
          // binary format is not decoded yet, token redaction cannot be safely
          // applied without corrupting offsets/fields. A sanitized sidecar is
          // produced separately for text-like binary content; raw artifacts
          // must remain private until reviewed for embedded credentials.
          bytes = Buffer.from(payload as Buffer);
        }
        const ext = isText ? 'txt' : 'bin';
        const file = `ws-${String(socketId).padStart(3, '0')}-${direction}-${String(rawFrames.length + 1).padStart(6, '0')}.${ext}`;
        fs.writeFileSync(path.join(rawRoot, file), bytes);
        if (!isText) {
          const safe = Buffer.from(bytes);
          for (const secret of secrets.filter(x => x && x.length >= 3)) {
            for (const enc of [Buffer.from(secret, 'utf8'), Buffer.from(secret, 'utf16le')]) {
              let at = 0;
              while ((at = safe.indexOf(enc, at)) !== -1) {
                safe.fill(0, at, at + enc.length);
                redactions.push(`known-secret@${at}:${enc.length}`);
                at += enc.length;
              }
            }
          }
          const ascii = safe.toString('latin1');
          const authRe = /(password|passwd|access[_-]?token|refresh[_-]?token|authorization|bearer|session[_-]?id|sid|jwt|api[_-]?key)(\s*[=:]\s*|\s+)([^\s,;\"'&}]+)/ig;
          let match: RegExpExecArray | null;
          while ((match = authRe.exec(ascii))) {
            const valueStart = match.index + match[0].length - match[3].length;
            safe.fill(0, valueStart, valueStart + match[3].length);
            redactions.push(`credential-field@${valueStart}:${match[3].length}`);
          }
          const sanitizedFile = file.replace(/\.bin$/, '.sanitized.bin');
          fs.writeFileSync(path.join(rawRoot, sanitizedFile), safe);
          rawFrames.push({ socketId, direction, kind: 'binary', timestamp, length: (payload as Buffer).length, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), file, sanitizedFile, redactions });
        } else {
          rawFrames.push({ socketId, direction, kind: 'text', timestamp, length: Buffer.byteLength(payload as string, 'utf8'), sha256: crypto.createHash('sha256').update(bytes).digest('hex'), file, redactions });
        }
        if (info.samples.length < 60) {
          if (isText) {
            let json = false; try { JSON.parse(payload as string); json = true; } catch { /* not JSON */ }
            info.samples.push({ direction, kind: 'text', json, len: Buffer.byteLength(payload as string, 'utf8'), preview: red.text((payload as string).slice(0, 300)) });
          } else info.samples.push({ direction, kind: 'binary', len: (payload as Buffer).length, hexHead: bytes.subarray(0, 64).toString('hex'), asciiHint: red.text(printable(bytes, false)), utf16Hint: red.text(printable(bytes, true)) });
        }
        if (cap) record(cap, isText ? red.text(payload as string) : bytes);
      };
      ws.on('framereceived', e => {
        const p = e.payload; info.framesRx++; info.bytesRx += typeof p === 'string' ? Buffer.byteLength(p, 'utf8') : p.length;
        typeof p === 'string' ? info.textRx++ : info.binRx++;
        saveFrame('incoming', p);
      });
      ws.on('framesent', e => {
        const p = e.payload; info.framesTx++; info.bytesTx += typeof p === 'string' ? Buffer.byteLength(p, 'utf8') : p.length;
        typeof p === 'string' ? info.textTx++ : info.binTx++;
        saveFrame('outgoing', p);
      });
      ws.on('close', () => { info.closed = true; info.closedAt = new Date().toISOString(); });
    });
  };
  attachPage(page);
  // Attach to every page created in this BrowserContext, not just the initial tab.
  context?.on('page', attachPage);

  async function finish(dir = path.join(process.cwd(), 'artifacts', 'probe')) {
    fs.mkdirSync(dir, { recursive: true });
    const frames: any[] = [];
    for (const f of page.frames()) {
      const globals = await f.evaluate(() => Object.keys(window).filter(k => /chart|candle|ohlc|bars|rates|terminal|mt5|mql|quote|symbol/i.test(k)).slice(0, 80)).catch(() => []);
      const resources = await f.evaluate(() => performance.getEntriesByType('resource').map(e => (e as PerformanceResourceTiming).name.split('?')[0]).filter(n => /chart|candle|bar|history|rates|ohlc|quote|tick/i.test(n)).slice(0, 80)).catch(() => []);
      frames.push({ url: noQuery(f.url()), globals, resources });
    }
    const textSym = (s: any) => /EURUSD/.test(`${s.preview || ''}${s.asciiHint || ''}${s.utf16Hint || ''}`);
    const report = {
      schema: 2, captured_at: new Date().toISOString(),
      rawFrameCapture: { directory: 'websocket-raw', frameCount: rawFrames.length, manifest: 'websocket-frames.json' },
      findings: {
        websockets: sockets.length,
        binaryWebsocket: sockets.some(s => s.binRx > 0),
        jsonWebsocket: sockets.some(s => s.samples.some((x: any) => x.json)),
        symbolVisibleInFrames: sockets.some(s => s.samples.some(textSym)),
        httpHintedEndpoints: [...http.entries()].filter(([k, v]) => HINT.test(k) || v.sample).length,
      },
      frames, websockets: sockets, http: Object.fromEntries(http),
    };
    fs.writeFileSync(path.join(dir, 'probe-report.json'), JSON.stringify(report, null, 2));
    fs.mkdirSync(rawRoot, { recursive: true });
    fs.writeFileSync(path.join(rawRoot, 'websocket-frames.json'), JSON.stringify({ schema: 1, captured_at: new Date().toISOString(), note: 'Primary .bin files preserve exact WebSocket payload bytes. Sanitized sidecars redact recognizable textual credential fields. Raw binary files may contain opaque credentials and must be kept private until protocol-aware review.', frames: rawFrames }, null, 2));
    console.log('[probe] Report saved:', path.join(dir, 'probe-report.json'), JSON.stringify(report.findings));
  }
  const counters = () => ({
    framesRx: sockets.reduce((a, x) => a + x.framesRx, 0), bytesRx: sockets.reduce((a, x) => a + x.bytesRx, 0),
    binRx: sockets.reduce((a, x) => a + x.binRx, 0), textRx: sockets.reduce((a, x) => a + x.textRx, 0),
  });
  const beginCapture = (label: string) => { cap = { label, frames: [], bufs: [] }; };
  const endCapture = () => { const c = cap; cap = null; if (c) captures.push(c); return c; };
  return { finish, counters, beginCapture, endCapture, captures: () => captures };
}
