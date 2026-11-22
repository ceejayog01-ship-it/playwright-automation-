#!/usr/bin/env python3
"""Dukascopy historical bootstrap for EURUSD M30.

Downloads only when the persistent store has fewer than the configured minimum.
Dukascopy rows are insert-only: existing Headway rows are never overwritten.
All timestamps are normalized to UTC candle-open times; forming candles are dropped.
"""
from __future__ import annotations
import argparse, csv, json, os, subprocess, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge"
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))
from tsebridge.store import get_store, export_m30_csv, quality_report
from tsebridge.common import iso, parse_utc, DataError

HALF_HOUR = timedelta(minutes=30)


def _timestamp(value: str) -> datetime:
    raw = str(value).strip()
    try:
        # dukascopy-node CSV timestamps are Unix epoch milliseconds (UTC).
        if raw.replace(".", "", 1).isdigit():
            return datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
        return parse_utc(raw)
    except (ValueError, OverflowError) as exc:
        raise DataError(f"invalid Dukascopy timestamp: {raw}") from exc


def parse_dukascopy_csv(path: Path, now: datetime) -> list[dict]:
    rows_by_ts: dict[str, dict] = {}
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"timestamp", "open", "high", "low", "close"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise DataError(f"unexpected Dukascopy CSV columns: {reader.fieldnames}")
        for raw in reader:
            t = _timestamp(raw["timestamp"])
            if t.minute not in (0, 30) or t.second or t.microsecond:
                raise DataError(f"M30 timestamp is not half-hour aligned: {iso(t)}")
            if t + HALF_HOUR > now:
                continue  # never ingest a still-forming candle
            o, h, l, c = (float(raw[k]) for k in ("open", "high", "low", "close"))
            if not all(map(lambda x: x > 0 and x < float("inf"), (o, h, l, c))):
                raise DataError(f"non-finite/non-positive OHLC at {iso(t)}")
            if h < max(o, c, l) or l > min(o, c, h):
                raise DataError(f"invalid OHLC relationship at {iso(t)}")
            key = iso(t)
            rows_by_ts[key] = {
                "symbol": "EURUSD", "start_ts": key, "open": o, "high": h,
                "low": l, "close": c, "coverage": 1.0,
                "minutes_present": 30, "samples": 0,
                "source": "dukascopy_bootstrap",
            }
    return [rows_by_ts[k] for k in sorted(rows_by_ts)]


def validate_stored_rows(rows: list[dict]) -> None:
    """Do not count malformed or misaligned database rows toward the 500-bar gate."""
    for r in rows:
        t = parse_utc(r["start_ts"])
        if t.minute not in (0, 30) or t.second or t.microsecond:
            raise DataError(f"stored M30 timestamp is not half-hour aligned: {r['start_ts']}")
        o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
        if not all(x > 0 and x < float("inf") for x in (o, h, l, c)) or h < max(o, c, l) or l > min(o, c, h):
            raise DataError(f"stored M30 OHLC is invalid at {r['start_ts']}")


def overlap_comparison(existing: list[dict], downloaded: list[dict]) -> dict:
    """Report, but do not silently suppress, price differences on overlapping timestamps."""
    native = {r["start_ts"]: r for r in existing if str(r.get("source", "")).startswith("headway")}
    diffs = []
    for r in downloaded:
        old = native.get(r["start_ts"])
        if old:
            diffs.append(abs(float(old["close"]) - float(r["close"])) / 0.0001)
    return {
        "overlap_bars": len(diffs),
        "mean_abs_close_difference_pips": round(sum(diffs) / len(diffs), 4) if diffs else None,
        "max_abs_close_difference_pips": round(max(diffs), 4) if diffs else None,
        "note": "Diagnostic only; Headway rows retain priority. No price-feed equivalence is assumed.",
    }


def download(out_dir: Path, lookback_days: int, now: datetime) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    start = (now - timedelta(days=lookback_days)).date().isoformat()
    filename = f"eurusd-m30-{lookback_days}d"
    cmd = [
        "npx", "--yes", "dukascopy-node@1.51.1",
        "-i", "eurusd", "-from", start, "-to", "now",
        "-t", "m30", "-p", "bid", "-f", "csv",
        "-dir", str(out_dir), "-fn", filename,
        "--cache", "--retries", "3", "--rate-limit-retries", "3",
        "--batch-pause", "1500", "--request-interval", "100", "--retry-on-empty",
    ]
    print("Running pinned Dukascopy downloader:", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=ROOT / "headway" / "headway-mt5-web-automation", check=True)
    result = out_dir / f"{filename}.csv"
    if not result.exists():
        raise DataError(f"Dukascopy downloader completed but output file is missing: {result}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-bars", type=int, default=int(os.getenv("DUKASCOPY_MIN_M30_BARS", "500")))
    ap.add_argument("--lookback-days", type=int, default=int(os.getenv("DUKASCOPY_LOOKBACK_DAYS", "60")))
    ap.add_argument("--output", default="work/eurusd_m30.csv")
    ap.add_argument("--report", default="work/dukascopy_bootstrap.json")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    store = get_store()
    before = store.load_m30("EURUSD", limit=args.min_bars)
    validate_stored_rows(before)
    downloaded = 0
    source = "existing_store"
    comparison = {"overlap_bars": 0, "mean_abs_close_difference_pips": None,
                  "max_abs_close_difference_pips": None, "note": "No bootstrap download was needed."}
    if len(before) < args.min_bars:
        source = "dukascopy_bootstrap"
        data_dir = ROOT / "work" / "dukascopy"
        csv_path = download(data_dir, args.lookback_days, now)
        imported = parse_dukascopy_csv(csv_path, now)
        downloaded = len(imported)
        comparison = overlap_comparison(before, imported)
        # insert-only is essential: existing Headway-native candles retain priority.
        store.upsert_m30(imported, ignore_existing=True)
    after = store.load_m30("EURUSD", limit=args.min_bars)
    validate_stored_rows(after)
    out = ROOT / args.output
    export_m30_csv(after, out)
    quality = quality_report(after, now, min_bars=args.min_bars)
    ok = bool(quality.get("ok"))
    report = {
        "provider": "dukascopy-node@1.51.1", "symbol": "EURUSD", "timeframe": "M30",
        "minimum_bars": args.min_bars, "bars_before": len(before),
        "downloaded_valid_bars": downloaded, "bars_after": len(after),
        "source": source, "latest_bar_start_utc": after[-1]["start_ts"] if after else None,
        "overlap_comparison": comparison, "quality": quality, "output": str(out), "ok": ok,
        "note": "Dukascopy bootstrap rows are insert-only; existing Headway rows are preserved. Freshness is required before a market scan.",
    }
    report_path = ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not ok:
        print(f"::error::M30 history quality gate failed: {quality.get('reason')} (bars={len(after)}, required={args.min_bars})", file=sys.stderr)
        return 10
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"::error::Dukascopy download failed with exit code {exc.returncode}", file=sys.stderr)
        raise SystemExit(11)
    except Exception as exc:
        print(f"::error::Dukascopy bootstrap failed: {exc}", file=sys.stderr)
        raise SystemExit(12)
