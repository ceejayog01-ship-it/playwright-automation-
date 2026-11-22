"""Candle store. Primary source is native Headway MT5 M30 history.

Native path (preferred):
  - Headway automation --mode=history writes history_EURUSD_M30.csv / .json
  - This module upserts those bars into Supabase (source='headway_native')
  - No M1 construction, no 1-second sampling aggregation for strategy.

Legacy path (diagnostic only):
  - 1 Hz sampler still exists for debugging; its M1->M30 aggregation is retained
    but MUST NOT be used as the strategy feed.

Backends: Supabase (when credentials set) or local CSV (data/).
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


def parse_native_m30_csv(path, symbol: str = "EURUSD") -> List[dict]:
    """Native history CSV (timestamp,open,high,low,close) -> M30 rows with source=headway_native."""
    try:
        with open(path, newline="") as f:
            raw = list(csv.DictReader(f))
        rows = []
        for r in raw:
            o, h, l, c = (float(r[k]) for k in ("open", "high", "low", "close"))
            if not _ohlc_ok(o, h, l, c):
                raise DataError(f"inconsistent native M30 OHLC at {r['timestamp']}")
            t = parse_utc(r["timestamp"])
            if t.minute % 30 or t.second or t.microsecond:
                t = floor30(t)
            rows.append({
                "symbol": symbol,
                "start_ts": iso(t),
                "open": o, "high": h, "low": l, "close": c,
                "coverage": 1.0,
                "minutes_present": 30,
                "samples": 0,
                "source": "headway_native",
            })
        by_ts = {r["start_ts"]: r for r in rows}
        return [by_ts[k] for k in sorted(by_ts)]
    except (OSError, KeyError, ValueError) as exc:
        raise DataError(f"cannot read native M30 csv {path}: {exc}") from exc


def parse_native_m30_json(path, symbol: str = "EURUSD") -> List[dict]:
    """history_EURUSD_M30.json produced by fetchNativeM30."""
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict) or "bars" not in data:
            raise DataError("native history json missing bars")
        rows = []
        for b in data["bars"]:
            o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
            if not _ohlc_ok(o, h, l, c):
                continue
            t = parse_utc(b["start"])
            t = floor30(t)
            rows.append({
                "symbol": symbol,
                "start_ts": iso(t),
                "open": o, "high": h, "low": l, "close": c,
                "coverage": 1.0,
                "minutes_present": 30,
                "samples": 0,
                "source": data.get("source", "headway_native"),
            })
        by_ts = {r["start_ts"]: r for r in rows}
        return [by_ts[k] for k in sorted(by_ts)]
    except (OSError, KeyError, ValueError, TypeError) as exc:
        raise DataError(f"cannot read native M30 json {path}: {exc}") from exc


def parse_m1_csv(path, symbol: str, run_id: str) -> List[dict]:
    """LEGACY: TS sampler output -> M1 rows. Diagnostic only."""
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
    """LEGACY aggregation M1 -> M30. Not used for strategy when native history is present."""
    groups: Dict[datetime, Dict[datetime, dict]] = {}
    for r in m1:
        t = parse_utc(r["start_ts"])
        groups.setdefault(floor30(t), {})[t] = r
    out = []
    for start in sorted(groups):
        if start + HALF > now:
            continue
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
        self.root.mkdir(parents=True, exist_ok=True)
        self.m30_path = self.root / "candles_m30.csv"

    def upsert_m30(self, rows: List[dict], ignore_existing: bool = False) -> int:
        existing = {}
        if self.m30_path.exists():
            with open(self.m30_path, newline="") as f:
                for r in csv.DictReader(f):
                    existing[r["start_ts"]] = r
        inserted = 0
        for r in rows:
            key = r["start_ts"]
            if ignore_existing and key in existing:
                continue
            existing[key] = {k: r.get(k, "") for k in M30_COLS}
            inserted += 1
        with open(self.m30_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=M30_COLS)
            w.writeheader()
            for k in sorted(existing):
                w.writerow(existing[k])
        return inserted

    def load_m30(self, symbol: str, limit: int = 500) -> List[dict]:
        if not self.m30_path.exists():
            return []
        with open(self.m30_path, newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("symbol") == symbol]
        for r in rows:
            for k in _NUM:
                if k in r and r[k] != "":
                    r[k] = float(r[k])
            for k in _INT:
                if k in r and r[k] != "":
                    r[k] = int(r[k])
        return sorted(rows, key=lambda x: x["start_ts"])[-limit:]


class SupabaseStore:
    def __init__(self, sb: Supabase):
        self.sb = sb

    def upsert_m30(self, rows: List[dict], ignore_existing: bool = False) -> int:
        if not rows:
            return 0
        body = [{k: r.get(k) for k in M30_COLS} for r in rows]
        resolution = "ignore-duplicates" if ignore_existing else "merge-duplicates"
        self.sb._call("POST", "candles_m30", params={"on_conflict": "symbol,start_ts"}, body=body,
                      prefer=f"resolution={resolution},return=minimal")
        return len(rows)

    def load_m30(self, symbol: str, limit: int = 500) -> List[dict]:
        rows = self.sb._call(
            "GET", "candles_m30",
            params={"symbol": f"eq.{symbol}", "order": "start_ts.desc", "limit": str(limit)},
        ) or []
        return list(reversed(rows))


def get_store():
    sb = from_env()
    if sb:
        return SupabaseStore(sb)
    return LocalStore(Path("data"))


def export_m30_csv(rows: List[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for r in rows:
            w.writerow([r["start_ts"], r["open"], r["high"], r["low"], r["close"]])


def quality_report(rows: List[dict], now: datetime, min_bars: int = None) -> dict:
    min_bars = min_bars or C.MIN_HISTORY_BARS
    if not rows:
        return {"ok": False, "reason": "no_bars", "bars": 0}
    latest = parse_utc(rows[-1]["start_ts"])
    age_min = (now - (latest + HALF)).total_seconds() / 60
    if age_min > 40:
        return {"ok": False, "reason": f"latest_bar_stale_age_min={age_min:.0f}", "bars": len(rows)}
    if len(rows) < min_bars:
        return {"ok": False, "reason": f"insufficient_history_{len(rows)}<{min_bars}", "bars": len(rows)}
    return {"ok": True, "reason": "ok", "bars": len(rows), "latest": iso(latest), "source": rows[-1].get("source")}


# ------------------------------------------------------------------ CLI
def main(argv=None):
    p = argparse.ArgumentParser(prog="tsebridge.store")
    sub = p.add_subparsers(dest="cmd", required=True)

    ing = sub.add_parser("ingest-native", help="Ingest native M30 history (primary path)")
    ing.add_argument("--csv", help="history_EURUSD_M30.csv")
    ing.add_argument("--json", help="history_EURUSD_M30.json")
    ing.add_argument("--symbol", default="EURUSD")
    ing.add_argument("--out", help="also write a clean CSV for TSE scan")
    ing.add_argument("--quality", help="write quality json")

    leg = sub.add_parser("ingest-m1", help="LEGACY: ingest sampled M1 and aggregate to M30 (diagnostic)")
    leg.add_argument("--csv", required=True)
    leg.add_argument("--run-id", default="legacy")
    leg.add_argument("--symbol", default="EURUSD")

    exp = sub.add_parser("export-m30", help="Export current M30 history for TSE")
    exp.add_argument("--out", required=True)
    exp.add_argument("--quality", help="write quality json")
    exp.add_argument("--symbol", default="EURUSD")
    exp.add_argument("--limit", type=int, default=500)

    args = p.parse_args(argv)
    store = get_store()
    now = datetime.now(timezone.utc)

    if args.cmd == "ingest-native":
        rows = []
        if args.json:
            rows = parse_native_m30_json(args.json, args.symbol)
        elif args.csv:
            rows = parse_native_m30_csv(args.csv, args.symbol)
        else:
            print("need --csv or --json", file=sys.stderr)
            return 2
        n = store.upsert_m30(rows)
        print(f"upserted {n} native M30 bars (source=headway_native)")
        if args.out:
            export_m30_csv(rows[-500:], Path(args.out))
        if args.quality:
            q = quality_report(rows, now)
            Path(args.quality).write_text(json.dumps(q, indent=2) + "\n")
            if not q["ok"]:
                print(f"quality: {q['reason']}", file=sys.stderr)
                return 10
        return 0

    if args.cmd == "ingest-m1":
        m1 = parse_m1_csv(args.csv, args.symbol, args.run_id)
        m30 = build_m30(m1, now)
        n = store.upsert_m30(m30)
        print(f"(legacy) upserted {n} aggregated M30 bars from {len(m1)} M1")
        return 0

    if args.cmd == "export-m30":
        rows = store.load_m30(args.symbol, args.limit)
        export_m30_csv(rows, Path(args.out))
        q = quality_report(rows, now)
        if args.quality:
            Path(args.quality).write_text(json.dumps(q, indent=2) + "\n")
        print(f"exported {len(rows)} M30 bars -> {args.out} (ok={q['ok']})")
        return 0 if q["ok"] else 10

    return 1


if __name__ == "__main__":
    sys.exit(main())

def m30_bars(store, symbol: str, start: datetime, n: int) -> List[Bar]:
    """Compatibility helper used by cycle.py for lifecycle evaluation.
    Returns Bar objects from the most recent history ending at/before start.
    """
    rows = store.load_m30(symbol, limit=max(n + 50, 200))
    bars = []
    for r in rows:
        t = parse_utc(r["start_ts"])
        if t > start:
            continue
        bars.append(Bar(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                        float(r.get("coverage", 1.0))))
    return bars[-n:] if len(bars) > n else bars
