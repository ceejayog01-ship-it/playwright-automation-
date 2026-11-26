"""Local M30 candle store.

Market candles originate ONLY from Yahoo Finance (see marketdata.py). Headway never supplies candles;
there is no ingestion path for terminal-derived, sampled-quote or other-vendor bars.

Backend: local CSV (data/candles_m30.csv), persisted between scheduled runs by the GitHub Actions
cache. Rows carry a `source` column; rows whose source is not the approved provider are purged by
`purge_other_sources` (this removes bars that older versions of the pipeline left in a restored cache).
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
from .common import iso, parse_utc
from .lifecycle import Bar

HALF = timedelta(minutes=30)
M30_COLS = ["symbol", "start_ts", "open", "high", "low", "close", "coverage", "minutes_present", "samples", "source"]
_NUM = {"open", "high", "low", "close", "coverage"}
_INT = {"minutes_present", "samples"}


def floor30(dt: datetime) -> datetime:
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


class LocalStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.m30_path = self.root / "candles_m30.csv"

    def _read_all(self) -> Dict[str, dict]:
        existing: Dict[str, dict] = {}
        if self.m30_path.exists():
            with open(self.m30_path, newline="") as f:
                for r in csv.DictReader(f):
                    existing[(r.get("symbol") or "") + "|" + r["start_ts"]] = r
        return existing

    def _write_all(self, existing: Dict[str, dict]) -> None:
        tmp = self.m30_path.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=M30_COLS)
            w.writeheader()
            for k in sorted(existing, key=lambda k: (existing[k].get("symbol", ""), existing[k]["start_ts"])):
                w.writerow({c: existing[k].get(c, "") for c in M30_COLS})
        os.replace(tmp, self.m30_path)

    def upsert_m30(self, rows: List[dict], ignore_existing: bool = False) -> int:
        existing = self._read_all()
        changed = 0
        for r in rows:
            key = r["symbol"] + "|" + r["start_ts"]
            if ignore_existing and key in existing:
                continue
            existing[key] = {k: r.get(k, "") for k in M30_COLS}
            changed += 1
        self._write_all(existing)
        return changed

    def purge_other_sources(self, keep_source: str) -> Dict[str, int]:
        """Delete every stored bar whose source != keep_source. Returns {source: count_removed}."""
        existing = self._read_all()
        removed: Dict[str, int] = {}
        kept: Dict[str, dict] = {}
        for k, r in existing.items():
            if r.get("source") == keep_source:
                kept[k] = r
            else:
                removed[r.get("source") or "unknown"] = removed.get(r.get("source") or "unknown", 0) + 1
        if removed:
            self._write_all(kept)
        return removed

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


def get_store() -> LocalStore:
    # GitHub Actions runners are ephemeral; market-cycle.yml restores and saves this local store
    # through the Actions cache before/after each serialized run.
    return LocalStore(Path(os.environ.get("BRIDGE_DATA_DIR", "data")))


def export_m30_csv(rows: List[dict], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for r in rows:
            w.writerow([r["start_ts"], r["open"], r["high"], r["low"], r["close"]])


def quality_report(rows: List[dict], now: datetime, min_bars: Optional[int] = None) -> dict:
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


def m30_bars(store, symbol: str, start: datetime, now: datetime) -> List[Bar]:
    """Completed M30 bars with open time >= start, as lifecycle Bars (the forming bar is excluded).

    Signature matches cycle.py's provider callback `(start, now)`. (Earlier versions treated `now` as a
    bar count and returned bars at/before `start`, which raised TypeError whenever a position was open.)
    """
    out: List[Bar] = []
    for r in store.load_m30(symbol, limit=100_000):
        t = parse_utc(r["start_ts"])
        if t < start or t + HALF > now:
            continue
        out.append(Bar(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                       float(r.get("coverage", 1.0) or 1.0)))
    return out


# ------------------------------------------------------------------ CLI
def main(argv=None):
    p = argparse.ArgumentParser(prog="tsebridge.store")
    sub = p.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export-m30", help="Export the stored M30 history as a clean CSV")
    exp.add_argument("--out", required=True)
    exp.add_argument("--quality", help="write quality json")
    exp.add_argument("--symbol", default="EURUSD")
    exp.add_argument("--limit", type=int, default=500)
    args = p.parse_args(argv)
    now = datetime.now(timezone.utc)
    rows = get_store().load_m30(args.symbol, args.limit)
    export_m30_csv(rows, Path(args.out))
    q = quality_report(rows, now)
    if args.quality:
        Path(args.quality).write_text(json.dumps(q, indent=2) + "\n")
    print(f"exported {len(rows)} M30 bars -> {args.out} (ok={q['ok']})")
    return 0 if q["ok"] else 10


if __name__ == "__main__":
    sys.exit(main())
