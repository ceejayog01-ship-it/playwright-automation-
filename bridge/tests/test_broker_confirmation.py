"""Broker-confirmation rules: nothing is called executed/closed without terminal evidence; stale signals expire; positions stay managed."""
import json
from tsebridge import cycle, notify, verify
from tsebridge.eventlog import EventLog, State
from helpers import BAR, dt, raw_signal
from test_cycle import NOW, write_auth, write_signals, provider_from, events, types


def run_rt(tmp, now=NOW, signals=None, auth=None, order_result=None, notices=None, expire=True):
    return cycle.run_cycle(signals_path=signals, auth_path=auth, audit_dir=tmp / "audit", now=now,
                           bars_provider=provider_from([]), run_id="T", order_result_path=order_result,
                           notifier=(notices.extend if notices is not None else None), expire_unplaced_orders=expire)


def strat(tmp, sid="TSE-A-EURUSD-202611191000-R05", **kw):
    r = {"schema": 1, "kind": "STRATEGY_ORDER", "account_mode": "demo", "signal_id": sid, "symbol": "EURUSD", "side": "buy",
         "lots": 0.5, "sl": 1.098, "execute": True, "dry_run": False, "outcome": "executed_confirmed_with_ticket",
         "ticket_id": "555001", "open_price": 1.1001, "opened_confirmed": True, "run_id": "R1", "started_at": "2026-11-19T10:41:00Z",
         "finished_at": "2026-11-19T10:42:00Z"}
    r.update(kw)
    p = tmp / "strategy_order_result.json"
    p.write_text(json.dumps(r))
    return p


def monitor(tmp, actions, execute=True, outcome="ok", captured="2026-11-19T10:50:00Z"):
    p = tmp / "position_monitor.json"
    p.write_text(json.dumps({"schema": 1, "kind": "POSITION_MONITOR", "account_mode": "demo", "captured_at": captured, "open_positions": [],
                             "managed": [], "actions": actions, "margin_before": 500.0, "margin_after": 0.0, "execute": execute,
                             "dry_run": not execute, "outcome": outcome, "stages": [], "run_id": "R2"}))
    return p


def setup_open(tmp):
    sig, auth = write_signals(tmp, raw_signal()), write_auth(tmp)
    run_rt(tmp, signals=sig, auth=auth)
    run_rt(tmp, order_result=strat(tmp))
    return sig, auth


def test_confirmed_order_is_reported_as_executed_only_with_broker_confirmation(tmp_path):
    n = []
    run_rt(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))
    run_rt(tmp_path, order_result=strat(tmp_path), notices=n)
    assert [x["kind"] for x in n] == ["ORDER_CONFIRMED"] and n[0]["ticket_id"] == "555001"
    assert "CONFIRMED BY BROKER" in notify.format_notice(n[0])
    st = State.from_events(EventLog(tmp_path / "audit" / "events.jsonl", "x").events)
    assert st.positions["TSE-A-EURUSD-202611191000-R05"]["open"]["ticket_id"] == "555001"


def test_unconfirmed_click_is_never_reported_as_executed_and_keeps_the_position_managed(tmp_path):
    n = []
    run_rt(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))
    code = run_rt(tmp_path, order_result=strat(tmp_path, outcome="executed_unconfirmed", opened_confirmed=False, ticket_id=None,
                                               open_price=None), notices=n)
    assert code == 5 and [x["kind"] for x in n] == ["ORDER_UNCONFIRMED"]
    txt = notify.format_notice(n[0])
    assert "NOT CONFIRMED" in txt and "executed" not in txt.lower() and "BROKER" not in txt
    assert not events(tmp_path, "ORDER_FILLED") and not events(tmp_path, "ORDER_FAILED")
    managed = json.loads((tmp_path / "work" / "managed_positions.json").read_text())
    assert [m["signal_id"] for m in managed] == ["TSE-A-EURUSD-202611191000-R05"]


def test_dry_run_results_never_notify_or_confirm(tmp_path):
    n = []
    run_rt(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))
    run_rt(tmp_path, order_result=strat(tmp_path, execute=False, dry_run=True, outcome="dry_run_ok", opened_confirmed=False,
                                        ticket_id=None), notices=n)
    assert n == [] and not events(tmp_path, "ORDER_FILLED")


def test_failed_order_notifies_and_closes_the_phantom_position(tmp_path):
    n = []
    run_rt(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))
    run_rt(tmp_path, order_result=strat(tmp_path, outcome="failed:order_rejected:not enough money", opened_confirmed=False,
                                        ticket_id=None), notices=n)
    assert [x["kind"] for x in n] == ["ORDER_FAILED"]
    assert verify.verify(tmp_path / "audit")[0] == []


def test_broker_confirmed_close_is_recorded_once_and_removes_the_position_from_management(tmp_path):
    setup_open(tmp_path)
    n = []
    sid = "TSE-A-EURUSD-202611191000-R05"
    mon = monitor(tmp_path, [{"signal_id": sid, "ticket_id": "555001", "reason": "TIME", "attempted": True, "closed": True,
                              "detail": "position_gone_from_trade_tab", "exit_time": "2026-11-19T10:50:00Z"}])
    run_rt(tmp_path, order_result=mon, notices=n)
    run_rt(tmp_path, order_result=mon, notices=n)                                   # same file again: deduplicated
    assert [x["kind"] for x in n] == ["BROKER_CLOSE_CONFIRMED"] and len(events(tmp_path, "BROKER_CLOSE_CONFIRMED")) == 1
    assert json.loads((tmp_path / "work" / "managed_positions.json").read_text()) == []
    assert verify.verify(tmp_path / "audit")[0] == []


def test_failed_close_demands_attention_and_stays_managed(tmp_path):
    setup_open(tmp_path)
    n = []
    sid = "TSE-A-EURUSD-202611191000-R05"
    mon = monitor(tmp_path, [{"signal_id": sid, "ticket_id": "555001", "reason": "TIME", "attempted": True, "closed": False,
                              "detail": "close_clicked_but_position_still_visible"}], outcome="close_failed")
    assert run_rt(tmp_path, order_result=mon, notices=n) == 5
    assert [x["kind"] for x in n] == ["BROKER_CLOSE_FAILED"] and not events(tmp_path, "BROKER_CLOSE_CONFIRMED")
    assert len(json.loads((tmp_path / "work" / "managed_positions.json").read_text())) == 1


def test_dry_run_monitor_never_confirms_a_close(tmp_path):
    setup_open(tmp_path)
    sid = "TSE-A-EURUSD-202611191000-R05"
    mon = monitor(tmp_path, [{"signal_id": sid, "ticket_id": "555001", "reason": "TIME", "attempted": True, "closed": False,
                              "detail": "dry_run: would close"}], execute=False, outcome="dry_run_ok")
    assert run_rt(tmp_path, order_result=mon) == 0 and not events(tmp_path, "BROKER_CLOSE_CONFIRMED")


def test_absent_position_is_not_called_a_confirmed_close(tmp_path):
    setup_open(tmp_path)
    n = []
    sid = "TSE-A-EURUSD-202611191000-R05"
    run_rt(tmp_path, order_result=monitor(tmp_path, [{"signal_id": sid, "ticket_id": "555001", "reason": "NOT_FOUND", "attempted": False,
                                                      "closed": False, "detail": "no matching open position"}]), notices=n)
    assert [x["kind"] for x in n] == ["POSITION_NOT_VISIBLE"] and not events(tmp_path, "BROKER_CLOSE_CONFIRMED")
    assert "not independently confirmed" in notify.format_notice(n[0])


def test_candle_replay_close_does_not_unmanage_a_position_the_broker_has_not_closed(tmp_path):
    """The ledger replays SL/TIME on candles; the broker position must stay managed until the terminal confirms it is gone."""
    sig, auth = setup_open(tmp_path)
    bars = [b for b in __import__("helpers").flat_bars(dt(2026, 11, 19, 10, 30), 60)]
    later = dt(2026, 11, 20, 12, 0)                                                 # well past the 12h/24-bar deadline
    cycle.run_cycle(signals_path=None, auth_path=auth, audit_dir=tmp_path / "audit", now=later, bars_provider=provider_from(bars),
                    run_id="T")
    st = State.from_events(EventLog(tmp_path / "audit" / "events.jsonl", "x").events)
    sid = "TSE-A-EURUSD-202611191000-R05"
    assert st.positions[sid]["close"]["reason"] == "TIME"
    managed = json.loads((tmp_path / "work" / "managed_positions.json").read_text())
    assert [m["signal_id"] for m in managed] == [sid] and managed[0]["ticket_id"] == "555001"


def test_unplaced_signal_expires_instead_of_trading_stale_and_sent_ones_never_expire(tmp_path):
    n = []
    sig = write_signals(tmp_path, raw_signal(signal_id="P1"), raw_signal(signal_id="P2", strategy_id="B"))
    run_rt(tmp_path, signals=sig, auth=write_auth(tmp_path))
    run_rt(tmp_path, order_result=strat(tmp_path, sid="P1"))                       # only P1 was sent (cap: one per cycle)
    later = NOW + 3 * BAR                                                           # next cycles
    run_rt(tmp_path, now=later, notices=n)
    st = State.from_events(EventLog(tmp_path / "audit" / "events.jsonl", "x").events)
    assert st.positions["P2"]["close"]["reason"] == "ORDER_EXPIRED" and st.positions["P1"]["close"] is None
    assert [x["kind"] for x in n] == ["ORDER_EXPIRED"]
    pend = json.loads((tmp_path / "work" / "pending_orders.json").read_text())
    assert pend == []
    assert verify.verify(tmp_path / "audit")[0] == []


def test_expiry_is_off_in_record_only_mode(tmp_path):
    run_rt(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path), expire=False)
    run_rt(tmp_path, now=NOW + 5 * BAR, expire=False)
    assert not events(tmp_path, "ORDER_EXPIRED")


def test_result_files_of_other_kinds_do_not_raise_data_errors(tmp_path):
    setup_open(tmp_path)
    run_rt(tmp_path, order_result=monitor(tmp_path, []))
    run_rt(tmp_path, order_result=strat(tmp_path))
    assert not events(tmp_path, "DATA_ERROR")


def test_notifier_failure_never_changes_state_or_exit_code(tmp_path):
    def broken(_):
        raise RuntimeError("telegram down")
    run_rt(tmp_path, signals=write_signals(tmp_path, raw_signal()), auth=write_auth(tmp_path))
    assert cycle.run_cycle(signals_path=None, auth_path=None, audit_dir=tmp_path / "audit", now=NOW, bars_provider=provider_from([]),
                           order_result_path=strat(tmp_path), notifier=broken) == 0
    assert events(tmp_path, "ORDER_FILLED")
