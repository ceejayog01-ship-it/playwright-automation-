"""Pure position-lifecycle simulation: stop-loss + time exit, no take-profit.

Deterministic: same position + same bars => same outcome. No clock, no I/O.

Fill model (documented, conservative, no look-ahead):
  * Position opens at the close of the signal bar, at the signal's entry price.
  * Each later M30 bar (open time t < deadline) is checked for the SL:
      BUY : bar open <= SL -> fill at the OPEN (gap); else bar low  <= SL -> fill at SL
      SELL: bar open >= SL -> fill at the OPEN (gap); else bar high >= SL -> fill at SL
  * Time exit: the first bar with t >= deadline exits at that bar's OPEN.
  * Spread/slippage/commission are not modelled.
  * Bars must be contiguous. A hole is tolerated only across the weekend closure;
    any other hole returns DATA_GAP and the position stays open (never guessed).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from . import config as C
from .common import iso, parse_utc

BAR = timedelta(minutes=C.BAR_MINUTES)


@dataclass(frozen=True)
class Bar:
    t: datetime            # bar OPEN time, UTC
    o: float
    h: float
    l: float
    c: float
    cov: float = 1.0       # share of the bar's minutes actually observed (Headway sampling coverage)


@dataclass(frozen=True)
class Outcome:
    status: str            # "EXIT" | "OPEN" | "DATA_GAP"
    exit: Optional[dict] = None
    note: str = ""


def open_position(sig: dict) -> dict:
    opened = parse_utc(sig["bar_time"]) + BAR
    deadline = opened + int(sig["horizon_bars"]) * BAR
    side = str(sig["side"]).upper()
    if side not in ("BUY", "SELL"):
        side = "BUY"
    lots = float(sig.get("lots") or C.LOTS)
    return {
        "signal_id": sig["signal_id"], "pair": sig.get("pair") or C.PAIR, "side": side, "lots": lots,
        "entry": sig["entry"], "sl": sig["sl"], "horizon_bars": int(sig["horizon_bars"]),
        "opened_at": iso(opened), "deadline": iso(deadline),
        "strategy_id": sig.get("strategy_id"), "strategy_rank": sig.get("strategy_rank") or 0,
        "ticket_id": sig.get("ticket_id"),  # filled later by ORDER_FILLED
    }


def _weekend_gap(start: datetime, end: datetime) -> bool:
    return (start.weekday() == 4 and start.hour >= 20
            and end.weekday() in (6, 0) and (end - start) <= timedelta(hours=60))


def _exit(pos: dict, reason: str, price: float, bar_t: datetime, deadline: datetime, gap: bool, min_cov: float = 1.0) -> Outcome:
    diff = price - pos["entry"] if pos["side"] == "BUY" else pos["entry"] - price
    return Outcome("EXIT", {
        "signal_id": pos["signal_id"], "reason": reason, "price": round(price, 6), "time": iso(bar_t),
        "gap_fill": gap, "delay_min": max(0, int((bar_t - deadline).total_seconds() // 60)) if reason == "TIME" else 0,
        "pips": round(diff / C.PIP, 1), "pnl_usd": round(diff * pos["lots"] * C.CONTRACT_SIZE, 2),
        "min_coverage": round(min_cov, 3),     # lowest observed-coverage of any bar the SL was checked on
    })


def evaluate(pos: dict, bars: List[Bar]) -> Outcome:
    opened, deadline = parse_utc(pos["opened_at"]), parse_utc(pos["deadline"])
    side, sl = pos["side"], pos["sl"]
    prev_end = opened
    min_cov = 1.0
    for b in sorted(bars, key=lambda x: x.t):
        if b.t < opened:
            continue
        if b.t != prev_end and not _weekend_gap(prev_end, b.t):
            return Outcome("DATA_GAP", note=f"no bars between {iso(prev_end)} and {iso(b.t)}")
        if b.t >= deadline:
            return _exit(pos, "TIME", b.o, b.t, deadline, False, min_cov)
        min_cov = min(min_cov, b.cov)
        if side == "BUY":
            if b.o <= sl:
                return _exit(pos, "SL", b.o, b.t, deadline, True, min_cov)
            if b.l <= sl:
                return _exit(pos, "SL", sl, b.t, deadline, False, min_cov)
        else:
            if b.o >= sl:
                return _exit(pos, "SL", b.o, b.t, deadline, True, min_cov)
            if b.h >= sl:
                return _exit(pos, "SL", sl, b.t, deadline, False, min_cov)
        prev_end = b.t + BAR
    return Outcome("OPEN")
