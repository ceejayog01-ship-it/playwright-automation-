import pytest
from tsebridge import marketdata as MD
from tsebridge.common import DataError
from helpers import dt

NOW = dt(2026, 11, 25, 10, 5)          # the 10:00 bar is still forming; 09:30 is the latest completed bar


def v(t, o=1.0965, h=1.0967, l=1.0960, c=1.0963):
    return {"datetime": t, "open": str(o), "high": str(h), "low": str(l), "close": str(c)}


def payload(*values, **meta):
    m = {"symbol": "EUR/USD", "interval": "30min", "exchange_timezone": "UTC"}
    m.update(meta)
    return {"meta": m, "values": list(values), "status": "ok"}


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body

    def json(self):
        if isinstance(self._b, Exception):
            raise self._b
        return self._b


def test_parse_drops_forming_bar_sorts_dedupes_and_tags_source():
    rows = MD.parse_time_series(payload(v("2026-11-25 10:00:00"), v("2026-11-25 09:30:00"), v("2026-11-25 09:00:00"),
                                        v("2026-11-25 09:00:00", h=1.0975, c=1.0970)), NOW)
    assert [r["start_ts"] for r in rows] == ["2026-11-25T09:00:00+00:00", "2026-11-25T09:30:00+00:00"]
    assert {r["source"] for r in rows} == {"yfinance"} and rows[0]["close"] == 1.0970   # last duplicate wins


@pytest.mark.parametrize("bad", [
    {"values": "nope"}, {},
    payload(v("2026-11-25 09:15:00")),                                   # off the M30 grid
    payload(v("2026-11-25 09:00:00", h=1.0, l=1.1)),                     # inconsistent OHLC
    payload(v("2026-11-25 09:00:00", o=0.0001, h=0.0002, l=0.0001, c=0.0002)),   # outside the sanity band
    payload(v("2026-11-25 09:00:00", o="nan")),
    payload(v("2026-11-25 09:00:00"), exchange_timezone="America/New_York"),
    payload(v("2026-11-25 09:00:00"), symbol="GBP/USD"),
    payload(v("2026-11-25 09:00:00"), interval="1h"),
    payload({"datetime": "2026-11-25 09:00:00", "open": "1.1"}),         # missing fields
])
def test_malformed_payloads_are_rejected_whole(bad):
    with pytest.raises(DataError):
        MD.parse_time_series(bad, NOW)


def test_expected_latest_open_is_the_last_completed_bar():
    assert MD.expected_latest_open(dt(2026, 11, 25, 10, 5)) == dt(2026, 11, 25, 9, 30)
    assert MD.expected_latest_open(dt(2026, 11, 25, 10, 30)) == dt(2026, 11, 25, 10, 0)


def test_gap_report_ignores_weekend_but_flags_midweek_holes():
    rows = [{"start_ts": t} for t in ("2026-11-20T21:30:00+00:00", "2026-11-22T22:00:00+00:00",
                                      "2026-11-23T10:00:00+00:00", "2026-11-23T11:00:00+00:00")]
    rep = MD.gap_report(rows)
    assert rep["non_weekend_gaps"] == 2          # Sun 22:00 -> Mon 10:00 and 10:00 -> 11:00 are NOT weekend gaps


def test_yfinance_fetch_uses_test_adapter_without_api_key():
    seen = {}
    class Adapter:
        status_code = 200
        def json(self):
            return payload(v("2026-11-25 09:00:00"))
    def get(url, params=None, headers=None, timeout=None):
        seen.update(url=url, params=params, headers=headers)
        return Adapter()
    result = MD.fetch_time_series("", "EURUSD", outputsize=500, http_get=get)
    assert result["values"][0]["datetime"] == "2026-11-25 09:00:00"
    assert seen["params"]["symbol"] == "EURUSD=X" and seen["params"]["interval"] == "30m"


def test_unsupported_pair_fails_before_provider_call():
    with pytest.raises(MD.ProviderError):
        MD.fetch_time_series("", pair="XAUUSD", http_get=lambda *a, **k: pytest.fail("must not call"))
