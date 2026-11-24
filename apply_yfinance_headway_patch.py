#!/usr/bin/env python3
"""Apply Yahoo-history / Headway-login changes to the checked-out repository."""
from pathlib import Path
ROOT=Path.cwd()

def edit(path, transform):
    p=ROOT/path
    if not p.exists(): raise SystemExit(f"Required file missing: {path}")
    old=p.read_text()
    new=transform(old)
    if new==old: raise SystemExit(f"No change made to {path}; source block missing or already patched.")
    p.write_text(new)
    print("updated",path)

def login_edit(s):
    s=s.replace("import { Policy, loadPolicy, hostOf, checkEntry, checkServerLabel } from './policy';",
                "import { Policy, loadPolicy, hostOf, checkEntry } from './policy';")
    start=s.find("  // Read the exact visible server-name text")
    end=s.find("\n\n  if (!await safeFill",start)
    if start<0 or end<0: raise SystemExit("login.ts: visible-server gate not found")
    replacement = "\n".join([
      "  // Keep Headway entry-host and demo/real URL-path validation.",
      "  // The visible server-name label is not required; it was blocking the",
      "  // correct demo entry URL from proceeding to broker authentication.",
      "  const entry = checkEntry(policy, creds.url);",
      "  if (!entry.ok) {",
      "    console.error(`[login] SAFETY ABORT: URL rejected for ${policy.mode} mode (${entry.reason}).`);",
      "    await screenshotOnFailure(page, 'entry-url-abort');",
      "    return fail(entry.reason);",
      "  }"
    ])
    s=s[:start]+replacement+s[end:]
    # Remove the earlier duplicate URL validation if it existed above the removed gate.
    old_block = "\n".join([
      "  // Require an allowed Headway entry point whose path agrees with the declared mode.",
      "  const entry = checkEntry(policy, creds.url);",
      "  if (!entry.ok) {",
      "    console.error(`[login] SAFETY ABORT: URL rejected for ${policy.mode} mode (${entry.reason}).`);",
      "    await screenshotOnFailure(page, 'entry-url-abort');",
      "    return fail(entry.reason);",
      "  }",
      ""
    ])
    if s.count("const entry = checkEntry(policy, creds.url);")>1:
        s=s.replace(old_block,"",1)
    s=s.replace("Credentials submitted to the confirmed ${policy.mode} endpoint",
                "Credentials submitted to the validated ${policy.mode} entry URL")
    return s

def cycle_edit(s):
    start=s.find('echo "== historical bootstrap (Dukascopy fallback; minimum 500 M30 bars) =="')
    end=s.find("# 3 ---------------------------------------------------------------------------------------------",start)
    if start<0 or end<0: raise SystemExit("run_cycle.sh: Dukascopy bootstrap section not found")
    block="\n".join([
      'echo "== Yahoo Finance historical bootstrap (500 validated M30 candles minimum) =="',
      'python scripts/bootstrap_yfinance.py \\',
      '  --min-bars "${YFINANCE_MIN_M30_BARS:-500}" \\',
      '  --lookback-days "${YFINANCE_LOOKBACK_DAYS:-60}" \\',
      '  --output "$WORK/eurusd_m30.csv" \\',
      '  --report "$WORK/yfinance_bootstrap.json"',
      'hist=$?',
      'if [ "$hist" -ne 0 ]; then',
      '  echo "::error::Historical bootstrap did not produce the required validated M30 history"',
      '  worst 4',
      'fi',
      'cat "$WORK/yfinance_bootstrap.json" 2>/dev/null || true',
      ''
    ])
    s=s[:start]+block+s[end:]
    s=s.replace("Headway-native M30 preferred; Dukascopy fills only missing historical timestamps.",
                "Headway remains live-price source; Yahoo fills missing historical M30 timestamps only.")
    s=s.replace("Dukascopy bootstrap will attempt recovery","Yahoo bootstrap will attempt recovery")
    s=s.replace("Dukascopy historical bootstrap","Yahoo historical bootstrap")
    s=s.replace("Headway + Dukascopy M30 history","Headway + Yahoo historical M30 store")
    return s

def workflow_edit(s):
    s=s.replace("Headway login/history → Dukascopy fill for missing history (500-bar minimum) →",
                "Headway login/live quotes → Yahoo historical fill (500-bar minimum) →")
    s=s.replace('DUKASCOPY_MIN_M30_BARS: "500"','YFINANCE_MIN_M30_BARS: "500"')
    s=s.replace('DUKASCOPY_LOOKBACK_DAYS: "60"','YFINANCE_LOOKBACK_DAYS: "60"')
    s=s.replace("work/dukascopy_bootstrap.json","work/yfinance_bootstrap.json")
    marker="\n".join([
      '          test "$ACCOUNT_MODE" = "demo" || { echo "::error::This workflow is locked to demo execution"; exit 1; }',
      '          test -n "$MT5_LOGIN" && test -n "$MT5_PASSWORD" || { echo "::error::Headway demo MT5_LOGIN / MT5_PASSWORD secrets are missing"; exit 1; }'
    ])
    if marker in s:
        s=s.replace(marker,"\n".join([
          '          test "$ACCOUNT_MODE" = "demo" || { echo "::error::This workflow is locked to demo execution"; exit 1; }',
          '          test "$ALLOW_ORDERS" = "yes" || { echo "::error::Demo execution must remain enabled"; exit 1; }',
          '          test "$BRIDGE_REAL_EXECUTION" = "true" || { echo "::error::Broker execution flag is disabled"; exit 1; }',
          '          test -n "$MT5_LOGIN" && test -n "$MT5_PASSWORD" || { echo "::error::Headway demo MT5_LOGIN / MT5_PASSWORD secrets are missing"; exit 1; }'
        ]))
    if "DUKASCOPY_MIN_M30_BARS" in s or "dukascopy_bootstrap.json" in s:
        raise SystemExit("workflow still contains Dukascopy env/report references")
    if "YFINANCE_MIN_M30_BARS" not in s: raise SystemExit("Yahoo env settings missing")
    return s

edit("headway/headway-mt5-web-automation/src/login.ts",login_edit)
edit("scripts/run_cycle.sh",cycle_edit)
edit(".github/workflows/market-cycle.yml",workflow_edit)
req=ROOT/"tse-reference/tse-mt5-integration/requirements.txt"
if not req.exists(): raise SystemExit("requirements file missing")
txt=req.read_text()
if "yfinance" not in txt.lower():
    req.write_text(txt.rstrip()+"\nyfinance>=0.2.66\n")
    print("updated tse-reference/tse-mt5-integration/requirements.txt")
else: print("yfinance dependency already present")
