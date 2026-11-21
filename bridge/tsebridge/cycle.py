"""One bridge cycle: recover -> intake signals -> (auth-gated) open -> advance lifecycle -> audit.

Exit codes: 0 ok | 1 unexpected error | 2 audit-log integrity failure | 3 auth gate blocked signals | 4 data problem
            | 5 dummy order needs manual attention (unverified or not closed)
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, List

from . import config as C
from .auth import check_auth
from .candles import load_csv, closed_only
from .store import get_store, m30_bars
from .common import DataError, iso, parse_utc
from .eventlog import EventLog, IntegrityError, State
from .lifecycle import Bar, evaluate, open_position
from .signals import Reject, check_fresh, fingerprint, normalize, raw_sha

TRADE_COLS = ["signal_id", "side", "lots", "entry", "sl", "opened_at", "deadline", "exit_reason",
              "exit_price", "exit_time", "gap_fill", "delay_min", "pips", "pnl_usd", "min_coverage"]


def load_signals(path) -> List[dict]:
    if not path or not Path(path).exists():
        return []
    try:
        data = json.loads(Path(path).read_text() or "[]")
    except ValueError as exc:
        raise DataError(f"signals file is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise DataError("signals file must contain a JSON list")
    return data


def _read_json(path):
    if not path or not Path(path).exists():
        return None
    try:
        return json.loads(Path(path).read_text())
    except ValueError:
        return {"_unreadable": True}


def ingest_quotes(path, now, emit):
    """Headway Market Watch snapshot -> QUOTE_SNAPSHOT (bid/ask/spread). Source is the web terminal only."""
    q = _read_json(path)
    if q is None:
        return
    rows = [r for r in (q.get("quotes") or []) if str(r.get("symbol", "")).upper() == C.PAIR] if isinstance(q, dict) else []
    try:
        age = (now - parse_utc(q["captured_at"])).total_seconds() / 60
    except (KeyError, TypeError, ValueError):
        age = None
    if not rows or age is None or age > C.AUTH_MAX_AGE_MIN or age < -2 or q.get("status") != "ok":
        emit("DATA_ERROR", {"scope": "quotes", "message": f"unusable quotes file (status={q.get('status') if isinstance(q, dict) else None}, rows={len(rows)}, age_min={age})"})
        return
    bid, ask = float(rows[0]["bid"]), float(rows[0]["ask"])
    spread = round((ask - bid) / C.PIP, 1)
    if not (0 <= spread <= 50):
        emit("DATA_ERROR", {"scope": "quotes", "message": f"implausible spread {spread} pips"})
        return
    emit("QUOTE_SNAPSHOT", {"symbol": C.PAIR, "bid": bid, "ask": ask, "spread_pips": spread, "captured_at": q["captured_at"],
                            "account_mode": q.get("account_mode"), "source": "headway_web_terminal"})



def ingest_strategy_order(path, st, emit):
    """Headway strategy_order_result.json -> STRATEGY_ORDER + ORDER_FILLED (ticket attach)."""
    r = _read_json(path)
    if r is None:
        return False
    if not (isinstance(r, dict) and r.get("schema") == 1 and r.get("kind") == "STRATEGY_ORDER"):
        emit("DATA_ERROR", {"scope": "strategy_order_result", "message": "invalid STRATEGY_ORDER result"})
        return False
    key = f"strat|{r.get('run_id')}|{r.get('started_at')}|{r.get('signal_id')}"
    if key in st.dummy_keys:
        return False
    emit("STRATEGY_ORDER", {
        "signal_id": r.get("signal_id"), "side": r.get("side"), "lots": r.get("lots"),
        "symbol": r.get("symbol"), "sl": r.get("sl"), "outcome": r.get("outcome"),
        "ticket_id": r.get("ticket_id"), "open_price": r.get("open_price"),
        "execute": r.get("execute"), "run_id": r.get("run_id"), "started_at": r.get("started_at"),
        "opened_confirmed": r.get("opened_confirmed"),
    })
    needs_attention = r.get("outcome") in ("executed_unconfirmed", "executed_confirmed_close_failed")
    if r.get("opened_confirmed") and r.get("signal_id"):
        emit("ORDER_FILLED", {
            "signal_id": r["signal_id"],
            "ticket_id": r.get("ticket_id"),
            "open_price": r.get("open_price"),
            "lots": r.get("lots"),
            "outcome": r.get("outcome"),
            "run_id": r.get("run_id"),
            "started_at": r.get("started_at"),
        })
    elif r.get("signal_id") and str(r.get("outcome", "")).startswith("failed"):
        emit("ORDER_FAILED", {
            "signal_id": r["signal_id"],
            "outcome": r.get("outcome"),
            "run_id": r.get("run_id"),
            "started_at": r.get("started_at"),
            "finished_at": r.get("finished_at"),
        })
        needs_attention = True
    return needs_attention

def ingest_dummy_order(path, st, emit):
    """Guarded connectivity-test order result -> DUMMY_ORDER. Returns True if it needs manual attention."""
    r = _read_json(path)
    if r is None:
        return False
    ok = (isinstance(r, dict) and r.get("schema") == 1 and r.get("kind") == "DUMMY_ORDER"
          and str(r.get("symbol", "")).upper() == C.PAIR and isinstance(r.get("lots"), (int, float))
          and 0 < r["lots"] <= C.MAX_DUMMY_LOTS + 1e-9 and r.get("account_mode") in ("demo", "real"))
    if not ok:
        emit("DATA_ERROR", {"scope": "order_result", "message": "invalid or out-of-contract DUMMY_ORDER result"})
        return False
    if f"{r.get('run_id')}|{r.get('started_at')}" in st.dummy_keys:
        return False
    emit("DUMMY_ORDER", r)
    return r.get("outcome") in ("executed_unconfirmed", "executed_confirmed_close_failed")



def export_pending_orders(st: State, work: Path) -> list:
    """Signals accepted but not yet filled on the broker (no ticket_id)."""
    pending = []
    for sid, sig in st.accepted.items():
        pos = st.positions.get(sid)
        if pos and pos.get("close"):
            continue
        ticket = (pos or {}).get("open", {}).get("ticket_id") if pos else None
        if ticket:
            continue
        # pending if no position yet OR position without ticket under real execution
        pending.append({
            "signal_id": sid,
            "symbol": sig.get("pair") or C.PAIR,
            "side": str(sig.get("side", "buy")).lower(),
            "lots": float(sig.get("lots") or C.LOTS),
            "entry": sig.get("entry"),
            "sl": sig.get("sl"),
            "horizon_bars": sig.get("horizon_bars"),
            "deadline": (pos or {}).get("open", {}).get("deadline") if pos else None,
            "opened_at": (pos or {}).get("open", {}).get("opened_at") if pos else None,
            "strategy_id": sig.get("strategy_id"),
        })
    work.mkdir(parents=True, exist_ok=True)
    (work / "pending_orders.json").write_text(json.dumps(pending, indent=2) + "\n")
    return pending

def write_reports(audit: Path, log: EventLog, st: State) -> None:
    rows = []
    for p in st.positions.values():
        o, c = p["open"], p["close"]
        if c:
            rows.append([o["signal_id"], o["side"], o["lots"], o["entry"], o["sl"], o["opened_at"], o["deadline"], c["reason"],
                         c.get("price", ""), c.get("time", ""), c.get("gap_fill", ""), c.get("delay_min", ""), c.get("pips", ""),
                         c.get("pnl_usd", ""), c.get("min_coverage", "")])
    with open(audit / "trades.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(TRADE_COLS)
        w.writerows(rows)
    priced = [r for r in rows if r[7] in ("SL", "TIME")]
    summary = {
        "config": C.snapshot(), "events": len(log.events), "head_hash": log.events[-1]["hash"],
        "signals_seen": len(st.signals), "positions_total": len(st.positions),
        "positions_open": len(st.open_positions()), "positions_closed": len(priced),
        "positions_voided": sum(1 for r in rows if r[7] == "VOID"),
        "closed_pnl_usd": round(sum(r[13] for r in priced), 2),
        "exits_by_reason": {k: sum(1 for r in rows if r[7] == k) for k in ("SL", "TIME", "VOID")},
    }
    (audit / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")



    # Export open positions for the Headway monitor (ticket_id when known).
    managed = []
    for pos in st.open_positions():
        managed.append({
            "signal_id": pos.get("signal_id"),
            "ticket_id": pos.get("ticket_id"),
            "symbol": pos.get("pair") or "EURUSD",
            "side": str(pos.get("side", "")).lower(),
            "lots": pos.get("lots") or pos.get("broker_lots") or C.LOTS,
            "entry": pos.get("broker_open_price") or pos.get("entry"),
            "sl": pos.get("sl"),
            "deadline": pos.get("deadline"),
            "opened_at": pos.get("opened_at"),
            "strategy_id": pos.get("strategy_id"),
        })
    work = Path(os.environ.get("BRIDGE_WORK_DIR") or audit.parent / "work")
    try:
        work.mkdir(parents=True, exist_ok=True)
        (work / "managed_positions.json").write_text(json.dumps(managed, indent=2) + "\n")
    except OSError:
        pass
def run_cycle(*, signals_path, auth_path, audit_dir, now: datetime,
              bars_provider: Callable[[datetime, datetime], List[Bar]], run_id: str = "local",
              quotes_path=None, order_result_path=None) -> int:
    audit = Path(audit_dir)
    audit.mkdir(parents=True, exist_ok=True)
    ts = iso(now)
    try:
        log = EventLog(audit / "events.jsonl", ts)
    except IntegrityError as exc:
        print(f"AUDIT LOG INTEGRITY FAILURE: {exc}", file=sys.stderr)
        return 2
    st = State.from_events(log.events)

    def emit(typ: str, data: dict) -> dict:
        ev = log.append(typ, data, ts)
        st.apply(ev)
        return ev

    n = dict(accepted=0, rejected=0, duplicates=0, deferred=0, opened=0, closed=0, voided=0)
    code = 0

    def bump(c: int) -> None:
        nonlocal code
        if code == 0 or c == 1:
            code = c

    def open_from(sig: dict) -> None:
        emit("POSITION_OPENED", open_position(sig))
        n["opened"] += 1

    emit("RUN_STARTED", {"run_id": run_id, "config": C.snapshot()})
    try:
        for sid in st.accepted_unopened():                       # crash recovery (accepted, never opened)
            emit("RECOVERY", {"action": "open_accepted_unopened", "signal_id": sid})
            open_from(st.accepted[sid])

        ingest_quotes(quotes_path, now, emit)
        if ingest_dummy_order(order_result_path, st, emit):
            if code == 0:
                code = 5
        # strategy order result path: same --order-result or dedicated env/file next to it
        strat_path = order_result_path
        if strat_path and Path(strat_path).name == 'order_result.json':
            alt = Path(strat_path).with_name('strategy_order_result.json')
            if alt.exists():
                strat_path = str(alt)
        if ingest_strategy_order(strat_path, st, emit):
            if code == 0:
                code = 5

        try:
            raws = load_signals(signals_path)
        except DataError as exc:
            emit("DATA_ERROR", {"scope": "signals_file", "message": str(exc)})
            raws, _ = [], bump(4)

        auth = None                                              # evaluated lazily, once, only if needed
        for raw in raws:
            rsha = raw_sha(raw)
            try:
                sig = normalize(raw)
            except Reject as r:
                sid = raw.get("signal_id") if isinstance(raw, dict) and isinstance(raw.get("signal_id"), str) else None
                emit("SIGNAL_REJECTED", {"signal_id": sid, "raw_sha256": rsha, "reason": r.reason})
                n["rejected"] += 1
                continue
            fp, sid = fingerprint(sig), sig["signal_id"]
            if sid in st.signals:
                if st.signals[sid]["fingerprint"] == fp:
                    emit("SIGNAL_DUPLICATE", {"signal_id": sid, "fingerprint": fp})
                    n["duplicates"] += 1
                else:
                    emit("SIGNAL_REJECTED", {"signal_id": sid, "raw_sha256": rsha, "fingerprint": fp,
                                             "reason": "id_conflict_different_content"})
                    n["rejected"] += 1
                continue
            try:
                check_fresh(sig, now)
            except Reject as r:
                emit("SIGNAL_REJECTED", {"signal_id": sid, "raw_sha256": rsha, "fingerprint": fp, "reason": r.reason})
                n["rejected"] += 1
                continue
            if auth is None:
                ok, why, detail = check_auth(auth_path, now)
                auth = (ok, why)
                emit("AUTH_VERIFIED" if ok else "AUTH_FAILED",
                     {"reason": why, "server": detail.get("server"), "verified_at": detail.get("verified_at"),
                      "login_run_id": detail.get("run_id")})
            if not auth[0]:                                      # transient: does NOT burn the signal id
                emit("SIGNAL_DEFERRED", {"signal_id": sid, "reason": f"auth_gate:{auth[1]}"})
                n["deferred"] += 1
                bump(3)
                continue
            emit("SIGNAL_ACCEPTED", {"signal_id": sid, "raw_sha256": rsha, "fingerprint": fp, "signal": sig})
            n["accepted"] += 1
            open_from(sig)

        live = st.open_positions()                               # lifecycle runs regardless of auth state
        if live:
            start = min(parse_utc(p["opened_at"]) for p in live)
            try:
                bars = bars_provider(start, now)
            except DataError as exc:
                emit("DATA_ERROR", {"scope": "candles", "message": str(exc)[:300]})
                bump(4)
                bars = None
            if bars is not None:
                for p in live:
                    out = evaluate(p, bars)
                    if out.status == "EXIT":
                        emit("POSITION_CLOSED", out.exit)
                        n["closed"] += 1
                    elif out.status == "DATA_GAP" and now >= parse_utc(p["deadline"]) + timedelta(hours=C.VOID_AFTER_HOURS):
                        # The price path is permanently unobservable (sampling outage). Never invent a fill: VOID it.
                        emit("POSITION_VOIDED", {"signal_id": p["signal_id"], "reason": "VOID", "time": ts,
                                                 "note": f"price path unobservable: {out.note}"})
                        n["voided"] += 1
                    elif out.status == "DATA_GAP":
                        if st.last_error.get(p["signal_id"]) != out.note:
                            emit("DATA_ERROR", {"scope": "lifecycle", "position_id": p["signal_id"],
                                                "key": out.note, "message": out.note})
                        bump(4)
    except IntegrityError:
        raise
    except Exception as exc:                                     # never leave an unexplained gap in the log
        emit("ERROR", {"type": type(exc).__name__, "message": str(exc)[:300]})
        code = 1
    emit("RUN_COMPLETED", {"run_id": run_id, "exit_code": code, "open_positions": len(st.open_positions()), **n})
    work = Path(os.environ.get("BRIDGE_WORK_DIR") or Path(audit_dir).parent / "work")
    pending = export_pending_orders(st, work)
    if pending:
        emit("PENDING_ORDERS_EXPORTED", {"count": len(pending), "ids": [p["signal_id"] for p in pending]})
    write_reports(audit, log, st)
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signals", default="signals_out.json")
    ap.add_argument("--auth", default="artifacts/auth_result.json")
    ap.add_argument("--audit-dir", default="audit")
    ap.add_argument("--quotes", help="artifacts/quotes.json from the Headway terminal (optional)")
    ap.add_argument("--order-result", help="artifacts/order_result.json from a dummy-order run (optional)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--candles-csv", help="use a local M30 CSV instead of the candle store (replay/testing)")
    ap.add_argument("--now", help="ISO-8601 UTC override (replay/testing)")
    ap.add_argument("--real-execution", action="store_true",
                    help="Place real orders for accepted signals (demo/real account). Default: record only.")
    a = ap.parse_args(argv)
    if a.real_execution:
        os.environ["BRIDGE_REAL_EXECUTION"] = "true"
    now = parse_utc(a.now) if a.now else datetime.now(timezone.utc)
    if a.candles_csv:
        provider = lambda start, n: [b for b in closed_only(load_csv(a.candles_csv), n) if b.t >= start]
    else:
        store = get_store()
        provider = lambda start, n: m30_bars(store, C.PAIR, start, n)
    return run_cycle(signals_path=a.signals, auth_path=a.auth, audit_dir=a.audit_dir, now=now, bars_provider=provider,
                     run_id=a.run_id or os.environ.get("GITHUB_RUN_ID", "local"),
                     quotes_path=a.quotes, order_result_path=a.order_result)


if __name__ == "__main__":
    sys.exit(main())
