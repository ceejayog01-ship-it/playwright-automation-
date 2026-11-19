"""Append-only, hash-chained JSONL event log + deterministic state replay.

The log is the single source of truth. All state (seen signals, open/closed
positions) is derived by replaying it, so an interrupted run recovers by
re-reading the log and an auditor can re-derive everything independently.
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

from .common import canon, sha256

GENESIS = "0" * 64


class IntegrityError(Exception):
    pass


def _digest(ev: dict) -> str:
    body = {k: ev[k] for k in ("seq", "ts", "type", "data", "prev")}
    return sha256(canon(body))


def verify_chain(events: List[dict]) -> None:
    prev = GENESIS
    for i, ev in enumerate(events, start=1):
        if ev.get("seq") != i:
            raise IntegrityError(f"seq break at line {i}: found {ev.get('seq')}")
        if ev.get("prev") != prev:
            raise IntegrityError(f"prev-hash break at seq {i}")
        if ev.get("hash") != _digest(ev):
            raise IntegrityError(f"hash mismatch at seq {i} (event altered)")
        prev = ev["hash"]


class EventLog:
    def __init__(self, path, now_iso: Optional[str] = None):
        import json
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.events: List[dict] = []
        torn = None
        if self.path.exists():
            raw = self.path.read_bytes()
            if raw and not raw.endswith(b"\n"):          # crash mid-write: drop the torn last line
                cut = raw.rfind(b"\n") + 1
                torn, raw = raw[cut:], raw[:cut]
                with open(self.path, "wb") as f:
                    f.write(raw)
                    f.flush()
                    os.fsync(f.fileno())
            for n, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
                try:
                    self.events.append(json.loads(line))
                except ValueError as exc:
                    raise IntegrityError(f"unparseable line {n}: {exc}") from exc
            verify_chain(self.events)
        if torn is not None:
            self.append("RECOVERY", {"action": "truncated_torn_tail", "bytes": len(torn),
                                     "sha256": sha256(torn.decode("utf-8", "replace"))}, now_iso)

    def append(self, typ: str, data: dict, ts: Optional[str]) -> dict:
        import json
        prev = self.events[-1]["hash"] if self.events else GENESIS
        ev = {"seq": len(self.events) + 1, "ts": ts, "type": typ, "data": data, "prev": prev}
        ev["hash"] = _digest(ev)
        with open(self.path, "ab") as f:
            f.write((json.dumps(ev, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        self.events.append(ev)
        return ev


class State:
    """Derived state. apply() is used for both replay and live updates (one code path)."""

    def __init__(self):
        self.signals: Dict[str, dict] = {}      # key -> {"status", "fingerprint"}
        self.positions: Dict[str, dict] = {}    # signal_id -> {"open", "close"}
        self.accepted: Dict[str, dict] = {}     # signal_id -> normalized signal
        self.last_error: Dict[str, str] = {}
        self.dummy_keys = set()                  # DUMMY_ORDER dedupe keys (run_id + started_at)

    @classmethod
    def from_events(cls, events: List[dict]) -> "State":
        st = cls()
        for ev in events:
            st.apply(ev)
        return st

    def apply(self, ev: dict) -> None:
        t, d = ev["type"], ev["data"]
        if t == "SIGNAL_ACCEPTED":
            self.signals[d["signal_id"]] = {"status": "accepted", "fingerprint": d["fingerprint"]}
            self.accepted[d["signal_id"]] = d["signal"]
        elif t == "SIGNAL_REJECTED":
            key = d.get("signal_id") or "sha:" + d["raw_sha256"]
            self.signals.setdefault(key, {"status": "rejected", "fingerprint": d.get("fingerprint")})
        elif t == "POSITION_OPENED":
            self.positions[d["signal_id"]] = {"open": d, "close": None}
        elif t in ("POSITION_CLOSED", "POSITION_VOIDED"):
            self.positions[d["signal_id"]]["close"] = d
            self.last_error.pop(d["signal_id"], None)
        elif t == "DUMMY_ORDER":
            self.dummy_keys.add(f"{d.get('run_id')}|{d.get('started_at')}")
        elif t == "DATA_ERROR" and d.get("position_id"):
            self.last_error[d["position_id"]] = d.get("key", "")

    def accepted_unopened(self) -> List[str]:
        return [sid for sid in self.accepted if sid not in self.positions]

    def open_positions(self) -> List[dict]:
        return [p["open"] for p in self.positions.values() if p["close"] is None]
