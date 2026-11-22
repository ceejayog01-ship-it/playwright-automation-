import importlib.util
import sys
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("bootstrap_dukascopy", ROOT / "scripts" / "bootstrap_dukascopy.py")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


def test_parse_dukascopy_csv_epoch_ms_and_drop_forming(tmp_path):
    now = bootstrap.datetime(2026, 11, 22, 12, 45, tzinfo=timezone.utc)
    path = tmp_path / "dukascopy.csv"
    # 12:00 is complete at 12:45; 12:30 is still forming.
    path.write_text(
        "timestamp,open,high,low,close\n"
        "1795348800000,1.10,1.11,1.09,1.105\n"
        "1795350600000,1.105,1.12,1.10,1.115\n"
    )
    rows = bootstrap.parse_dukascopy_csv(path, now)
    assert len(rows) == 1
    assert rows[0]["source"] == "dukascopy_bootstrap"
    assert rows[0]["minutes_present"] == 30
    assert rows[0]["start_ts"].endswith("+00:00")


def test_parse_dukascopy_rejects_misaligned_timestamp(tmp_path):
    now = bootstrap.datetime(2026, 11, 22, 13, 0, tzinfo=timezone.utc)
    path = tmp_path / "bad.csv"
    path.write_text("timestamp,open,high,low,close\n1795348860000,1.1,1.2,1.0,1.15\n")
    try:
        bootstrap.parse_dukascopy_csv(path, now)
    except bootstrap.DataError:
        pass
    else:
        raise AssertionError("misaligned M30 timestamp should be rejected")


def test_bootstrap_overlap_comparison_reports_feed_difference():
    t = "2026-11-22T12:00:00+00:00"
    existing = [{"start_ts": t, "close": 1.1010, "source": "headway_native"}]
    downloaded = [{"start_ts": t, "close": 1.1000, "source": "dukascopy_bootstrap"}]
    result = bootstrap.overlap_comparison(existing, downloaded)
    assert result["overlap_bars"] == 1
    assert result["mean_abs_close_difference_pips"] == 10.0
