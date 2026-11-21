from datetime import datetime, timedelta, timezone

from tsebridge.lifecycle import Bar

UTC = timezone.utc
BAR = timedelta(minutes=30)


def dt(*a):
    return datetime(*a, tzinfo=UTC)


def flat_bars(start, n, price=1.1000, rng=0.0002):
    """n contiguous M30 bars from `start`, tight range around `price`."""
    return [Bar(start + i * BAR, price, price + rng, price - rng, price) for i in range(n)]


def raw_signal(**kw):
    s = {"signal_id": "TSE-A-EURUSD-202611191000-R05", "pair": "EURUSD", "side": "BUY", "design": "A",
         "entry": 1.1000, "sl": 1.0980, "tp": 0.0, "horizon_bars": 24,
         "bar_time": "2026-11-19T10:00:00+00:00", "strategy_id": "STRAT_X", "strategy_rank": 5,
         "signal_ts": 1789999999}
    s.update(kw)
    return s


def pos(side="BUY", entry=1.1000, sl=1.0980, horizon=24, bar_time=dt(2026, 11, 19, 10, 0)):
    from tsebridge.lifecycle import open_position
    return open_position({"signal_id": "S1", "pair": "EURUSD", "side": side, "entry": entry, "sl": sl,
                          "horizon_bars": horizon, "bar_time": bar_time.isoformat(), "strategy_id": "X",
                          "strategy_rank": 1})
