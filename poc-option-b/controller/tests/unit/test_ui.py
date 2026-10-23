"""UI concepts: presets, contrast checks, prototypes and the docs/ui files."""

import pytest

from controller.app.ui import Brand, Concept, check, contrast, presets, render, ui_files


def concept(**colors) -> Concept:
    preset = presets()[0]
    return Concept.model_validate({
        "id": "enterprise", "name": "Enterprise", "preset": preset["id"], "layout": "sidebar", "density": "compact",
        "rationale": "dense tables for HR", "tokens": {**preset["tokens"], "colors": {**preset["tokens"]["colors"], **colors}},
        "navigation": ["Dashboard", "Employees"],
        "screens": [{"name": "Sign in", "blocks": [{"type": "form", "title": "Sign in", "fields": ["Login", "Password"]}]},
                    {"name": "Dashboard", "role": "HR_ADMIN", "blocks": [
                        {"type": "stats", "items": ["Present: 42"]}, {"type": "alert", "tone": "danger", "title": "<b>x</b>"},
                        {"type": "table", "columns": ["Name"], "rows": [["Lan"]]}]}]})


def test_the_platform_has_five_default_concepts_that_pass_aa():
    loaded = presets()
    # every concept takes the mobile layout (bottom navigation) on the mobile device
    assert len(loaded) == 5 and {p["layout"] for p in loaded} == {"sidebar", "topnav"}
    for preset in loaded:
        c = concept(**preset["tokens"]["colors"])
        assert all(r["ok"] for r in check(c, "wcag-aa")), (preset["id"], [r for r in check(c, "wcag-aa") if not r["ok"]])


def test_contrast_follows_wcag_and_the_level_asked():
    assert contrast("#000000", "#FFFFFF") == 21.0 and contrast("#777777", "#FFFFFF") < 4.5
    weak = concept(text="#9AA0A6")
    assert not next(r for r in check(weak, "wcag-aa") if r["pair"] == "text on background")["ok"]
    assert all(r["ok"] for r in check(weak, "none"))


def test_a_prototype_is_self_contained_and_escapes_its_text():
    c = concept(primary="#123456")
    page = render(c, c.screens[1], "mobile", "Attendance")
    assert "--primary: #123456" in page and "<script" not in page and "&lt;b&gt;x&lt;/b&gt;" in page
    assert "class='bottom'" in page and "width=390" in page  # mobile: bottom navigation at phone width


def test_the_docs_ui_files_hold_the_guide_tokens_prototypes_and_logo():
    c = concept()
    files = ui_files(c, Brand(colors=["#1F5FAD"], logo_name="logo.png"), "fits HR", "warmer accent", "Attendance",
                     ("logo.png", b"\x89PNG"))
    assert set(files) == {"docs/ui/UI.md", "docs/ui/design-tokens.json", "docs/ui/brand/logo.png",
                          "docs/ui/concepts/enterprise/sign-in.html", "docs/ui/concepts/enterprise/dashboard.html"}
    assert "Why it was chosen: fits HR" in files["docs/ui/UI.md"] and "warmer accent" in files["docs/ui/UI.md"]
    assert "../../brand/logo.png" in files["docs/ui/concepts/enterprise/dashboard.html"]


def test_brand_colors_must_be_hex():
    with pytest.raises(ValueError, match="#1F5FAD"):
        Brand(colors=["blue"])


def test_the_request_form_previews_a_default_concept_in_the_brand_typed_so_far():
    from controller.app.ui import presets, preview_concept, render
    preset = next(p for p in presets() if p["id"] == "clean-saas")
    concept = preview_concept(preset, ["#E8590C", "#123456"], ["Inter"], ["Employee Management", "Shift Management"])
    colors = concept.tokens.colors
    assert colors["primary"] == "#E8590C" and colors["accent"] == "#123456" and colors["on_primary"] == "#111827"
    assert colors["sidebar"] == "#F4F4F5"  # a preset's optional colors stay
    assert concept.tokens.font_family.startswith("'Inter', ") and concept.navigation[0] == "Employee Management"
    assert [s.name for s in concept.screens] == ["overview", "form", "sign-in", "empty"]
    page = render(concept, concept.screens[0], "mobile", "Attendance")
    assert "<script" not in page and "Attendance" in page and "Employee Management item 1" in page
