import json
import pytest
from tsebridge import cycle, supabase_sync as S
from tsebridge.eventlog import EventLog
from tsebridge.store import SupabaseStore
from fakepg import FakePG, KEY
from helpers import dt, raw_signal, flat_bars
from test_cycle import write_auth, write_signals, provider_from, write_quotes
from test_store import row

NOW = dt(2026, 11, 19, 10, 40)


@pytest.fixture()
def pg():
    p = FakePG()
    yield p
    p.close()


def client(pg):
    return S.Supabase(pg.url, KEY, sleep=lambda s: None)


def make_audit(tmp, bars=None, now=NOW):
    cycle.run_cycle(signals_path=write_signals(tmp, raw_signal()), auth_path=write_auth(tmp), audit_dir=tmp / "audit",
                    now=now, bars_provider=provider_from([]), quotes_path=write_quotes(tmp), run_id="R1")
    cycle.run_cycle(signals_path=None, auth_path=None, audit_dir=tmp / "audit", now=dt(2026, 11, 19, 23, 10),
                    bars_provider=provider_from(flat_bars(dt(2026, 11, 19, 10, 30), 30)), run_id="R2")


def test_sync_pushes_chain_and_derived_tables_idempotently(tmp_path, pg):
    make_audit(tmp_path)
    sb = client(pg)
    r1 = S.sync(sb, tmp_path / "audit", dt(2026, 11, 19, 22, 41), {"run_id": "R2", "exit_code": 0})
    n = r1["events_total"]
    assert len(pg.tables["bridge_events"]) == n == r1["events_pushed"]
    pos = list(pg.tables["positions"].values())[0]
    assert pos["status"] == "CLOSED" and pos["exit_reason"] == "TIME" and pos["lots"] == 0.5 and pos["horizon_hours"] == 12
    assert pos["min_coverage"] == 1.0 and list(pg.tables["signals"].values())[0]["status"] == "accepted"
    assert len(pg.tables["quotes"]) == 1 and list(pg.tables["runs"].values())[0]["exit_code"] == 0
    r2 = S.sync(sb, tmp_path / "audit", dt(2026, 11, 19, 22, 42), {"run_id": "R3", "exit_code": 0})     # nothing new
    assert r2["events_pushed"] == 0 and len(pg.tables["bridge_events"]) == n and len(pg.tables["positions"]) == 1


def test_pull_rebuilds_a_fresh_runner_and_chain_continues(tmp_path, pg):
    make_audit(tmp_path)
    S.sync(client(pg), tmp_path / "audit", NOW, {"run_id": "R2"})
    fresh = tmp_path / "fresh"
    assert S.pull(client(pg), fresh / "audit")["pulled"] == len(pg.tables["bridge_events"])
    assert (fresh / "audit" / "events.jsonl").read_text() == (tmp_path / "audit" / "events.jsonl").read_text()
    EventLog(fresh / "audit" / "events.jsonl", "x").append("RUN_STARTED", {}, "t")                      # chain keeps extending


def test_diverged_remote_is_refused_not_overwritten(tmp_path, pg):
    make_audit(tmp_path)
    S.sync(client(pg), tmp_path / "audit", NOW, {})
    key = ("2",)
    pg.tables["bridge_events"][key]["hash"] = "f" * 64                                                  # someone edited the remote chain
    pg.tables["bridge_events"][tuple([str(len(pg.tables["bridge_events"]))])]["hash"] = "e" * 64
    with pytest.raises(S.Divergence):
        S.sync(client(pg), tmp_path / "audit", NOW, {})
    with pytest.raises(Exception):
        S.pull(client(pg), tmp_path / "other")                                                          # forged remote chain fails verification


def test_retries_transient_errors_and_never_leaks_the_key(tmp_path, pg):
    sb = client(pg)
    pg.fail_next = 2
    assert sb.upsert("runs", [{"run_id": "x"}], "run_id") == 1                                          # 503, 503, then ok
    bad = S.Supabase(pg.url, "wrong-key", sleep=lambda s: None)
    with pytest.raises(S.SupabaseError) as e:
        bad.upsert("runs", [{"run_id": "x"}], "run_id")
    assert "wrong-key" not in str(e.value) and "401" in str(e.value)


def test_candle_store_roundtrip_over_http(tmp_path, pg):
    store = SupabaseStore(client(pg))
    bars = [row(dt(2026, 11, 19, 10, 0)), row(dt(2026, 11, 19, 10, 30), source="dukascopy_bootstrap")]
    assert store.upsert_m30(bars) == 2
    loaded = store.load_m30("EURUSD", 10)
    assert len(loaded) == 2 and loaded[0]["source"] == "headway_native"


def test_supabase_secret_key_environment_alias(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test")
    assert S.from_env().key == "sb_secret_test"


def test_new_supabase_secret_key_uses_apikey_without_bearer():
    captured = {}
    def http(method, url, headers, body, timeout=30):
        captured.update(headers)
        return 200, b"[]"
    sb = S.Supabase("https://example.supabase.co", "sb_secret_test", http=http, sleep=lambda s: None)
    sb._call("GET", "candles_m30")
    assert captured["apikey"] == "sb_secret_test"
    assert "Authorization" not in captured


def test_watchdog_flags_a_silent_pipeline_only_while_the_market_is_open(tmp_path, pg):
    sb = client(pg)
    sb.upsert("runs", [{"run_id": "R", "finished_at": "2026-11-19T09:00:00+00:00"}], "run_id")
    assert S.watchdog(sb, dt(2026, 11, 19, 10, 40), 50)["stale"] is True            # Thu, 100 min silent
    assert S.watchdog(sb, dt(2026, 11, 19, 9, 30), 50)["stale"] is False            # 30 min: healthy
    assert S.watchdog(sb, dt(2026, 11, 21, 12, 0), 50)["stale"] is False            # Saturday: closed
    assert S.fx_market_open(dt(2026, 11, 22, 21, 30)) and not S.fx_market_open(dt(2026, 11, 20, 22, 30))
    assert S.watchdog(S.Supabase(pg.url, KEY), dt(2026, 11, 22, 21, 30), 50)["stale"] is False   # grace right after the weekly open


def test_cli_wiring_and_exit_codes(tmp_path, pg, monkeypatch, capsys):
    monkeypatch.setenv("SUPABASE_URL", pg.url)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", KEY)
    make_audit(tmp_path)
    a = str(tmp_path / "audit")
    assert S.main(["sync", "--audit-dir", a, "--run-id", "R9", "--exit-code", "0", "--now", "2026-11-19T23:20:00+00:00"]) == 0
    assert list(pg.tables["runs"].values())[0]["run_id"] == "R9"
    assert S.main(["pull", "--audit-dir", str(tmp_path / "fresh")]) == 0
    assert S.main(["watchdog", "--now", "2026-11-19T23:40:00+00:00"]) == 0               # 20 min since heartbeat
    assert S.main(["watchdog", "--now", "2026-11-20T01:30:00+00:00"]) == 1               # 130 min silent, market open
    assert S.main(["verify-remote"]) == 0
    pg.tables["bridge_events"][("3",)]["data"] = {"tampered": True}                      # edit a MIDDLE row, head untouched
    assert S.main(["verify-remote"]) == 6                                                # full-chain check catches it
    pg.tables["bridge_events"][("9",)]["hash"] = "0" * 64
    assert S.main(["sync", "--audit-dir", a, "--now", "2026-11-19T23:20:00+00:00"]) == 6  # head mismatch caught by sync too
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "wrong")
    assert S.main(["pull", "--audit-dir", str(tmp_path / "x")]) == 7                     # bad key -> supabase error


def test_unconfigured_supabase_is_a_noop_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert S.main(["sync", "--audit-dir", str(tmp_path)]) == 0
