import { Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { attachProbe, buildRedactor } from './probe';
import { parseOhlcText, redactBuffer, classifyChartProbe, ChartProbeSummary, Ohlc } from './chartparse';

/**
 * CHART PROBE - answers one question: how can the 500 EURUSD M30 candles be read from the web terminal?
 * Tries, and records evidence for, the three possible routes:
 *   1. WebSocket frames: records every frame while the chart loads and while history is scrolled; analyses each
 *      (JSON candles? binary MqlRates-like records?) and saves the largest raw frames (credentials zeroed) for decoding.
 *   2. Hover text: sweeps the mouse across the chart and parses any OHLC / date-time text that appears.
 *   3. History scrolling: Home, PageUp, drag and wheel, measuring which action makes the terminal fetch more data.
 * READ-ONLY: only mouse moves, drags and navigation keys, kept to the middle band of the chart (never the corners, where a
 * one-click-trading panel can sit). It never clicks Buy/Sell, never presses F9 and never types. Use a DEMO account anyway.
 */

type Probe = ReturnType<typeof attachProbe>;

async function allText(page: Page, red: { text: (s: string) => string }): Promise<string> {
  const parts: string[] = [];
  for (const f of page.frames()) parts.push(await f.locator('body').innerText({ timeout: 2000 }).catch(() => ''));
  return red.text(parts.join('\n'));
}

async function findChart(page: Page): Promise<{ x: number; y: number; width: number; height: number } | null> {
  let best: { x: number; y: number; width: number; height: number } | null = null;
  for (const f of page.frames()) {
    const canvases = f.locator('canvas');
    const n = Math.min(await canvases.count().catch(() => 0), 15);
    for (let i = 0; i < n; i++) {
      const b = await canvases.nth(i).boundingBox().catch(() => null);
      if (b && b.width > 300 && b.height > 200 && (!best || b.width * b.height > best.width * best.height)) best = b;
    }
  }
  return best;
}

async function tooltipCandidates(page: Page, red: { text: (s: string) => string }): Promise<string[]> {
  const out: string[] = [];
  for (const f of page.frames()) {
    const items: string[] = await f.evaluate(() => {
      const sel = '[class*="tooltip" i],[class*="legend" i],[class*="crosshair" i],[class*="ohlc" i],[class*="data-window" i],[class*="datawindow" i],[role="tooltip"]';
      return Array.from(document.querySelectorAll(sel))
        .filter((e: any) => e.offsetParent !== null && (e.innerText || '').trim().length > 0)
        .slice(0, 10).map((e: any) => String(e.innerText).trim().slice(0, 200));
    }).catch(() => [] as string[]);
    out.push(...items.map(red.text));
  }
  return out.slice(0, 20);
}

async function hoverScan(page: Page, box: { x: number; y: number; width: number; height: number }, points: number, settleMs: number, red: { text: (s: string) => string }) {
  const baseline = new Set((await allText(page, red)).split('\n').map(l => l.trim()).filter(Boolean));
  const y = box.y + box.height * 0.55;
  const readouts: Array<{ x: number; delta: string; parsed: Ohlc | null }> = [];
  for (let i = 0; i < points; i++) {
    const x = box.x + 40 + (i * (box.width - 80)) / Math.max(1, points - 1);
    await page.mouse.move(x, y, { steps: 4 });
    await page.waitForTimeout(settleMs);
    const delta = (await allText(page, red)).split('\n').map(l => l.trim()).filter(l => l && !baseline.has(l)).join('\n');
    readouts.push({ x: Math.round(x), delta: delta.slice(0, 300), parsed: parseOhlcText(delta) });
  }
  const keys = new Set(readouts.filter(r => r.parsed).map(r => `${r.parsed!.o}|${r.parsed!.h}|${r.parsed!.l}|${r.parsed!.c}|${r.parsed!.time}`));
  const withTime = new Set(readouts.filter(r => r.parsed && r.parsed.time).map(r => r.parsed!.time));
  return { points, parsedCount: readouts.filter(r => r.parsed).length, distinct: keys.size, withTime: withTime.size, readouts, tooltips: await tooltipCandidates(page, red) };
}

async function dragChart(page: Page, box: { x: number; y: number; width: number; height: number }, fraction: number) {
  const y = box.y + box.height * 0.55, sx = box.x + box.width * 0.25;
  await page.mouse.move(sx, y);
  await page.mouse.down();
  await page.mouse.move(sx + box.width * fraction, y, { steps: 14 });
  await page.mouse.up();
}

export async function runChartProbe(page: Page, probe: Probe, secrets: string[], outDir = path.join(process.cwd(), 'artifacts', 'chart-probe')) {
  fs.mkdirSync(path.join(outDir, 'frames'), { recursive: true });
  const red = buildRedactor(secrets);
  const shot = (name: string) => page.screenshot({ path: path.join(outDir, name) }).catch(() => {});

  console.log('[chart-probe] letting the chart load...');
  await page.waitForTimeout(8000);
  probe.endCapture();                                       // closes the 'login_and_initial_load' window opened by index.ts
  await shot('chart-1-initial.png');

  const box = await findChart(page);
  console.log(box ? `[chart-probe] chart canvas ${Math.round(box.width)}x${Math.round(box.height)}` : '[chart-probe] NO chart canvas found');

  const fullText = await allText(page, red);
  const labelsPresent = /\bOpen\b/i.test(fullText) && /\bHigh\b/i.test(fullText) && /\bLow\b/i.test(fullText);
  const report: Record<string, any> = { schema: 1, captured_at: new Date().toISOString(), chart: box, labelsPresentOnPage: labelsPresent, actions: [], hover: {} };

  if (box) {
    report.hover.beforeScroll = await hoverScan(page, box, 40, 150, red);
    await shot('chart-2-hover.png');

    const cx = box.x + box.width / 2, cy = box.y + box.height * 0.55;
    const actions: Array<{ name: string; run: () => Promise<void> }> = [
      { name: 'key_Home', run: async () => { await page.mouse.move(cx, cy); await page.keyboard.press('Home'); } },
      { name: 'key_PageUp_x3', run: async () => { await page.mouse.move(cx, cy); for (let i = 0; i < 3; i++) { await page.keyboard.press('PageUp'); await page.waitForTimeout(400); } } },
      { name: 'drag_right_x5', run: async () => { for (let i = 0; i < 5; i++) { await dragChart(page, box, 0.5); await page.waitForTimeout(700); } } },
      { name: 'wheel_up', run: async () => { await page.mouse.move(cx, cy); await page.mouse.wheel(0, -800); } },
    ];
    for (const a of actions) {
      const before = probe.counters();
      probe.beginCapture(a.name);
      try { await a.run(); } catch (e) { report.actions.push({ name: a.name, error: String(e).slice(0, 160) }); }
      await page.waitForTimeout(2500);
      const cap = probe.endCapture();
      const after = probe.counters();
      report.actions.push({
        name: a.name, framesDelta: after.framesRx - before.framesRx, binaryDelta: after.binRx - before.binRx, bytesDelta: after.bytesRx - before.bytesRx,
        biggestFrame: cap ? Math.max(0, ...cap.frames.map((f: any) => f.len)) : 0,
        maxRatesLike: cap ? Math.max(0, ...cap.frames.map((f: any) => f.analysis?.ratesLikeRecords ?? 0)) : 0,
        maxJsonCandles: cap ? Math.max(0, ...cap.frames.map((f: any) => f.ohlcJson?.count ?? 0)) : 0,
      });
      console.log(`[chart-probe] ${a.name}: +${after.framesRx - before.framesRx} frames, +${after.bytesRx - before.bytesRx} bytes`);
    }
    await shot('chart-3-after-scroll.png');
    report.hover.afterScroll = await hoverScan(page, box, 40, 150, red);
  }

  // ---- evidence: analysis of every captured window + raw frames (credentials zeroed) for offline decoding
  const captures = probe.captures();
  const saved: string[] = [];
  for (const c of captures) {
    c.bufs.forEach((b, i) => {
      const name = `frames/${c.label}-${i + 1}.bin`;
      fs.writeFileSync(path.join(outDir, name), redactBuffer(b, secrets));
      saved.push(name);
    });
  }
  const frames = captures.flatMap(c => c.frames.map((f: any) => ({ ...f, label: c.label })));
  const scrollFrames = captures.filter(c => c.label !== 'login_and_initial_load').flatMap(c => c.frames);
  const bestAnalysis = frames.filter((f: any) => f.analysis).sort((a: any, b: any) => b.analysis.ratesLikeRecords - a.analysis.ratesLikeRecords)[0];
  const hover = [report.hover.beforeScroll, report.hover.afterScroll].filter(Boolean);
  const summary: ChartProbeSummary = {
    chartFound: !!box,
    hoverDistinct: Math.max(0, ...hover.map((h: any) => h.distinct)),
    hoverWithTime: Math.max(0, ...hover.map((h: any) => h.withTime)),
    wsJsonOhlcMax: Math.max(0, ...frames.map((f: any) => f.ohlcJson?.count ?? 0)),
    wsRatesLikeMax: Math.max(0, ...frames.map((f: any) => f.analysis?.ratesLikeRecords ?? 0)),
    wsBinaryFramesAfterScroll: scrollFrames.filter((f: any) => f.kind === 'binary').length,
    wsBytesAfterScroll: scrollFrames.filter((f: any) => f.kind === 'binary').reduce((a: number, f: any) => a + f.len, 0),
  };
  const verdict = classifyChartProbe(summary);
  Object.assign(report, { summary, ...verdict, savedFrames: saved, bestBinaryAnalysis: bestAnalysis?.analysis ?? null,
    windows: captures.map(c => ({ label: c.label, frames: c.frames.length, binary: c.frames.filter((f: any) => f.kind === 'binary').length })) });
  fs.writeFileSync(path.join(outDir, 'chart-probe-report.json'), JSON.stringify(report, null, 2));
  await probe.finish(path.join(outDir, 'network'));
  console.log(`[chart-probe] VERDICT: ${verdict.verdict}\n[chart-probe] ${verdict.advice}\n[chart-probe] report: ${path.join(outDir, 'chart-probe-report.json')}`);
  return report;
}
