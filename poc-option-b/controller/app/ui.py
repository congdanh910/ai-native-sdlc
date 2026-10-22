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

def _css(concept: Concept, width: int) -> str:
    t, c = concept.tokens, concept.tokens.colors
    gap = t.spacing * (1 if concept.density == "compact" else 1.5)
    return f"""
:root {{ {' '.join(f'--{k.replace("_", "-")}: {v};' for k, v in c.items())}
  --radius: {t.radius}px; --gap: {gap}px; --shadow: {t.shadow}; }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--background); color: var(--text); font: {t.base_size}px/1.5 {t.font_family}; }}
h1, h2, h3 {{ font-family: {t.heading_font or t.font_family}; margin: 0 0 var(--gap); }}
h1 {{ font-size: 1.6em; }} h2 {{ font-size: 1.15em; }}
.app {{ display: flex; min-height: 100vh; {"flex-direction: column;" if concept.layout != "sidebar" else ""} }}
.side {{ width: 220px; background: var(--surface); border-right: 1px solid var(--border); padding: var(--gap); }}
.top {{ display: flex; gap: var(--gap); align-items: center; background: var(--primary); color: var(--on-primary);
  padding: var(--gap) calc(var(--gap) * 2); }}
.top a {{ color: var(--on-primary); }}
.nav a {{ display: block; padding: calc(var(--gap) / 1.5) var(--gap); border-radius: var(--radius); color: var(--text);
  text-decoration: none; }}
.top .nav a {{ display: inline-block; color: var(--on-primary); }}
.nav a.on {{ background: var(--primary); color: var(--on-primary); }}
.brand {{ font-weight: 700; margin-bottom: var(--gap); }}
main {{ flex: 1; padding: calc(var(--gap) * 2); max-width: {width}px; }}
.card {{ background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow);
  padding: calc(var(--gap) * 1.5); margin-bottom: calc(var(--gap) * 1.5); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax({160 if width > 500 else 140}px, 1fr)); gap: var(--gap); }}
.stat b {{ display: block; font-size: 1.5em; color: var(--primary); }}
.muted {{ color: var(--muted); }}
table {{ width: 100%; border-collapse: collapse; }}
th, td {{ text-align: left; padding: calc(var(--gap) / 1.5); border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 600; }}
label {{ display: block; margin-bottom: var(--gap); }}
input {{ display: block; width: 100%; margin-top: 4px; padding: calc(var(--gap) / 1.2); border: 1px solid var(--border);
  border-radius: var(--radius); font: inherit; background: var(--surface); color: var(--text); }}
button {{ font: inherit; padding: calc(var(--gap) / 1.2) calc(var(--gap) * 2); border-radius: var(--radius); border: 0;
  background: var(--primary); color: var(--on-primary); cursor: pointer; margin-right: var(--gap); }}
button.secondary {{ background: transparent; color: var(--primary); border: 1px solid var(--primary); }}
.alert {{ border-left: 4px solid var(--primary); }}
.alert.success {{ border-color: var(--success); }} .alert.warning {{ border-color: var(--warning); }}
.alert.danger {{ border-color: var(--danger); color: var(--danger); }}
.bar {{ height: 10px; background: var(--accent); border-radius: var(--radius); }}
.empty {{ text-align: center; padding: calc(var(--gap) * 4); color: var(--muted); }}
.bottom {{ position: sticky; bottom: 0; display: flex; justify-content: space-around; background: var(--surface);
  border-top: 1px solid var(--border); padding: var(--gap); }}
.bottom a {{ color: var(--text); text-decoration: none; }}
"""


def _block(block: Block) -> str:
    title = f"<h2>{escape(block.title)}</h2>" if block.title else ""
    text = f"<p class='muted'>{escape(block.text)}</p>" if block.text else ""
    if block.type == "heading":
        return f"<h1>{escape(block.title)}</h1>{text}"
    if block.type == "stats":
        cells = "".join(f"<div class='card stat'><span class='muted'>{escape(i.split(':', 1)[0])}</span>"
                        f"<b>{escape(i.split(':', 1)[1].strip() if ':' in i else '')}</b></div>" for i in block.items)
        return f"{title}<div class='grid'>{cells}</div>"
    if block.type == "table":
        head = "".join(f"<th>{escape(c)}</th>" for c in block.columns)
        body = "".join("<tr>" + "".join(f"<td>{escape(v)}</td>" for v in row) + "</tr>" for row in block.rows)
        return f"<div class='card'>{title}{text}<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
    if block.type == "form":
        fields = "".join(f"<label>{escape(f)}<input aria-label='{escape(f)}'></label>" for f in block.fields)
        buttons = "".join(f"<button{' class=secondary' if n else ''}>{escape(b)}</button>"
                          for n, b in enumerate(block.items or ["Save"]))
        return f"<div class='card'>{title}{text}<form onsubmit='return false'>{fields}{buttons}</form></div>"
    if block.type == "cards":
        cards = "".join(f"<div class='card'>{escape(i)}</div>" for i in block.items)
        return f"{title}<div class='grid'>{cards}</div>"
    if block.type == "list":
        rows = "".join(f"<tr><td>{escape(i)}</td></tr>" for i in block.items)
        return f"<div class='card'>{title}<table><tbody>{rows}</tbody></table></div>"
    if block.type == "chart":
        values = [(i.split(":", 1)[0], float(re.sub(r"[^0-9.]", "", i.split(":", 1)[1]) or 0) if ":" in i else 0)
                  for i in block.items]
        top = max([v for _, v in values] or [1]) or 1
        bars = "".join(f"<div><span class='muted'>{escape(label)}</span><div class='bar' style='width:{v / top * 100:.0f}%'>"
                       f"</div></div>" for label, v in values)
        return f"<div class='card'>{title}{bars}</div>"
    if block.type == "alert":
        return f"<div class='card alert {block.tone}' role='status'><b>{escape(block.title)}</b> {escape(block.text)}</div>"
    if block.type == "empty":
        return f"<div class='card empty'><h2>{escape(block.title or 'Nothing here yet')}</h2>{text}</div>"
    buttons = "".join(f"<button{' class=secondary' if n else ''}>{escape(b)}</button>" for n, b in enumerate(block.items))
    return f"<div>{title}{buttons}</div>"


def render(concept: Concept, screen: Screen, device: str = "desktop", product: str = "", logo: str = "") -> str:
    """One prototype screen, self-contained (no script, no external request), in the concept's tokens and layout."""
    width = DEVICES.get(device, 1280)
    layout = "mobile" if device == "mobile" else concept.layout
    brand = (f"<img src='{escape(logo)}' alt='' style='max-height:28px;vertical-align:middle'> " if logo else "") \
        + escape(product or concept.name)
    links = "".join(f"<a href='#' class='{'on' if n == 0 else ''}'>{escape(item)}</a>"
                    for n, item in enumerate(concept.navigation))
    body = "".join(_block(b) for b in screen.blocks)
    if layout == "sidebar":
        frame = f"<aside class='side'><div class='brand'>{brand}</div><nav class='nav'>{links}</nav></aside><main>{body}</main>"
    elif layout == "topnav":
        frame = f"<header class='top'><span class='brand'>{brand}</span><nav class='nav'>{links}</nav></header><main>{body}</main>"
    else:
        tabs = "".join(f"<a href='#'>{escape(item)}</a>" for item in concept.navigation[:4])
        frame = f"<header class='top'><span class='brand'>{brand}</span></header><main>{body}</main><nav class='bottom'>{tabs}</nav>"
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width={width}'>"
            f"<title>{escape(concept.name)}: {escape(screen.name)}</title><style>{_css(concept, width)}</style></head>"
            f"<body><div class='app'>{frame}</div></body></html>")


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
