#!/usr/bin/env python3
"""Fetch the latest EURUSD M30 candles from Yahoo Finance (the sole market-price provider).

Flow: purge bars from any other source -> fetch -> strict validation -> upsert -> quality gate -> export
for the TSE scanner. Fail-closed: if the gate fails, no scan and no order is attempted this cycle.

Exit codes
  0   data valid and fresh (>= MIN bars, latest completed candle present)  -> scan may run
  10  quality gate failed (insufficient history or latest completed candle missing)
  11  provider / network failure
  12  reserved configuration failure
  20  market closed (stale only because it is inside the weekend closure) -> skip scan, not an error
Yahoo Finance requires no API key.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge"
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))

from tsebridge import marketdata as MD                      # noqa: E402
from tsebridge.common import DataError, iso                  # noqa: E402
from tsebridge.store import export_m30_csv, get_store, quality_report   # noqa: E402

PAIR = "EURUSD"


def in_weekend_closure(now: datetime) -> bool:
    """Fri 21:00 UTC -> Sun 22:30 UTC. Used ONLY to turn 'no new candle' into a clean skip, never to trade."""
    wd, hm = now.weekday(), now.hour * 60 + now.minute
    return (wd == 4 and hm >= 21 * 60) or wd == 5 or (wd == 6 and hm < 22 * 60 + 30)


def run(argv=None, *, http_get=None, clock=None, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-bars", type=int, default=int(os.getenv("MARKET_MIN_M30_BARS", "500")))
    ap.add_argument("--output", default="work/eurusd_m30.csv")
    ap.add_argument("--report", default="work/market_data_report.json")
    ap.add_argument("--wait-seconds", type=int, default=int(os.getenv("MARKET_WAIT_FOR_BAR_SECONDS", "100")),
                    help="how long to wait for the just-closed candle to be published")
    args = ap.parse_args(argv)
    now_fn = clock or (lambda: datetime.now(timezone.utc))
    started = now_fn()
    report = {"provider": MD.PROVIDER, "symbol": PAIR, "timeframe": "M30", "minimum_bars": args.min_bars,
              "started_at": iso(started), "ok": False, "exit_code": None}

    def finish(code: int, **extra) -> int:
        report.update(extra)
        report["exit_code"] = code
        report["finished_at"] = iso(now_fn())
        out = Path(args.report) if Path(args.report).is_absolute() else ROOT / args.report
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return code

    store = get_store()
    purged = store.purge_other_sources(MD.PROVIDER)         # drop bars left by older pipeline versions
    if purged:
        print(f"::warning::removed non-Yahoo-Finance candles from the restored store: {purged}")
    before = store.load_m30(PAIR, limit=100_000)

    rows: list = []
    deadline = time.monotonic() + max(0, args.wait_seconds)
    attempts = 0
    while True:
        attempts += 1
        now = now_fn()
        try:
            payload = MD.fetch_time_series("", PAIR, outputsize=args.min_bars + 20, http_get=http_get, sleep=sleep)
            rows = MD.parse_time_series(payload, now, PAIR)
        except MD.ProviderError as exc:
            print(f"::error::Yahoo Finance request failed: {str(exc)}", file=sys.stderr)
            return finish(11, reason="provider_error", detail=str(exc), attempts=attempts,
                          purged_non_provider_rows=purged)
        except DataError as exc:
            print(f"::error::Yahoo Finance payload rejected: {exc}", file=sys.stderr)
            return finish(10, reason="payload_rejected", detail=str(exc)[:300], attempts=attempts,
                          purged_non_provider_rows=purged)
        want = MD.expected_latest_open(now)
        if rows and datetime.fromisoformat(rows[-1]["start_ts"]) >= want:
            break
        if time.monotonic() >= deadline:
            break
        sleep(20)

    changed = store.upsert_m30(rows)
    after = store.load_m30(PAIR, limit=args.min_bars)
    now = now_fn()
    want = MD.expected_latest_open(now)
    latest = datetime.fromisoformat(after[-1]["start_ts"]) if after else None
    quality = quality_report(after, now, min_bars=args.min_bars)
    fresh = latest is not None and latest >= want
    base = dict(attempts=attempts, bars_before=len(before), fetched_valid_bars=len(rows), rows_written=changed,
                purged_non_provider_rows=purged, bars_after=len(after),
                latest_bar_start_utc=iso(latest) if latest else None, expected_latest_bar_start_utc=iso(want),
                quality=quality, gaps=MD.gap_report(after), output=args.output)
    if len(after) < args.min_bars:
        print(f"::error::only {len(after)} valid completed M30 bars; {args.min_bars} required. No scan, no orders.", file=sys.stderr)
        return finish(10, reason="insufficient_history", **base)
    if not fresh:
        if in_weekend_closure(now):
            print("::notice::market closed (weekend); no new candle expected. Skipping scan.")
            return finish(20, reason="market_closed", market_closed=True, **base)
        print(f"::error::latest completed candle {iso(want)} is missing (latest stored {base['latest_bar_start_utc']}). No scan, no orders.", file=sys.stderr)
        return finish(10, reason="latest_completed_candle_missing", **base)
    export_m30_csv(after, ROOT / args.output if not Path(args.output).is_absolute() else Path(args.output))
    return finish(0, reason="ok", ok=True, **base)


if __name__ == "__main__":
    raise SystemExit(run())
