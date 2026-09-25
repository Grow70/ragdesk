"""Extract text from PDF pages while reporting pages with no usable text."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.parsers.text import ParsedSection, ParseError


@dataclass(frozen=True, slots=True)
class PdfPageWarning:
    page_number: int
    source_locator: str
    code: Literal["PDF_NO_TEXT_PAGE", "PDF_IMAGE_ONLY_PAGE"]
    message: str


@dataclass(frozen=True, slots=True)
class PdfParseResult:
    sections: list[ParsedSection]
    warnings: list[PdfPageWarning]
    status: Literal["complete", "partial"]


def _clean_text(extracted: str | None) -> str:
    if not extracted:
        return ""
    return "\n".join(line.strip() for line in extracted.splitlines() if line.strip())


def parse_pdf(path: str | Path, *, document_id: str | None = None) -> PdfParseResult:
    """Parse a trusted local PDF without OCR or page-layout reconstruction."""
    source_path = Path(path)
    if source_path.suffix.lower() != ".pdf":
        raise ParseError("UNSUPPORTED_FORMAT", "Expected a PDF file")
    sections: list[ParsedSection] = []
    warnings: list[PdfPageWarning] = []
    try:
        with source_path.open("rb") as stream:
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                raise ParseError("ENCRYPTED_PDF", "Encrypted PDF is not supported")
            for page_number, page in enumerate(reader.pages, start=1):
                text = _clean_text(page.extract_text())
                locator = f"page:{page_number}"
                if text:
                    sections.append(
                        ParsedSection(
                            text=text,
                            section_index=len(sections),
                            heading_path=None,
                            page_number=page_number,
                            source_locator=locator,
                            document_id=document_id,
                            start_line=None,
                            end_line=None,
                            block_type="paragraph",
                        )
                    )
                    continue
                has_image = len(page.images) > 0
                warnings.append(
                    PdfPageWarning(
                        page_number=page_number,
                        source_locator=locator,
                        code=(
                            "PDF_IMAGE_ONLY_PAGE" if has_image else "PDF_NO_TEXT_PAGE"
                        ),
                        message=(
                            "Image page has no extractable text; OCR is unsupported"
                            if has_image
                            else "Page has no extractable text; it may be blank"
                        ),
                    )
                )
    except FileNotFoundError as exc:
        raise ParseError("FILE_NOT_FOUND", "Source file does not exist") from exc
    except ParseError:
        raise
    except PdfReadError as exc:
        raise ParseError("INVALID_PDF", "PDF is damaged or malformed") from exc
    except OSError as exc:
        raise ParseError("READ_ERROR", "Could not read PDF file") from exc
    except Exception as exc:
        raise ParseError("PDF_PARSE_ERROR", "Could not parse PDF file") from exc
    if not sections:
        raise ParseError(
            "NO_EXTRACTABLE_TEXT",
            "PDF has no extractable text; OCR is not supported",
        )
    return PdfParseResult(
        sections=sections,
        warnings=warnings,
        status="partial" if warnings else "complete",
    )
