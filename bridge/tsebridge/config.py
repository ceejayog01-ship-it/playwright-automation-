"""Locked execution contract.

These are constants, not environment-driven, so an auditor can read the whole
contract in one place. Only operational tolerances (ages) can be overridden by
environment, and every run records the effective values in its RUN_STARTED event.
"""
import os

VERSION = "2.0.0"                # real-execution + native M30 edition

PAIR = "EURUSD"
SIDES = ("BUY", "SELL")
LOTS = 0.50
CONTRACT_SIZE = 100_000          # units per 1.00 lot (EURUSD, USD-quoted => P&L in USD)
PIP = 0.0001

DESIGN = "A"                     # Design A only: SL + time exit. Design B (TP) is removed.
TAKE_PROFIT = None               # never used; any signal carrying a TP is rejected
BAR_MINUTES = 30                 # M30
ALLOWED_HORIZON_BARS = (12, 24, 48)   # 6h / 12h / 24h original strategy exits

# Sanity bounds on the SL distance supplied by TSE (2 x ATR14 M30). Guards against bad data only.
SL_MIN_PIPS = 3.0
SL_MAX_PIPS = 150.0

# Account the Headway login gate must prove. "demo" is the default; "real" is an explicit opt-in.
ACCOUNT_MODE = os.getenv("BRIDGE_ACCOUNT_MODE", "demo").strip().lower()
EXPECTED_SERVER = os.getenv("BRIDGE_EXPECTED_SERVER", "Headway-Demo" if ACCOUNT_MODE == "demo" else "").strip()
MAX_DUMMY_LOTS = 0.10            # connectivity-test order is capped; strategy uses LOTS

# Data policy: Headway-native M30 bars take precedence. Dukascopy is an insert-only
# historical bootstrap for missing timestamps; fresh Headway quotes remain mandatory.
# Coverage threshold retained for compatibility with any residual sampled bars; native/bootstrap bars are 1.0.
MIN_M30_COVERAGE = float(os.getenv("BRIDGE_MIN_M30_COVERAGE", "0.75"))
MIN_HISTORY_BARS = int(os.getenv("BRIDGE_MIN_HISTORY_BARS", "120"))      # TSE needs this much M30 history
VOID_AFTER_HOURS = float(os.getenv("BRIDGE_VOID_AFTER_HOURS", "6"))

MAX_SIGNAL_AGE_MIN = float(os.getenv("BRIDGE_MAX_SIGNAL_AGE_MIN", "25"))
AUTH_MAX_AGE_MIN = float(os.getenv("BRIDGE_AUTH_MAX_AGE_MIN", "20"))

# Real execution flag (cycle passes --real-execution). When false, cycle still records signals
# but does not place orders (safe default for unit tests).
REAL_EXECUTION = os.getenv("BRIDGE_REAL_EXECUTION", "true").strip().lower() in ("1", "true", "yes")

assert TAKE_PROFIT is None and DESIGN == "A" and LOTS == 0.50 and PAIR == "EURUSD"


def snapshot() -> dict:
    return {
        "version": VERSION, "pair": PAIR, "sides": list(SIDES), "lots": LOTS, "design": DESIGN,
        "take_profit": TAKE_PROFIT, "bar_minutes": BAR_MINUTES,
        "allowed_horizon_bars": list(ALLOWED_HORIZON_BARS),
        "sl_bounds_pips": [SL_MIN_PIPS, SL_MAX_PIPS],
        "account_mode": ACCOUNT_MODE, "expected_server": EXPECTED_SERVER,
        "max_dummy_lots": MAX_DUMMY_LOTS, "min_m30_coverage": MIN_M30_COVERAGE,
        "min_history_bars": MIN_HISTORY_BARS, "void_after_hours": VOID_AFTER_HOURS,
        "data_source": "headway_native_preferred_dukascopy_bootstrap_m30",
        "real_execution": REAL_EXECUTION,
        "max_signal_age_min": MAX_SIGNAL_AGE_MIN, "auth_max_age_min": AUTH_MAX_AGE_MIN,
    }
