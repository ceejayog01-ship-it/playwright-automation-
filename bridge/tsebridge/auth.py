"""Headway connection gate. Reads artifacts/auth_result.json (schema 2) written by the Playwright login step.

The login must prove the SAME account type the bridge is configured for (config.ACCOUNT_MODE). A demo login
can never satisfy a real-mode bridge and vice versa.
"""
import json
import re
from typing import Tuple

from . import config as C
from .common import parse_utc


def _norm(x) -> str:
    return re.sub(r"\s+", " ", str(x or "")).strip().lower()


def check_auth(path, now) -> Tuple[bool, str, dict]:
    if C.ACCOUNT_MODE not in ("demo", "real"):
        return False, "bad_bridge_account_mode", {}
    try:
        with open(path) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False, "auth_result_missing_or_unreadable", {}
    if d.get("schema") != 2 or d.get("status") != "verified":
        return False, f"login_not_verified:{d.get('reason', 'unknown')}", d
    if d.get("account_mode") != C.ACCOUNT_MODE:
        return False, f"account_mode_mismatch:{d.get('account_mode')}!={C.ACCOUNT_MODE}", d
    url_path = _norm(d.get("url_path")).rstrip("/")
    # No exact broker/server-name matching: Headway changes how it renders
    # server labels. The account mode is checked against the endpoint path,
    # while login success is established by the terminal workspace itself.
    if C.ACCOUNT_MODE == "demo":
        if not url_path.endswith("/mt5-demo"):
            return False, "url_not_demo_endpoint", d
    else:
        if "demo" in url_path:
            return False, "demo_endpoint_in_real_mode", d
    try:
        age = (now - parse_utc(d["verified_at"])).total_seconds() / 60
    except (KeyError, ValueError):
        return False, "bad_verified_at", d
    if age < -2 or age > C.AUTH_MAX_AGE_MIN:
        return False, "auth_stale", d
    return True, "ok", d
