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


def render(concept: Concept, screen: Screen, device: str = "desktop", product: str = "", logo: str = "") -> str:
    """One prototype screen, self-contained (no script, no external request), in the concept's tokens and layout."""
    width = DEVICES.get(device, 1280)
    layout = "mobile" if device == "mobile" else concept.layout
    name = escape(product or concept.name)
    mark = f"<img src='{escape(logo)}' alt=''>" if logo else f"<span class='mark'>{escape(_initials(product or concept.name))}</span>"
    brand = f"<div class='brand'>{mark}<span>{name}</span></div>"
    if _is_sign_in(screen):
        form = "".join(_block(b) for b in screen.blocks if b.type in ("form", "alert"))
        form = form.replace("<div class='card'>", "<div>", 1)
        frame = (f"<div class='auth'><div class='hero'>{brand.replace('mark', 'mark')}<div><h1>Welcome back to {name}</h1>"
                 f"<p>Everything your team needs, in one place. Sign in with the account your administrator gave you.</p></div>"
                 f"<small>© {name}</small></div><div class='panel'><div class='box'><div><h1>Sign in</h1>"
                 f"<p class='muted' style='margin:6px 0 0'>Enter your details to continue.</p></div>{form}</div></div></div>")
        return _page(concept, screen, width, frame)
    body = "".join(_block(b, n == 0) for n, b in enumerate(screen.blocks))
    user = "<span class='avatar'>LN</span>"
    tools = (f"<span class='iconbtn'>{icon('bell')}<span class='dot'></span></span>{user}")
    if layout == "sidebar":
        links = "".join(f"<a href='#' class='{'on' if n == 0 else ''}'>{icon(nav_icon(item))}<span>{escape(item)}</span></a>"
                        for n, item in enumerate(concept.navigation))
        frame = (f"<div class='app'><aside class='side'>{brand}<div class='navlabel'>Menu</div>{links}"
                 f"<div class='foot'>{icon('settings')}<span>Settings</span></div></aside>"
                 f"<div class='col'><header class='topbar'><span class='search'>{icon('search', 16)}Search…</span>"
                 f"<span class='spacer'></span>{tools}</header><main>{body}</main></div></div>")
    elif layout == "topnav":
        links = "".join(f"<a href='#' class='{'on' if n == 0 else ''}'>{icon(nav_icon(item), 16)}{escape(item)}</a>"
                        for n, item in enumerate(concept.navigation[:4]))
        frame = (f"<div class='app stack'><header class='topbar brandbar'>{brand.replace('brand', 'brand', 1)}"
                 f"<nav class='links'>{links}</nav><span class='spacer'></span>"
                 f"<span class='search' style='max-width:220px'>{icon('search', 16)}Search…</span>{tools}</header>"
                 f"<main style='margin:0 auto'>{body}</main></div>")
    else:
        shorts: list[str] = []
        for item in concept.navigation[:4]:  # one word each, never the same twice (Face Enrollment, Face Recognition)
            words = [w for w in re.split(r"[\s/&-]+", item) if w]
            shorts.append(next((w for w in words if w not in shorts), item)[:12])
        tabs = "".join(f"<a href='#' class='{'on' if n == 0 else ''}'>{icon(nav_icon(item), 22)}{escape(short)}</a>"
                       for n, (item, short) in enumerate(zip(concept.navigation[:4], shorts)))
        frame = (f"<div class='app stack'><header class='topbar'>{brand}<span class='spacer'></span>{tools}</header>"
                 f"<main>{body}</main><nav class='bottom'>{tabs}</nav></div>")
    return _page(concept, screen, width, frame)


def _page(concept: Concept, screen: Screen, width: int, frame: str) -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width={width}'>"
            f"<title>{escape(concept.name)}: {escape(screen.name)}</title><style>{_css(concept, width)}</style></head>"
            f"<body>{frame}</body></html>")


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
