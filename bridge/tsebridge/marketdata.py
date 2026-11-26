"""Yahoo Finance via yfinance: the ONLY source of market candles / prices for this system.

Policy (enforced by tests/test_data_policy.py):
  * Market candles come from Yahoo Finance and nowhere else.
  * Headway never collects candles or supplies prices. Headway is used for login, account access,
    order execution and broker confirmations only.
  * No other price vendor, and no candles built from sampled terminal quotes.

Design notes
  * `yfinance.download("EURUSD=X", period="60d", interval="30m")` is called once per cycle.
  * Yahoo Finance requires no API key. Provider error text is scrubbed before it is logged.
  * Parsing is strict. A malformed payload is rejected as a whole (never partially trusted):
    non-UTC timezone, off-grid timestamps, non-finite or inconsistent OHLC, implausible prices.
  * The still-forming candle is always dropped; rows are tagged source="yfinance".
"""
from __future__ import annotations

import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

from .common import DataError, iso

PROVIDER = "yfinance"
INTERVAL = "30m"
BAR = timedelta(minutes=30)
SYMBOLS = {"EURUSD": "EURUSD=X"}                 # bridge symbol -> Yahoo Finance symbol
PRICE_BAND = {"EURUSD": (0.5, 2.5)}             # sanity band only; rejects unit/decimal corruption
_UTC_NAMES = {"utc", "etc/utc", "gmt", "etc/gmt"}


class ProviderError(Exception):
    """Provider/network failure. `retryable` marks transient conditions (429 / 5xx / timeouts)."""

    def __init__(self, message: str, retryable: bool = False, status: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def scrub(text: str, secret: Optional[str]) -> str:
    text = str(text)
    if secret:
        text = text.replace(secret, "<redacted>")
    return text[:300]


def floor30(dt: datetime) -> datetime:
    return dt.replace(minute=0 if dt.minute < 30 else 30, second=0, microsecond=0)


def expected_latest_open(now: datetime) -> datetime:
    """Open time of the most recent candle that should be complete at `now`."""
    return floor30(now.astimezone(timezone.utc)) - BAR


def fetch_time_series(api_key: str = "", pair: str = "EURUSD", outputsize: int = 520, *,
                      http_get: Optional[Callable] = None, retries: int = 3, timeout: float = 20.0,
                      sleep: Callable[[float], None] = time.sleep) -> dict:
    """Download Yahoo Finance 30-minute history and return the validated-parser shape.

    Yahoo's intraday history is limited to roughly 60 days; 520 M30 bars fit comfortably
    within that window. `http_get` is only a deterministic test seam, not used in production.
    """
    if pair not in SYMBOLS:
        raise ProviderError(f"unsupported pair {pair}")
    if http_get is not None:
        try:
            resp = http_get("yfinance-test-adapter", params={"symbol": SYMBOLS[pair], "interval": INTERVAL,
                               "outputsize": int(outputsize)}, headers={}, timeout=timeout)
            status = getattr(resp, "status_code", 200)
            if status != 200:
                raise ProviderError(f"Yahoo test adapter returned HTTP {status}")
            body = resp.json()
            if isinstance(body, dict) and str(body.get("status", "")).lower() == "error":
                msg = re.sub(r"(?i)\bkey\s+\S+", "key [redacted]", str(body.get("message", "provider error")))
                raise ProviderError("Yahoo Finance provider error: " + msg[:160], retryable=False)
            return body
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Yahoo Finance test adapter failed: {type(exc).__name__}", retryable=True) from exc
    try:
        import yfinance as yf
        frame = yf.download(SYMBOLS[pair], period="60d", interval="30m", auto_adjust=False,
                           progress=False, threads=False, timeout=timeout)
        if frame is None or frame.empty:
            raise ProviderError("Yahoo Finance returned no intraday candles", retryable=True)
        # yfinance may return MultiIndex columns even for a single ticker.
        if getattr(frame.columns, "nlevels", 1) > 1:
            frame.columns = frame.columns.get_level_values(0)
        frame = frame.tail(int(outputsize)).copy()
        values = []
        for stamp, row in frame.iterrows():
            ts = stamp.to_pydatetime() if hasattr(stamp, "to_pydatetime") else stamp
            if ts.tzinfo is None:
                raise ProviderError("Yahoo Finance returned timezone-naive candle timestamps")
            ts = ts.astimezone(timezone.utc)
            values.append({"datetime": ts.strftime("%Y-%m-%d %H:%M:%S"),
                           "open": str(row["Open"]), "high": str(row["High"]),
                           "low": str(row["Low"]), "close": str(row["Close"])})
        return {"meta": {"symbol": "EUR/USD", "interval": "30min", "exchange_timezone": "UTC"},
                "values": values, "status": "ok"}
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError(f"Yahoo Finance request failed: {type(exc).__name__}: {str(exc)[:180]}", retryable=True) from exc


def parse_time_series(payload: dict, now: datetime, pair: str = "EURUSD") -> List[dict]:
    """Payload -> validated, completed, de-duplicated M30 rows (ascending). Raises DataError."""
    if not isinstance(payload, dict) or not isinstance(payload.get("values"), list):
        raise DataError("provider payload has no 'values' list")
    meta = payload.get("meta") or {}
    if isinstance(meta, dict):
        sym = meta.get("symbol")
        if sym and str(sym).upper() not in {SYMBOLS[pair].upper(), "EUR/USD", "EURUSD"}:
            raise DataError(f"provider returned symbol {sym}, expected {SYMBOLS[pair]}")
        itv = meta.get("interval")
        if itv and str(itv).lower() not in {INTERVAL, "30min", "30m"}:
            raise DataError(f"provider returned interval {itv}, expected {INTERVAL}")
        tz = meta.get("exchange_timezone") or meta.get("timezone")
        if tz and str(tz).lower() not in _UTC_NAMES:
            raise DataError(f"provider timezone is {tz}, expected UTC")
    lo, hi = PRICE_BAND[pair]
    rows: Dict[str, dict] = {}
    for v in payload["values"]:
        try:
            raw_t = str(v["datetime"]).strip()
            t = datetime.strptime(raw_t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            o, h, l, c = (float(v[k]) for k in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(f"malformed candle record: {type(exc).__name__}") from exc
        if t.minute not in (0, 30) or t.second:
            raise DataError(f"candle timestamp is not on the M30 grid: {raw_t}")
        if not all(math.isfinite(x) for x in (o, h, l, c)) or not all(lo <= x <= hi for x in (o, h, l, c)):
            raise DataError(f"implausible or non-finite price at {raw_t}")
        if h < max(o, c, l) or l > min(o, c, h):
            raise DataError(f"inconsistent OHLC at {raw_t}")
        if t + BAR > now:
            continue                                            # forming candle: never ingested
        rows[iso(t)] = {"symbol": pair, "start_ts": iso(t), "open": o, "high": h, "low": l, "close": c,
                        "coverage": 1.0, "minutes_present": 30, "samples": 0, "source": PROVIDER}
    return [rows[k] for k in sorted(rows)]


def gap_report(rows: List[dict]) -> dict:
    """Diagnostic only: missing M30 slots that are not the weekend closure."""
    missing = []
    prev = None
    for r in rows:
        t = datetime.fromisoformat(r["start_ts"])
        if prev is not None and t - prev > BAR:
            weekend = prev.weekday() == 4 and prev.hour >= 20 and t.weekday() in (6, 0) and (t - prev) <= timedelta(hours=60)
            if not weekend:
                missing.append({"after": iso(prev), "before": iso(t), "missing_bars": int((t - prev) / BAR) - 1})
        prev = t
    return {"non_weekend_gaps": len(missing), "gaps": missing[:20]}
