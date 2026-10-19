"""Extraction without a model: parts, repairs, merging, consolidation, checks and the read-only CLI call."""

import json
from pathlib import Path

import pytest

from controller.app.discovery.extract import Part, plan_parts, repair_chunk, repair_consolidation
from controller.app.discovery.inventory import (ChunkResult, Consolidation, Inventory, apply_consolidation, check,
                                                merge_chunks, render)
from controller.app.doing.process import ProcessResult
from controller.app.team.readonly import CliReasoningClient, ReadonlyTeam
from controller.tests.unit.test_langflow import context
from controller.tests.unit.test_readonly import claude_output

PART = Part("s1", "deck.pdf", "pdf", "pages 1-20 of 26", file="abc-deck.pdf", pages="1-20")


def test_a_pdf_is_read_in_ranges_and_a_text_view_in_slices():
    parts = plan_parts("s1", "deck.pdf", "pdf", "/x/abc-deck.pdf", 26, None, pages_per_read=20, chars_per_read=5000)
    assert [(p.label, p.pages, p.file) for p in parts] == [("pages 1-20 of 26", "1-20", "abc-deck.pdf"),
                                                           ("pages 21-26 of 26", "21-26", "abc-deck.pdf")]
    text = "\n".join(f"[p{i}] line {i} " + "x" * 990 for i in range(1, 8))
    slices = plan_parts("s2", "brief.docx", "docx", "/x/b.docx", 7, text, pages_per_read=20, chars_per_read=2500)
    assert [p.label for p in slices] == ["lines 1-2 of 7", "lines 3-4 of 7", "lines 5-6 of 7", "lines 7-7 of 7"]
    assert "\n".join(p.text for p in slices) == text and not slices[0].file
    assert plan_parts("s3", "board.png", "image", "/x/b.png", None, None, pages_per_read=20,
                      chars_per_read=5000)[0].label == "the whole image"


def test_answers_are_repaired_to_cite_the_part_they_read():
    data = {"items": [{"type": "Entity", "name": "Camera", "sources": ["page 3", {"source": "deck.pdf", "location": "[page 3]"}]},
                      {"type": "business rule", "name": "", "text": "A camera MUST belong to one site.", "sources": []},
                      {"type": "use-case", "name": "View live video", "aliases": "Live view"}],
            "gaps": [{"kind": "THRESHOLD", "question": "How many cameras per site?", "items": "Camera"}]}
    chunk = ChunkResult.model_validate(repair_chunk(data, PART))
    camera, rule, live = chunk.items
    assert (camera.type, [c.model_dump() for c in camera.sources]) == ("data", [{"source": "s1", "location": "page 3"}])
    assert (rule.type, rule.name, rule.sources[0].location) == ("rule", "A camera MUST belong to one site.", "pages 1-20 of 26")
    assert (live.type, live.aliases) == ("function", ["Live view"])
    assert chunk.gaps[0].kind == "threshold" and chunk.gaps[0].items == ["Camera"] and chunk.read == ["pages 1-20 of 26"]
    with pytest.raises(ValueError, match="type must be one of"):
        ChunkResult.model_validate(repair_chunk({"items": [{"type": "wish", "name": "x"}]}, PART))
    assert repair_consolidation({"merges": [{"keep": "REQ-001", "drop": "REQ-003"}, {"keep": "REQ-002"}]})["merges"] == [
        {"keep": "REQ-001", "drop": ["REQ-003"], "name": ""}]


def chunk(*items, gaps=(), read=()):
    return ChunkResult.model_validate({"items": [{"sources": [{"source": "s1", "location": loc}], **i} for i, loc in items],
                                       "gaps": list(gaps), "read": list(read)})


def test_parts_merge_into_one_numbered_inventory_and_ids_never_change():
    first = chunk(({"type": "actor", "name": "Operator"}, "page 2"), ({"type": "feature", "name": "Live view"}, "page 4"),
                  gaps=[{"kind": "actor", "question": "Who approves?", "items": ["Operator"]}], read=["page 1", "page 2"])
    second = chunk(({"type": "feature", "name": "live  VIEW"}, "page 21"), ({"type": "actor", "name": "Monitoring staff"}, "page 22"),
                   ({"type": "permission", "name": "Watch", "parent": "Monitoring staff", "text": "views live video"}, "page 23"),
                   gaps=[{"kind": "actor", "question": "Who approves ?", "items": []}], read=["page 21"])
    inventory = merge_chunks([("s1", first), ("s1", second)])
    assert [(i.id, i.name) for i in inventory.items] == [("REQ-001", "Operator"), ("REQ-002", "Live view"),
                                                         ("REQ-003", "Monitoring staff"), ("REQ-004", "Watch")]
    assert [c.location for c in inventory.items[1].sources] == ["page 4", "page 21"]
    assert len(inventory.gaps) == 1 and inventory.coverage == {"s1": ["page 1", "page 2", "page 21"]}

    merged = apply_consolidation(inventory, Consolidation.model_validate({
        "merges": [{"keep": "REQ-001", "drop": ["REQ-003", "REQ-002"], "name": ""}],  # REQ-002 is a feature: kept apart
        "gaps": [{"kind": "conflict", "question": "Is live view in phase 1?", "items": ["REQ-002", "REQ-004"]}]}))
    assert [(i.id, i.name, i.aliases) for i in merged.items] == [("REQ-001", "Operator", ["Monitoring staff"]),
                                                                  ("REQ-002", "Live view", []), ("REQ-004", "Watch", [])]
    assert merged.gaps[1].items == ["REQ-002", "REQ-004"]
    assert check(merged, {"s1"}) == []  # "Monitoring staff" is now an alias of the Operator
    assert check(inventory.model_copy(update={"items": inventory.items[:3] + [inventory.items[3].model_copy(
        update={"parent": "Guest"})]}), {"s2"})[-1] == "REQ-004 gives a permission to Guest, who is not listed as an actor"
    text = render("Trial", merged, {"s1": "deck.pdf"})
    assert "## Actors" in text and "| REQ-001 | Operator (also: Monitoring staff) |" in text and "deck.pdf page 2; deck.pdf page 22" in text


def test_the_business_analyst_reads_the_pdf_in_the_sources_folder(settings, tmp_path):
    seen = []
    answer = {"items": [{"type": "goal", "name": "Fewer missed incidents", "sources": [{"location": "page 2"}]}],
              "read": ["page 1", "page 2"]}

    def runner(args, cwd, timeout, input_text, env):
        seen.append((cwd, input_text))
        return ProcessResult(args, 0, claude_output(json.dumps(answer)), 3.0)
    team = ReadonlyTeam(settings, "claude", client=CliReasoningClient("claude", runner=runner))
    base = context()
    discovery = base.__class__(**{**base.__dict__, "sources_dir": str(tmp_path), "repository": {"known": {}}})
    result = team.extract_part(discovery, PART)
    cwd, prompt = seen[0]
    assert cwd == Path(tmp_path) and '"file": "abc-deck.pdf"' in prompt and '"pages": "1-20"' in prompt
    assert "requirement inventory" in prompt and result.items[0].sources[0].source == "s1"
    with pytest.raises(ValueError, match="cannot read PDF"):
        ReadonlyTeam(settings, "codex").extract_part(discovery, PART)


def test_a_part_whose_answer_is_unusable_is_halved_and_read_again(settings):
    from controller.app.coordinator import Loaded, _extract
    from controller.app.discovery.extract import split
    from controller.app.integrations.langflow import InvalidModelOutput
    from controller.app.team.mock import MockTeam
    assert [p.label for p in split(PART)] == ["pages 1-10 of 26", "pages 11-20 of 26"]
    text = Part("s2", "b.md", "text", "lines 3-6 of 9", text="c\nd\ne\nf")
    assert [(p.label, p.text) for p in split(text)] == [("lines 3-4 of 9", "c\nd"), ("lines 5-6 of 9", "e\nf")]
    assert split(Part("s1", "d.pdf", "pdf", "pages 7-7 of 26", file="d.pdf", pages="7-7")) == []

    class LongAnswers(MockTeam):
        def __init__(self):
            self.seen = []

        def extract_part(self, context, part):
            self.seen.append((part.label, context.policy.max_format_retries))
            if part.pages not in ("1-1", "2-2"):
                raise InvalidModelOutput("the answer is not valid JSON: Extra data", "")
            return super().extract_part(context, part)
    team = LongAnswers()
    base = context()
    loaded = Loaded(None, "extract", None, 1, context=base,
                    extra={"parts": [Part("s1", "d.pdf", "pdf", "pages 1-2 of 2", file="d.pdf", pages="1-2")],
                           "minutes": [], "reused": {}, "previous": None, "previous_parts": [], "minutes_read": [],
                           "questions": [], "asked": [], "decisions": [], "round": 1, "new_input": ["first round"]})
    result = _extract(team, loaded)
    assert team.seen == [("pages 1-2 of 2", 0), ("pages 1-1 of 2", base.policy.max_format_retries),
                         ("pages 2-2 of 2", base.policy.max_format_retries)]
    assert [p["covers"] for p in result["parts"]] == ["pages 1-1 of 2", "pages 2-2 of 2"]


def test_a_second_round_keeps_ids_and_statuses_and_decisions_change_items():
    from controller.app.discovery.inventory import Item
    first = chunk(({"type": "actor", "name": "Operator", "aliases": ["Monitoring staff"]}, "page 2"),
                  ({"type": "feature", "name": "Live view"}, "page 4"))
    previous = merge_chunks([("s1", first)])
    previous = previous.model_copy(update={"items": [previous.items[0], previous.items[1].model_copy(
        update={"status": "confirmed"})]})
    again = chunk(({"type": "feature", "name": "Export"}, "page 9"), ({"type": "feature", "name": "Live view"}, "page 4"),
                  ({"type": "actor", "name": "Monitoring staff"}, "page 2"))
    round2 = merge_chunks([("s1", again)], previous)
    assert [(i.id, i.name, i.status) for i in round2.items] == [("REQ-001", "Operator", "proposed"),
                                                                ("REQ-002", "Live view", "confirmed"),
                                                                ("REQ-003", "Export", "proposed")]
    decided = apply_consolidation(round2, Consolidation.model_validate({"updates": [
        {"id": "REQ-003", "decision": "D-001", "status": "out of scope"},
        {"id": "REQ-001", "decision": "D-009", "status": "confirmed"}]}), decisions={"D-001"})
    export = decided.items[2]
    assert export.status == "out_of_scope" and export.sources[-1].source == "decision:D-001"
    assert decided.items[0].status == "proposed"  # D-009 is not an accepted decision
    assert check(decided, {"s1"}, {"D-001"}) == [] and "cites unknown sources decision:D-001" in check(decided, {"s1"})[0]
    assert "| REQ-003 | Export | — | — | — | out_of_scope | — | deck.pdf page 9; D-001 |" in render("T", decided, {"s1": "deck.pdf"})


def test_an_inventory_from_before_rounds_becomes_chunks_per_source():
    from controller.app.coordinator import _chunks_from
    first = chunk(({"type": "actor", "name": "Operator"}, "page 2"), gaps=[{"kind": "actor", "question": "Who approves?"}],
                  read=["page 1"])
    data = merge_chunks([("s1", first)]).model_dump()
    chunks = _chunks_from(data)
    assert list(chunks) == ["s1"] and chunks["s1"][0]["read"] == ["page 1"]
    again = merge_chunks([("s1", ChunkResult.model_validate(chunks["s1"][0]))], Inventory.model_validate(data))
    assert [(i.id, i.name) for i in again.items] == [("REQ-001", "Operator")] and again.gaps[0].question == "Who approves?"


def test_the_srs_trace_check_finds_unused_and_unknown_items():
    from controller.app.project_docs import check_srs_trace
    baseline = {"items": [{"id": "REQ-001", "type": "feature", "name": "Reports"},
                          {"id": "REQ-002", "type": "option", "name": "Cloud"}]}
    table = lambda rows: "## Appendix A. Requirement traceability\n\n| REQ | Type | Name | In this SRS |\n|---|---|---|---|\n" + rows
    assert check_srs_trace(table("| REQ-001 | feature | Reports | RE-FR-01 |\n"), baseline) == []
    assert "not used" in check_srs_trace(table("| REQ-001 | feature | Reports | — |\n"), baseline)[0]
    assert "not in the baseline" in check_srs_trace(table("| REQ-001 | feature | Reports | RE-US-01 |\n| REQ-009 | ? | x | 1.2 Scope |\n"),
                                                    baseline)[0]
    assert check_srs_trace("# SRS", baseline) == ["'Appendix A. Requirement traceability' is missing"]


def test_consolidation_links_orphan_functions_to_their_feature():
    first = chunk(({"type": "feature", "name": "Reports"}, "page 2"), ({"type": "function", "name": "Export"}, "page 5"))
    inventory = merge_chunks([("s1", first)])
    linked = apply_consolidation(inventory, Consolidation.model_validate(
        {"links": [{"id": "REQ-002", "parent": "Reports"}, {"id": "REQ-001", "parent": "x"}]}))
    assert [(i.id, i.parent) for i in linked.items] == [("REQ-001", "x"), ("REQ-002", "Reports")]
    from controller.app.discovery.baseline import advisories, check
    rows = {c["id"]: c for c in check(linked, [], [], [], [])}
    assert rows["functions"]["ok"]
    assert "belongs to no feature" in {c["id"]: c for c in check(inventory, [], [], [], [])}["functions"]["detail"]


def test_out_of_scope_items_cited_by_the_srs_are_not_unknown():
    from controller.app.project_docs import check_srs_trace, render_trace
    from controller.app.team.mock import MockTeam
    from controller.app.team.normalize import srs as normalize_srs
    baseline = {"items": [{"id": "REQ-001", "type": "feature", "name": "Reports", "text": "", "parent": ""}],
                "out_of_scope": [{"id": "REQ-002", "type": "feature", "name": "Archive"}]}
    model = MockTeam().write_srs(context().__class__(**{**context().__dict__, "baseline": baseline}))
    data = model.model_dump()
    data["trace_notes"].append({"req": "REQ-002", "section": "1.2 Out of scope", "note": ""})
    data["refs"] = ["REQ-001"]  # a model put refs at the top: dropped, not rejected
    from controller.app.project_docs import Srs
    srs = Srs.model_validate(normalize_srs(data))
    rows = "\n".join(render_trace(srs, baseline))
    assert "| REQ-002 | feature (out of scope) | Archive |" in rows
    assert check_srs_trace(rows, baseline) == []


def test_a_suggestion_must_be_an_answer_not_an_instruction():
    from controller.app.discovery.questions import useful_suggestion
    question = "Hướng dẫn nghiêng trái, nghiêng phải áp dụng khi đăng ký mẫu khuôn mặt hay khi ghi nhận từng lượt vào/ra?"
    assert useful_suggestion("Xác định bước thực hiện và điều kiện hoàn tất việc lấy các góc khuôn mặt.", question) is None
    assert useful_suggestion("Provide the tested threshold and its approval.", "What is the threshold?") is None
    assert useful_suggestion("  ", question) is None
    assert useful_suggestion(question, question) is None  # the question again
    answer = "Áp dụng khi đăng ký mẫu khuôn mặt: lấy đủ 3 góc mới lưu mẫu; lượt vào/ra chỉ cần nhìn camera. (thông lệ)"
    assert useful_suggestion(answer, question) == answer
    from controller.app.discovery.extract import repair_consolidation
    assert repair_consolidation({"suggestions": [{"question": "Q-023", "suggestion": answer}, {"question": "Q-1"}]})[
        "suggestions"] == [{"question": "Q-023", "suggestion": answer}]


def test_check_again_keeps_only_conflict_questions():
    from controller.app.coordinator import Loaded, _extract
    from controller.app.team.mock import MockTeam

    class Asks(MockTeam):
        def consolidate(self, context, items, gaps, decisions=(), asked=(), round_info=None):
            self.round_info = round_info
            return Consolidation.model_validate({"gaps": [{"kind": "scope", "question": "More detail on export?"},
                                                          {"kind": "conflict", "question": "Slide 3 and D-002 differ?"}]})
    team = Asks()
    stored = {"s1": [chunk(({"type": "feature", "name": "Reports"}, "page 2")).model_dump()]}
    extra = {"parts": [], "minutes": [], "reused": stored, "previous": None, "previous_parts": [], "minutes_read": [],
             "questions": [], "asked": [], "decisions": [], "round": 4, "new_input": []}
    result = _extract(team, Loaded(None, "extract", None, 1, context=context(), extra=extra))
    assert [g["question"] for g in result["inventory"]["gaps"]] == ["Slide 3 and D-002 differ?"] and result["again"]
    assert team.round_info == {"round": 4, "new_input": []}


def test_a_feature_out_of_scope_takes_its_functions_with_it():
    first = chunk(({"type": "feature", "name": "Alternative attendance"}, "page 2"),
                  ({"type": "function", "name": "Check in by card", "parent": "Alternative attendance"}, "page 3"),
                  ({"type": "rule", "name": "One card per person", "parent": "Check in by card"}, "page 3"),
                  ({"type": "function", "name": "Check in by PIN", "parent": "Alternative attendance"}, "page 3"))
    inventory = merge_chunks([("s1", first)])
    from controller.app.discovery.baseline import advisories, check
    out = Consolidation.model_validate({"updates": [{"id": "REQ-001", "decision": "D-001", "status": "out_of_scope"}]})
    kept = Consolidation.model_validate({"updates": [*out.model_dump()["updates"],
                                                     {"id": "REQ-004", "decision": "D-002", "status": "confirmed"}]})
    applied = apply_consolidation(inventory, kept, {"D-001", "D-002"})
    assert [(i.id, i.status) for i in applied.items] == [("REQ-001", "out_of_scope"), ("REQ-002", "out_of_scope"),
                                                          ("REQ-003", "out_of_scope"), ("REQ-004", "confirmed")]
    assert applied.items[2].sources[-1].source == "decision:D-001"
    # an inventory from before the cascade: the gate names the feature and the decision
    stale = inventory.model_copy(update={"items": [applied.items[0], *inventory.items[1:]]})
    detail = {c["id"]: c for c in check(stale, [], [], [], [])}["functions"]["detail"]
    assert "REQ-002 Check in by card belongs to REQ-001 Alternative attendance, which is out of scope (D-001)" in detail
    nfr = merge_chunks([("s1", chunk(({"type": "nfr", "name": "Privacy", "text": "Data is protected"}, "page 4")))])
    assert advisories(nfr, [])[0]["status"] == "warning"


def test_a_rewrite_lists_the_sections_that_changed_and_the_replies_to_feedback():
    from controller.app.documents import document_diff, section_changes
    from controller.app.project_docs import sds_part_model
    from controller.app.team.normalize import feedback_replies, sds_part
    old = "# SDS\n## 4.2 Technology Stack\n| identity | Keycloak |\n## 5 APIs\nsame\n## 6 Old\nx\n"
    new = "# SDS\n## 4.2 Technology Stack\n| identity | built-in accounts |\n## 5 APIs\nsame\n## 7 New\ny\n"
    assert section_changes(old, new) == [
        {"heading": "4.2 Technology Stack", "added": 1, "removed": 1, "state": "changed"},
        {"heading": "7 New", "added": 1, "removed": 0, "state": "new"},
        {"heading": "6 Old", "added": 0, "removed": 1, "state": "removed"}]
    assert "-| identity | Keycloak |" in document_diff(old, new, "v1", "v2")
    replies = feedback_replies([{"feedback": "no Keycloak", "change": "Accounts in the backend", "where": "4.2, 7.1"},
                                {"feedback": "x", "change": ""}, "junk"])
    assert replies == [{"feedback": "no Keycloak", "change": "Accounts in the backend", "where": ["4.2", "7.1"]}]
    part = sds_part({"api_standards": ["REST"], "feedback_replies": replies}, "sds-interfaces")
    assert sds_part_model("sds-interfaces").model_validate(part).feedback_replies[0].where == ["4.2", "7.1"]
