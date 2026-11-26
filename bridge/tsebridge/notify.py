"""Telegram notifications for execution events.

Rules
  * A trade is described as executed ONLY from a broker-confirmed result (position confirmed in the terminal:
    margin rise and/or a new Trade-tab row). A click that could not be confirmed is reported as UNCONFIRMED,
    never as executed.
  * Best effort: a notification failure never changes trading state, never raises, and never prints the token.
  * Secrets come from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID (same names the TSE scanner already uses).
  * Signal alerts are sent by the (unchanged) TSE scanner; this module only reports execution/lifecycle events.
"""
from __future__ import annotations

import os
from typing import Callable, Iterable, List, Optional

NOTICE_KINDS = ("ORDER_CONFIRMED", "ORDER_UNCONFIRMED", "ORDER_FAILED", "ORDER_EXPIRED",
                "BROKER_CLOSE_CONFIRMED", "BROKER_CLOSE_FAILED", "POSITION_NOT_VISIBLE")


def format_notice(n: dict) -> str:
    k, sid = n.get("kind"), n.get("signal_id", "?")
    acct = str(n.get("account_mode") or "demo").upper()
    if k == "ORDER_CONFIRMED":
        tk = n.get("ticket_id")
        tail = f"Ticket {tk}" if tk else "Ticket not captured (position confirmed by terminal margin)"
        px = f" @ {n['open_price']}" if n.get("open_price") else ""
        return (f"[{acct}] ORDER CONFIRMED BY BROKER\n{n.get('side', '').upper()} {n.get('lots')} {n.get('symbol', 'EURUSD')}{px}\n"
                f"{tail}\nSL {n.get('sl')} (no TP)\nSignal {sid}")
    if k == "ORDER_UNCONFIRMED":
        return (f"[{acct}] ORDER NOT CONFIRMED - CHECK THE ACCOUNT MANUALLY\n"
                f"Buy/Sell was clicked but the terminal did not confirm a position; it may or may not be open.\n"
                f"Signal {sid}")
    if k == "ORDER_FAILED":
        return f"[{acct}] ORDER NOT PLACED\nSignal {sid}\nReason: {n.get('outcome')}"
    if k == "ORDER_EXPIRED":
        return f"[{acct}] SIGNAL EXPIRED (not sent to broker)\nSignal {sid}\nOnly one new order is allowed per cycle; this one was not placed in time."
    if k == "BROKER_CLOSE_CONFIRMED":
        return (f"[{acct}] POSITION CLOSED (broker-confirmed)\nSignal {sid}\nTicket {n.get('ticket_id') or '?'}\n"
                f"Reason: {n.get('reason')}")
    if k == "BROKER_CLOSE_FAILED":
        return (f"[{acct}] CLOSE NOT CONFIRMED - CHECK THE ACCOUNT MANUALLY\nSignal {sid}\nTicket {n.get('ticket_id') or '?'}\n"
                f"Reason attempted: {n.get('reason')}\n{n.get('detail')}")
    if k == "POSITION_NOT_VISIBLE":
        return (f"[{acct}] POSITION NO LONGER VISIBLE IN TERMINAL\nSignal {sid}\nTicket {n.get('ticket_id') or '?'}\n"
                f"Closure was not independently confirmed (e.g. broker stop-loss). Verify in the account history.")
    return f"[{acct}] {k} {sid}"


def send_text(text: str, *, post: Optional[Callable] = None, env=None) -> bool:
    env = os.environ if env is None else env
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return False
    try:
        if post is None:
            import requests
            post = requests.post
        r = post(f"https://api.telegram.org/bot{token}/sendMessage",
                 json={"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}, timeout=15)
        return getattr(r, "status_code", 0) == 200
    except Exception as exc:                                    # never leak the token via exception text
        print(f"[notify] telegram send failed: {type(exc).__name__}")
        return False


def send_notices(notices: Iterable[dict], *, post: Optional[Callable] = None, env=None) -> List[bool]:
    return [send_text(format_notice(n), post=post, env=env) for n in notices if n.get("kind") in NOTICE_KINDS]
