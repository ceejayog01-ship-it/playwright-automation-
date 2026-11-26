from datetime import timedelta
from tsebridge.lifecycle import Bar, evaluate
from helpers import BAR, dt, flat_bars, pos

OPEN = dt(2026, 11, 19, 10, 30)          # signal bar 10:00 closes 10:30


def series(n, **kw):
    return flat_bars(OPEN, n, **kw)


def test_position_has_no_take_profit_and_locked_lots():
    p = pos(horizon=48)
    assert "tp" not in p and "take_profit" not in p and p["lots"] == 0.50
    assert p["opened_at"].startswith("2026-11-19T10:30") and p["deadline"].startswith("2026-11-20T10:30")


def test_buy_sl_intrabar_fills_at_sl():
    bars = series(5)
    bars[2] = Bar(bars[2].t, 1.1000, 1.1005, 1.0975, 1.0990)
    o = evaluate(pos(), bars)
    assert o.status == "EXIT" and o.exit["reason"] == "SL" and o.exit["price"] == 1.0980 and not o.exit["gap_fill"]
    assert o.exit["pips"] == -20.0 and o.exit["pnl_usd"] == -100.0          # 0.0020 * 0.5 lot * 100k


def test_sell_sl_intrabar():
    bars = series(5)
    bars[1] = Bar(bars[1].t, 1.1000, 1.1025, 1.0995, 1.1010)
    o = evaluate(pos("SELL", 1.1000, 1.1020), bars)
    assert o.exit["reason"] == "SL" and o.exit["price"] == 1.1020 and o.exit["pnl_usd"] == -100.0


def test_gap_through_sl_fills_at_open_worse_than_sl():
    bars = series(4)
    bars[1] = Bar(bars[1].t, 1.0960, 1.0965, 1.0950, 1.0955)
    o = evaluate(pos(), bars)
    assert o.exit["price"] == 1.0960 and o.exit["gap_fill"] and o.exit["pips"] == -40.0


def test_time_exit_at_deadline_bar_open_profit_unbounded():
    bars = series(49, price=1.1000)
    bars[10] = Bar(bars[10].t, 1.1000, 1.1100, 1.0995, 1.1090)             # +100 pips spike: no TP, no exit
    bars[48] = Bar(bars[48].t, 1.1060, 1.1065, 1.1055, 1.1060)             # deadline bar
    o = evaluate(pos(horizon=48), bars)
    assert o.exit["reason"] == "TIME" and o.exit["price"] == 1.1060 and o.exit["time"].startswith("2026-11-20T10:30")
    assert o.exit["pips"] == 60.0 and o.exit["pnl_usd"] == 300.0 and o.exit["delay_min"] == 0


def test_deadline_bar_exits_time_even_if_its_low_is_below_sl():
    bars = series(49)
    bars[48] = Bar(bars[48].t, 1.0990, 1.0995, 1.0950, 1.0960)
    o = evaluate(pos(horizon=48), bars)
    assert o.exit["reason"] == "TIME" and o.exit["price"] == 1.0990


def test_each_original_horizon():
    for h in (12, 24, 48):
        p = pos(horizon=h)
        o = evaluate(p, flat_bars(OPEN, h + 1))
        assert o.exit["reason"] == "TIME" and o.exit["time"] == (OPEN + h * BAR).isoformat(timespec="seconds")


def test_still_open_before_deadline():
    assert evaluate(pos(), series(10)).status == "OPEN"


def test_midweek_gap_is_data_gap_not_guess():
    bars = series(5) + flat_bars(OPEN + 8 * BAR, 5)
    o = evaluate(pos(), bars)
    assert o.status == "DATA_GAP"


def test_weekend_gap_tolerated_and_time_exit_delayed():
    start = dt(2026, 11, 20, 20, 0)                                         # Friday evening signal bar
    p = pos(bar_time=start, horizon=12)                                     # opens 20:30, deadline Sat 02:30
    fri = flat_bars(dt(2026, 11, 20, 20, 30), 2)                            # 20:30, 21:00
    mon = [Bar(dt(2026, 11, 22, 22, 0), 1.1010, 1.1012, 1.1008, 1.1010)]    # Sunday reopen
    o = evaluate(p, fri + mon)
    assert o.status == "EXIT" and o.exit["reason"] == "TIME" and o.exit["delay_min"] > 0 and o.exit["price"] == 1.1010


def test_bars_before_open_are_ignored():
    early = flat_bars(OPEN - 4 * BAR, 4, price=1.0000)
    assert evaluate(pos(), early + series(3)).status == "OPEN"


def test_exit_reports_lowest_observed_coverage_on_the_path():
    bars = series(5)
    bars[1] = Bar(bars[1].t, bars[1].o, bars[1].h, bars[1].l, bars[1].c, cov=0.6)
    bars[3] = Bar(bars[3].t, 1.0990, 1.0995, 1.0970, 1.0975, cov=0.9)          # BUY SL hit here
    o = evaluate(pos(), bars)
    assert o.exit["reason"] == "SL" and o.exit["min_coverage"] == 0.6


def test_deadline_bar_coverage_does_not_count_only_its_open_is_used():
    bars = flat_bars(OPEN, 13)
    bars[12] = Bar(bars[12].t, 1.1, 1.1001, 1.0999, 1.1, cov=0.1)
    assert evaluate(pos(horizon=12), bars).exit["min_coverage"] == 1.0
