"""Signal bridge intake: strict validation/normalisation of TSE scan records."""
import math
import re
from datetime import timedelta
from typing import Any, Dict

from . import config as C
from .common import canon, parse_utc, iso, sha256

_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class Reject(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def raw_sha(raw: Any) -> str:
    return sha256(canon(raw))


def _num(raw: dict, key: str) -> float:
    try:
        v = float(raw[key])
    except (KeyError, TypeError, ValueError):
        raise Reject(f"bad_{key}")
    if not math.isfinite(v) or v <= 0:
        raise Reject(f"bad_{key}")
    return v


def normalize(raw: Any) -> Dict[str, Any]:
    """Structure/contract checks only (no clock). Raises Reject(reason) -> permanent rejection."""
    if not isinstance(raw, dict):
        raise Reject("not_an_object")
    sid = raw.get("signal_id")
    if not isinstance(sid, str) or not _ID.match(sid):
        raise Reject("bad_signal_id")
    if str(raw.get("pair", "")).upper() != C.PAIR:
        raise Reject("pair_not_allowed")
    side = str(raw.get("side", "")).upper()
    if side not in C.SIDES:
        raise Reject("bad_side")
    if str(raw.get("design", "")).upper() != C.DESIGN:
        raise Reject("design_not_allowed")            # Design B (TP) removed
    for k in ("tp", "take_profit_reference"):
        if raw.get(k) not in (None, 0, 0.0):
            raise Reject("take_profit_present")       # contract is "no TP"
    try:
        horizon = int(raw.get("horizon_bars"))
    except (TypeError, ValueError):
        raise Reject("bad_horizon_bars")
    if horizon not in C.ALLOWED_HORIZON_BARS:
        raise Reject("horizon_not_allowed")
    entry, sl = _num(raw, "entry"), _num(raw, "sl")
    if (side == "BUY" and sl >= entry) or (side == "SELL" and sl <= entry):
        raise Reject("sl_wrong_side")
    pips = abs(entry - sl) / C.PIP
    if not (C.SL_MIN_PIPS <= pips <= C.SL_MAX_PIPS):
        raise Reject("sl_distance_out_of_bounds")
    try:
        bar_time = parse_utc(raw.get("bar_time"))
    except (TypeError, ValueError):
        raise Reject("bad_bar_time")
    if bar_time.minute % C.BAR_MINUTES or bar_time.second or bar_time.microsecond:
        raise Reject("bar_time_not_on_m30_grid")
    return {
        "signal_id": sid, "pair": C.PAIR, "side": side, "design": C.DESIGN,
        "entry": entry, "sl": sl, "horizon_bars": horizon, "bar_time": iso(bar_time),
        "strategy_id": str(raw.get("strategy_id", "")), "strategy_rank": int(raw.get("strategy_rank") or 0),
    }


def fingerprint(sig: Dict[str, Any]) -> str:
    """Identity of a signal's trading content (excludes scan wall-clock fields)."""
    return sha256(canon(sig))


def check_fresh(sig: Dict[str, Any], now) -> None:
    close = parse_utc(sig["bar_time"]) + timedelta(minutes=C.BAR_MINUTES)
    age_min = (now - close).total_seconds() / 60
    if age_min < -1:
        raise Reject("bar_not_closed_yet")
    if age_min > C.MAX_SIGNAL_AGE_MIN:
        raise Reject("stale")
