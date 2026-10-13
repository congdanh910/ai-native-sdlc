import json
import time

from controller.app.processes import ManagedProcess


def test_start_detect_and_stop(tmp_path):
    process = ManagedProcess("sleeper", data_dir=tmp_path)
    pid = process.start(["sleep", "30"], cwd=tmp_path)
    assert process.pid() == pid
    assert process.stop(timeout=5) is True
    assert process.pid() is None
    assert not process.pid_file.exists()


def test_stop_without_process_is_harmless(tmp_path):
    assert ManagedProcess("none", data_dir=tmp_path).stop() is False


def test_stale_pid_file_is_ignored(tmp_path):
    process = ManagedProcess("stale", data_dir=tmp_path)
    process.pid_file.parent.mkdir(parents=True)
    process.pid_file.write_text(json.dumps({"pid": 999999, "args": "30"}))
    assert process.pid() is None
    assert not process.pid_file.exists()


def test_reused_pid_of_other_program_is_not_ours(tmp_path):
    other = ManagedProcess("other", data_dir=tmp_path)
    pid = other.start(["sleep", "30"], cwd=tmp_path)
    try:
        mine = ManagedProcess("mine", data_dir=tmp_path)
        mine.pid_file.write_text(json.dumps({"pid": pid, "args": "-N -L 1:2:3"}))
        assert mine.pid() is None
    finally:
        other.stop(timeout=5)


def test_log_captures_output(tmp_path):
    process = ManagedProcess("echo", data_dir=tmp_path)
    process.start(["sh", "-c", "echo hello"], cwd=tmp_path)
    time.sleep(0.5)
    assert "hello" in process.log_tail()
