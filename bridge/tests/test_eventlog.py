import json
import pytest
from tsebridge.eventlog import EventLog, IntegrityError, State


def test_chain_persists_and_reloads(tmp_path):
    p = tmp_path / "e.jsonl"
    log = EventLog(p, "t")
    log.append("A", {"x": 1}, "t1")
    log.append("B", {"y": 2}, "t2")
    assert len(EventLog(p, "t").events) == 2


def test_tamper_detected(tmp_path):
    p = tmp_path / "e.jsonl"
    log = EventLog(p, "t")
    log.append("POSITION_CLOSED", {"signal_id": "S", "pnl_usd": -100.0}, "t1")
    p.write_text(p.read_text().replace("-100.0", "100.0"))
    with pytest.raises(IntegrityError):
        EventLog(p, "t")


def test_deleted_event_detected(tmp_path):
    p = tmp_path / "e.jsonl"
    log = EventLog(p, "t")
    for i in range(3):
        log.append("A", {"i": i}, "t")
    lines = p.read_text().splitlines()
    p.write_text("\n".join([lines[0], lines[2]]) + "\n")
    with pytest.raises(IntegrityError):
        EventLog(p, "t")


def test_torn_tail_recovered_and_recorded(tmp_path):
    p = tmp_path / "e.jsonl"
    log = EventLog(p, "t")
    log.append("A", {"i": 1}, "t")
    with open(p, "ab") as f:
        f.write(b'{"seq":2,"ts":"t","type":"B","da')          # simulated crash mid-write
    log2 = EventLog(p, "t9")
    assert [e["type"] for e in log2.events] == ["A", "RECOVERY"]
    assert len(EventLog(p, "t").events) == 2                  # chain still valid after recovery
