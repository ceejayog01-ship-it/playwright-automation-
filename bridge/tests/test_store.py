import pytest
from tsebridge.store import LocalStore, export_m30_csv, quality_report, m30_bars, floor30
from helpers import dt, BAR


def row(t, o=1.1, h=1.101, l=1.099, c=1.1005, source="yfinance", symbol="EURUSD"):
    return {"symbol": symbol, "start_ts": t.isoformat(), "open": o, "high": h, "low": l, "close": c,
            "coverage": 1.0, "minutes_present": 30, "samples": 0, "source": source}


def test_floor30():
    assert floor30(dt(2026, 11, 19, 10, 29)) == dt(2026, 11, 19, 10, 0)
    assert floor30(dt(2026, 11, 19, 10, 30)) == dt(2026, 11, 19, 10, 30)


def test_upsert_replaces_and_insert_only_preserves(tmp_path):
    store = LocalStore(tmp_path / "store")
    t = dt(2026, 11, 19, 10, 0)
    store.upsert_m30([row(t)])
    store.upsert_m30([row(t, 1.2, 1.21, 1.19, 1.205)])                      # normal upsert replaces
    assert store.load_m30("EURUSD", 10)[0]["open"] == 1.2
    inserted = store.upsert_m30([row(t, 9, 10, 8, 9.5), row(t + BAR)], ignore_existing=True)
    got = store.load_m30("EURUSD", 10)
    assert inserted == 1 and got[0]["open"] == 1.2 and len(got) == 2


def test_purge_other_sources_removes_everything_not_yfinance(tmp_path):
    store = LocalStore(tmp_path / "store")
    t = dt(2026, 11, 19, 10, 0)
    store.upsert_m30([row(t, source="headway_native"), row(t + BAR, source="dukascopy_bootstrap"),
                      row(t + 2 * BAR, source="yahoo_bootstrap"), row(t + 3 * BAR, source="yfinance")])
    removed = store.purge_other_sources("yfinance")
    assert removed == {"headway_native": 1, "dukascopy_bootstrap": 1, "yahoo_bootstrap": 1}
    left = store.load_m30("EURUSD", 10)
    assert [r["source"] for r in left] == ["yfinance"]
    assert store.purge_other_sources("yfinance") == {}


def test_quality_report_and_export(tmp_path):
    now = dt(2026, 11, 19, 10, 32)
    store = LocalStore(tmp_path / "store")
    store.upsert_m30([row(dt(2026, 11, 19, 10, 0) - i * BAR) for i in reversed(range(130))])
    loaded = store.load_m30("EURUSD", 500)
    report = quality_report(loaded, now, min_bars=120)
    assert report["ok"] and report["bars"] == 130 and report["source"] == "yfinance"
    out = tmp_path / "candles.csv"
    export_m30_csv(loaded, out)
    assert len(out.read_text().splitlines()) == 131
    assert not quality_report(loaded[-10:], now, min_bars=120)["ok"]


def test_m30_bars_returns_completed_bars_from_start_and_excludes_forming(tmp_path):
    """Regression: the provider callback is (start, now). It used to treat `now` as a count (TypeError)."""
    store = LocalStore(tmp_path / "store")
    t = dt(2026, 11, 19, 10, 0)
    store.upsert_m30([row(t - BAR), row(t), row(t + BAR), row(t + 2 * BAR)])
    now = t + 2 * BAR + 5 * BAR / 30                                        # 12:05 -> the 12:00 bar is still forming
    bars = m30_bars(store, "EURUSD", t, now)
    assert [b.t for b in bars] == [t, t + BAR]


def test_provider_callback_works_with_a_datetime_now(tmp_path):
    from tsebridge import config as C
    store = LocalStore(tmp_path / "store")
    store.upsert_m30([row(dt(2026, 11, 19, 10, 0))])
    provider = lambda start, now: m30_bars(store, C.PAIR, start, now)
    assert len(provider(dt(2026, 11, 19, 9, 0), dt(2026, 11, 19, 11, 0))) == 1
