"""UI concepts of a project: the customer's brand, the platform's presets, the UX Designer's concepts, the checks
the controller runs on them, the prototypes it renders, and the files the SDS pull request adds under docs/ui/.

The UX Designer describes each concept as design tokens and screens made of a few block types; the controller renders
the prototypes from them, so every concept is built the same way and only the design differs.
"""

from html import escape
import json
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from controller.app.config import PROJECT_ROOT

PRESETS_DIR = PROJECT_ROOT / "ui-presets"
UI_DIR = "docs/ui"
HEX = re.compile(r"#[0-9A-Fa-f]{6}")
COLOR_KEYS = ("primary", "on_primary", "accent", "background", "surface", "text", "muted", "border", "success",
              "warning", "danger")
DEVICES = {"desktop": 1280, "tablet": 820, "mobile": 390, "kiosk": 1080}
MIN_CONTRAST = {"wcag-aa": 4.5, "wcag-aaa": 7.0, "none": 0.0}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Brand(Contract):
    """What the customer gives: all optional. Without anything the platform's presets are the concepts."""
    colors: list[str] = []  # #RRGGBB, primary first
    fonts: list[str] = []
    figma_url: str = ""
    logo_name: str = ""  # the uploaded logo's file name; its bytes are the run's brand_logo artifact
    devices: list[Literal["desktop", "tablet", "mobile", "kiosk"]] = ["desktop", "mobile"]
    accessibility: Literal["wcag-aa", "wcag-aaa", "none"] = "wcag-aa"
    tone: str = ""  # formal, friendly ...
    language: str = ""  # the interface language

    @field_validator("colors")
    @classmethod
    def _colors(cls, value: list[str]) -> list[str]:
        bad = [c for c in value if not HEX.fullmatch(c.strip())]
        if bad:
            raise ValueError(f"colors must look like #1F5FAD: {', '.join(bad)}")
        return [c.strip().upper() for c in value]

    def given(self) -> bool:
        return bool(self.colors or self.fonts or self.figma_url or self.logo_name)


class Tokens(Contract):
    colors: dict[str, str]
    font_family: str = Field(min_length=1)
    heading_font: str = ""
    base_size: int = Field(default=14, ge=12, le=22)
    radius: int = Field(default=8, ge=0, le=24)
    spacing: int = Field(default=8, ge=4, le=16)
    shadow: str = "none"

    @field_validator("colors")
    @classmethod
    def _palette(cls, value: dict[str, str]) -> dict[str, str]:
        missing = [k for k in COLOR_KEYS if k not in value]
        bad = [f"{k}={v}" for k, v in value.items() if not HEX.fullmatch(str(v))]
        if missing or bad:
            raise ValueError(f"colors need {', '.join(missing) or 'valid values'}" + (f"; not #RRGGBB: {', '.join(bad)}"
                                                                                      if bad else ""))
        return {k: v.upper() for k, v in value.items()}


class Block(Contract):
    """One part of a screen. type decides which other keys matter."""
    type: Literal["heading", "stats", "table", "form", "cards", "list", "chart", "alert", "empty", "actions"]
    title: str = ""
    text: str = ""
    items: list[str] = []  # stats "label: value", cards, list rows, chart "label: number", actions (buttons)
    columns: list[str] = []  # table
    rows: list[list[str]] = []  # table
    fields: list[str] = []  # form
    tone: Literal["info", "success", "warning", "danger"] = "info"  # alert


class Screen(Contract):
    name: str = Field(min_length=1)
    role: str = ""  # the SRS role that uses it
    blocks: list[Block] = Field(min_length=1)


class Concept(Contract):
    id: str = Field(pattern=r"^[a-z0-9-]{2,40}$")
    name: str = Field(min_length=1)
    preset: str = ""  # the platform preset it starts from, or empty when it follows the customer's design
    layout: Literal["sidebar", "topnav", "mobile"]
    # the kind of interface (render.TEMPLATES): workspace, console, enterprise, portal or service; empty: from the preset
    template: Literal["", "workspace", "console", "enterprise", "portal", "service"] = ""
    density: Literal["compact", "comfortable"] = "comfortable"
    rationale: str = Field(min_length=1)
    tokens: Tokens
    navigation: list[str] = Field(min_length=1)
    screens: list[Screen] = Field(min_length=2, max_length=5)


class UiConcepts(Contract):
    concepts: list[Concept] = Field(min_length=1, max_length=5)
    notes: str = ""
    feedback_replies: list[dict] = []


def presets() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(PRESETS_DIR.glob("*.json"))]


# ---------------------------------------------------------------- checks

def _luminance(color: str) -> float:
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return round((high + 0.05) / (low + 0.05), 2)


PAIRS = (("text", "background"), ("text", "surface"), ("muted", "surface"), ("on_primary", "primary"),
         ("danger", "surface"), ("primary", "surface"))


def check(concept: Concept, accessibility: str) -> list[dict]:
    """Contrast of the pairs people read, against the project's accessibility level."""
    need = MIN_CONTRAST.get(accessibility, 4.5)
    colors = concept.tokens.colors
    return [{"pair": f"{fg} on {bg}", "ratio": (ratio := contrast(colors[fg], colors[bg])), "ok": ratio >= need,
             "need": need} for fg, bg in PAIRS]


# ---------------------------------------------------------------- prototypes
#
# Optional colors a concept may add to the required ones: sidebar and on_sidebar (a dark or brand-colored sidebar).

ICON_PATHS = {  # 24x24 outline glyphs, drawn with currentColor
    "home": "M3 11l9-8 9 8M5 10v10h14V10",
    "users": "M16 19v-1a4 4 0 00-4-4H6a4 4 0 00-4 4v1M9 10a3 3 0 100-6 3 3 0 000 6zM22 19v-1a4 4 0 00-3-3.9M16 4.1a3 3 0 010 5.8",
    "camera": "M3 8h4l2-3h6l2 3h4v11H3zM12 17a4 4 0 100-8 4 4 0 000 8z",
    "clock": "M12 21a9 9 0 100-18 9 9 0 000 18zM12 7v5l3 2",
    "calendar": "M4 5h16v16H4zM4 9h16M8 3v4M16 3v4",
    "chart": "M4 20V10M10 20V4M16 20v-7M22 20H2",
    "bell": "M18 8a6 6 0 10-12 0c0 7-3 8-3 8h18s-3-1-3-8M10 20a2 2 0 004 0",
    "shield": "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
    "file": "M14 3H6v18h12V7zM14 3v4h4M9 13h6M9 17h6",
    "settings": "M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z",
    "grid": "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
    "search": "M11 18a7 7 0 100-14 7 7 0 000 14zM21 21l-5-5",
    "plus": "M12 5v14M5 12h14",
    "alert": "M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
    "check": "M20 6L9 17l-5-5",
    "map": "M9 4L3 6v14l6-2 6 2 6-2V4l-6 2zM9 4v14M15 6v14",
}
ICON_WORDS = (("employee", "users"), ("account", "users"), ("people", "users"), ("face", "camera"), ("camera", "camera"),
              ("recogni", "camera"), ("check", "clock"), ("attendance", "clock"), ("time", "clock"), ("shift", "calendar"),
              ("leave", "calendar"), ("schedule", "calendar"), ("report", "chart"), ("dashboard", "chart"),
              ("calculat", "chart"), ("notif", "bell"), ("permission", "shield"), ("user", "shield"), ("role", "shield"),
              ("audit", "file"), ("history", "file"), ("correction", "file"), ("document", "file"), ("site", "map"),
              ("location", "map"), ("setting", "settings"), ("integration", "settings"), ("overview", "home"),
              ("home", "home"))
STATUS_TONES = {"active": "success", "approved": "success", "done": "success", "present": "success", "on time": "success",
                "pending": "warning", "late": "warning", "review": "warning", "draft": "warning",
                "closed": "neutral", "inactive": "neutral", "not working": "neutral",
                "rejected": "danger", "absent": "danger", "failed": "danger", "error": "danger"}


def icon(name: str, size: int = 18) -> str:
    return (f"<svg class='ic' width='{size}' height='{size}' viewBox='0 0 24 24' fill='none' stroke='currentColor' "
            f"stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round' aria-hidden='true'>"
            f"<path d='{ICON_PATHS.get(name, ICON_PATHS['grid'])}'/></svg>")


def nav_icon(label: str) -> str:
    low = label.lower()
    return next((glyph for word, glyph in ICON_WORDS if word in low), "grid")


def _initials(text: str) -> str:
    words = [w for w in re.split(r"\s+", text.strip()) if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def _dark(color: str) -> bool:
    return _luminance(color) < 0.2


def _css(concept: Concept, width: int) -> str:
    t, c = concept.tokens, concept.tokens.colors
    compact = concept.density == "compact"
    gap = t.spacing * (1 if compact else 1.4)
    sidebar, on_sidebar = c.get("sidebar", c["surface"]), c.get("on_sidebar", c["text"])
    dark = _dark(c["background"])
    radius_small = max(t.radius - 4, 0)
    return f"""
:root {{ {' '.join(f'--{k.replace("_", "-")}: {v};' for k, v in c.items())}
  --sidebar: {sidebar}; --on-sidebar: {on_sidebar};
  --radius: {t.radius}px; --radius-sm: {radius_small}px; --gap: {gap}px; --shadow: {t.shadow};
  --tint: color-mix(in srgb, var(--primary) {'22' if dark else '10'}%, var(--surface));
  --hover: color-mix(in srgb, var(--text) {'8' if dark else '5'}%, transparent); color-scheme: {'dark' if dark else 'light'}; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; height: 100%; }}
body {{ background: var(--background); color: var(--text); font: {t.base_size}px/1.55 {t.font_family};
  -webkit-font-smoothing: antialiased; }}
h1, h2, h3 {{ font-family: {t.heading_font or t.font_family}; margin: 0; letter-spacing: -0.01em; }}
h1 {{ font-size: 1.75em; font-weight: 700; }} h2 {{ font-size: 1.05em; font-weight: 650; }}
a {{ color: var(--primary); text-decoration: none; }}
.ic {{ flex: none; }}
.app {{ display: flex; min-height: 100vh; }}
.app.stack {{ flex-direction: column; }}
.col {{ flex: 1; display: flex; flex-direction: column; min-width: 0; }}
.side {{ width: {216 if compact else 240}px; background: var(--sidebar); color: var(--on-sidebar); padding: calc(var(--gap) * 1.5) var(--gap);
  display: flex; flex-direction: column; gap: 4px; border-right: 1px solid color-mix(in srgb, var(--on-sidebar) 10%, transparent); }}
.brand {{ display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 1.05em; padding: 4px 8px calc(var(--gap) * 1.5); }}
.mark {{ width: 30px; height: 30px; border-radius: var(--radius-sm); background: var(--primary); color: var(--on-primary);
  display: grid; place-items: center; font-weight: 800; font-size: 13px; }}
.brand img {{ max-height: 30px; max-width: 120px; }}
.navlabel {{ font-size: 11px; text-transform: uppercase; letter-spacing: .08em; opacity: .55; padding: 10px 10px 4px; }}
.side a {{ display: flex; align-items: center; gap: 10px; padding: {7 if compact else 9}px 10px; border-radius: var(--radius-sm);
  color: inherit; opacity: .82; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.side a:hover {{ background: color-mix(in srgb, var(--on-sidebar) 8%, transparent); opacity: 1; }}
.side a.on {{ background: color-mix(in srgb, var(--primary) {'100' if sidebar == c['primary'] else '16'}%, transparent);
  color: {('var(--on-primary)' if sidebar == c['primary'] else 'var(--on-sidebar)')}; opacity: 1; font-weight: 650; }}
.side .foot {{ margin-top: auto; display: flex; align-items: center; gap: 10px; padding: 10px; font-size: .9em; opacity: .85; }}
.topbar {{ display: flex; align-items: center; gap: calc(var(--gap) * 1.5); padding: 12px calc(var(--gap) * 2.5);
  background: var(--surface); border-bottom: 1px solid var(--border); }}
.topbar.brandbar {{ background: var(--primary); color: var(--on-primary); border: 0; }}
.topbar .links {{ display: flex; gap: 4px; min-width: 0; overflow: hidden; }}
.topbar .links a {{ color: inherit; padding: 7px 12px; border-radius: 999px; opacity: .8; font-weight: 500; display: flex; gap: 8px;
  align-items: center; white-space: nowrap; max-width: 170px; overflow: hidden; text-overflow: ellipsis; flex: none; }}
.topbar.brandbar .mark {{ background: var(--on-primary); color: var(--primary); }}
.topbar.brandbar .brand {{ padding: 0; }}
.topbar .links a.on {{ background: color-mix(in srgb, currentColor 14%, transparent); opacity: 1; }}
.search {{ flex: 1; max-width: 360px; display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-radius: 999px;
  background: color-mix(in srgb, currentColor 6%, transparent); color: inherit; opacity: .8; font-size: .92em; }}
.spacer {{ flex: 1; }}
.iconbtn {{ width: 36px; height: 36px; border-radius: 999px; display: grid; place-items: center; position: relative; opacity: .8; }}
.iconbtn .dot {{ position: absolute; top: 8px; right: 9px; width: 7px; height: 7px; border-radius: 9px; background: var(--danger); }}
.avatar {{ width: 34px; height: 34px; border-radius: 999px; display: grid; place-items: center; font-weight: 700; font-size: 12px;
  background: color-mix(in srgb, var(--accent) 22%, var(--surface)); color: var(--text); flex: none; }}
main {{ flex: 1; padding: calc(var(--gap) * {2.5 if width > 600 else 1.5}); display: flex; flex-direction: column;
  gap: calc(var(--gap) * 2); max-width: {max(width, 1200)}px; width: 100%; min-width: 0; }}
.tablewrap {{ overflow-x: auto; }}
main > * {{ min-width: 0; }}
.crumbs {{ color: var(--muted); font-size: .88em; margin-bottom: 6px; }}
.pagehead {{ display: flex; align-items: flex-end; gap: var(--gap); flex-wrap: wrap; }}
.pagehead .sub {{ color: var(--muted); margin-top: 4px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);
  padding: calc(var(--gap) * 2); }}
.cardhead {{ display: flex; align-items: center; gap: var(--gap); margin-bottom: calc(var(--gap) * 1.5); flex-wrap: wrap; }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax({180 if width > 600 else 140}px, 1fr)); gap: calc(var(--gap) * 1.5); }}
.stat .label {{ color: var(--muted); font-size: .9em; display: flex; align-items: center; gap: 8px; }}
.stat .value {{ font-size: 1.9em; font-weight: 750; letter-spacing: -0.02em; margin: 6px 0 2px; }}
.stat .row {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
.stat .ibox {{ width: 34px; height: 34px; border-radius: var(--radius-sm); display: grid; place-items: center; background: var(--tint); color: var(--primary); }}
.delta {{ font-size: .82em; font-weight: 650; padding: 2px 8px; border-radius: 999px; }}
.up {{ color: var(--success); background: color-mix(in srgb, var(--success) 12%, transparent); }}
.down {{ color: var(--danger); background: color-mix(in srgb, var(--danger) 12%, transparent); }}
.chip {{ display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px; border-radius: 999px; border: 1px solid var(--border);
  font-size: .86em; color: var(--muted); background: var(--surface); }}
.chip.on {{ border-color: var(--primary); color: var(--primary); background: var(--tint); }}
table {{ width: 100%; border-collapse: separate; border-spacing: 0; }}
th {{ text-align: left; font-size: .8em; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); font-weight: 600;
  padding: 10px 12px; border-bottom: 1px solid var(--border); background: color-mix(in srgb, var(--text) 3%, var(--surface)); }}
th:first-child {{ border-top-left-radius: var(--radius-sm); }} th:last-child {{ border-top-right-radius: var(--radius-sm); }}
td {{ padding: {9 if compact else 12}px 12px; border-bottom: 1px solid var(--border); }}
tr:last-child td {{ border-bottom: 0; }}
tbody tr:hover td {{ background: var(--hover); }}
.who {{ display: flex; align-items: center; gap: 10px; font-weight: 600; }}
.who .avatar {{ width: 28px; height: 28px; font-size: 11px; background: var(--tint); color: var(--primary); }}
.pill {{ display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 999px; font-size: .84em; font-weight: 600; }}
.pill::before {{ content: ""; width: 6px; height: 6px; border-radius: 6px; background: currentColor; }}
.pill.success {{ color: var(--success); background: color-mix(in srgb, var(--success) 12%, transparent); }}
.pill.warning {{ color: var(--warning); background: color-mix(in srgb, var(--warning) 14%, transparent); }}
.pill.danger {{ color: var(--danger); background: color-mix(in srgb, var(--danger) 12%, transparent); }}
.pill.neutral {{ color: var(--muted); background: color-mix(in srgb, var(--muted) 12%, transparent); }}
.muted {{ color: var(--muted); }}
form .fields {{ display: grid; grid-template-columns: repeat(auto-fit, minmax({260 if width > 600 else 200}px, 1fr)); gap: calc(var(--gap) * 1.5); }}
label {{ display: flex; flex-direction: column; gap: 6px; font-weight: 600; font-size: .92em; }}
input {{ padding: {9 if compact else 11}px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); font: inherit; font-weight: 400;
  background: var(--surface); color: var(--text); outline: none; }}
input.focus {{ border-color: var(--primary); box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 22%, transparent); }}
input.invalid {{ border-color: var(--danger); }}
.hint {{ font-weight: 400; font-size: .85em; color: var(--danger); }}
.buttons {{ display: flex; gap: var(--gap); margin-top: calc(var(--gap) * 2); flex-wrap: wrap; }}
button {{ font: inherit; font-weight: 600; padding: {8 if compact else 10}px 16px; border-radius: var(--radius-sm); border: 1px solid transparent;
  background: var(--primary); color: var(--on-primary); cursor: pointer; display: inline-flex; align-items: center; gap: 8px;
  box-shadow: 0 1px 2px color-mix(in srgb, var(--primary) 30%, transparent); }}
button.ghost {{ background: var(--surface); color: var(--text); border-color: var(--border); box-shadow: none; }}
.alert {{ display: flex; gap: 12px; align-items: flex-start; padding: 14px 16px; border-radius: var(--radius); border: 1px solid; }}
.alert.info {{ color: var(--text); border-color: color-mix(in srgb, var(--primary) 35%, transparent); background: var(--tint); }}
.alert.info .ic {{ color: var(--primary); }}
.alert.success {{ border-color: color-mix(in srgb, var(--success) 40%, transparent); background: color-mix(in srgb, var(--success) 9%, var(--surface)); }}
.alert.warning {{ border-color: color-mix(in srgb, var(--warning) 45%, transparent); background: color-mix(in srgb, var(--warning) 10%, var(--surface)); }}
.alert.danger {{ border-color: color-mix(in srgb, var(--danger) 40%, transparent); background: color-mix(in srgb, var(--danger) 8%, var(--surface)); }}
.alert.danger .ic, .alert.danger b {{ color: var(--danger); }}
.alert p {{ margin: 2px 0 0; color: var(--muted); }}
.empty {{ text-align: center; padding: calc(var(--gap) * 5) var(--gap); display: flex; flex-direction: column; align-items: center; gap: 10px; }}
.empty p {{ margin: 0; color: var(--muted); max-width: 380px; }}
.bars {{ display: flex; align-items: flex-end; gap: 14px; height: 180px; padding-top: 10px;
  background: repeating-linear-gradient(to top, var(--border) 0 1px, transparent 1px 45px); }}
.bars div {{ flex: 1; display: flex; flex-direction: column; align-items: center; gap: 6px; height: 100%; justify-content: flex-end; }}
.bars span.b {{ width: 70%; max-width: 46px; border-radius: var(--radius-sm) var(--radius-sm) 2px 2px;
  background: linear-gradient(180deg, var(--primary), color-mix(in srgb, var(--primary) 55%, var(--accent))); }}
.bars small {{ color: var(--muted); }}
.list li {{ display: flex; align-items: center; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }}
.list {{ list-style: none; margin: 0; padding: 0; }} .list li:last-child {{ border: 0; }}
.bottom {{ position: sticky; bottom: 0; display: flex; justify-content: space-around; background: var(--surface);
  border-top: 1px solid var(--border); padding: 8px 4px 12px; margin-top: auto; }}
.bottom a {{ color: var(--muted); display: flex; flex-direction: column; align-items: center; gap: 3px; font-size: 11px; font-weight: 600; }}
.bottom a.on {{ color: var(--primary); }}
.auth {{ display: grid; grid-template-columns: {'1fr 1fr' if width > 700 else '1fr'}; min-height: 100vh; }}
.auth .hero {{ background: linear-gradient(140deg, var(--primary), color-mix(in srgb, var(--primary) 45%, var(--accent)));
  color: var(--on-primary); padding: 48px; display: {'flex' if width > 700 else 'none'}; flex-direction: column; justify-content: space-between; }}
.auth .hero h1 {{ font-size: 2.2em; max-width: 420px; line-height: 1.15; }}
.auth .hero p {{ opacity: .85; max-width: 380px; }}
.auth .panel {{ display: grid; place-items: center; padding: 32px; }}
.auth .box {{ width: 100%; max-width: 380px; display: flex; flex-direction: column; gap: 18px; }}
.auth form .fields {{ grid-template-columns: 1fr; }}
.auth button {{ width: 100%; justify-content: center; }}
{'.grid { grid-template-columns: 1fr 1fr; gap: 10px; } .stat .spark, .cardhead .chip, .cardhead button, .search { display: none; }'
 ' .stat .value { font-size: 1.5em; } td, th { padding: 8px; } .topbar { padding: 10px 14px; }' if width <= 600 else ''}
"""


def _status(value: str) -> str | None:
    return STATUS_TONES.get(value.strip().lower())


def _block(block: Block, first: bool = False) -> str:
    title = f"<h2>{escape(block.title)}</h2>" if block.title else ""
    text = f"<p class='muted' style='margin:4px 0 0'>{escape(block.text)}</p>" if block.text else ""
    if block.type == "heading":
        return (f"<div class='pagehead'><div><div class='crumbs'>Home / {escape(block.title)}</div><h1>{escape(block.title)}</h1>"
                f"{'<div class=sub>' + escape(block.text) + '</div>' if block.text else ''}</div></div>")
    if block.type == "stats":
        cells = []
        for n, item in enumerate(block.items):
            label, _, value = item.partition(":")
            up = n % 3 != 2
            delta = f"<span class='delta {'up' if up else 'down'}'>{'+' if up else '−'}{(n * 7 + 4) % 19 + 2}%</span>"
            import math
            trend = [(x if up else 10 - x) * 1.1 + 4 * math.sin(x * (1.3 + n * 0.7) + n) for x in range(11)]
            low, high = min(trend), max(trend)
            points = " ".join(f"{x * 12},{27 - (v - low) / ((high - low) or 1) * 24:.1f}" for x, v in enumerate(trend))
            spark = (f"<svg class='spark' width='120' height='30' viewBox='0 0 120 30' aria-hidden='true'><polyline points='{points}' "
                     f"fill='none' stroke='var(--primary)' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'/></svg>")
            cells.append(f"<div class='card stat'><div class='row'><span class='label'>{escape(label.strip())}</span>"
                         f"<span class='ibox'>{icon(nav_icon(label))}</span></div><div class='value'>{escape(value.strip())}</div>"
                         f"<div class='row'>{delta}{spark}</div></div>")
        return f"{title}<div class='grid'>{''.join(cells)}</div>"
    if block.type == "table":
        head = "".join(f"<th>{escape(c)}</th>" for c in block.columns)
        rows = []
        for row in block.rows:
            cells = []
            for i, value in enumerate(row):
                tone = _status(value)
                if i == 0:
                    cells.append(f"<td><span class='who'><span class='avatar'>{escape(_initials(value))}</span>{escape(value)}</span></td>")
                elif tone:
                    cells.append(f"<td><span class='pill {tone}'>{escape(value)}</span></td>")
                else:
                    cells.append(f"<td>{escape(value)}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        chips = "<span class='chip on'>All</span><span class='chip'>Active</span><span class='chip'>Pending</span>"
        return (f"<div class='card'><div class='cardhead'>{title}<span class='spacer'></span>{chips}"
                f"<button class='ghost'>{icon('search', 16)}Filter</button></div>{text}"
                f"<div class='tablewrap'><table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div></div>")
    if block.type == "form":
        fields = []
        for n, f in enumerate(block.fields):
            kind = "password" if "password" in f.lower() else "email" if "email" in f.lower() else "text"
            fields.append(f"<label>{escape(f)}<input type='{kind}' aria-label='{escape(f)}' class='{'focus' if n == 0 else ''}'"
                          f" placeholder='{escape(f)}'></label>")
        buttons = "".join(f"<button{' class=ghost' if n else ''}>{escape(b)}</button>" for n, b in enumerate(block.items or ["Save"]))
        return (f"<div class='card'>{('<div class=cardhead>' + title + '</div>') if title else ''}{text}"
                f"<form onsubmit='return false'><div class='fields'>{''.join(fields)}</div><div class='buttons'>{buttons}</div></form></div>")
    if block.type == "cards":
        cards = "".join(f"<div class='card'><span class='ibox stat'></span><b>{escape(i)}</b></div>" for i in block.items)
        return f"{title}<div class='grid'>{cards}</div>"
    if block.type == "list":
        rows = "".join(f"<li><span class='avatar'>{escape(_initials(i))}</span><span>{escape(i)}</span></li>" for i in block.items)
        return f"<div class='card'><div class='cardhead'>{title}</div><ul class='list'>{rows}</ul></div>"
    if block.type == "chart":
        values = [(i.split(":", 1)[0], float(re.sub(r"[^0-9.]", "", i.split(":", 1)[1]) or 0) if ":" in i else 0)
                  for i in block.items]
        top = max([v for _, v in values] or [1]) or 1
        bars = "".join(f"<div><span class='b' style='height:{max(v / top * 100, 4):.0f}%' title='{escape(label)}: {v:g}'></span>"
                       f"<small>{escape(label)}</small></div>" for label, v in values)
        return f"<div class='card'><div class='cardhead'>{title}<span class='spacer'></span><span class='chip on'>This week</span></div><div class='bars'>{bars}</div></div>"
    if block.type == "alert":
        glyph = {"success": "check", "info": "bell"}.get(block.tone, "alert")
        return (f"<div class='alert {block.tone}' role='status'>{icon(glyph, 20)}<div><b>{escape(block.title)}</b>"
                f"{'<p>' + escape(block.text) + '</p>' if block.text else ''}</div></div>")
    if block.type == "empty":
        art = ("<svg width='120' height='96' viewBox='0 0 120 96' aria-hidden='true'><circle cx='60' cy='48' r='44' fill='var(--tint)'/>"
               "<rect x='36' y='26' width='48' height='52' rx='6' fill='var(--surface)' stroke='var(--border)' stroke-width='2'/>"
               "<rect x='44' y='38' width='32' height='5' rx='2.5' fill='var(--primary)' opacity='.55'/>"
               "<rect x='44' y='50' width='24' height='5' rx='2.5' fill='var(--border)'/><rect x='44' y='62' width='28' height='5' rx='2.5' fill='var(--border)'/>"
               "<circle cx='86' cy='70' r='12' fill='var(--primary)'/><path d='M86 64v12M80 70h12' stroke='var(--on-primary)' stroke-width='2.5' stroke-linecap='round'/></svg>")
        return f"<div class='card empty'>{art}<h2>{escape(block.title or 'Nothing here yet')}</h2>{'<p>' + escape(block.text) + '</p>' if block.text else ''}</div>"
    buttons = "".join(f"<button{' class=ghost' if n else ''}>{icon('plus', 16) if n == 0 else ''}{escape(b)}</button>"
                      for n, b in enumerate(block.items))
    return f"<div class='buttons' style='margin:0'>{title}{buttons}</div>"


def _is_sign_in(screen: Screen) -> bool:
    fields = [f.lower() for b in screen.blocks for f in b.fields]
    return any("password" in f for f in fields) and len(screen.blocks) <= 4


# Five page templates: the structure of the app (navigation, page header, how numbers, records, forms, sign-in and
# empty states are laid out). Tokens give the look; the template gives the kind of interface.
TEMPLATES = ("workspace", "console", "enterprise", "portal", "service")
PRESET_TEMPLATES = {"clean-saas": "workspace", "midnight-console": "console", "ocean-enterprise": "enterprise",
                    "warm-friendly": "portal", "clear-public": "service"}


def template_of(concept: Concept) -> str:
    if concept.template:
        return concept.template
    return PRESET_TEMPLATES.get(concept.preset) or ("portal" if concept.layout == "topnav" else "workspace")


def _nav_links(concept: Concept, size: int = 18, limit: int | None = None, labels: bool = True) -> str:
    items = concept.navigation[:limit] if limit else concept.navigation
    return "".join(f"<a href='#' class='{'on' if n == 0 else ''}' title='{escape(item)}'>{icon(nav_icon(item), size)}"
                   f"{'<span>' + escape(item) + '</span>' if labels else ''}</a>" for n, item in enumerate(items))


def _short_labels(navigation: list[str], limit: int = 4) -> list[str]:
    shorts: list[str] = []
    for item in navigation[:limit]:  # one word each, never the same twice (Face Enrollment, Face Recognition)
        words = [w for w in re.split(r"[\s/&-]+", item) if w]
        shorts.append(next((w for w in words if w not in shorts), item)[:12])
    return shorts


def _bottom_nav(concept: Concept) -> str:
    return "<nav class='bottom'>" + "".join(
        f"<a href='#' class='{'on' if n == 0 else ''}'>{icon(nav_icon(item), 22)}{escape(short)}</a>"
        for n, (item, short) in enumerate(zip(concept.navigation[:4], _short_labels(concept.navigation)))) + "</nav>"


def _stats_variant(block: Block, template: str) -> str:
    items = [(i.partition(":")[0].strip(), i.partition(":")[2].strip()) for i in block.items]
    if template == "workspace":  # one strip of KPIs split by rules
        cells = "".join(f"<div class='kpi'><span class='muted'>{escape(k)}</span><b>{escape(v)}</b>"
                        f"<span class='delta {'up' if n % 3 != 2 else 'down'}'>{'+' if n % 3 != 2 else '−'}{(n * 7 + 4) % 19 + 2}%</span></div>"
                        for n, (k, v) in enumerate(items))
        return f"<div class='card kpis'>{cells}</div>"
    if template == "console":  # compact tiles, mono numbers, status bar
        cells = "".join(f"<div class='tile'><div class='muted tiny'>{escape(k).upper()}</div><div class='mono big'>{escape(v)}</div>"
                        f"<div class='meter'><span style='width:{30 + (n * 23) % 60}%'></span></div></div>" for n, (k, v) in enumerate(items))
        return f"<div class='grid'>{cells}</div>"
    if template == "enterprise":  # cards with a colored edge
        tones = ("primary", "accent", "warning", "success")
        cells = "".join(f"<div class='card edge' style='border-left-color:var(--{tones[n % 4]})'><span class='muted'>{escape(k)}</span>"
                        f"<b class='num'>{escape(v)}</b><small class='muted'>vs last week {'+' if n % 3 != 2 else '−'}{(n * 7 + 4) % 19 + 2}%</small></div>"
                        for n, (k, v) in enumerate(items))
        return f"<div class='grid'>{cells}</div>"
    if template == "portal":  # pastel tiles with round icons
        tones = ("primary", "accent", "success", "warning")
        cells = "".join(f"<div class='pastel' style='--tone:var(--{tones[n % 4]})'><span class='round'>{icon(nav_icon(k), 22)}</span>"
                        f"<div><b class='num'>{escape(v)}</b><div class='muted'>{escape(k)}</div></div></div>" for n, (k, v) in enumerate(items))
        return f"<div class='grid'>{cells}</div>"
    rows = "".join(f"<div class='summary-row'><dt>{escape(k)}</dt><dd>{escape(v)}</dd><a href='#'>View</a></div>" for k, v in items)
    return f"<h2>At a glance</h2><dl class='summary'>{rows}</dl>"


def _table_variant(block: Block, template: str) -> str:
    if template == "portal":  # records as cards
        cards = []
        for row in block.rows:
            tone = next((_status(v) for v in row if _status(v)), None)
            status = next((v for v in row if _status(v)), "")
            meta = " · ".join(escape(v) for v in row[1:] if v != status)
            pill = f"<span class='pill {tone}'>{escape(status)}</span>" if tone else ""
            cards.append(f"<div class='rec'><span class='avatar'>{escape(_initials(row[0]))}</span><div class='grow'><b>{escape(row[0])}</b>"
                         f"<div class='muted'>{meta}</div></div>{pill}"
                         f"<span class='chev'>›</span></div>")
        return f"<div class='card'><div class='cardhead'><h2>{escape(block.title)}</h2><span class='spacer'></span><a href='#'>See all</a></div>{''.join(cards)}</div>"
    head = "".join(f"<th>{escape(c)}</th>" for c in block.columns)
    rows = []
    for r, row in enumerate(block.rows):
        cells = []
        for i, value in enumerate(row):
            tone = _status(value)
            if i == 0 and template == "console":
                cells.append(f"<td class='mono'>#{1040 + r} <span class='dim'>{escape(value)}</span></td>")
            elif i == 0 and template != "service":
                cells.append(f"<td><span class='who'><span class='avatar'>{escape(_initials(value))}</span>{escape(value)}</span></td>")
            elif tone and template == "console":
                cells.append(f"<td><span class='dotted {tone}'>{escape(value).lower()}</span></td>")
            elif tone and template == "service":
                cells.append(f"<td><strong class='tag {tone}'>{escape(value).upper()}</strong></td>")
            elif tone:
                cells.append(f"<td><span class='pill {tone}'>{escape(value)}</span></td>")
            else:
                cells.append(f"<td>{escape(value)}</td>")
        check = "<td class='cb'><input type='checkbox' aria-label='select'></td>" if template == "enterprise" else ""
        rows.append(f"<tr>{check}{''.join(cells)}</tr>")
    checkhead = "<th class='cb'><input type='checkbox' aria-label='select all'></th>" if template == "enterprise" else ""
    table = f"<div class='tablewrap'><table><thead><tr>{checkhead}{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
    if template == "service":
        return f"<h2>{escape(block.title)}</h2>{table}"
    if template == "enterprise":
        filters = ("<div class='filters'><span class='select'>Status: All ▾</span><span class='select'>Owner: Anyone ▾</span>"
                   f"<span class='select'>21–23 Oct 2026 ▾</span><span class='spacer'></span><button class='ghost'>Export</button>"
                   f"<button>{icon('plus', 16)}New</button></div>")
        pager = ("<div class='pager'><span class='muted'>Showing 1–5 of 128</span><span class='spacer'></span>"
                 "<span class='pg'>‹</span><span class='pg on'>1</span><span class='pg'>2</span><span class='pg'>3</span><span class='pg'>›</span></div>")
        return f"<div class='card flush'><div class='cardhead pad'><h2>{escape(block.title)}</h2></div>{filters}{table}{pager}</div>"
    if template == "console":
        return (f"<div class='card'><div class='cardhead'><h2>{escape(block.title)}</h2><span class='spacer'></span>"
                f"<span class='kbd'>F</span><span class='muted tiny'>filter</span></div>{table}</div>")
    chips = "<span class='chip on'>All</span><span class='chip'>Active</span><span class='chip'>Pending</span>"
    return (f"<div class='card'><div class='cardhead'><h2>{escape(block.title)}</h2><span class='spacer'></span>{chips}"
            f"<button class='ghost'>{icon('search', 16)}Filter</button></div>{table}</div>")


def _form_variant(block: Block, template: str) -> str:
    fields = []
    for n, f in enumerate(block.fields):
        kind = "password" if "password" in f.lower() else "email" if "email" in f.lower() else "text"
        low = f.lower()
        said = ("Use the email address you sign in to work with" if "email" in low else "" if "password" in low
                else "For example, Lan Nguyen" if "name" in low or "owner" in low else "For example, 27 3 2026" if "date" in low
                else "Optional" if "note" in low else "")
        hint = f"<span class='fieldhint'>{said}</span>" if template == "service" and said else ""
        cls = "focus" if n == 0 else ""
        if template == "enterprise":
            fields.append(f"<label class='row'><span>{escape(f)}</span><input type='{kind}' class='{cls}' placeholder='{escape(f)}'></label>")
        else:
            fields.append(f"<label>{escape(f)}{hint}<input type='{kind}' class='{cls}' placeholder='{'' if template == 'service' else escape(f)}'></label>")
    buttons = "".join(f"<button{' class=ghost' if n else ''}>{escape(b)}</button>" for n, b in enumerate(block.items or ["Save"]))
    body = f"<form onsubmit='return false'><div class='fields'>{''.join(fields)}</div><div class='buttons'>{buttons}</div></form>"
    if template == "service":
        return body
    if template == "workspace":
        return (f"<div class='card'><div class='section'><div><h2>Details</h2><p class='muted'>What people see in lists and "
                f"reports.</p></div>{body}</div></div>")
    return f"<div class='card'>{body}</div>"


def _empty_variant(block: Block, template: str) -> str:
    if template == "service":
        return (f"<h2>{escape(block.title)}</h2><p>{escape(block.text)}</p>")
    if template == "console":
        return (f"<div class='card empty'><div class='mono dim'>$ list --all</div><div class='mono'>0 results</div>"
                f"<h2>{escape(block.title)}</h2><p>{escape(block.text)}</p></div>")
    return _block(block)


def _alert_variant(block: Block, template: str) -> str:
    if template == "service" and block.tone == "danger":
        return (f"<div class='errsum' role='alert'><h2>There is a problem</h2><a href='#'>{escape(block.text or block.title)}</a></div>")
    return _block(block)


def _blocks(screen: Screen, template: str) -> str:
    out = []
    for n, block in enumerate(screen.blocks):
        if block.type == "stats":
            out.append(_stats_variant(block, template))
        elif block.type == "table":
            out.append(_table_variant(block, template))
        elif block.type == "form":
            out.append(_form_variant(block, template))
        elif block.type == "empty":
            out.append(_empty_variant(block, template))
        elif block.type == "alert":
            out.append(_alert_variant(block, template))
        elif block.type == "heading" and n == 0:
            out.append(_heading_variant(block, template))
        else:
            out.append(_block(block))
    return "".join(out)


def _heading_variant(block: Block, template: str) -> str:
    title, text = escape(block.title), escape(block.text)
    if template == "workspace":
        return (f"<div class='pagehead'><div><h1>{title}</h1>{'<div class=sub>' + text + '</div>' if text else ''}</div>"
                f"<span class='spacer'></span><button class='ghost'>Share</button><button>{icon('plus', 16)}New</button></div>"
                f"<div class='tabs'><a class='on'>Overview</a><a>Activity</a><a>Reports</a><a>Settings</a></div>")
    if template == "console":
        return (f"<div class='pagehead'><div><div class='mono dim tiny'>~/{slug(block.title)}</div><h1>{title}</h1></div>"
                f"<span class='spacer'></span><span class='live'><i></i>Live</span></div>")
    if template == "enterprise":
        return (f"<div class='pagehead'><div><div class='crumbs'>Home › Workspace › {title}</div><h1>{title}</h1>"
                f"{'<div class=sub>' + text + '</div>' if text else ''}</div></div>"
                f"<div class='tabs'><a class='on'>All records</a><a>Assigned to me</a><a>Archived</a></div>")
    if template == "portal":
        return (f"<div class='hero'><div><div class='hello'>Good morning, Lan 👋</div><h1>{title}</h1>"
                f"{'<p>' + text + '</p>' if text else ''}</div><button class='light'>{icon('plus', 16)}Quick action</button></div>")
    return f"<span class='caption'>Workspace</span><h1 class='xl'>{title}</h1>{'<p class=lead>' + text + '</p>' if text else ''}"


def _sign_in(concept: Concept, screen: Screen, template: str, name: str, mark: str) -> str:
    form = "".join(_form_variant(b, template) if b.type == "form" else _alert_variant(b, template)
                   for b in screen.blocks if b.type in ("form", "alert"))
    form = form.replace("<div class='card'>", "<div>", 1)
    if template == "workspace":
        return (f"<div class='center'><div class='authcard'><div class='brand'>{mark}<span>{name}</span></div><h1>Sign in</h1>"
                f"<p class='muted'>Welcome back. Enter your details.</p>{form}<div class='or'><span>or</span></div>"
                f"<button class='ghost wide'>{icon('shield', 16)}Continue with SSO</button></div></div>")
    if template == "console":
        return (f"<div class='center glow'><div class='authcard glass'><div class='brand'>{mark}<span>{name}</span></div>"
                f"<h1>Welcome back</h1><p class='muted'>Sign in to the control room.</p>{form}"
                f"<p class='muted tiny'>Protected by single sign-on · v2.4</p></div></div>")
    if template == "portal":
        art = ("<svg width='220' height='120' viewBox='0 0 220 120' aria-hidden='true'><circle cx='60' cy='60' r='50' fill='var(--tint)'/>"
               "<circle cx='160' cy='50' r='34' fill='color-mix(in srgb, var(--accent) 22%, var(--surface))'/>"
               "<rect x='70' y='40' width='80' height='60' rx='14' fill='var(--surface)' stroke='var(--border)' stroke-width='2'/>"
               "<circle cx='110' cy='62' r='10' fill='var(--primary)'/><rect x='92' y='80' width='36' height='8' rx='4' fill='var(--border)'/></svg>")
        return (f"<div class='center warm'><div class='authcard soft'>{art}<h1>Hi there!</h1>"
                f"<p class='muted'>Sign in to {name} with your work email.</p>{form}</div></div>")
    if template == "service":
        return (f"<header class='gov'><div class='wrap'><b>{name}</b></div></header><div class='phase'><div class='wrap'>"
                f"<strong class='tag'>BETA</strong> This is a new service. Your feedback will help us improve it.</div></div>"
                f"<main class='narrow'><h1 class='xl'>Sign in to {name}</h1>{form}<p><a href='#'>Problems signing in</a></p></main>")
    return (f"<div class='auth'><div class='hero2'><div class='brand'>{mark}<span>{name}</span></div><div><h1>Welcome back to {name}</h1>"
            f"<p>Everything your team needs, in one place. Sign in with the account your administrator gave you.</p></div>"
            f"<small>© {name}</small></div><div class='panel'><div class='box'><div><h1>Sign in</h1>"
            f"<p class='muted' style='margin:6px 0 0'>Enter your details to continue.</p></div>{form}</div></div></div>")


def render(concept: Concept, screen: Screen, device: str = "desktop", product: str = "", logo: str = "") -> str:
    """One prototype screen, self-contained (no script, no external request), in the concept's tokens and template."""
    width = DEVICES.get(device, 1280)
    template = template_of(concept)
    mobile = device == "mobile"
    name = escape(product or concept.name)
    mark = f"<img src='{escape(logo)}' alt=''>" if logo else f"<span class='mark'>{escape(_initials(product or concept.name))}</span>"
    brand = f"<div class='brand'>{mark}<span>{name}</span></div>"
    if _is_sign_in(screen):
        return _page(concept, screen, width, _sign_in(concept, screen, template, name, mark), template)
    body = _blocks(screen, template)
    tools = f"<span class='iconbtn'>{icon('bell')}<span class='dot'></span></span><span class='avatar'>LN</span>"
    if mobile:
        frame = (f"<div class='app stack'><header class='topbar'>{brand}<span class='spacer'></span>{tools}</header>"
                 f"<main>{body}</main>{_bottom_nav(concept)}</div>")
    elif template == "workspace":
        frame = (f"<div class='app'><aside class='side'>{brand}<div class='navlabel'>Workspace</div>{_nav_links(concept, limit=4)}"
                 f"<div class='navlabel'>More</div>{_nav_links_rest(concept, 4)}<div class='foot'><span class='avatar'>LN</span>"
                 f"<span>Lan Nguyen<br><small class='muted'>HR Admin</small></span></div></aside>"
                 f"<div class='col'><header class='topbar'><span class='search'>{icon('search', 16)}Search…<span class='kbd'>⌘K</span></span>"
                 f"<span class='spacer'></span>{tools}</header><main>{body}</main></div></div>")
    elif template == "console":
        frame = (f"<div class='app'><aside class='rail'>{mark}{_nav_links(concept, 20, labels=False)}<span class='spacer'></span>"
                 f"{icon('settings', 20)}</aside><div class='col'><header class='topbar'><b>{name}</b><span class='dim'>/</span>"
                 f"<span>{escape(concept.navigation[0])}</span><span class='spacer'></span><span class='cmd'>{icon('search', 15)}"
                 f"Type a command<span class='kbd'>⌘K</span></span>{tools}</header><main>{body}</main></div></div>")
    elif template == "enterprise":
        frame = (f"<div class='app'><aside class='side'>{brand}{_nav_links(concept)}<div class='foot'>{icon('settings')}"
                 f"<span>Administration</span></div></aside><div class='col'><header class='topbar'>"
                 f"<span class='search'>{icon('search', 16)}Search records, people, settings</span><span class='spacer'></span>"
                 f"<span class='muted'>Head office ▾</span>{tools}</header><main>{body}</main></div></div>")
    elif template == "portal":
        frame = (f"<div class='app stack'><header class='topbar'>{brand}<nav class='pills'>{_nav_links(concept, 16, limit=4)}</nav>"
                 f"<span class='spacer'></span>{tools}</header><main class='centered'>{body}</main></div>")
    else:
        links = "".join(f"<a href='#' class='{'on' if n == 0 else ''}'>{escape(item)}</a>" for n, item in enumerate(concept.navigation[:4]))
        frame = (f"<header class='gov'><div class='wrap'><b>{name}</b><span class='spacer'></span><a href='#'>Sign out</a></div></header>"
                 f"<div class='phase'><div class='wrap'><strong class='tag'>BETA</strong> This is a new service. Your feedback will "
                 f"help us improve it.</div></div><nav class='govnav'><div class='wrap'>{links}</div></nav>"
                 f"<main class='narrow'>{body}</main><footer class='govfoot'><div class='wrap'>Help · Cookies · Accessibility "
                 f"statement · Contact</div></footer>")
    return _page(concept, screen, width, frame, template)


def _nav_links_rest(concept: Concept, start: int) -> str:
    rest = concept.navigation[start:] or ["Reports", "Settings"]
    return "".join(f"<a href='#' title='{escape(item)}'>{icon(nav_icon(item))}<span>{escape(item)}</span></a>" for item in rest)


def _template_css(template: str, width: int) -> str:
    wide = width > 600
    common = """
.kbd { font: 11px/1 ui-monospace, Menlo, monospace; padding: 3px 6px; border-radius: 5px; border: 1px solid var(--border); color: var(--muted); margin-left: auto; }
.mono { font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace; } .tiny { font-size: 11px; } .dim { opacity: .55; }
.tabs { display: flex; gap: 22px; border-bottom: 1px solid var(--border); margin-top: -6px; }
.tabs a { padding: 10px 0; color: var(--muted); font-weight: 600; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tabs a.on { color: var(--text); border-color: var(--primary); }
.center { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
.authcard { width: 100%; max-width: 400px; display: flex; flex-direction: column; gap: 14px; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius); padding: 32px; box-shadow: 0 12px 40px rgba(0,0,0,.08); }
.authcard .brand { padding: 0; } .authcard form .fields { grid-template-columns: 1fr; } .authcard button { width: 100%; justify-content: center; }
.authcard h1 { font-size: 1.5em; } .authcard p { margin: 0; }
.or { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: .85em; }
.or::before, .or::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.wide { width: 100%; justify-content: center; }
.auth .hero2 { background: linear-gradient(140deg, var(--primary), color-mix(in srgb, var(--primary) 45%, var(--accent)));
  color: var(--on-primary); padding: 48px; display: flex; flex-direction: column; justify-content: space-between; }
.auth .hero2 h1 { font-size: 2.2em; max-width: 420px; line-height: 1.15; } .auth .hero2 p { opacity: .85; max-width: 380px; }
.auth .hero2 .mark { background: var(--on-primary); color: var(--primary); }
"""
    css = {
        "workspace": """
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); padding: 0; }
.kpi { padding: 20px 24px; display: flex; flex-direction: column; gap: 4px; border-left: 1px solid var(--border); }
.kpi:first-child { border-left: 0; } .kpi b { font-size: 1.9em; letter-spacing: -0.03em; } .kpi .delta { align-self: flex-start; }
.section { display: grid; grid-template-columns: 240px 1fr; gap: 32px; } .section p { margin: 4px 0 0; }
.side a.on { background: var(--surface); box-shadow: 0 1px 2px rgba(0,0,0,.06); color: var(--text); }
.search { border: 1px solid var(--border); background: var(--surface); min-width: 280px; }
""",
        "console": """
.rail { width: 64px; background: var(--sidebar); display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 14px 0;
  border-right: 1px solid var(--border); color: var(--on-sidebar); }
.rail a { width: 42px; height: 42px; display: grid; place-items: center; border-radius: 10px; color: inherit; opacity: .7; }
.rail a.on { background: color-mix(in srgb, var(--primary) 22%, transparent); color: var(--primary); opacity: 1;
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--primary) 40%, transparent), 0 0 18px color-mix(in srgb, var(--primary) 35%, transparent); }
.rail .mark { margin-bottom: 14px; }
.topbar { gap: 10px; } .cmd { display: flex; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid var(--border);
  border-radius: 8px; color: var(--muted); min-width: 260px; font-size: .9em; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px 16px; }
.tile .big { font-size: 2em; font-weight: 700; margin: 4px 0 10px; color: var(--text); }
.meter { height: 4px; border-radius: 4px; background: var(--border); } .meter span { display: block; height: 100%; border-radius: 4px;
  background: linear-gradient(90deg, var(--primary), var(--accent)); box-shadow: 0 0 10px var(--accent); }
td { font-size: .92em; } .dotted::before { content: "● "; } .dotted.success { color: var(--success); } .dotted.warning { color: var(--warning); }
.dotted.neutral { color: var(--muted); } .dotted.danger { color: var(--danger); }
.live { display: inline-flex; align-items: center; gap: 8px; font-size: .85em; color: var(--success); }
.live i { width: 8px; height: 8px; border-radius: 8px; background: var(--success); box-shadow: 0 0 10px var(--success); }
.glow { background: radial-gradient(600px 400px at 50% 30%, color-mix(in srgb, var(--primary) 35%, transparent), transparent 70%), var(--background); }
.glass { background: color-mix(in srgb, var(--surface) 75%, transparent); backdrop-filter: blur(10px);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--primary) 25%, transparent), 0 30px 80px rgba(0,0,0,.5); }
""",
        "enterprise": """
.edge { border-left: 4px solid var(--primary); display: flex; flex-direction: column; gap: 4px; } .edge .num { font-size: 1.7em; }
.card.flush { padding: 0; overflow: hidden; } .pad { padding: 18px 20px 0; }
.filters { display: flex; gap: 8px; align-items: center; padding: 12px 20px; border-bottom: 1px solid var(--border); flex-wrap: wrap; }
.select { padding: 7px 12px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); font-size: .9em; }
th { border-radius: 0 !important; } .cb { width: 36px; } tbody tr:nth-child(even) td { background: color-mix(in srgb, var(--text) 2.5%, var(--surface)); }
td:first-child, th:first-child { padding-left: 20px; }
.pager { display: flex; align-items: center; gap: 6px; padding: 12px 20px; border-top: 1px solid var(--border); }
.pg { min-width: 30px; height: 30px; display: grid; place-items: center; border: 1px solid var(--border); border-radius: var(--radius-sm); }
.pg.on { background: var(--primary); color: var(--on-primary); border-color: var(--primary); }
label.row { display: grid; grid-template-columns: 160px 1fr; align-items: center; } form .fields:has(label.row) { grid-template-columns: 1fr; }
""",
        "portal": """
.topbar { background: var(--surface); padding: 14px 32px; }
.pills { display: flex; gap: 4px; padding: 4px; border-radius: 999px; background: color-mix(in srgb, var(--primary) 7%, var(--surface)); margin-left: 24px; }
.pills a { display: flex; align-items: center; gap: 8px; padding: 8px 16px; border-radius: 999px; color: var(--muted); font-weight: 600;
  white-space: nowrap; max-width: 170px; overflow: hidden; text-overflow: ellipsis; flex: none; }
.pills { min-width: 0; overflow: hidden; }
.pills a.on { background: var(--surface); color: var(--primary); box-shadow: 0 2px 8px rgba(0,0,0,.06); }
main.centered { margin: 0 auto; max-width: 1080px; }
.hero { display: flex; align-items: center; gap: 24px; padding: 28px 32px; border-radius: calc(var(--radius) + 8px); color: var(--on-primary);
  background: linear-gradient(120deg, var(--primary), color-mix(in srgb, var(--primary) 50%, var(--accent))); }
.hero h1 { font-size: 2em; } .hero p { margin: 6px 0 0; opacity: .9; } .hero > div { flex: 1; } .hello { opacity: .9; margin-bottom: 6px; }
button.light { background: var(--surface); color: var(--primary); }
.pastel { display: flex; align-items: center; gap: 16px; padding: 22px; border-radius: var(--radius);
  background: color-mix(in srgb, var(--tone) 9%, var(--surface)); border: 1px solid color-mix(in srgb, var(--tone) 18%, transparent); }
.round { width: 52px; height: 52px; border-radius: 999px; display: grid; place-items: center; background: color-mix(in srgb, var(--tone) 18%, var(--surface)); color: var(--tone); }
.pastel .num { font-size: 1.8em; display: block; }
.rec { display: flex; align-items: center; gap: 14px; padding: 14px; border-radius: calc(var(--radius) - 4px); }
.rec:hover { background: var(--hover); } .rec + .rec { border-top: 1px solid var(--border); } .grow { flex: 1; min-width: 0; }
.chev { font-size: 1.6em; color: var(--muted); } .rec .avatar { width: 42px; height: 42px; background: var(--tint); color: var(--primary); }
input { border-radius: 999px; padding-left: 18px; } button { border-radius: 999px; padding-left: 22px; padding-right: 22px; }
.warm { background: radial-gradient(500px 300px at 20% 10%, var(--tint), transparent 70%), var(--background); }
.authcard.soft { border-radius: 28px; align-items: center; text-align: center; }
.authcard.soft > div, .authcard.soft form { width: 100%; text-align: left; }
""",
        "service": f"""
body {{ background: var(--background); }}
.wrap {{ max-width: 960px; margin: 0 auto; padding: 0 {30 if wide else 15}px; display: flex; align-items: center; gap: 16px; }}
header.gov {{ background: #0B0C0C; color: #FFFFFF; padding: 14px 0; border-bottom: 10px solid var(--primary); font-size: 1.15em; }}
header.gov a {{ color: #FFFFFF; font-size: .85em; text-decoration: underline; }}
.phase {{ padding: 10px 0; border-bottom: 1px solid var(--border); font-size: .9em; }}
.tag {{ display: inline-block; padding: 3px 8px; background: var(--primary); color: var(--on-primary); font-size: .8em; letter-spacing: .05em; margin-right: 8px; }}
.tag.success {{ background: color-mix(in srgb, var(--success) 18%, #fff); color: var(--success); }}
.tag.warning {{ background: color-mix(in srgb, var(--warning) 18%, #fff); color: var(--warning); }}
.tag.neutral {{ background: #F3F2F1; color: #383F43; }} .tag.danger {{ background: color-mix(in srgb, var(--danger) 15%, #fff); color: var(--danger); }}
.govnav {{ border-bottom: 1px solid var(--border); }} .govnav .wrap {{ gap: 24px; }}
.govnav a {{ white-space: nowrap; padding: 14px 0; color: var(--primary); text-decoration: underline; font-weight: 700; border-bottom: 4px solid transparent; }}
.govnav a.on {{ border-color: var(--primary); text-decoration: none; }}
main.narrow {{ max-width: 960px; margin: 0 auto; padding: 40px {30 if wide else 15}px; gap: 24px; }}
.caption {{ color: var(--muted); font-size: 1.15em; }} h1.xl {{ font-size: {2.6 if wide else 1.9}em; line-height: 1.1; margin-top: -14px; }}
.lead {{ font-size: 1.25em; margin: 0; }} h2 {{ font-size: 1.5em; }}
.summary {{ margin: 0; border-top: 1px solid var(--border); }}
.summary-row {{ display: grid; grid-template-columns: 1fr 1fr auto; gap: 20px; padding: 12px 0; border-bottom: 1px solid var(--border); }}
.summary dt {{ font-weight: 700; }} .summary dd {{ margin: 0; }} a {{ text-decoration: underline; }}
table th {{ background: transparent; text-transform: none; letter-spacing: 0; font-size: 1em; color: var(--text); font-weight: 700;
  border-bottom: 1px solid var(--border); }} td, th {{ padding: 12px 20px 12px 0 !important; }} tbody tr:hover td {{ background: transparent; }}
button {{ background: var(--success); color: #FFFFFF; border-radius: 0; box-shadow: 0 2px 0 #002D18; padding: 10px 18px; font-size: 1.05em; }}
button.ghost {{ background: #F3F2F1; color: var(--text); box-shadow: 0 2px 0 #929191; border: 0; }}
label {{ font-size: 1.15em; font-weight: 700; }} .fieldhint {{ font-weight: 400; color: var(--muted); font-size: .85em; }}
input {{ border: 2px solid var(--text); border-radius: 0; max-width: 480px; background: #FFFFFF; }}
input.focus {{ outline: 3px solid var(--accent); outline-offset: 0; box-shadow: inset 0 0 0 2px var(--text); border-color: var(--text); }}
form .fields {{ grid-template-columns: 1fr; }}
.errsum {{ border: 5px solid var(--danger); padding: 20px; }} .errsum a {{ color: var(--danger); font-weight: 700; }}
.govfoot {{ border-top: 1px solid var(--border); background: #F3F2F1; padding: 30px 0; color: var(--muted); margin-top: 40px; }}
.empty, .card {{ box-shadow: none; }}
""",
    }[template]
    return common + css


def _page(concept: Concept, screen: Screen, width: int, frame: str, template: str = "workspace") -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width={width}'>"
            f"<title>{escape(concept.name)}: {escape(screen.name)}</title><style>{_css(concept, width)}"
            f"{_template_css(template, width)}</style></head><body class='t-{template}'>{frame}</body></html>")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "screen"


# ---------------------------------------------------------------- repository files

def guide(concept: Concept, brand: Brand, reason: str, adjustments: str, product: str) -> str:
    """docs/ui/UI.md: the chosen concept and the rules every screen follows."""
    t = concept.tokens
    colors = "\n".join(f"| `{k}` | `{v}` |" for k, v in t.colors.items())
    screens = "\n".join(f"- {s.name}" + (f" ({s.role})" if s.role else "") + f": `concepts/{concept.id}/{slug(s.name)}.html`"
                        for s in concept.screens)
    brand_lines = [f"- Colors given: {', '.join(brand.colors)}" if brand.colors else "",
                   f"- Fonts given: {', '.join(brand.fonts)}" if brand.fonts else "",
                   f"- Figma: {brand.figma_url}" if brand.figma_url else "",
                   f"- Logo: `brand/{brand.logo_name}`" if brand.logo_name else ""]
    return f"""# UI: {product}

The UI concept chosen at the UI Concept gate. Every screen of the product follows it; change it only through a run.

## Concept

**{concept.name}** ({concept.layout} layout, {concept.density} density{f", from the {concept.preset} preset" if concept.preset else ""}).

{concept.rationale}

Why it was chosen: {reason}{f"{chr(10)}{chr(10)}Adjustments asked: {adjustments}" if adjustments else ""}

## Customer brand

{chr(10).join(line for line in brand_lines if line) or "- None given; the concept's palette and fonts apply."}

## Design tokens

The single source is `design-tokens.json`. Code reads them as theme variables; it never writes a color, size or font
of its own.

| Token | Value |
|---|---|
{colors}
| font | {t.font_family} |
| heading font | {t.heading_font or t.font_family} |
| base size | {t.base_size}px |
| radius | {t.radius}px |
| spacing unit | {t.spacing}px |

## Rules

- Screens are built from the project's base components (layout, navigation, button, form field, table, card, alert,
  empty, loading and error states); a new visual pattern becomes a component first.
- Colors, sizes, radii and fonts come from the tokens; no inline style and no literal color in screens.
- Every screen has its empty, loading and error states.
- Tables mark their header cells for assistive technology (`<th scope="col">`, or `scope="row"` for row headers);
  forms label every field; buttons and links say what they do.
- Accessibility: {brand.accessibility.upper()}: text contrast as checked at the gate, labels on every field, visible
  focus, the whole flow works with the keyboard.
- Devices: {', '.join(brand.devices)}.{f"{chr(10)}- Interface language: {brand.language}." if brand.language else ""}{f"{chr(10)}- Tone: {brand.tone}." if brand.tone else ""}

## Prototype screens

{screens}

Navigation: {', '.join(concept.navigation)}.
"""


def tokens_json(concept: Concept) -> str:
    t = concept.tokens
    return json.dumps({"concept": concept.id, "layout": concept.layout, "density": concept.density,
                       "color": t.colors, "font": {"family": t.font_family, "heading": t.heading_font or t.font_family,
                                                   "base_size_px": t.base_size},
                       "radius_px": t.radius, "spacing_px": t.spacing, "shadow": t.shadow}, indent=2) + "\n"


def ui_files(concept: Concept, brand: Brand, reason: str, adjustments: str, product: str,
             logo: tuple[str, bytes] | None = None) -> dict[str, str | bytes]:
    """What the SDS pull request adds: the guide, the tokens, the chosen concept's prototypes, the logo."""
    data_url = ""
    files: dict[str, str | bytes] = {f"{UI_DIR}/UI.md": guide(concept, brand, reason, adjustments, product),
                                     f"{UI_DIR}/design-tokens.json": tokens_json(concept)}
    if logo:
        files[f"{UI_DIR}/brand/{logo[0]}"] = logo[1]
        data_url = f"../../brand/{logo[0]}"
    for screen in concept.screens:
        files[f"{UI_DIR}/concepts/{concept.id}/{slug(screen.name)}.html"] = render(concept, screen, "desktop", product,
                                                                                   data_url)
    return files


def logo_bytes(artifact: str | None) -> tuple[str, bytes] | None:
    """The brand_logo artifact: {"name": ..., "data": base64}."""
    if not artifact:
        return None
    import base64
    stored = json.loads(artifact)
    return Path(stored["name"]).name, base64.b64decode(stored["data"])


# ---------------------------------------------------------------- preview while the SDS request is written

PREVIEW_SCREENS = ("overview", "form", "sign-in", "empty")


def preview_concept(preset: dict, colors: list[str], fonts: list[str], navigation: list[str]) -> Concept:
    """A preset with the customer's brand applied, as the request form shows it before any model is asked: primary
    and accent from the brand colors (text on primary in white or near black, whichever reads better), the first font
    in front of the preset's stack."""
    tokens = json.loads(json.dumps(preset["tokens"]))
    if colors:
        tokens["colors"]["primary"] = colors[0].upper()
        tokens["colors"]["on_primary"] = max(("#FFFFFF", "#111827"), key=lambda c: contrast(c, colors[0]))
    if len(colors) > 1:
        tokens["colors"]["accent"] = colors[1].upper()
    if fonts:
        stack = ", ".join(f"'{f}'" for f in fonts)
        tokens["font_family"] = f"{stack}, {preset['tokens']['font_family']}"
        tokens["heading_font"] = tokens["font_family"]
    nav = navigation[:6] or ["Overview", "Records", "Reports", "Settings"]
    return Concept(id=preset["id"], name=preset["name"], preset=preset["id"], layout=preset["layout"],
                   template=preset.get("template", ""),
                   density=preset.get("density", "comfortable"), rationale=preset.get("best_for", "") or "preset",
                   tokens=Tokens.model_validate(tokens), navigation=nav, screens=preview_screens(nav))


def preview_screens(features: list[str]) -> list[Screen]:
    """Sample screens with the product's features in them: what any concept is judged on before the SRS screens."""
    named = (features + ["Records", "Requests", "Reports"])[:3]
    rows = [[f"{feature} item {n}", owner, status, date] for n, (feature, owner, status, date) in enumerate(zip(
        named * 2, ["Lan Nguyen", "Minh Tran", "Hoa Pham", "Duc Le", "An Vo", "Bao Ho"],
        ["Active", "Pending", "Active", "Closed", "Pending", "Active"],
        ["2026-10-21", "2026-10-21", "2026-10-22", "2026-10-22", "2026-10-23", "2026-10-23"]), start=1)][:5]
    return [
        Screen(name="overview", blocks=[
            Block(type="heading", title="Overview", text="Sample data to judge the look; the real screens come from the SRS."),
            Block(type="stats", items=[f"{f}: {n}" for f, n in zip(named, (128, 14, 3))]),
            Block(type="table", title="Recent activity", columns=["Name", "Owner", "Status", "Updated"], rows=rows),
            Block(type="actions", items=["Create", "Export"])]),
        Screen(name="form", blocks=[
            Block(type="heading", title=f"New {named[0].lower()} item"),
            Block(type="alert", tone="danger", title="Check the highlighted field", text="Name is required."),
            Block(type="form", fields=["Name", "Owner", "Start date", "Notes"], items=["Save", "Cancel"])]),
        Screen(name="sign-in", blocks=[
            Block(type="heading", title="Sign in"),
            Block(type="form", fields=["Email", "Password"], items=["Sign in"]),
            Block(type="alert", tone="info", title="Demo", text="Use the account your administrator gave you.")]),
        Screen(name="empty", blocks=[
            Block(type="heading", title=named[1]),
            Block(type="empty", title=f"No {named[1].lower()} yet", text="Items appear here once someone creates one."),
            Block(type="actions", items=["Create the first one"])]),
    ]
