"""Markdown and TXT parsing with original line locations."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BlockType = Literal["paragraph", "list", "code"]

_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+|$)(.*)$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_LIST = re.compile(r"^ {0,3}(?:[-+*]|[0-9]+[.)])(?:[ \t]+|$)")
_THEMATIC = re.compile(r"^ {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")


@dataclass(frozen=True, slots=True)
class ParsedSection:
    text: str
    section_index: int
    heading_path: list[str] | None
    page_number: int | None
    source_locator: str
    document_id: str | None
    start_line: int | None
    end_line: int | None
    block_type: BlockType


class ParseError(Exception):
    """A parser failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _section(
    lines: list[str],
    start_line: int,
    headings: list[tuple[int, str]],
    document_id: str | None,
    block_type: BlockType,
    index: int,
) -> ParsedSection:
    if block_type == "code":
        body = "\n".join(lines)
    else:
        body = "\n".join(line.rstrip() for line in lines).strip()
    end_line = start_line + len(lines) - 1
    locator = (
        f"lines:{start_line}"
        if start_line == end_line
        else f"lines:{start_line}-{end_line}"
    )
    return ParsedSection(
        text=body,
        section_index=index,
        heading_path=[title for _level, title in headings] or None,
        page_number=None,
        source_locator=locator,
        document_id=document_id,
        start_line=start_line,
        end_line=end_line,
        block_type=block_type,
    )


def _parse_txt(source: str, document_id: str | None) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    lines: list[str] = []
    start_line = 0

    def flush() -> None:
        nonlocal lines
        if lines:
            sections.append(
                _section(lines, start_line, [], document_id, "paragraph", len(sections))
            )
            lines = []

    for line_number, line in enumerate(source.splitlines(), start=1):
        if not line.strip():
            flush()
        else:
            if not lines:
                start_line = line_number
            lines.append(line.strip())
    flush()
    return sections


def _parse_markdown(source: str, document_id: str | None) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    headings: list[tuple[int, str]] = []
    lines: list[str] = []
    block_type: BlockType = "paragraph"
    start_line = 0
    fence: str | None = None

    def flush() -> None:
        nonlocal lines
        if lines:
            if block_type != "code" or any(line.strip() for line in lines[1:-1]):
                sections.append(
                    _section(
                        lines,
                        start_line,
                        headings,
                        document_id,
                        block_type,
                        len(sections),
                    )
                )
            lines = []

    def push_heading(level: int, title: str) -> None:
        if not title:
            raise ParseError("INVALID_HEADING", "Markdown heading is empty")
        while headings and headings[-1][0] >= level:
            headings.pop()
        headings.append((level, title))

    for line_number, line in enumerate(source.splitlines(), start=1):
        if fence is not None:
            lines.append(line)
            marker = re.escape(fence[0])
            if re.fullmatch(rf" {{0,3}}{marker}{{{len(fence)},}}[ \t]*", line):
                fence = None
                flush()
            continue

        opening = _FENCE.match(line)
        if opening:
            flush()
            start_line = line_number
            block_type = "code"
            lines = [line]
            fence = opening.group("marker")
            continue

        heading = _HEADING.match(line)
        if heading:
            flush()
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group(2)).strip()
            push_heading(len(heading.group(1)), title)
            continue

        setext = _SETEXT.match(line)
        if setext and block_type == "paragraph" and len(lines) == 1:
            title = lines[0].strip()
            lines = []
            push_heading(1 if setext.group(1)[0] == "=" else 2, title)
            continue

        if not line.strip():
            flush()
            continue

        if _THEMATIC.match(line):
            flush()
            continue

        is_list = bool(_LIST.match(line))
        if lines and block_type == "list" and not is_list and not line[0].isspace():
            flush()
        elif lines and block_type == "paragraph" and is_list:
            flush()
        if not lines:
            start_line = line_number
            block_type = "list" if is_list else "paragraph"
        lines.append(line)

    if fence is not None:
        raise ParseError("UNCLOSED_CODE_FENCE", "Markdown code fence is not closed")
    flush()
    return sections


def parse_file(
    path: str | Path, *, document_id: str | None = None
) -> list[ParsedSection]:
    """Parse one trusted local path without fetching links or executing source text."""
    source_path = Path(path)
    if source_path.suffix.lower() not in {".md", ".txt"}:
        raise ParseError("UNSUPPORTED_FORMAT", "Only Markdown and TXT are supported")
    try:
        raw = source_path.read_bytes()
    except FileNotFoundError as exc:
        raise ParseError("FILE_NOT_FOUND", "Source file does not exist") from exc
    except OSError as exc:
        raise ParseError("READ_ERROR", "Could not read source file") from exc
    try:
        source = raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ParseError("UNSUPPORTED_ENCODING", "Expected UTF-8 text") from exc
    if any(ord(char) < 32 and char not in "\t\n\r" for char in source):
        raise ParseError("UNSUPPORTED_ENCODING", "Expected UTF-8 text")
    try:
        sections = (
            _parse_markdown(source, document_id)
            if source_path.suffix.lower() == ".md"
            else _parse_txt(source, document_id)
        )
    except ParseError:
        raise
    except Exception as exc:
        raise ParseError("PARSE_ERROR", "Could not parse source file") from exc
    if not sections:
        raise ParseError("EMPTY_BODY", "Source file has no body text")
    return sections
