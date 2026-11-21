"""Supabase (PostgREST) client + audit sync. Standard library only.

Supabase is the durable store between ephemeral CI runs: candles, the hash-chained event log, and the derived
signals / positions / quotes / dummy-order tables. The local hash chain stays authoritative: sync only ever
APPENDS events (never rewrites) and refuses to continue if the remote chain has diverged from the local one.
Use the SERVICE ROLE key as a CI secret only; RLS is enabled with no public policies (see supabase/schema.sql).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import config as C
from .common import DataError, iso, parse_utc
from .eventlog import EventLog, IntegrityError, State, verify_chain


class SupabaseError(DataError):
    pass


class Divergence(Exception):
    pass


def _default_http(method, url, headers, body, timeout=30):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class Supabase:
    def __init__(self, url: str, key: str, http: Callable = _default_http, sleep: Callable = time.sleep):
        if not url or not key:
            raise SupabaseError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        self.base, self.key, self.http, self.sleep = url.rstrip("/") + "/rest/v1/", key, http, sleep

    def _redact(self, text: str) -> str:
        return text.replace(self.key, "***")

    def _call(self, method: str, table: str, params=None, body=None, prefer: Optional[str] = None):
        url = self.base + table + (("?" + urllib.parse.urlencode(params)) if params else "")
        headers = {"apikey": self.key, "Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
        if prefer:
            headers["Prefer"] = prefer
        data = json.dumps(body).encode("utf-8") if body is not None else None
        last = ""
        for attempt in range(4):
            try:
                status, raw = self.http(method, url, headers, data)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = self._redact(str(exc))
                self.sleep(2 ** attempt)
                continue
            if status in (429, 500, 502, 503, 504):
                last = f"HTTP {status}"
                self.sleep(2 ** attempt)
                continue
            if status >= 400:
                raise SupabaseError(f"{method} {table}: HTTP {status}: {self._redact(raw.decode('utf-8', 'replace'))[:300]}")
            return json.loads(raw) if raw else None
        raise SupabaseError(f"{method} {table}: failed after retries ({last})")

    def upsert(self, table: str, rows: List[dict], on_conflict: str, ignore: bool = False, batch: int = 500) -> int:
        prefer = ("resolution=ignore-duplicates" if ignore else "resolution=merge-duplicates") + ",return=minimal"
        for i in range(0, len(rows), batch):
            self._call("POST", table, {"on_conflict": on_conflict}, rows[i:i + batch], prefer)
        return len(rows)

    def select(self, table: str, **params):
        return self._call("GET", table, params) or []

    def select_all(self, table: str, params: Dict[str, str], page: int = 1000) -> List[dict]:
        out, offset = [], 0
        while True:
            rows = self._call("GET", table, {**params, "limit": str(page), "offset": str(offset)}) or []
            out += rows
            if len(rows) < page:
                return out
            offset += page


def from_env(http: Callable = _default_http) -> Optional[Supabase]:
    url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    return Supabase(url, key, http) if url and key else None


# ------------------------------------------------------------------ audit sync
def _event_rows(events: List[dict]) -> List[dict]:
    return [{"seq": e["seq"], "ts": e["ts"], "type": e["type"], "data": e["data"], "prev_hash": e["prev"], "hash": e["hash"]} for e in events]


def _signal_rows(events: List[dict]) -> List[dict]:
    rows: Dict[str, dict] = {}
    for e in events:
        d = e["data"]
        if e["type"] == "SIGNAL_ACCEPTED":
            s = d["signal"]
            rows.setdefault(d["signal_id"], {
                "signal_id": d["signal_id"], "status": "accepted", "reject_reason": None, "pair": s["pair"], "side": s["side"],
                "design": s["design"], "entry": s["entry"], "sl": s["sl"], "horizon_bars": s["horizon_bars"],
                "bar_time": s["bar_time"], "strategy_id": s["strategy_id"], "strategy_rank": s["strategy_rank"],
                "fingerprint": d["fingerprint"], "raw_sha256": d["raw_sha256"], "event_seq": e["seq"]})
        elif e["type"] == "SIGNAL_REJECTED":
            key = d.get("signal_id") or "sha:" + d["raw_sha256"]
            rows.setdefault(key, {"signal_id": key, "status": "rejected", "reject_reason": d["reason"], "pair": None, "side": None,
                                  "design": None, "entry": None, "sl": None, "horizon_bars": None, "bar_time": None,
                                  "strategy_id": None, "strategy_rank": None, "fingerprint": d.get("fingerprint"),
                                  "raw_sha256": d["raw_sha256"], "event_seq": e["seq"]})
    return list(rows.values())


def _position_rows(st: State, now_iso: str) -> List[dict]:
    rows = []
    for sid, p in st.positions.items():
        o, c = p["open"], p["close"]
        status = "OPEN" if not c else ("VOID" if c["reason"] == "VOID" else "CLOSED")
        rows.append({
            "signal_id": sid, "pair": o["pair"], "side": o["side"], "lots": o["lots"], "entry": o["entry"], "sl": o["sl"],
            "horizon_bars": o["horizon_bars"], "horizon_hours": o["horizon_bars"] * C.BAR_MINUTES / 60,
            "opened_at": o["opened_at"], "deadline": o["deadline"], "strategy_id": o["strategy_id"],
            "strategy_rank": o["strategy_rank"], "status": status,
            "exit_reason": c["reason"] if c else None, "exit_price": c.get("price") if c else None,
            "exit_time": c.get("time") if c else None, "gap_fill": c.get("gap_fill") if c else None,
            "delay_min": c.get("delay_min") if c else None, "pips": c.get("pips") if c else None,
            "pnl_usd": c.get("pnl_usd") if c else None, "min_coverage": c.get("min_coverage") if c else None,
            "void_note": c.get("note") if c and c["reason"] == "VOID" else None, "updated_at": now_iso})
    return rows


def local_events(audit_dir) -> List[dict]:
    path = Path(audit_dir) / "events.jsonl"
    if not path.exists():
        return []
    events = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    verify_chain(events)
    return events


def pull(sb: Supabase, audit_dir) -> dict:
    """Restore/extend the local chain from Supabase (CI runners start empty). Chain is re-verified before writing."""
    events = local_events(audit_dir)
    remote = sb.select_all("bridge_events", {"select": "seq,ts,type,data,prev_hash,hash", "seq": f"gt.{len(events)}", "order": "seq.asc"})
    new = [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], "data": r["data"], "prev": r["prev_hash"], "hash": r["hash"]} for r in remote]
    verify_chain(events + new)                       # raises IntegrityError if the remote tail does not extend our chain
    if new:
        path = Path(audit_dir) / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as f:
            for e in new:
                f.write(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return {"local_before": len(events), "pulled": len(new)}


def verify_remote(sb: Supabase) -> dict:
    """Recompute the whole remote hash chain from the stored rows. Catches an edited row anywhere, not just at the head."""
    rows = sb.select_all("bridge_events", {"select": "seq,ts,type,data,prev_hash,hash", "order": "seq.asc"})
    events = [{"seq": r["seq"], "ts": r["ts"], "type": r["type"], "data": r["data"], "prev": r["prev_hash"], "hash": r["hash"]} for r in rows]
    verify_chain(events)
    return {"events": len(events), "head_hash": events[-1]["hash"][:12] if events else None}


def sync(sb: Supabase, audit_dir, now: datetime, run: dict) -> dict:
    events = local_events(audit_dir)
    if not events:
        raise Divergence("no local events to sync")
    head = sb.select("bridge_events", select="seq,hash", order="seq.desc", limit="1")
    remote_seq = head[0]["seq"] if head else 0
    if head and (remote_seq > len(events) or events[remote_seq - 1]["hash"] != head[0]["hash"]):
        raise Divergence(f"remote chain (seq {remote_seq}) does not match local chain: refusing to sync")
    new = [e for e in events if e["seq"] > remote_seq]
    sb.upsert("bridge_events", _event_rows(new), "seq", ignore=True, batch=200)
    st = State.from_events(events)
    sb.upsert("signals", _signal_rows(events), "signal_id")
    sb.upsert("positions", _position_rows(st, iso(now)), "signal_id")
    quotes = [{"event_seq": e["seq"], "captured_at": e["data"]["captured_at"], "symbol": e["data"]["symbol"], "bid": e["data"]["bid"],
               "ask": e["data"]["ask"], "spread_pips": e["data"]["spread_pips"], "account_mode": e["data"].get("account_mode")}
              for e in events if e["type"] == "QUOTE_SNAPSHOT"]
    sb.upsert("quotes", quotes, "event_seq", ignore=True)
    dummies = [{"id": f"{e['data'].get('run_id')}|{e['data'].get('started_at')}", "event_seq": e["seq"], "account_mode": e["data"]["account_mode"],
                "symbol": e["data"]["symbol"], "side": e["data"]["side"], "lots": e["data"]["lots"], "executed": e["data"]["execute"],
                "outcome": e["data"]["outcome"], "margin_before": e["data"].get("margin_before"),
                "margin_after_open": e["data"].get("margin_after_open"), "margin_after_close": e["data"].get("margin_after_close"),
                "report": e["data"]} for e in events if e["type"] == "DUMMY_ORDER"]
    sb.upsert("dummy_orders", dummies, "id", ignore=True)
    if run.get("run_id"):
        sb.upsert("runs", [{"run_id": run["run_id"], "finished_at": iso(now), "exit_code": run.get("exit_code"),
                            "account_mode": C.ACCOUNT_MODE, "git_sha": os.environ.get("GITHUB_SHA"), "events": len(events)}], "run_id")
    after = sb.select("bridge_events", select="seq,hash", order="seq.desc", limit="1")
    if not after or after[0]["seq"] != len(events) or after[0]["hash"] != events[-1]["hash"]:
        raise Divergence("remote head does not equal local head after sync")
    return {"events_pushed": len(new), "events_total": len(events), "signals": len(_signal_rows(events)),
            "positions": len(st.positions), "head_hash": events[-1]["hash"][:12]}


def fx_market_open(dt: datetime) -> bool:
    """Conservative FX week: Sun 21:00 UTC -> Fri 22:00 UTC (covers both US DST offsets)."""
    wd, h = dt.weekday(), dt.hour
    return (wd in (0, 1, 2, 3)) or (wd == 4 and h < 22) or (wd == 6 and h >= 21)


def watchdog(sb: Supabase, now: datetime, max_age_min: float) -> dict:
    """Fails (exit 1) if no cycle heartbeat landed recently while the FX market is open. Grace: first 45 min after open."""
    rows = sb.select("runs", select="run_id,finished_at", order="finished_at.desc", limit="1")
    age = (now - parse_utc(rows[0]["finished_at"])).total_seconds() / 60 if rows else None
    open_ = fx_market_open(now)
    grace = (now.weekday() == 6 and now.hour == 21) or (now.weekday() == 6 and now.hour == 22 and now.minute < 45)
    stale = open_ and not grace and (age is None or age > max_age_min)
    return {"market_open": open_, "last_run_age_min": None if age is None else round(age, 1), "stale": stale}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("pull", "sync", "watchdog", "verify-remote"):
        sp = sub.add_parser(name)
        sp.add_argument("--audit-dir", default="audit")
        sp.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
        sp.add_argument("--exit-code", type=int, default=None)
        sp.add_argument("--max-age-min", type=float, default=50.0)
        sp.add_argument("--now")
    a = ap.parse_args(argv)
    sb = from_env()
    if sb is None:
        print("Supabase not configured (SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY): skipped")
        return 0
    now = parse_utc(a.now) if a.now else datetime.now(timezone.utc)
    try:
        if a.cmd == "pull":
            print("PULL", json.dumps(pull(sb, a.audit_dir)))
        elif a.cmd == "verify-remote":
            print("VERIFY-REMOTE", json.dumps(verify_remote(sb)))
        elif a.cmd == "sync":
            print("SYNC", json.dumps(sync(sb, a.audit_dir, now, {"run_id": a.run_id, "exit_code": a.exit_code})))
        else:
            r = watchdog(sb, now, a.max_age_min)
            print("WATCHDOG", json.dumps(r))
            return 1 if r["stale"] else 0
    except (Divergence, IntegrityError) as exc:
        print(f"CHAIN PROBLEM: {exc}", file=sys.stderr)
        return 6
    except SupabaseError as exc:
        print(f"SUPABASE ERROR: {exc}", file=sys.stderr)
        return 7
    return 0


if __name__ == "__main__":
    sys.exit(main())
