#!/usr/bin/env python3
"""Yahoo Finance historical-only EURUSD M30 bootstrap; Headway remains live/execution source."""
from __future__ import annotations
import argparse, json, math, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "bridge"
if str(BRIDGE) not in sys.path:
    sys.path.insert(0, str(BRIDGE))
from tsebridge.store import get_store, export_m30_csv, quality_report
from tsebridge.common import iso, parse_utc, DataError

HALF = timedelta(minutes=30)
QUARTER = timedelta(minutes=15)

def _ohlc(values):
    o, h, l, c = map(float, values)
    if not all(math.isfinite(x) and x > 0 for x in (o,h,l,c)):
        raise DataError("Yahoo OHLC contains non-finite or non-positive values")
    if h < max(o,c,l) or l > min(o,c,h):
        raise DataError("Yahoo OHLC relationship is invalid")
    return o,h,l,c

def parse_yfinance_frame(frame, now):
    """Build M30 only from both complete 15m constituents; timestamps must be tz-aware."""
    if frame is None or getattr(frame, "empty", True):
        raise DataError("Yahoo Finance returned no intraday data")
    data = frame.copy()
    if getattr(data.columns, "nlevels", 1) > 1:
        data.columns = [next((str(v).lower() for v in col if str(v).lower() in
                              {"open","high","low","close"}), str(col[0]).lower())
                        for col in data.columns]
    data.columns = [str(c).lower() for c in data.columns]
    if not {"open","high","low","close"}.issubset(data.columns):
        raise DataError(f"Yahoo response missing OHLC columns: {list(data.columns)}")
    quarters = {}
    for idx, row in data.iterrows():
        t = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
        if t.tzinfo is None:
            raise DataError("Yahoo timestamps are timezone-naive; refusing to guess timezone")
        t = t.astimezone(timezone.utc).replace(second=0, microsecond=0)
        if t.minute not in (0,15,30,45):
            raise DataError(f"Yahoo timestamp is not 15m-aligned: {iso(t)}")
        quarters[t] = _ohlc((row["open"],row["high"],row["low"],row["close"]))
    groups = {}
    for t, values in quarters.items():
        start = t.replace(minute=0 if t.minute < 30 else 30)
        groups.setdefault(start,{})[t] = values
    result = []
    for start, parts in sorted(groups.items()):
        if start + HALF > now:
            continue
        if start not in parts or start + QUARTER not in parts:
            continue
        a,b = parts[start],parts[start+QUARTER]
        o,h,l,c = _ohlc((a[0],max(a[1],b[1]),min(a[2],b[2]),b[3]))
        result.append({"symbol":"EURUSD","start_ts":iso(start),"open":o,"high":h,"low":l,
                       "close":c,"coverage":1.0,"minutes_present":30,"samples":0,
                       "source":"yfinance_bootstrap"})
    return result

def validate_stored(rows):
    for r in rows:
        t = parse_utc(r["start_ts"])
        if t.minute not in (0,30) or t.second or t.microsecond:
            raise DataError(f"Stored M30 timestamp misaligned: {r['start_ts']}")
        _ohlc((r["open"],r["high"],r["low"],r["close"]))

def overlap_comparison(existing, downloaded):
    native = {r["start_ts"]:r for r in existing if str(r.get("source","")).startswith("headway")}
    diffs = [abs(float(native[r["start_ts"]]["close"])-float(r["close"]))/0.0001
             for r in downloaded if r["start_ts"] in native]
    return {"overlap_bars":len(diffs),
            "mean_abs_close_difference_pips":round(sum(diffs)/len(diffs),4) if diffs else None,
            "max_abs_close_difference_pips":round(max(diffs),4) if diffs else None,
            "note":"Diagnostic only; Headway rows retain priority. Feed equivalence is not assumed."}

def download_yahoo(days):
    if not 1 <= days <= 60:
        raise DataError("Yahoo intraday lookback must be 1..60 days")
    import yfinance as yf
    return yf.download("EURUSD=X", period=f"{days}d", interval="15m",
                       auto_adjust=False, actions=False, progress=False,
                       threads=False, timeout=30, multi_level_index=False)

def main(argv=None):
    ap=argparse.ArgumentParser()
    ap.add_argument("--min-bars",type=int,default=int(os.getenv("YFINANCE_MIN_M30_BARS","500")))
    ap.add_argument("--lookback-days",type=int,default=int(os.getenv("YFINANCE_LOOKBACK_DAYS","60")))
    ap.add_argument("--output",default="work/eurusd_m30.csv")
    ap.add_argument("--report",default="work/yfinance_bootstrap.json")
    args=ap.parse_args(argv)
    if args.min_bars < 1 or not 1 <= args.lookback_days <= 60:
        raise DataError("min-bars must be positive and Yahoo lookback must be 1..60 days")
    now=datetime.now(timezone.utc).replace(second=0,microsecond=0)
    store=get_store()
    before=store.load_m30("EURUSD",limit=args.min_bars)
    validate_stored(before)
    downloaded=0
    comparison={"overlap_bars":0,"mean_abs_close_difference_pips":None,
                "max_abs_close_difference_pips":None,"note":"No Yahoo download required."}
    provider="existing_store"
    if len(before) < args.min_bars:
        provider="Yahoo Finance EURUSD=X (15m constituents -> M30)"
        imported=parse_yfinance_frame(download_yahoo(args.lookback_days),now)
        downloaded=len(imported)
        comparison=overlap_comparison(before,imported)
        store.upsert_m30(imported,ignore_existing=True)
    after=store.load_m30("EURUSD",limit=args.min_bars)
    validate_stored(after)
    out=ROOT/args.output
    export_m30_csv(after,out)
    quality=quality_report(after,now,min_bars=args.min_bars)
    report={"provider":provider,"ticker":"EURUSD=X","timeframe":"M30","source_interval":"15m",
            "minimum_bars":args.min_bars,"lookback_days":args.lookback_days,
            "bars_before":len(before),"downloaded_valid_m30_bars":downloaded,
            "bars_after":len(after),"latest_bar_start_utc":after[-1]["start_ts"] if after else None,
            "overlap_comparison":comparison,"quality":quality,"output":str(out),
            "ok":bool(quality.get("ok")),"live_price_source":"Headway only",
            "note":"Historical bootstrap only; no live Yahoo quotes, no synthetic bars, incomplete 15m pairs discarded, Headway candles preserved."}
    rp=ROOT/args.report
    rp.parent.mkdir(parents=True,exist_ok=True)
    rp.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    if not report["ok"]:
        print(f"::error::M30 history quality gate failed: {quality.get('reason')}",file=sys.stderr)
        return 10
    return 0

if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as exc:
        print(f"::error::Yahoo Finance bootstrap failed: {exc}",file=sys.stderr)
        raise SystemExit(12)
