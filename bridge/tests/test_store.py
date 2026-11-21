import csv
import pytest
from tsebridge import config as C
from tsebridge.common import DataError
from tsebridge.store import LocalStore, build_m30, export_m30, import_m30, ingest_m1, m30_bars, parse_m1_csv, floor30
from helpers import dt, BAR


def m1(start, n, price=1.1, skip=(), sym="EURUSD", ticks=50):
    rows = []
    for i in range(n):
        if i in skip:
            continue
        t = start.replace(second=0) + i * (BAR / 30)
        rows.append({"symbol": sym, "start_ts": t.isoformat(timespec="seconds"), "open": price, "high": price + 0.0002,
                     "low": price - 0.0002, "close": price + 0.0001, "ticks": ticks, "source": "headway", "run_id": "r"})
    return rows


def write_m1_csv(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["timestamp", "open", "high", "low", "close", "ticks"])
        for r in rows:
            w.writerow([r["start_ts"].replace("+00:00", ".000Z"), r["open"], r["high"], r["low"], r["close"], r["ticks"]])


def test_floor30():
    assert floor30(dt(2026, 11, 19, 10, 29)) == dt(2026, 11, 19, 10, 0) and floor30(dt(2026, 11, 19, 10, 30)) == dt(2026, 11, 19, 10, 30)


def test_build_m30_ohlc_coverage_and_completeness():
    rows = m1(dt(2026, 11, 19, 10, 0), 45)                                    # 10:00-10:44: one full bar + a forming one
    out = build_m30(rows, dt(2026, 11, 19, 10, 45))
    assert len(out) == 1 and out[0]["start_ts"].startswith("2026-11-19T10:00") and out[0]["coverage"] == 1.0
    assert out[0]["open"] == 1.1 and out[0]["close"] == 1.1001 and out[0]["samples"] == 1500
    gappy = build_m30(m1(dt(2026, 11, 19, 10, 0), 30, skip=range(0, 8)), dt(2026, 11, 19, 10, 40))
    assert gappy[0]["minutes_present"] == 22 and gappy[0]["coverage"] == round(22 / 30, 4)
    assert gappy[0]["open"] == 1.1                                            # real first observed minute, nothing invented


def test_ingest_is_idempotent_and_bars_span_runs(tmp_path):
    store = LocalStore(tmp_path / "d")
    now = dt(2026, 11, 19, 11, 5)
    a, b = m1(dt(2026, 11, 19, 10, 4), 27), m1(dt(2026, 11, 19, 10, 31), 27)  # two sampling runs
    write_m1_csv(tmp_path / "a.csv", a); write_m1_csv(tmp_path / "b.csv", b)
    ingest_m1(store, tmp_path / "a.csv", "EURUSD", "r1", dt(2026, 11, 19, 10, 32))
    ingest_m1(store, tmp_path / "b.csv", "EURUSD", "r2", now)
    ingest_m1(store, tmp_path / "b.csv", "EURUSD", "r2", now)                 # replay: no duplicates
    bars = store.m30_since("EURUSD", "2026-11-19T00:00:00+00:00")
    assert [r["start_ts"][11:16] for r in bars] == ["10:00", "10:30"]
    assert len(store.m1_since("EURUSD", "2026-11-19T00:00:00+00:00")) == 54
    assert bars[0]["minutes_present"] == 26                                    # 10:04-10:29
    assert bars[1]["minutes_present"] == 28                                    # run1's 10:30 minute + run2's 10:31-10:57: bars span runs


def test_bad_m1_rows_abort_ingest(tmp_path):
    rows = m1(dt(2026, 11, 19, 10, 0), 3)
    rows[1]["high"] = 1.0                                                    # high below open/close
    write_m1_csv(tmp_path / "x.csv", rows)
    with pytest.raises(DataError):
        parse_m1_csv(tmp_path / "x.csv", "EURUSD", "r")


def seeded_store(tmp_path, n_bars, last_start, cov_skip=()):
    store = LocalStore(tmp_path / "d")
    rows = []
    for i in range(n_bars):
        s = last_start - (n_bars - 1 - i) * BAR
        rows.append({"symbol": "EURUSD", "start_ts": s.isoformat(timespec="seconds"), "open": 1.1, "high": 1.101, "low": 1.099,
                     "close": 1.1, "coverage": 0.5 if i in cov_skip else 0.9, "minutes_present": 27, "samples": 1000, "source": "headway"})
    store.upsert_m30(rows)
    return store


def test_export_gates_scan_on_history_and_fresh_solid_latest_bar(tmp_path):
    now = dt(2026, 11, 19, 10, 32)
    last = dt(2026, 11, 19, 10, 0)
    ok = export_m30(seeded_store(tmp_path, 130, last), "EURUSD", now, tmp_path / "o.csv", tmp_path / "q.json", 120, 0.75)
    assert ok["ok"] and ok["bars_valid"] == 130 and len((tmp_path / "o.csv").read_text().splitlines()) == 131
    short = export_m30(seeded_store(tmp_path / "s", 60, last), "EURUSD", now, tmp_path / "o2.csv", tmp_path / "q2.json", 120, 0.75)
    assert short["reason"] == "insufficient_history"
    stale = export_m30(seeded_store(tmp_path / "t", 130, last - BAR), "EURUSD", now, tmp_path / "o3.csv", tmp_path / "q3.json", 120, 0.75)
    assert stale["reason"] == "latest_bar_missing_or_degraded"
    degraded = export_m30(seeded_store(tmp_path / "u", 130, last, cov_skip=(129,)), "EURUSD", now, tmp_path / "o4.csv", tmp_path / "q4.json", 120, 0.75)
    assert degraded["reason"] == "latest_bar_missing_or_degraded" and degraded["bars_dropped_low_coverage"] == 1


def test_import_never_overwrites_headway_bars_and_is_tagged(tmp_path):
    store = seeded_store(tmp_path, 3, dt(2026, 11, 19, 10, 0))
    from tsebridge.candles import write_csv
    from tsebridge.lifecycle import Bar
    write_csv([Bar(dt(2026, 11, 19, 8, 30), 1.2, 1.21, 1.19, 1.2), Bar(dt(2026, 11, 19, 10, 0), 9, 9, 9, 9)], tmp_path / "h.csv")
    assert import_m30(store, tmp_path / "h.csv", "EURUSD", "import:my-dataset") == {"imported": 1, "skipped_existing": 1}
    rows = {r["start_ts"][11:16]: r for r in store.m30_since("EURUSD", "2026-11-19T00:00:00+00:00")}
    assert rows["08:30"]["source"] == "import:my-dataset" and rows["10:00"]["source"] == "headway" and rows["10:00"]["open"] == 1.1


def test_m30_bars_for_lifecycle_carry_coverage_and_only_closed(tmp_path):
    store = seeded_store(tmp_path, 3, dt(2026, 11, 19, 10, 0), cov_skip=(1,))
    bars = m30_bars(store, "EURUSD", dt(2026, 11, 19, 9, 0), dt(2026, 11, 19, 10, 15))
    assert [(b.t.hour * 60 + b.t.minute, b.cov) for b in bars] == [(540, 0.9), (570, 0.5)]   # 10:00 bar still forming at 10:15
