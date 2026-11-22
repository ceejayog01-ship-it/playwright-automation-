import csv
from datetime import timedelta

import pytest
from tsebridge.common import DataError
from tsebridge.store import (LocalStore, SupabaseStore, export_m30_csv, quality_report,
                             parse_native_m30_csv, m30_bars)
from tsebridge.lifecycle import Bar
from helpers import dt, BAR


def write_native(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for row in rows:
            w.writerow(row)


def row(t, o=1.1, h=1.101, l=1.099, c=1.1005, source="headway_native"):
    return {"symbol": "EURUSD", "start_ts": t.isoformat(), "open": o, "high": h, "low": l, "close": c,
            "coverage": 1.0, "minutes_present": 30, "samples": 0, "source": source}


def test_floor30():
    from tsebridge.store import floor30
    assert floor30(dt(2026, 11, 19, 10, 29)) == dt(2026, 11, 19, 10, 0)
    assert floor30(dt(2026, 11, 19, 10, 30)) == dt(2026, 11, 19, 10, 30)


def test_parse_native_m30_validates_ohlc_and_deduplicates(tmp_path):
    p = tmp_path / "history.csv"
    write_native(p, [["2026-11-19T10:00:00+00:00", 1.1, 1.11, 1.09, 1.105],
                     ["2026-11-19T10:00:00+00:00", 1.2, 1.21, 1.19, 1.205]])
    rows = parse_native_m30_csv(p)
    assert len(rows) == 1 and rows[0]["source"] == "headway_native"
    write_native(p, [["2026-11-19T10:00:00+00:00", 1.1, 1.0, 1.09, 1.105]])
    with pytest.raises(DataError):
        parse_native_m30_csv(p)


def test_bootstrap_insert_only_preserves_headway_rows(tmp_path):
    store = LocalStore(tmp_path / "store")
    t = dt(2026, 11, 19, 10, 0)
    store.upsert_m30([row(t)])
    inserted = store.upsert_m30([row(t, 9, 10, 8, 9.5, "dukascopy_bootstrap"),
                                 row(t + BAR, source="dukascopy_bootstrap")], ignore_existing=True)
    got = store.load_m30("EURUSD", 10)
    assert inserted == 1
    assert got[0]["source"] == "headway_native" and got[0]["open"] == 1.1
    assert got[1]["source"] == "dukascopy_bootstrap"


def test_quality_report_and_export(tmp_path):
    now = dt(2026, 11, 19, 10, 32)
    store = LocalStore(tmp_path / "store")
    rows = [row(dt(2026, 11, 19, 10, 0) - i * BAR) for i in reversed(range(130))]
    store.upsert_m30(rows)
    loaded = store.load_m30("EURUSD", 500)
    report = quality_report(loaded, now, min_bars=120)
    assert report["ok"] and report["bars"] == 130
    out = tmp_path / "candles.csv"
    export_m30_csv(loaded, out)
    assert len(out.read_text().splitlines()) == 131
    assert not quality_report(loaded[-10:], now, min_bars=120)["ok"]


def test_m30_bars_returns_history_up_to_requested_start(tmp_path):
    store = LocalStore(tmp_path / "store")
    t = dt(2026, 11, 19, 10, 0)
    store.upsert_m30([row(t - 2 * BAR), row(t - BAR), row(t)])
    bars = m30_bars(store, "EURUSD", t - BAR, 10)
    assert [b.t for b in bars] == [t - 2 * BAR, t - BAR]
