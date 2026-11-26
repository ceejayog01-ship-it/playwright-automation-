import json
import pytest
from tsebridge import cycle, verify, config as C
from tsebridge.candles import write_csv, load_csv, closed_only
from tsebridge.eventlog import EventLog, State
from tsebridge.lifecycle import Bar
from helpers import BAR, dt, flat_bars, raw_signal

NOW = dt(2026, 11, 19, 10, 40)            # 10:00 bar closed 10:30, 10 min old


def write_auth(tmp, now=NOW, **kw):
    d = {"schema": 2, "status": "verified", "reason": "ok", "account_mode": "demo", "server": "Headway-Demo",
         "terminal_host": "demo-mt5.hw-terminal.com", "url_path": "/webterminal/mt5-demo/",
         "verified_at": now.isoformat(), "run_id": "L1"}
    d.update(kw)
    p = tmp / "auth.json"
    p.write_text(json.dumps(d))
    return p


def write_signals(tmp, *raws):
    p = tmp / "signals.json"
    p.write_text(json.dumps(list(raws)))
    return p


def provider_from(bars):
    return lambda start, now: [b for b in closed_only(bars, now) if b.t >= start]


def run(tmp, now=NOW, signals=None, auth=None, bars=None):
    return cycle.run_cycle(signals_path=signals, auth_path=auth, audit_dir=tmp / "audit", now=now,
                           bars_provider=provider_from(bars or []), run_id="T")


def types(tmp):
    return [e["type"] for e in EventLog(tmp / "audit" / "events.jsonl", "x").events]


def test_open_then_rerun_is_idempotent(tmp_path):
    sig, auth = write_signals(tmp_path, raw_signal()), write_auth(tmp_path)
    assert run(tmp_path, signals=sig, auth=auth) == 0
    assert run(tmp_path, signals=sig, auth=auth) == 0
    t = types(tmp_path)
    assert t.count("POSITION_OPENED") == 1 and t.count("SIGNAL_DUPLICATE") == 1
    assert verify.verify(tmp_path / "audit")[0] == []


def test_design_b_and_tp_signals_never_open(tmp_path):
    sig = write_signals(tmp_path, raw_signal(signal_id="B1", design="B", tp=1.1015), raw_signal(signal_id="T1", tp=1.105))
    run(tmp_path, signals=sig, auth=write_auth(tmp_path))
    st = State.from_events(EventLog(tmp_path / "audit" / "events.jsonl", "x").events)
    assert not st.positions and "B1" in st.signals and "T1" in st.signals


def test_auth_failure_defers_without_burning_signal(tmp_path):
    sig = write_signals(tmp_path, raw_signal())
    assert run(tmp_path, signals=sig, auth=tmp_path / "missing.json") == 3
    assert "POSITION_OPENED" not in types(tmp_path) and "SIGNAL_DEFERRED" in types(tmp_path)
    assert run(tmp_path, now=dt(2026, 11, 19, 10, 50), signals=sig, auth=write_auth(tmp_path, dt(2026, 11, 19, 10, 50))) == 0
    assert types(tmp_path).count("POSITION_OPENED") == 1          # same id accepted once auth recovered


def test_stale_auth_blocks_but_server_label_is_not_a_gate(tmp_path):
    sig = write_signals(tmp_path, raw_signal())
    assert run(tmp_path, signals=sig, auth=write_auth(tmp_path, dt(2026, 11, 19, 9, 0))) == 3
    assert run(tmp_path, signals=sig, auth=write_auth(tmp_path, server="Headway-Real")) == 0


def test_id_conflict_rejected(tmp_path):
    auth = write_auth(tmp_path)
    run(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=auth)
    run(tmp_path, signals=write_signals(tmp_path, raw_signal(entry=1.1001, sl=1.0981)), auth=auth)
    assert types(tmp_path).count("POSITION_OPENED") == 1
    ev = [e for e in EventLog(tmp_path / "audit" / "events.jsonl", "x").events if e["type"] == "SIGNAL_REJECTED"]
    assert ev[-1]["data"]["reason"] == "id_conflict_different_content"


def test_crash_between_accept_and_open_recovers_exactly_once(tmp_path):
    from tsebridge.signals import normalize, fingerprint, raw_sha
    log = EventLog(tmp_path / "audit" / "events.jsonl", "x")
    raw = raw_signal()
    sig = normalize(raw)
    log.append("SIGNAL_ACCEPTED", {"signal_id": sig["signal_id"], "raw_sha256": raw_sha(raw),
                                   "fingerprint": fingerprint(sig), "signal": sig}, "x")   # crash here
    run(tmp_path)
    run(tmp_path)
    assert types(tmp_path).count("POSITION_OPENED") == 1 and "RECOVERY" in types(tmp_path)


def test_full_lifecycle_sl_and_time_and_independent_replay(tmp_path):
    auth = write_auth(tmp_path)
    s_sl = raw_signal(signal_id="SL1")
    s_tm = raw_signal(signal_id="TM1", side="SELL", sl=1.1020, horizon_bars=12)
    run(tmp_path, signals=write_signals(tmp_path, s_sl, s_tm), auth=auth)
    bars = flat_bars(dt(2026, 11, 19, 10, 30), 40)
    bars[3] = Bar(bars[3].t, 1.0995, 1.0998, 1.0970, 1.0975)                  # BUY SL hit at 1.0980
    later = dt(2026, 11, 19, 20, 0)
    assert run(tmp_path, now=later, bars=bars) == 0
    st = State.from_events(EventLog(tmp_path / "audit" / "events.jsonl", "x").events)
    assert st.positions["SL1"]["close"]["reason"] == "SL" and st.positions["SL1"]["close"]["pnl_usd"] == -100.0
    assert st.positions["TM1"]["close"]["reason"] == "TIME"                   # 12 bars = 6h -> 16:30 open
    assert run(tmp_path, now=later, bars=bars) == 0                           # no double close
    assert types(tmp_path).count("POSITION_CLOSED") == 2
    csv_path = tmp_path / "bars.csv"
    write_csv(bars, csv_path)
    assert verify.verify(tmp_path / "audit", str(csv_path))[0] == []          # independent replay agrees
    assert len((tmp_path / "audit" / "trades.csv").read_text().splitlines()) == 3


def test_verify_catches_doctored_exit_via_replay(tmp_path):
    from tsebridge.eventlog import _digest, GENESIS
    run(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))
    bars = flat_bars(dt(2026, 11, 19, 10, 30), 60)
    run(tmp_path, now=dt(2026, 11, 20, 14, 0), bars=bars)
    p = tmp_path / "audit" / "events.jsonl"
    evs = [json.loads(l) for l in p.read_text().splitlines()]
    prev = GENESIS
    for e in evs:                                                             # attacker re-chains a doctored P&L
        if e["type"] == "POSITION_CLOSED":
            e["data"]["price"] = 1.2000
        e["prev"] = prev
        e["hash"] = _digest(e)
        prev = e["hash"]
    p.write_text("".join(json.dumps(e, sort_keys=True, separators=(",", ":")) + "\n" for e in evs))
    csv_path = tmp_path / "bars.csv"
    write_csv(bars, csv_path)
    errors, _ = verify.verify(tmp_path / "audit", str(csv_path))
    assert any("replay mismatch" in e for e in errors)


def gap_bars():
    return flat_bars(dt(2026, 11, 19, 10, 30), 4) + flat_bars(dt(2026, 11, 19, 15, 0), 30)      # hole 12:30-15:00


def test_midweek_data_gap_keeps_position_open_and_flags(tmp_path):
    run(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))      # 12h horizon -> deadline 22:30
    assert run(tmp_path, now=dt(2026, 11, 19, 20, 0), bars=gap_bars()) == 4
    assert "POSITION_CLOSED" not in types(tmp_path) and "POSITION_VOIDED" not in types(tmp_path)
    run(tmp_path, now=dt(2026, 11, 19, 20, 30), bars=gap_bars())
    assert types(tmp_path).count("DATA_ERROR") == 1                                              # not spammed per run


def test_unobservable_path_is_voided_after_grace_never_guessed(tmp_path):
    run(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))
    assert run(tmp_path, now=dt(2026, 11, 20, 5, 0), bars=gap_bars()) == 0                       # deadline 22:30 + 6h = 04:30 passed
    v = events(tmp_path, "POSITION_VOIDED")
    assert len(v) == 1 and v[0]["reason"] == "VOID" and "unobservable" in v[0]["note"]
    run(tmp_path, now=dt(2026, 11, 20, 6, 0), bars=gap_bars())
    assert types(tmp_path).count("POSITION_VOIDED") == 1 and "POSITION_CLOSED" not in types(tmp_path)
    errors, warnings = verify.verify(tmp_path / "audit")
    assert errors == [] and any("VOIDED" in w for w in warnings)
    summary = json.loads((tmp_path / "audit" / "summary.json").read_text())
    assert summary["positions_voided"] == 1 and summary["positions_closed"] == 0 and summary["closed_pnl_usd"] == 0.0


# ---------------- account mode (demo default, real opt-in) ----------------
def real_auth(tmp, **kw):
    base = dict(account_mode="real", server="Headway-Real", terminal_host="real-mt5.hw-terminal.com",
                url_path="/webterminal/mt5-real/")
    base.update(kw)
    return write_auth(tmp, **base)


def test_demo_bridge_rejects_a_real_login(tmp_path):
    sig = write_signals(tmp_path, raw_signal())
    assert run(tmp_path, signals=sig, auth=real_auth(tmp_path)) == 3
    assert "POSITION_OPENED" not in types(tmp_path)


def test_real_bridge_checks_mode_endpoint_not_exact_server_label(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ACCOUNT_MODE", "real")
    monkeypatch.setattr(C, "EXPECTED_SERVER", "")
    sig = write_signals(tmp_path, raw_signal())
    assert run(tmp_path, signals=sig, auth=write_auth(tmp_path)) == 3  # demo endpoint must not satisfy real
    assert run(tmp_path, signals=sig, auth=real_auth(tmp_path, server="Headway-Other")) == 0
    assert types(tmp_path).count("POSITION_OPENED") == 1


# ---------------- Headway prices removed + dummy order audit ----------------
def events(tmp, typ):
    return [e["data"] for e in EventLog(tmp / "audit" / "events.jsonl", "x").events if e["type"] == typ]


def test_headway_quotes_are_no_longer_an_input(tmp_path):
    """Headway supplies no market prices: run_cycle has no quotes input and never records a QUOTE_SNAPSHOT."""
    import inspect
    assert "quotes_path" not in inspect.signature(cycle.run_cycle).parameters
    run(tmp_path)
    assert not events(tmp_path, "QUOTE_SNAPSHOT")


def order_result(tmp, **kw):
    r = {"schema": 1, "kind": "DUMMY_ORDER", "account_mode": "demo", "symbol": "EURUSD", "side": "buy", "lots": 0.01,
         "execute": True, "dry_run": False, "close_requested": True, "stages": [], "margin_before": 0.0,
         "margin_after_open": 11.0, "margin_after_close": 0.0, "opened_confirmed": True, "closed_confirmed": True,
         "outcome": "executed_confirmed", "started_at": "2026-11-19T10:35:00Z", "finished_at": "2026-11-19T10:36:00Z", "run_id": "R1"}
    r.update(kw)
    p = tmp / "order_result.json"
    p.write_text(json.dumps(r))
    return p


def run_order(tmp, p):
    return cycle.run_cycle(signals_path=None, auth_path=None, audit_dir=tmp / "audit", now=NOW,
                           bars_provider=provider_from([]), order_result_path=p)


def test_dummy_order_logged_once_and_never_becomes_a_strategy_position(tmp_path):
    p = order_result(tmp_path)
    assert run_order(tmp_path, p) == 0 and run_order(tmp_path, p) == 0
    assert types(tmp_path).count("DUMMY_ORDER") == 1
    st = State.from_events(EventLog(tmp_path / "audit" / "events.jsonl", "x").events)
    assert not st.positions                                                   # strategy ledger untouched
    assert verify.verify(tmp_path / "audit")[0] == []


def test_dummy_order_out_of_contract_is_refused(tmp_path):
    assert "DUMMY_ORDER" not in (run_order(tmp_path, order_result(tmp_path, lots=0.5)), *types(tmp_path))
    assert events(tmp_path, "DATA_ERROR")[0]["scope"] == "order_result"
    run_order(tmp_path, order_result(tmp_path, symbol="XAUUSD", started_at="2026-11-19T11:00:00Z"))
    assert "DUMMY_ORDER" not in types(tmp_path)


def test_unconfirmed_or_unclosed_dummy_order_demands_manual_attention(tmp_path):
    p = order_result(tmp_path, outcome="executed_confirmed_close_failed", closed_confirmed=False, margin_after_close=11.0)
    assert run_order(tmp_path, p) == 5
    errors, warnings = verify.verify(tmp_path / "audit")
    assert errors == [] and any("MANUAL" in w for w in warnings)


def test_verify_rejects_forged_confirmation_without_margin_rise(tmp_path):
    run_order(tmp_path, order_result(tmp_path, margin_after_open=0.0))
    assert any("without a margin increase" in e for e in verify.verify(tmp_path / "audit")[0])
