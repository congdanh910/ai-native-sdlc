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


def test_parts_merge_into_one_numbered_inventory_and_consolidation_renumbers():
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
                                                                  ("REQ-002", "Live view", []), ("REQ-003", "Watch", [])]
    assert merged.gaps[1].items == ["REQ-002", "REQ-003"]
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
