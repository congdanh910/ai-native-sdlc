"""The backlog's checks, waves and story states work without a model or a database."""

from types import SimpleNamespace

from controller.app.backlog import check, find_cycle, repair, states, waves


def item(story, *deps, **extra):
    return {"story": story, "depends_on": list(deps), "reason": "", "hold": False, "done": "", **extra}


def run(status):
    return SimpleNamespace(id=status.lower(), status=status)


STORIES = ["EMP-US-01", "EMP-US-02", "EMP-US-03", "SHF-US-01"]


def test_a_backlog_names_every_story_once_and_only_known_ones():
    items = [item("EMP-US-01"), item("EMP-US-02", "EMP-US-01"), item("EMP-US-02"), item("XX-US-09", "EMP-US-07"),
             item("SHF-US-01", "SHF-US-01")]
    assert check(items, STORIES) == [
        "EMP-US-02 is in the backlog twice", "XX-US-09 is not a user story of the SRS",
        "XX-US-09 depends on EMP-US-07, which is not a user story of the SRS", "SHF-US-01 depends on itself",
        "not in the backlog: EMP-US-03", "dependency cycle: SHF-US-01 → SHF-US-01"]


def test_a_dependency_cycle_is_named_story_by_story():
    items = [item("EMP-US-01", "EMP-US-03"), item("EMP-US-02", "EMP-US-01"), item("EMP-US-03", "EMP-US-02"),
             item("SHF-US-01")]
    assert find_cycle(items) == ["EMP-US-01", "EMP-US-03", "EMP-US-02", "EMP-US-01"]
    assert waves(items) == {"SHF-US-01": 1}
    assert find_cycle([item("EMP-US-01"), item("EMP-US-02", "EMP-US-01")]) == []


def test_a_story_comes_one_wave_after_the_latest_story_it_needs():
    items = [item("EMP-US-01"), item("SHF-US-01"), item("EMP-US-02", "EMP-US-01"),
             item("EMP-US-03", "EMP-US-02", "SHF-US-01")]
    assert waves(items) == {"EMP-US-01": 1, "SHF-US-01": 1, "EMP-US-02": 2, "EMP-US-03": 3}


def test_a_story_is_ready_once_every_story_it_needs_is_merged_or_done_outside():
    items = [item("EMP-US-01"), item("EMP-US-02", "EMP-US-01"), item("EMP-US-03", "EMP-US-02", "SHF-US-01"),
             item("SHF-US-01", done="built by the Foundation"), item("CAM-US-01", hold=True)]
    runs = {"EMP-US-01": [run("CLOSED_UNMERGED"), run("COMPLETED")], "EMP-US-02": [run("CODING")]}
    found = states(items, runs, "")
    assert {s: (v["state"], v["detail"]) for s, v in found.items()} == {
        "EMP-US-01": ("delivered", "merged"), "EMP-US-02": ("running", "CODING"),
        "EMP-US-03": ("blocked", "waits for EMP-US-02"), "SHF-US-01": ("delivered", "built by the Foundation"),
        "CAM-US-01": ("held", "on hold")}
    assert found["EMP-US-02"]["run"] == {"id": "coding", "status": "CODING"}


def test_a_closed_run_puts_its_story_back_to_ready_and_an_unmerged_foundation_blocks_every_story():
    items = [item("EMP-US-01"), item("EMP-US-02", "EMP-US-01")]
    found = states(items, {"EMP-US-01": [run("CLOSED_UNMERGED")]}, "")
    assert found["EMP-US-01"]["state"] == "ready" and found["EMP-US-01"]["run"]["status"] == "CLOSED_UNMERGED"
    blocked = states(items, {}, "the Foundation is not merged")
    assert [v["detail"] for v in blocked.values()] == ["the Foundation is not merged"] * 2


def test_story_ids_from_the_model_are_made_plain():
    data = repair({"items": [{"story": "`EMP-US-02`: List Employees", "depends_on": ["EMP-US-01 (create)"]}]})
    assert data["items"][0] == {"story": "EMP-US-02", "depends_on": ["EMP-US-01"]}
