"""Character-based chunking boundaries, provenance, and statistics."""

import hashlib

import pytest

from app.chunking import ChunkConfig, chunk_sections
from app.parsers.pdf import parse_pdf
from app.parsers.text import ParsedSection


def _section(
    text: str,
    *,
    index: int = 0,
    heading: list[str] | None = None,
    page: int | None = None,
    start_line: int | None = 1,
    block_type: str = "paragraph",
    document_id: str | None = "doc-demo",
) -> ParsedSection:
    end_line = start_line + text.count("\n") if start_line is not None else None
    locator = f"page:{page}" if page is not None else f"lines:{start_line}-{end_line}"
    return ParsedSection(
        text=text,
        section_index=index,
        heading_path=heading,
        page_number=page,
        source_locator=locator,
        document_id=document_id,
        start_line=start_line,
        end_line=end_line,
        block_type=block_type,
    )


def test_defaults_and_short_text_produce_one_chunk_without_padding():
    assert ChunkConfig() == ChunkConfig(chunk_size=600, overlap=80)
    section = _section("报销 680 元。")
    result = chunk_sections([section])
    assert len(result.chunks) == 1
    chunk = result.chunks[0]
    assert chunk.text == section.text
    assert chunk.ordinal == 0
    assert chunk.content_sha256 == hashlib.sha256(section.text.encode()).hexdigest()
    assert chunk.source_spans[0].char_start == 0
    assert chunk.source_spans[0].char_end == len(section.text)
    assert result.stats.chunk_count == 1
    assert result.stats.duplicate_chunks == 0
    assert result.stats.overlong_chunks == 0


def test_same_heading_paragraphs_pack_but_heading_change_splits():
    sections = [
        _section("报销 680 元。", heading=["制度", "报销"], index=0, start_line=2),
        _section("期限 15 天。", heading=["制度", "报销"], index=1, start_line=4),
        _section("NX-210-P 生效。", heading=["制度", "产品"], index=2, start_line=7),
    ]
    result = chunk_sections(sections, ChunkConfig(chunk_size=100, overlap=10))
    assert [chunk.text for chunk in result.chunks] == [
        "报销 680 元。\n\n期限 15 天。",
        "NX-210-P 生效。",
    ]
    assert [chunk.heading_path for chunk in result.chunks] == [
        ["制度", "报销"],
        ["制度", "产品"],
    ]
    assert [span.section_index for span in result.chunks[0].source_spans] == [0, 1]
    assert (result.chunks[0].start_line, result.chunks[0].end_line) == (2, 4)
    assert [chunk.ordinal for chunk in result.chunks] == [0, 1]


def test_paragraph_boundary_wins_over_later_sentence_boundary():
    sections = [
        _section("甲" * 11, index=0),
        _section("乙乙乙。丙丙丙丙", index=1, start_line=3),
    ]
    result = chunk_sections(sections, ChunkConfig(chunk_size=20, overlap=2))
    assert result.chunks[0].text == "甲" * 11
    assert result.chunks[0].source_spans[0].char_end == 11


def test_chinese_sentence_boundary_is_preferred_before_hard_cut():
    text = "甲" * 12 + "。" + "乙" * 12 + "。" + "丙" * 12 + "。"
    result = chunk_sections([_section(text)], ChunkConfig(chunk_size=20, overlap=3))
    assert len(result.chunks) >= 3
    assert all(0 < len(chunk.text) <= 20 for chunk in result.chunks)
    assert all(chunk.text.endswith("。") for chunk in result.chunks)
    covered = set()
    for chunk in result.chunks:
        for span in chunk.source_spans:
            covered.update(range(span.char_start, span.char_end))
    assert covered == set(range(len(text)))


def test_long_unpunctuated_paragraph_uses_char_limit_and_overlap():
    text = "甲" * 240
    result = chunk_sections([_section(text)], ChunkConfig(chunk_size=100, overlap=20))
    assert [len(chunk.text) for chunk in result.chunks] == [100, 100, 80]
    assert [chunk.source_spans[0].char_start for chunk in result.chunks] == [
        0,
        80,
        160,
    ]
    assert all(len(chunk.text) <= 100 for chunk in result.chunks)
    assert result.stats.overlong_chunks == 0
    assert result.stats.input_chars == 240
    assert result.stats.output_chars == 280


def test_empty_text_creates_no_chunks():
    result = chunk_sections([_section(" \n ")])
    assert result.chunks == []
    assert result.stats.chunk_count == 0
    assert result.stats.short_chunks == 0
    assert chunk_sections([]).chunks == []


def test_code_block_stays_separate_and_preserves_markers():
    sections = [
        _section("说明。", heading=["开发"], index=0, start_line=2),
        _section(
            "```python\nprint('NX-210')\n```",
            heading=["开发"],
            index=1,
            start_line=4,
            block_type="code",
        ),
        _section("结束。", heading=["开发"], index=2, start_line=8),
    ]
    result = chunk_sections(sections, ChunkConfig(chunk_size=100, overlap=10))
    assert [chunk.text for chunk in result.chunks] == [
        "说明。",
        "```python\nprint('NX-210')\n```",
        "结束。",
    ]
    assert result.chunks[1].source_spans[0].start_line == 4
    assert result.chunks[1].source_spans[0].end_line == 6


def test_long_code_block_keeps_exact_source_slices():
    source = "```text\n    ABCDEFGHIJ\n    KLMNOPQRST\n```"
    result = chunk_sections(
        [_section(source, block_type="code")],
        ChunkConfig(chunk_size=16, overlap=4),
    )
    assert len(result.chunks) > 1
    assert all(len(chunk.text) <= 16 for chunk in result.chunks)
    for chunk in result.chunks:
        span = chunk.source_spans[0]
        assert chunk.text == source[span.char_start : span.char_end]


def test_pdf_pages_never_merge_and_keep_physical_locator():
    parsed = parse_pdf("tests/fixtures/pdf_mixed.pdf")
    result = chunk_sections(parsed.sections)
    assert [chunk.page_number for chunk in result.chunks] == [1, 4]
    assert [chunk.source_spans[0].source_locator for chunk in result.chunks] == [
        "page:1",
        "page:4",
    ]
    assert [chunk.ordinal for chunk in result.chunks] == [0, 1]
    assert all(len(chunk.source_spans) == 1 for chunk in result.chunks)


def test_determinism_and_exact_duplicate_statistics():
    sections = [
        _section("相同内容。", page=1, start_line=None, index=0),
        _section("相同内容。", page=2, start_line=None, index=1),
    ]
    config = ChunkConfig(chunk_size=30, overlap=5)
    first = chunk_sections(sections, config)
    assert first == chunk_sections(sections, config)
    assert first.stats.duplicate_chunks == 1
    assert first.stats.short_chunks == 2
    assert first.stats.longest_chars == len("相同内容。")


@pytest.mark.parametrize(
    ("size", "overlap"),
    [(0, 0), (-1, 0), (10, 10), (10, 11), (10, -1), ("600", 80), (True, 0)],
)
def test_invalid_config_is_rejected(size, overlap):
    with pytest.raises(ValueError):
        ChunkConfig(chunk_size=size, overlap=overlap)


def test_mixed_document_ids_are_rejected():
    sections = [_section("A", document_id="one"), _section("B", document_id="two")]
    with pytest.raises(ValueError, match="document"):
        chunk_sections(sections)
