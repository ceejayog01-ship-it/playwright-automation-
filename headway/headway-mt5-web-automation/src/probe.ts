import { Page } from 'playwright';
import { analyzeBinary, scanJsonForOhlc } from './chartparse';
import * as fs from 'fs';
import * as path from 'path';

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
    text: (s: string) => plain.reduce((acc, p) => acc.split(p).join('<redacted>'), s),
    hex: (h: string) => hexForms.reduce((acc, f) => acc.split(f).join('<redacted>'), h),
  };
}

const noQuery = (u: string) => u.split('?')[0].split('#')[0];

function printable(buf: Buffer, utf16: boolean, max = 120): string {
  const s = buf.toString(utf16 ? 'utf16le' : 'latin1');
  return s.replace(/[^\x20-\x7e]+/g, '·').slice(0, max);
}

export function attachProbe(page: Page, secrets: string[]) {
  const red = buildRedactor(secrets);
  const http = new Map<string, { method: string; type: string; status: number; contentType: string; count: number; bytes: number; sample?: string }>();
  const sockets: Array<Record<string, any>> = [];
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

  page.on('response', async r => {
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
      if (/json|text/.test(ct) && HINT.test(url) && bytes < 200_000) {
        entry.sample = red.text((await r.text().catch(() => '')).slice(0, 600));
      }
      http.set(key, entry);
    } catch { /* probing must never break the run */ }
  });

  page.on('websocket', ws => {
    const info: Record<string, any> = { url: noQuery(ws.url()), framesRx: 0, framesTx: 0, bytesRx: 0, textRx: 0, binRx: 0, samples: [], closed: false };
    sockets.push(info);
    const sample = (dir: 'rx' | 'tx', payload: string | Buffer) => {
      if (info.samples.length >= 60) return;
      if (typeof payload === 'string') {
        let json = false; try { JSON.parse(payload); json = true; } catch { /* not json */ }
        info.samples.push({ dir, kind: 'text', json, len: payload.length, preview: red.text(payload.slice(0, 300)) });
      } else {
        info.samples.push({
          dir, kind: 'binary', len: payload.length,
          hexHead: red.hex(payload.subarray(0, 64).toString('hex')),
          asciiHint: red.text(printable(payload, false)),
          utf16Hint: red.text(printable(payload, true)),
        });
      }
    };
    ws.on('framereceived', e => {
      info.framesRx++; const p = e.payload;
      info.bytesRx += typeof p === 'string' ? p.length : p.length;
      typeof p === 'string' ? info.textRx++ : info.binRx++;
      if (info.framesRx <= 40) sample('rx', p);
      if (cap) record(cap, p);
    });
    ws.on('framesent', e => { info.framesTx++; if (info.framesTx <= 10) sample('tx', e.payload); });
    ws.on('close', () => { info.closed = true; });
  });

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
      schema: 1, captured_at: new Date().toISOString(),
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
