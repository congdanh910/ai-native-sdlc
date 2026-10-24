"""Checks of one project run one at a time."""

import threading
import time


def test_two_checks_of_one_project_do_not_share_the_checkout_at_once(tmp_path, monkeypatch):
    from controller.app.projects import registry
    monkeypatch.setattr(registry, "DATA_DIR", tmp_path)
    inside, overlaps = [], []

    def check(key):
        with registry.checking(key):
            if inside:
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
    assert overlaps in ([], ["inventory"], ["attendance"])  # only another project may check at the same time
    assert time.monotonic() - started >= 0.4  # the two checks of attendance ran one after the other
