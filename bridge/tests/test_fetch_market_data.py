import importlib.util
import json
from datetime import timedelta
from pathlib import Path

import pytest
from tsebridge.store import LocalStore
from helpers import dt, BAR

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("fetch_market_data", ROOT / "scripts" / "fetch_market_data.py")
fmd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fmd)

NOW = dt(2026, 11, 25, 10, 3)          # latest completed bar opens 09:30


def series(last_open, n):
    vals = []
    for i in range(n):
        t = last_open - (n - 1 - i) * BAR
        vals.append({"datetime": t.strftime("%Y-%m-%d %H:%M:%S"), "open": "1.0965", "high": "1.0967", "low": "1.0960", "close": "1.0963"})
    return {"meta": {"symbol": "EUR/USD", "interval": "30min", "exchange_timezone": "UTC"}, "values": vals, "status": "ok"}


class R:
    status_code = 200

    def __init__(self, body):
        self._b = body

    def json(self):
        return self._b


def go(tmp, monkeypatch, body, now=NOW, key="", extra=()):
    monkeypatch.setenv("BRIDGE_DATA_DIR", str(tmp / "data"))
    if key:
        monkeypatch.setenv("YFINANCE_API_KEY", key)
    else:
        monkeypatch.delenv("YFINANCE_API_KEY", raising=False)
    rep = tmp / "report.json"
    out = tmp / "m30.csv"
    code = fmd.run(["--output", str(out), "--report", str(rep), "--wait-seconds", "0", *extra],
                   http_get=lambda *a, **k: R(body), clock=lambda: now, sleep=lambda s: None)
    return code, json.loads(rep.read_text()), out


def test_happy_path_writes_500_completed_bars_and_exports(tmp_path, monkeypatch):
    code, rep, out = go(tmp_path, monkeypatch, series(dt(2026, 11, 25, 10, 0), 520))      # includes the forming 10:00 bar
    assert code == 0 and rep["ok"] and rep["bars_after"] == 500 and rep["provider"] == "yfinance"
    assert rep["latest_bar_start_utc"] == "2026-11-25T09:30:00+00:00"
    assert len(out.read_text().splitlines()) == 501
    assert "K" not in json.dumps(rep).replace('"K"', "")                                      # no secret in the report


def test_non_yfinance_rows_in_a_restored_cache_are_purged_and_never_used(tmp_path, monkeypatch):
    store = LocalStore(tmp_path / "data")
    old = dt(2026, 11, 25, 9, 30)
    store.upsert_m30([{"symbol": "EURUSD", "start_ts": old.isoformat(), "open": 9, "high": 10, "low": 8, "close": 9.5, "coverage": 1.0,
                       "minutes_present": 30, "samples": 0, "source": "dukascopy_bootstrap"},
                      {"symbol": "EURUSD", "start_ts": (old - BAR).isoformat(), "open": 9, "high": 10, "low": 8, "close": 9.5, "coverage": 1.0,
                       "minutes_present": 30, "samples": 0, "source": "headway_native"}])
    code, rep, out = go(tmp_path, monkeypatch, series(dt(2026, 11, 25, 9, 30), 510))
    assert code == 0 and rep["purged_non_provider_rows"] == {"dukascopy_bootstrap": 1, "headway_native": 1}
    rows = LocalStore(tmp_path / "data").load_m30("EURUSD", 1000)
    assert {r["source"] for r in rows} == {"yfinance"} and all(r["close"] < 2 for r in rows)


def test_yfinance_requires_no_api_key_and_test_adapter_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIDGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("YFINANCE_API_KEY", raising=False)
    rep = tmp_path / "r.json"
    code = fmd.run(["--output", str(tmp_path / "o.csv"), "--report", str(rep), "--wait-seconds", "0"],
                   http_get=lambda *a, **k: R(series(dt(2026, 11, 25, 9, 30), 510)), clock=lambda: NOW, sleep=lambda s: None)
    assert code == 0 and json.loads(rep.read_text())["provider"] == "yfinance"


def test_insufficient_history_blocks_scan(tmp_path, monkeypatch):
    code, rep, out = go(tmp_path, monkeypatch, series(dt(2026, 11, 25, 9, 30), 300))
    assert code == 10 and rep["reason"] == "insufficient_history" and not out.exists()


def test_latest_completed_candle_missing_blocks_scan(tmp_path, monkeypatch):
    code, rep, out = go(tmp_path, monkeypatch, series(dt(2026, 11, 25, 9, 0), 510))     # 09:30 not published yet
    assert code == 10 and rep["reason"] == "latest_completed_candle_missing" and not out.exists()


def test_weekend_staleness_is_a_clean_skip_not_an_error(tmp_path, monkeypatch):
    sat = dt(2026, 11, 28, 12, 3)
    code, rep, out = go(tmp_path, monkeypatch, series(dt(2026, 11, 27, 21, 30), 510), now=sat)
    assert code == 20 and rep["market_closed"] and not out.exists()


def test_provider_failure_and_bad_payload_fail_closed(tmp_path, monkeypatch):
    code, rep, _ = go(tmp_path, monkeypatch, {"status": "error", "code": 401, "message": "invalid key K"}, key="")
    assert code == 11 and "K" not in rep["detail"].replace("key", "")
    bad = series(dt(2026, 11, 25, 9, 30), 510)
    bad["values"][5]["high"] = "0.5"
    code, rep, _ = go(tmp_path, monkeypatch, bad)
    assert code == 10 and rep["reason"] == "payload_rejected"


def test_weekend_window_helper():
    assert fmd.in_weekend_closure(dt(2026, 11, 27, 21, 30)) and fmd.in_weekend_closure(dt(2026, 11, 29, 22, 0))
    assert not fmd.in_weekend_closure(dt(2026, 11, 29, 23, 0)) and not fmd.in_weekend_closure(dt(2026, 11, 25, 10, 0))
