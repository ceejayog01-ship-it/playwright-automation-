import pytest
from tsebridge.signals import Reject, check_fresh, normalize
from helpers import dt, raw_signal


def reason(**kw):
    with pytest.raises(Reject) as e:
        normalize(raw_signal(**kw))
    return e.value.reason


def test_valid_design_a_buy_and_sell():
    assert normalize(raw_signal())["side"] == "BUY"
    assert normalize(raw_signal(side="SELL", sl=1.1020))["side"] == "SELL"


def test_design_b_rejected():
    assert reason(design="B", tp=1.1015) == "design_not_allowed"


def test_take_profit_rejected_even_on_design_a():
    assert reason(tp=1.1050) == "take_profit_present"


def test_contract_violations():
    assert reason(pair="GBPUSD") == "pair_not_allowed"
    assert reason(side="HOLD") == "bad_side"
    assert reason(horizon_bars=36) == "horizon_not_allowed"
    assert reason(sl=1.1020) == "sl_wrong_side"
    assert reason(sl=1.0999) == "sl_distance_out_of_bounds"
    assert reason(sl=float("nan")) == "bad_sl"
    assert reason(bar_time="2026-11-19T10:07:00+00:00") == "bar_time_not_on_m30_grid"
    assert reason(bar_time="2026-11-19T10:00:00") == "bad_bar_time"
    assert reason(signal_id="has space") == "bad_signal_id"


def test_freshness():
    s = normalize(raw_signal())
    check_fresh(s, dt(2026, 11, 19, 10, 40))
    with pytest.raises(Reject):
        check_fresh(s, dt(2026, 11, 19, 11, 30))
    with pytest.raises(Reject):
        check_fresh(s, dt(2026, 11, 19, 10, 20))
