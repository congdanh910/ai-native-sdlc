"""Source files of a discovery: stored once by content hash, counted, and turned into text where the CLIs
cannot read the format directly.

PDF and images are read by the Business Analyst's CLI as they are (Claude Code reads both), so for them only
the page count is needed to split the reading into calls. DOCX, XLSX and PPTX are turned into Markdown with the
standard library; every paragraph, cell and slide keeps its location ("p12", "Sheet1!B3", "slide 4") so that
requirement items can cite it.
"""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib

KINDS = {  # extension -> how the source is read
    "pdf": "pdf", "png": "image", "jpg": "image", "jpeg": "image", "webp": "image", "gif": "image",
    "docx": "docx", "xlsx": "xlsx", "pptx": "pptx", "md": "text", "txt": "text", "csv": "text", "eml": "text",
}
SAFE_NAME = re.compile(r"[^\w.\- ()]+", re.UNICODE)


class SourceError(ValueError):
    """The file cannot be used as a discovery source."""


@dataclass(frozen=True)
class Prepared:
    filename: str
    kind: str
    sha256: str
    size: int
    pages: int | None  # pages, slides or sheets; None when it cannot be counted
    text: str | None  # Markdown with locations for docx/xlsx/pptx/text; None for pdf and images


def safe_filename(name: str) -> str:
    name = Path(name.replace("\\", "/")).name.strip()
    name = SAFE_NAME.sub("_", name)[:200]
    if not name or name.startswith("."):
        raise SourceError("the file needs a name")
    return name


def kind_of(filename: str, accepted: tuple[str, ...]) -> str:
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in accepted or extension not in KINDS:
        raise SourceError(f".{extension or '?'} files are not accepted; accepted: {', '.join(accepted)}")
    return KINDS[extension]


def prepare(filename: str, data: bytes, *, accepted: tuple[str, ...], max_bytes: int, max_pages: int) -> Prepared:
    filename = safe_filename(filename)
    kind = kind_of(filename, accepted)
    if not data:
        raise SourceError(f"{filename} is empty")
    if len(data) > max_bytes:
        raise SourceError(f"{filename} is {len(data) // 1_000_000} MB; the project allows {max_bytes // 1_000_000} MB")
    text, pages = None, None
    if kind == "pdf":
        if not data.startswith(b"%PDF"):
            raise SourceError(f"{filename} is not a PDF")
        pages = pdf_pages(data)
    elif kind == "image":
        pages = 1
    elif kind == "text":
        text = data.decode("utf-8", errors="replace")
        pages = 1
    else:
        try:
            text, pages = {"docx": docx_text, "xlsx": xlsx_text, "pptx": pptx_text}[kind](data)
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
            raise SourceError(f"{filename} cannot be read as {kind.upper()}: {exc}") from exc
    if pages and pages > max_pages:
        raise SourceError(f"{filename} has {pages} pages; the project allows {max_pages}")
    return Prepared(filename, kind, hashlib.sha256(data).hexdigest(), len(data), pages, text)


# ---------------------------------------------------------------- PDF page count

PAGES_COUNT = re.compile(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)|/Count\s+(\d+)[^>]*?/Type\s*/Pages\b", re.S)
STREAM = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.S)


def pdf_pages(data: bytes) -> int | None:
    """The page count from the root Pages dictionary, also when it sits in a compressed object stream."""
    counts = [int(a or b) for a, b in PAGES_COUNT.findall(data)]
    if not counts:
        for match in STREAM.finditer(data):
            try:
                inflated = zlib.decompress(match[1])
            except zlib.error:
                continue
            counts += [int(a or b) for a, b in PAGES_COUNT.findall(inflated)]
    if counts:
        return max(counts)  # the root holds the total; intermediate Pages nodes hold parts of it
    pages = len(re.findall(rb"/Type\s*/Page\b(?!s)", data))
    return pages or None


# ---------------------------------------------------------------- Office files

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _texts(element, tag: str) -> str:
    return "".join(node.text or "" for node in element.iter(tag)).strip()


def docx_text(data: bytes) -> tuple[str, int | None]:
    """Paragraphs as `[p N] text`, tables as `[table N row M] cell | cell`. Pages are not stored in DOCX, so the
    count is the number of explicit page breaks plus one."""
    with zipfile.ZipFile(_bytes_io(data)) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{W}body")
    lines, paragraph, table = [], 0, 0
    breaks = len([b for b in root.iter(f"{W}br") if b.get(f"{W}type") == "page"])
    for child in list(body) if body is not None else []:
        if child.tag == f"{W}p":
            text = _texts(child, f"{W}t")
            if text:
                paragraph += 1
                style = child.find(f"{W}pPr/{W}pStyle")
                heading = style is not None and "heading" in (style.get(f"{W}val") or "").lower()
                lines.append(f"[p{paragraph}] {'## ' if heading else ''}{text}")
        elif child.tag == f"{W}tbl":
            table += 1
            for row_number, row in enumerate(child.iter(f"{W}tr"), start=1):
                cells = [_texts(cell, f"{W}t") for cell in row.iter(f"{W}tc")]
                if any(cells):
                    lines.append(f"[table{table} row{row_number}] " + " | ".join(cells))
    return "\n".join(lines) + "\n", breaks + 1


def xlsx_text(data: bytes) -> tuple[str, int | None]:
    """Every non-empty cell as `[Sheet!A1] value`, sheet by sheet; the count is the number of sheets."""
    with zipfile.ZipFile(_bytes_io(data)) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in ET.fromstring(archive.read("xl/sharedStrings.xml")).iter(f"{S}si"):
                shared.append(_texts(item, f"{S}t"))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        names = [sheet.get("name") for sheet in workbook.iter(f"{S}sheet")]
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.get("Id"): rel.get("Target") for rel in rels}
        sheets = [(sheet.get("name"), targets.get(sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")))
                  for sheet in workbook.iter(f"{S}sheet")]
        lines = []
        for name, target in sheets:
            if not target:
                continue
            path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            lines.append(f"## Sheet {name}")
            for cell in ET.fromstring(archive.read(path)).iter(f"{S}c"):
                value = cell.find(f"{S}v")
                inline = cell.find(f"{S}is")
                if inline is not None:
                    text = _texts(inline, f"{S}t")
                elif value is None or value.text is None:
                    continue
                elif cell.get("t") == "s":
                    text = shared[int(value.text)] if int(value.text) < len(shared) else ""
                else:
                    text = value.text
                if text.strip():
                    lines.append(f"[{name}!{cell.get('r')}] {text.strip()}")
    return "\n".join(lines) + "\n", len(names) or None


def pptx_text(data: bytes) -> tuple[str, int | None]:
    """Each slide's text as `[slide N] ...`, in slide order."""
    with zipfile.ZipFile(_bytes_io(data)) as archive:
        slides = sorted((n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1])[1]))
        lines = []
        for number, name in enumerate(slides, start=1):
            root = ET.fromstring(archive.read(name))
            paragraphs = [_texts(p, f"{A}t") for p in root.iter(f"{A}p")]
            text = " · ".join(p for p in paragraphs if p)
            if text:
                lines.append(f"[slide {number}] {text}")
    return "\n".join(lines) + "\n", len(slides) or None


def _bytes_io(data: bytes):
    import io
    return io.BytesIO(data)
