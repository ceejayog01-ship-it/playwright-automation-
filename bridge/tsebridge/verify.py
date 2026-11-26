"""Independent audit verifier. Exit 0 only if every check passes.

Checks: hash chain; event-order invariants; locked-contract fields on every position;
exit consistency; and (with --candles-csv) a full independent re-simulation of every closed trade.
"""
import argparse
import json
import sys
from pathlib import Path

from . import config as C
from .candles import load_csv
from .common import parse_utc
from .eventlog import IntegrityError, State, verify_chain
from .lifecycle import evaluate, open_position


def verify(audit_dir, candles_csv=None):
    errors, warnings = [], []
    path = Path(audit_dir) / "events.jsonl"
    if not path.exists():
        return ["events.jsonl missing"], warnings
    try:
        events = [json.loads(l) for l in path.read_text().splitlines()]
        verify_chain(events)
    except (ValueError, IntegrityError) as exc:
        return [f"chain: {exc}"], warnings

    accepted, opened, closed = set(), set(), set()
    runs_open = False
    for ev in events:
        t, d = ev["type"], ev["data"]
        if t == "RUN_STARTED":
            runs_open = True
        elif t == "RUN_COMPLETED":
            runs_open = False
        elif t == "SIGNAL_ACCEPTED":
            if d["signal_id"] in accepted:
                errors.append(f"seq {ev['seq']}: signal accepted twice {d['signal_id']}")
            accepted.add(d["signal_id"])
            s = d["signal"]
            if s["pair"] != C.PAIR or s["design"] != C.DESIGN or "tp" in s:
                errors.append(f"seq {ev['seq']}: accepted signal violates contract {d['signal_id']}")
        elif t == "POSITION_OPENED":
            sid = d["signal_id"]
            if sid not in accepted:
                errors.append(f"seq {ev['seq']}: position opened without accepted signal {sid}")
            if sid in opened:
                errors.append(f"seq {ev['seq']}: position opened twice {sid}")
            opened.add(sid)
            if d["lots"] != C.LOTS or d["pair"] != C.PAIR or "tp" in d or d["side"] not in C.SIDES \
                    or d["horizon_bars"] not in C.ALLOWED_HORIZON_BARS:
                errors.append(f"seq {ev['seq']}: position violates locked contract {sid}")
        elif t == "DUMMY_ORDER":
            lots = d.get("lots")
            if not isinstance(lots, (int, float)) or not 0 < lots <= C.MAX_DUMMY_LOTS + 1e-9 or str(d.get("symbol")).upper() != C.PAIR:
                errors.append(f"seq {ev['seq']}: DUMMY_ORDER outside its contract")
            if d.get("dry_run") and d.get("opened_confirmed"):
                errors.append(f"seq {ev['seq']}: dry-run claims an opened position")
            if d.get("opened_confirmed") and not (d.get("margin_after_open") is not None and d.get("margin_before") is not None
                                                  and d["margin_after_open"] > d["margin_before"]):
                errors.append(f"seq {ev['seq']}: DUMMY_ORDER 'confirmed' without a margin increase")
            if d.get("outcome") in ("executed_unconfirmed", "executed_confirmed_close_failed"):
                warnings.append(f"seq {ev['seq']}: dummy order needs MANUAL CHECK/CLOSE on the {d.get('account_mode')} account ({d.get('outcome')})")
        elif t == "STRATEGY_ORDER":
            if d.get("opened_confirmed") and not d.get("execute"):
                errors.append(f"seq {ev['seq']}: dry-run STRATEGY_ORDER claims an opened position")
            if d.get("execute") and d.get("outcome") in ("executed_unconfirmed", "executed_confirmed_close_failed"):
                warnings.append(f"seq {ev['seq']}: strategy order {d.get('signal_id')} needs MANUAL CHECK ({d.get('outcome')})")
        elif t == "BROKER_CLOSE_CONFIRMED":
            if d.get("signal_id") not in opened:
                errors.append(f"seq {ev['seq']}: broker close confirmation without a position {d.get('signal_id')}")
        elif t == "POSITION_VOIDED":
            sid = d["signal_id"]
            if sid not in opened or sid in closed:
                errors.append(f"seq {ev['seq']}: VOID on a position that is not open {sid}")
            closed.add(sid)
            warnings.append(f"seq {ev['seq']}: position {sid} VOIDED (price path unobservable) - excluded from P&L")
        elif t == "POSITION_CLOSED":
            sid = d["signal_id"]
            if sid not in opened:
                errors.append(f"seq {ev['seq']}: close without open {sid}")
            if sid in closed:
                errors.append(f"seq {ev['seq']}: closed twice {sid}")
            closed.add(sid)
            if d["reason"] not in ("SL", "TIME"):
                errors.append(f"seq {ev['seq']}: illegal exit reason {d['reason']} (no TP allowed)")
    if runs_open:
        warnings.append("last run has RUN_STARTED without RUN_COMPLETED (interrupted; recovery will handle it)")
    for sid in accepted - opened:
        warnings.append(f"accepted but not yet opened: {sid}")

    st = State.from_events(events)
    for sid, p in st.positions.items():
        c = p["close"]
        if not c or c["reason"] in ("VOID", "ORDER_FAILED", "ORDER_EXPIRED"):
            continue
        dl, ct = parse_utc(p["open"]["deadline"]), parse_utc(c["time"])
        if c["reason"] == "TIME" and ct < dl:
            errors.append(f"{sid}: TIME exit before deadline")
        if c["reason"] == "SL" and ct >= dl:
            errors.append(f"{sid}: SL exit at/after deadline")

    if candles_csv:
        bars = load_csv(candles_csv)
        for sid, p in st.positions.items():
            if not p["close"] or p["close"]["reason"] in ("VOID", "ORDER_FAILED", "ORDER_EXPIRED"):
                continue
            out = evaluate(p["open"], bars)
            c = p["close"]
            if out.status != "EXIT":
                errors.append(f"{sid}: replay inconclusive ({out.status} {out.note})")
            elif (out.exit["reason"], out.exit["price"], out.exit["time"]) != (c["reason"], c["price"], c["time"]):
                errors.append(f"{sid}: replay mismatch logged={c['reason']}@{c['price']} {c['time']} "
                              f"replay={out.exit['reason']}@{out.exit['price']} {out.exit['time']}")
    return errors, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-dir", default="audit")
    ap.add_argument("--candles-csv")
    a = ap.parse_args(argv)
    errors, warnings = verify(a.audit_dir, a.candles_csv)
    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("FAIL ", e)
    print("AUDIT VERIFY:", "FAILED" if errors else "PASSED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
