from tsebridge import notify


class Resp:
    def __init__(self, code=200):
        self.status_code = code


def test_no_credentials_means_no_request():
    assert notify.send_text("x", post=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")), env={}) is False


def test_sends_plain_text_and_never_exposes_token_on_failure(capsys):
    sent = {}
    ok = notify.send_text("hello", post=lambda url, json=None, timeout=None: (sent.update(url=url, body=json), Resp())[1],
                          env={"TELEGRAM_BOT_TOKEN": "TOK", "TELEGRAM_CHAT_ID": "42"})
    assert ok and sent["body"]["chat_id"] == "42" and "parse_mode" not in sent["body"]

    def boom(*a, **k):
        raise RuntimeError("https://api.telegram.org/botTOK/sendMessage failed")
    assert notify.send_text("x", post=boom, env={"TELEGRAM_BOT_TOKEN": "TOK", "TELEGRAM_CHAT_ID": "42"}) is False
    assert "TOK" not in capsys.readouterr().out


def test_only_known_notice_kinds_are_sent():
    out = []
    notify.send_notices([{"kind": "ORDER_CONFIRMED", "signal_id": "S"}, {"kind": "SOMETHING_ELSE"}],
                        post=lambda *a, **k: (out.append(1), Resp())[1], env={"TELEGRAM_BOT_TOKEN": "T", "TELEGRAM_CHAT_ID": "1"})
    assert len(out) == 1
