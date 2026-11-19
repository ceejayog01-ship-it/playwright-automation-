"""Candle store. Every price originates from the Headway web terminal.

The Playwright sampler records the terminal's bid once a second; it writes M1 bars. This module aggregates M1 -> M30
and records how much of each half-hour was actually OBSERVED (`coverage`). Observed coverage is the honest limit of a
sampled feed, so it is stored, thresholded for the strategy, and carried into every simulated exit.

Backends: Supabase (when SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY are set) or local CSV (data/).
Optional: `import-m30` loads M30 history you already own, tagged with its own `source`; rows from Headway always win.
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from . import config as C
from .common import DataError, iso, parse_utc
from .lifecycle import Bar
from .supabase_sync import Supabase, from_env

HALF = timedelta(minutes=30)
HISTORY_DAYS = 14
M1_COLS = ["symbol", "start_ts", "open", "high", "low", "close", "ticks", "source", "run_id"]
M30_COLS = ["symbol", "start_ts", "open", "high", "low", "close", "coverage", "minutes_present", "samples", "source"]
_NUM = {"open", "high", "low", "close", "coverage"}
_INT = {"ticks", "minutes_present", "samples"}


def floor30(dt: datetime) -> datetime:
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def _ohlc_ok(o, h, l, c) -> bool:
    return l > 0 and h >= max(o, c, l) and l <= min(o, c, h)


def parse_m1_csv(path, symbol: str, run_id: str) -> List[dict]:
    """TS sampler output (timestamp,open,high,low,close,ticks) -> M1 rows. Inconsistent bars abort the ingest."""
    try:
        with open(path, newline="") as f:
            raw = list(csv.DictReader(f))
        rows = []
        for r in raw:
            o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            if not _ohlc_ok(o, h, l, c):
                raise DataError(f"inconsistent M1 OHLC at {r['timestamp']}")
            t = parse_utc(r["timestamp"])
            if t.second or t.microsecond:
                raise DataError(f"M1 bar not minute-aligned: {r['timestamp']}")
            rows.append({"symbol": symbol, "start_ts": iso(t), "open": o, "high": h, "low": l, "close": c,
                         "ticks": int(r["ticks"]), "source": "headway", "run_id": run_id})
        return rows
    except (OSError, KeyError, ValueError) as exc:
        raise DataError(f"cannot read M1 csv {path}: {exc}") from exc


def build_m30(m1: List[dict], now: datetime) -> List[dict]:
    """Aggregate M1 -> M30 for COMPLETE half-hours only. coverage = distinct observed minutes / 30."""
    groups: Dict[datetime, Dict[datetime, dict]] = {}
    for r in m1:
        t = parse_utc(r["start_ts"])
        groups.setdefault(floor30(t), {})[t] = r
    out = []
    for start in sorted(groups):
        if start + HALF > now:
            continue                                    # still forming
        mins = [groups[start][t] for t in sorted(groups[start])]
        out.append({"symbol": mins[0]["symbol"], "start_ts": iso(start), "open": mins[0]["open"],
                    "high": max(m["high"] for m in mins), "low": min(m["low"] for m in mins), "close": mins[-1]["close"],
                    "coverage": round(len(mins) / 30, 4), "minutes_present": len(mins),
                    "samples": sum(int(m["ticks"]) for m in mins), "source": "headway"})
    return out


# ------------------------------------------------------------------ backends
class LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def _read(self, name, cols) -> List[dict]:
        p = self.root / f"{name}.csv"
        if not p.exists():
            return []
        with open(p, newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            for k in cols:
                if k in _NUM:
                    r[k] = float(r[k])
                elif k in _INT:
                    r[k] = int(r[k])
        return rows

    def _upsert(self, name, cols, rows):
        cur = {(r["symbol"], r["start_ts"]): r for r in self._read(name, cols)}
        for r in rows:
            cur[(r["symbol"], r["start_ts"])] = {k: r.get(k, "") for k in cols}
        merged = sorted(cur.values(), key=lambda r: (r["symbol"], r["start_ts"]))
        if merged:
            keep = parse_utc(merged[-1]["start_ts"]) - timedelta(days=HISTORY_DAYS)
            merged = [r for r in merged if parse_utc(r["start_ts"]) >= keep]
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / f"{name}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(merged)

    def upsert_m1(self, rows): self._upsert("candles_m1", M1_COLS, rows)
    def upsert_m30(self, rows): self._upsert("candles_m30", M30_COLS, rows)

    def _since(self, name, cols, symbol, since_iso):
        t0 = parse_utc(since_iso)
        return [r for r in self._read(name, cols) if r["symbol"] == symbol and parse_utc(r["start_ts"]) >= t0]

    def m1_since(self, symbol, since_iso): return self._since("candles_m1", M1_COLS, symbol, since_iso)
    def m30_since(self, symbol, since_iso): return self._since("candles_m30", M30_COLS, symbol, since_iso)
    def m30_existing(self, symbol, since_iso): return {r["start_ts"]: r for r in self.m30_since(symbol, since_iso)}


class SupabaseStore:
    def __init__(self, sb: Supabase):
        self.sb = sb

    def upsert_m1(self, rows): self.sb.upsert("candles_m1", rows, "symbol,start_ts")
    def upsert_m30(self, rows): self.sb.upsert("candles_m30", rows, "symbol,start_ts")

    def _since(self, table, symbol, since_iso):
        return self.sb.select_all(table, {"symbol": f"eq.{symbol}", "start_ts": f"gte.{since_iso}", "order": "start_ts.asc"})

    def m1_since(self, symbol, since_iso): return self._since("candles_m1", symbol, since_iso)
    def m30_since(self, symbol, since_iso): return self._since("candles_m30", symbol, since_iso)
    def m30_existing(self, symbol, since_iso): return {r["start_ts"]: r for r in self.m30_since(symbol, since_iso)}


def get_store():
    sb = from_env()
    return SupabaseStore(sb) if sb else LocalStore(os.environ.get("BRIDGE_DATA_DIR", "data"))


# ------------------------------------------------------------------ operations
def ingest_m1(store, csv_path, symbol: str, run_id: str, now: datetime) -> dict:
    rows = parse_m1_csv(csv_path, symbol, run_id)
    if not rows:
        return {"m1_rows": 0, "m30_rows": 0}
    store.upsert_m1(rows)
    first = floor30(min(parse_utc(r["start_ts"]) for r in rows))
    m30 = build_m30(store.m1_since(symbol, iso(first)), now)       # rebuild from ALL stored minutes (bars span runs)
    store.upsert_m30(m30)
    return {"m1_rows": len(rows), "m30_rows": len(m30), "latest_m30": m30[-1]["start_ts"] if m30 else None}


def import_m30(store, csv_path, symbol: str, source: str) -> dict:
    """Optional: history you already own. Never overwrites a Headway-derived bar; tagged so audits see the mix."""
    bars = load_m30_csv(csv_path)
    existing = store.m30_existing(symbol, iso(datetime(2000, 1, 1, tzinfo=timezone.utc)))
    rows = [{"symbol": symbol, "start_ts": iso(b.t), "open": b.o, "high": b.h, "low": b.l, "close": b.c, "coverage": 1.0,
             "minutes_present": 30, "samples": 0, "source": source} for b in bars if iso(b.t) not in existing]
    store.upsert_m30(rows)
    return {"imported": len(rows), "skipped_existing": len(bars) - len(rows)}


def load_m30_csv(path) -> List[Bar]:
    from .candles import load_csv
    return load_csv(path)


def m30_bars(store, symbol: str, start: datetime, now: datetime) -> List[Bar]:
    """Closed M30 bars for the lifecycle simulation, carrying observed coverage."""
    out = []
    for r in store.m30_since(symbol, iso(start)):
        t = parse_utc(r["start_ts"])
        if t + HALF <= now and float(r["coverage"]) > 0:
            out.append(Bar(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), cov=float(r["coverage"])))
    return out


def export_m30(store, symbol: str, now: datetime, out_csv, quality_path, min_bars: int, min_cov: float) -> dict:
    """History for the TSE scan. Bars below min coverage are dropped; the scan only runs if the newest closed bar is solid."""
    rows = store.m30_since(symbol, iso(now - timedelta(days=HISTORY_DAYS)))
    valid = [r for r in rows if float(r["coverage"]) >= min_cov][-400:]
    expected_last = iso(floor30(now) - HALF)
    latest = valid[-1]["start_ts"] if valid else None
    reason = "ok"
    if len(valid) < min_bars:
        reason = "insufficient_history"
    elif latest != expected_last:
        reason = "latest_bar_missing_or_degraded"
    q = {"ok": reason == "ok", "reason": reason, "bars_valid": len(valid), "bars_needed": min_bars,
         "bars_dropped_low_coverage": sum(1 for r in rows if float(r["coverage"]) < min_cov), "latest_valid_bar": latest,
         "expected_latest_bar": expected_last, "min_coverage": min_cov, "sources": sorted({r["source"] for r in valid}),
         "mean_coverage": round(sum(float(r["coverage"]) for r in valid) / len(valid), 3) if valid else None}
    Path(out_csv).parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for r in valid:
            w.writerow([r["start_ts"], r["open"], r["high"], r["low"], r["close"]])
    Path(quality_path).parent.mkdir(parents=True, exist_ok=True)
    Path(quality_path).write_text(json.dumps(q, indent=2) + "\n")
    return q


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("ingest-m1"); a1.add_argument("--csv", required=True); a1.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    a2 = sub.add_parser("export-m30"); a2.add_argument("--out", required=True); a2.add_argument("--quality", required=True)
    a3 = sub.add_parser("import-m30"); a3.add_argument("--csv", required=True); a3.add_argument("--source", required=True)
    for sp in (a1, a2, a3):
        sp.add_argument("--now")
    a = ap.parse_args(argv)
    now = parse_utc(a.now) if a.now else datetime.now(timezone.utc)
    try:
        store = get_store()
        if a.cmd == "ingest-m1":
            print("INGEST", json.dumps(ingest_m1(store, a.csv, C.PAIR, a.run_id, now)))
        elif a.cmd == "import-m30":
            print("IMPORT", json.dumps(import_m30(store, a.csv, C.PAIR, a.source)))
        else:
            q = export_m30(store, C.PAIR, now, a.out, a.quality, C.MIN_HISTORY_BARS, C.MIN_M30_COVERAGE)
            print("HISTORY", json.dumps(q))
            return 0 if q["ok"] else 10
    except DataError as exc:
        print(f"DATA ERROR: {exc}", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
