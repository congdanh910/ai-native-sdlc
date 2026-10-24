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


def test_a_job_keeps_the_exact_prompts_it_sent(tmp_path, monkeypatch, settings):
    import json, uuid
    from controller.app import joblog
    from controller.app.doing.process import ProcessResult
    from controller.app.team.readonly import CliReasoningClient
    monkeypatch.setattr(joblog, "DATA_DIR", tmp_path)
    run, execution = uuid.uuid4(), uuid.uuid4()
    joblog.start(run, execution, "extract (business_analyst)")
    client = CliReasoningClient("claude", runner=lambda args, cwd, timeout, input_text, env: ProcessResult(
        args, 0, json.dumps({"result": "{}", "is_error": False}), 1.0))
    client.run("sdlc-v3-consolidate", {"items": [{"id": "REQ-001", "name": "Điểm danh"}]}, session_id="s")
    joblog.stop()
    listed = joblog.list_prompts(run, execution)
    assert [p["name"] for p in listed] == ["01-consolidate-claude.md"]
    text = joblog.read_prompt(run, execution, "01-consolidate-claude.md")
    assert "Business Analyst" in text and '"name": "Điểm danh"' in text  # instructions, then the run's JSON
    assert joblog.read_prompt(run, execution, "../x.log") is None
    assert "prompt saved: 01-consolidate-claude.md" in joblog.read(run, execution)[0]


def test_a_step_of_a_job_is_reused_on_retry_only_with_the_same_inputs(tmp_path, monkeypatch):
    from controller.app.discovery.inventory import ChunkResult
    monkeypatch.setattr(joblog, "DATA_DIR", tmp_path)
    asked = []

    def compute():
        asked.append(1)
        return ChunkResult.model_validate({"items": [], "read": [f"page {len(asked)}"]})
    # no job log (a test, a script): the step just runs
    assert joblog.resumable("read part", {"part": "pages 1-10"}, compute, ChunkResult.model_validate)[1] is False
    joblog.start(uuid.uuid4(), uuid.uuid4(), "extract")
    try:
        first, reused = joblog.resumable("read part", {"part": "pages 1-10"}, compute, ChunkResult.model_validate)
        assert not reused and first.read == ["page 2"]
        again, reused = joblog.resumable("read part", {"part": "pages 1-10"}, compute, ChunkResult.model_validate)
        assert reused and again == first and len(asked) == 2
        changed, reused = joblog.resumable("read part", {"part": "pages 1-10", "prompt": "v2"}, compute,
                                           ChunkResult.model_validate)
        assert not reused and changed.read == ["page 3"]
    finally:
        joblog.stop()
