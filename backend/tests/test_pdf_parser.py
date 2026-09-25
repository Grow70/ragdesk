"""Fixed synthetic PDF checks for physical pages and partial extraction."""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from app.parsers.pdf import parse_pdf
from app.parsers.text import ParseError

FIXTURE = Path(__file__).parent / "fixtures" / "pdf_mixed.pdf"


def _select_pages(tmp_path, name: str, page_indexes: tuple[int, ...]) -> Path:
    source = PdfReader(FIXTURE, strict=True)
    writer = PdfWriter()
    for index in page_indexes:
        writer.add_page(source.pages[index])
    path = tmp_path / name
    with path.open("wb") as output:
        writer.write(output)
    return path


def test_fixed_pdf_keeps_physical_pages_and_chinese_facts():
    result = parse_pdf(FIXTURE, document_id="PDF-DEMO-001")
    assert result.status == "partial"
    assert [section.section_index for section in result.sections] == [0, 1]
    assert [section.page_number for section in result.sections] == [1, 4]
    assert [section.source_locator for section in result.sections] == [
        "page:1",
        "page:4",
    ]
    assert [warning.page_number for warning in result.warnings] == [2, 3]
    assert [warning.source_locator for warning in result.warnings] == [
        "page:2",
        "page:3",
    ]
    assert [warning.code for warning in result.warnings] == [
        "PDF_NO_TEXT_PAGE",
        "PDF_IMAGE_ONLY_PAGE",
    ]
    assert all(section.document_id == "PDF-DEMO-001" for section in result.sections)
    assert all(section.heading_path is None for section in result.sections)
    assert all(section.start_line is None for section in result.sections)
    assert all(section.end_line is None for section in result.sections)
    assert all(section.block_type == "paragraph" for section in result.sections)
    assert "人民币 680 元" in result.sections[0].text
    assert "2026-09-25" in result.sections[0].text
    assert "NX-210-P" in result.sections[0].text
    assert "15 个自然日" in result.sections[1].text
    assert "680 元" not in result.sections[1].text


def test_text_only_pdf_reports_complete(tmp_path):
    path = _select_pages(tmp_path, "text_only.pdf", (0, 3))
    result = parse_pdf(path)
    assert result.status == "complete"
    assert result.warnings == []
    assert [section.page_number for section in result.sections] == [1, 2]


@pytest.mark.parametrize("page_index", [1, 2])
def test_pdf_without_any_extractable_text_is_rejected(tmp_path, page_index):
    path = _select_pages(tmp_path, "no_text.pdf", (page_index,))
    with pytest.raises(ParseError) as failure:
        parse_pdf(path)
    assert failure.value.code == "NO_EXTRACTABLE_TEXT"
    assert "OCR" in str(failure.value)


def test_encrypted_pdf_is_rejected(tmp_path):
    path = _select_pages(tmp_path, "encrypted.pdf", (0,))
    reader = PdfReader(path, strict=True)
    writer = PdfWriter()
    writer.add_page(reader.pages[0])
    writer.encrypt(user_password="synthetic-test-password", algorithm="RC4-128")
    with path.open("wb") as output:
        writer.write(output)
    with pytest.raises(ParseError) as failure:
        parse_pdf(path)
    assert failure.value.code == "ENCRYPTED_PDF"


def test_damaged_and_missing_pdf_errors(tmp_path):
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(b"%PDF-1.4\ninvalid xref and trailer")
    with pytest.raises(ParseError) as failure:
        parse_pdf(damaged)
    assert failure.value.code == "INVALID_PDF"
    with pytest.raises(ParseError) as failure:
        parse_pdf(tmp_path / "missing.pdf")
    assert failure.value.code == "FILE_NOT_FOUND"
