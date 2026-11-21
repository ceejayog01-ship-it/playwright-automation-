import { Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';
import { attachProbe } from './probe';

type Probe = ReturnType<typeof attachProbe>;

/** Read-only WebSocket evidence capture. No chart selection, chart manipulation,
 * screenshot checks, or account-number matching are performed in this stage. */
export async function runChartProbe(
  page: Page,
  probe: Probe,
  _secrets: string[],
  _legacyIdentity?: unknown,
  outDir = path.join(process.cwd(), 'artifacts', 'chart-probe'),
) {
  fs.mkdirSync(outDir, { recursive: true });
  console.log('[chart-probe] read-only WebSocket capture; visual chart checks are disabled.');
  await page.waitForTimeout(12000);
  probe.endCapture();
  await probe.finish(path.join(outDir, 'network'));
  const report = {
    schema: 3,
    captured_at: new Date().toISOString(),
    purpose: 'native historical-rate protocol discovery',
    visualChartVerification: 'NOT_REQUIRED',
    chartSelectionOrManipulation: 'NOT_PERFORMED',
    nativeCandles: {
      retrievalImplemented: false,
      count: 0,
      required: 500,
      validated: false,
      reason: 'The native historical-rate decoder is pending inspection of captured WebSocket payloads.',
    },
    nextStep: 'Inspect the raw WebSocket frame files and manifest before implementing the historical-rate decoder.',
  };
  fs.writeFileSync(path.join(outDir, 'chart-probe-report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log('[chart-probe] capture complete; no candle data has been decoded or validated.');
  return report;
}
