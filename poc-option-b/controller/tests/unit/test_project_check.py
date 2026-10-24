"""Checks of one project run one at a time."""

import threading
import time


def test_two_checks_of_one_project_do_not_share_the_checkout_at_once(tmp_path, monkeypatch):
    from controller.app.projects import registry
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path)
    inside, overlaps = [], []

    def check(key):
        with registry.checking(key):
            if key in inside:
                overlaps.append(key)
            inside.append(key)
            time.sleep(0.2)
            inside.remove(key)

    threads = [threading.Thread(target=check, args=(key,)) for key in ("attendance", "attendance", "inventory")]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert overlaps == []  # another project may check at the same time, the same project never
    assert time.monotonic() - started >= 0.4  # the two checks of attendance ran one after the other


def test_a_checkout_folder_git_does_not_know_is_removed_before_the_next_check(tmp_path):
    import subprocess
    from controller.app.projects import registry
    from controller.app.projects.repo import Mirror
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "--bare", str(repo)], check=True)
    leftover = tmp_path / "checks" / "attendance" / "37f0c3c1b374"
    leftover.mkdir(parents=True)  # a check that broke left an empty folder, no longer a worktree
    registry._remove_checkout(Mirror(repo), leftover)
    assert not leftover.exists()
