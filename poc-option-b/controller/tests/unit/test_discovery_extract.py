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
                           "questions": [], "asked": [], "decisions": []})
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
