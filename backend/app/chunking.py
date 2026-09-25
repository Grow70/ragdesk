"""Deterministic character-based drafts from parsed document sections."""

from dataclasses import dataclass
from hashlib import sha256

from app.parsers.text import ParsedSection


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    """Limits in Unicode characters (Python string code points), not model tokens."""

    chunk_size: int = 600
    overlap: int = 80

    def __post_init__(self) -> None:
        if type(self.chunk_size) is not int or self.chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")
        if type(self.overlap) is not int or not 0 <= self.overlap < self.chunk_size:
            raise ValueError("overlap must be an integer in [0, chunk_size)")


@dataclass(frozen=True, slots=True)
class ChunkSourceSpan:
    section_index: int
    source_locator: str
    char_start: int
    char_end: int
    page_number: int | None
    start_line: int | None
    end_line: int | None


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    ordinal: int
    text: str
    document_id: str | None
    heading_path: list[str] | None
    page_number: int | None
    start_line: int | None
    end_line: int | None
    source_spans: tuple[ChunkSourceSpan, ...]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class ChunkStats:
    chunk_count: int
    input_chars: int
    output_chars: int
    shortest_chars: int
    longest_chars: int
    average_chars: float
    short_chunks: int
    overlong_chunks: int
    duplicate_chunks: int


@dataclass(frozen=True, slots=True)
class ChunkingResult:
    chunks: list[ChunkDraft]
    stats: ChunkStats


@dataclass(frozen=True, slots=True)
class _Segment:
    section: ParsedSection
    group_start: int
    group_end: int
    source_start: int


def _same_group(left: ParsedSection, right: ParsedSection) -> bool:
    return (
        left.document_id == right.document_id
        and left.heading_path == right.heading_path
        and left.page_number == right.page_number
        and left.block_type != "code"
        and right.block_type != "code"
    )


def _split_end(text: str, start: int, size: int) -> int:
    limit = min(start + size, len(text))
    if limit == len(text):
        return limit

    # Prefer a paragraph end, then a sentence end, then a line end. Ignore
    # boundaries too close to the start so overlap can still make progress.
    earliest = start + max(1, size // 2)
    paragraph_start = text.rfind("\n\n", start, limit)
    if paragraph_start >= 0 and paragraph_start + 2 >= earliest:
        return paragraph_start + 2
    for markers in ("。！？!?；;", "\n"):
        for index in range(limit - 1, earliest - 2, -1):
            if text[index] in markers:
                return index + 1
    return limit


def _span(segment: _Segment, start: int, end: int) -> ChunkSourceSpan:
    section = segment.section
    char_start = segment.source_start + start - segment.group_start
    char_end = segment.source_start + end - segment.group_start
    first_line = (
        section.start_line + section.text[:char_start].count("\n")
        if section.start_line is not None
        else None
    )
    last_line = (
        section.start_line + section.text[: char_end - 1].count("\n")
        if section.start_line is not None
        else None
    )
    locator = section.source_locator
    if first_line is not None and last_line is not None:
        locator = (
            f"lines:{first_line}"
            if first_line == last_line
            else f"lines:{first_line}-{last_line}"
        )
    return ChunkSourceSpan(
        section_index=section.section_index,
        source_locator=locator,
        char_start=char_start,
        char_end=char_end,
        page_number=section.page_number,
        start_line=first_line,
        end_line=last_line,
    )


def _chunk_group(
    content: str,
    segments: list[_Segment],
    config: ChunkConfig,
    first_ordinal: int,
) -> list[ChunkDraft]:
    chunks: list[ChunkDraft] = []
    start = 0
    is_code = segments[0].section.block_type == "code"
    while start < len(content):
        end = _split_end(content, start, config.chunk_size)
        raw = content[start:end]
        leading = 0 if is_code else len(raw) - len(raw.lstrip())
        trailing = 0 if is_code else len(raw) - len(raw.rstrip())
        body_start = start + leading
        body_end = end - trailing
        if body_start < body_end and raw.strip():
            spans = tuple(
                _span(
                    segment,
                    max(body_start, segment.group_start),
                    min(body_end, segment.group_end),
                )
                for segment in segments
                if segment.group_start < body_end and segment.group_end > body_start
            )
            if spans:
                body = content[body_start:body_end]
                first = segments[0].section
                lines_start = [
                    span.start_line for span in spans if span.start_line is not None
                ]
                lines_end = [
                    span.end_line for span in spans if span.end_line is not None
                ]
                chunks.append(
                    ChunkDraft(
                        ordinal=first_ordinal + len(chunks),
                        text=body,
                        document_id=first.document_id,
                        heading_path=first.heading_path.copy()
                        if first.heading_path
                        else None,
                        page_number=first.page_number,
                        start_line=min(lines_start) if lines_start else None,
                        end_line=max(lines_end) if lines_end else None,
                        source_spans=spans,
                        content_sha256=sha256(body.encode("utf-8")).hexdigest(),
                    )
                )
        if end == len(content):
            break
        start = max(start + 1, end - config.overlap)
    return chunks


def chunk_sections(
    sections: list[ParsedSection], config: ChunkConfig | None = None
) -> ChunkingResult:
    """Create traceable chunk drafts without persistence or model calls."""

    config = config if config is not None else ChunkConfig()
    document_ids = {
        section.document_id for section in sections if section.document_id is not None
    }
    if len(document_ids) > 1:
        raise ValueError("sections must belong to one document")

    chunks: list[ChunkDraft] = []
    group_text = ""
    segments: list[_Segment] = []
    previous: ParsedSection | None = None
    input_chars = 0

    def flush() -> None:
        nonlocal group_text, segments, previous
        if segments:
            chunks.extend(_chunk_group(group_text, segments, config, len(chunks)))
        group_text = ""
        segments = []
        previous = None

    for section in sections:
        if not section.text.strip():
            continue
        input_chars += len(section.text)
        if previous is not None and not _same_group(previous, section):
            flush()
        if segments:
            group_text += "\n\n"
        offset = len(group_text)
        group_text += section.text
        segments.append(_Segment(section, offset, len(group_text), 0))
        previous = section
        if section.block_type == "code":
            flush()
    flush()

    lengths = [len(chunk.text) for chunk in chunks]
    seen: set[str] = set()
    duplicate_count = 0
    for chunk in chunks:
        if chunk.content_sha256 in seen:
            duplicate_count += 1
        seen.add(chunk.content_sha256)
    output_chars = sum(lengths)
    return ChunkingResult(
        chunks=chunks,
        stats=ChunkStats(
            chunk_count=len(chunks),
            input_chars=input_chars,
            output_chars=output_chars,
            shortest_chars=min(lengths, default=0),
            longest_chars=max(lengths, default=0),
            average_chars=output_chars / len(chunks) if chunks else 0.0,
            short_chunks=sum(length * 2 < config.chunk_size for length in lengths),
            overlong_chunks=sum(length > config.chunk_size for length in lengths),
            duplicate_chunks=duplicate_count,
        ),
    )
