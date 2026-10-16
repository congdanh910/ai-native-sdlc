"""Discovery sources: Office files become text with locations; PDFs are counted, not converted."""

import io
import zipfile
import zlib

import pytest

from controller.app.discovery.sources import SourceError, pdf_pages, prepare

ACCEPTED = ("pdf", "docx", "xlsx", "pptx", "png", "md")
LIMITS = {"accepted": ACCEPTED, "max_bytes": 1_000_000, "max_pages": 50}
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def office(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def test_docx_paragraphs_headings_and_tables_keep_their_location():
    data = office({"word/document.xml": f'<w:document {W}><w:body>'
                   '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Scope</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>HR approves leave.</w:t></w:r></w:p>'
                   '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
                   '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Role</w:t></w:r></w:p></w:tc>'
                   '<w:tc><w:p><w:r><w:t>Can</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>'})
    prepared = prepare("Brief.docx", data, **LIMITS)
    assert prepared.kind == "docx" and prepared.pages == 2
    assert prepared.text.splitlines() == ["[p1] ## Scope", "[p2] HR approves leave.", "[table1 row1] Role | Can"]


def test_xlsx_cells_are_listed_by_sheet_and_address():
    data = office({
        "xl/workbook.xml": f'<workbook {S} {R}><sheets><sheet name="Actors" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                      '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/sharedStrings.xml": f'<sst {S}><si><t>Actor</t></si><si><t>Dispatcher</t></si></sst>',
        "xl/worksheets/sheet1.xml": f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row>'
                                    '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>3</v></c></row></sheetData></worksheet>'})
    prepared = prepare("matrix.xlsx", data, **LIMITS)
    assert prepared.pages == 1
    assert prepared.text.splitlines() == ["## Sheet Actors", "[Actors!A1] Actor", "[Actors!A2] Dispatcher", "[Actors!B2] 3"]


def test_pptx_slides_are_numbered_in_order():
    slide = lambda text: f'<p:sld xmlns:p="x" {A}><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>'
    data = office({"ppt/slides/slide10.xml": slide("Later"), "ppt/slides/slide2.xml": slide("Goals")})
    prepared = prepare("deck.pptx", data, **LIMITS)
    assert prepared.pages == 2 and prepared.text.splitlines() == ["[slide 1] Goals", "[slide 2] Later"]


def test_pdf_pages_are_counted_also_inside_compressed_object_streams():
    plain = b"%PDF-1.4\n1 0 obj << /Type /Pages /Kids [3 0 R] /Count 7 >> endobj\n"
    assert pdf_pages(plain) == 7
    packed = zlib.compress(b"<< /Type /Pages /Count 12 /Kids [] >>")
    compressed = b"%PDF-1.7\n5 0 obj << /Type /ObjStm /Filter /FlateDecode >>\nstream\n" + packed + b"\nendstream\n"
    assert pdf_pages(compressed) == 12
    prepared = prepare("deck.pdf", plain, **LIMITS)
    assert prepared.kind == "pdf" and prepared.pages == 7 and prepared.text is None and len(prepared.sha256) == 64


def test_bad_sources_are_refused_with_a_reason():
    with pytest.raises(SourceError, match=r"\.exe files are not accepted"):
        prepare("setup.exe", b"MZ", **LIMITS)
    with pytest.raises(SourceError, match="is not a PDF"):
        prepare("fake.pdf", b"hello", **LIMITS)
    with pytest.raises(SourceError, match="the project allows 50"):
        prepare("long.pdf", b"%PDF-1.4 << /Type /Pages /Count 80 >>", **LIMITS)
    with pytest.raises(SourceError, match="cannot be read as DOCX"):
        prepare("broken.docx", b"not a zip", **LIMITS)
    assert prepare("../../etc/passwd.md", b"x", **LIMITS).filename == "passwd.md"
