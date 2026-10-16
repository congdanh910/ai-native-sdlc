import json
import sys
import uuid

from controller.app import joblog
from controller.app.doing.process import run_bounded


def test_cli_events_become_one_readable_line():
    read = {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",
                                                           "input": {"file_path": "src/attendance.js"}}]}}
    assert joblog.summarise(json.dumps(read)) == "Read src/attendance.js"
    done = {"type": "result", "is_error": False, "num_turns": 4, "duration_ms": 61000, "total_cost_usd": 0.4}
    assert joblog.summarise(json.dumps(done)) == "finished (ok): 4 turns, 61s, API-price estimate $0.40 (subscription usage, not billed)"
    command = {"type": "item.started", "item": {"type": "command_execution", "command": "node --test"}}
    assert joblog.summarise(json.dumps(command)) == "runs node --test"
    assert joblog.summarise(json.dumps({"type": "user", "message": {}})) is None
    assert joblog.summarise("plain output") == "plain output"


def test_a_subprocess_streams_into_the_job_log(tmp_path, monkeypatch):
    monkeypatch.setattr(joblog, "DATA_DIR", tmp_path)
    run_id, execution_id = uuid.uuid4(), uuid.uuid4()
    joblog.start(run_id, execution_id, "plan (tech_lead_ai) claimed")
    try:
        result = run_bounded([sys.executable, "-c", "import sys; print('reading files'); print(sys.stdin.read())"],
                             timeout=30, input_text="token=abcd1234 hello")
    finally:
        joblog.stop()
    assert result.ok and "hello" in result.output
    text, offset = joblog.read(run_id, execution_id)
    assert "plan (tech_lead_ai) claimed" in text and "reading files" in text
    assert "abcd1234" not in text and "[redacted]" in text
    assert "exited 0" in text
    more, _ = joblog.read(run_id, execution_id, offset)
    assert more == ""
