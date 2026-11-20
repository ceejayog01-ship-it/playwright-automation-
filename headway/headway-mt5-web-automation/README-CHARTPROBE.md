# Chart probe: how do we read the 500 EURUSD M30 candles from the web terminal?

Run it once on a **demo** account and send back the `artifacts/chart-probe/` folder (zipped).

```bash
npm ci
npx playwright install chromium
cp .env.example .env        # set MT5_LOGIN, MT5_PASSWORD (demo), WEB_TERMINAL_URL, ACCOUNT_MODE=demo, HEADLESS=false
npm run chartprobe
```

It logs in (the verified login), waits for the chart, then records evidence for three routes:

1. **WebSocket frames** while the chart loads and while history is scrolled (JSON candles? binary MqlRates-like records?). The largest raw frames are saved to `frames/*.bin` with your login/password zeroed.
2. **Hover text**: sweeps the mouse across the chart and parses any OHLC and date/time that appears.
3. **History scrolling**: Home, PageUp, drag, wheel, measuring which one makes the terminal fetch more data.

`chart-probe-report.json` ends with a `verdict` (`ws_json`, `ws_binary_rates`, `hover_text`, `ws_binary_unknown` or `none`) and the recommended next step.

**Safety:** read-only. Only mouse moves/drags (middle band of the chart, never the corners) and Home/PageUp. It never clicks Buy/Sell, never presses F9, never types. Turn **one-click trading off** in the terminal anyway, and use demo.
**Privacy:** the screenshots show your balance, and saved frames may contain other account data (credentials are zeroed). Share the folder privately.
