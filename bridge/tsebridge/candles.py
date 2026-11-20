"""M30 candle CSV I/O and OHLC sanity checks. All prices originate from the Headway web terminal (see store.py)."""
import csv
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

from . import config as C
from .common import DataError, iso, parse_utc
from .lifecycle import Bar

def _check(b: Bar) -> Bar:
    if not (b.h >= max(b.o, b.c, b.l) and b.l <= min(b.o, b.c, b.h) and b.l > 0):
        raise DataError(f"inconsistent OHLC at {iso(b.t)}")
    return b


def closed_only(bars: List[Bar], now: datetime) -> List[Bar]:
    """Drop the still-forming bar and de-duplicate by open time."""
    out = {b.t: b for b in bars if b.t + timedelta(minutes=C.BAR_MINUTES) <= now}
    return [out[k] for k in sorted(out)]


def write_csv(bars: List[Bar], path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for b in bars:
            w.writerow([iso(b.t), b.o, b.h, b.l, b.c])


def load_csv(path) -> List[Bar]:
    try:
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        return [_check(Bar(parse_utc(r["timestamp"]), float(r["open"]), float(r["high"]),
                           float(r["low"]), float(r["close"]))) for r in rows]
    except (OSError, KeyError, ValueError) as exc:
        raise DataError(f"cannot read candles csv {path}: {exc}") from exc
