"""Fixed-source Markdown/TXT parsing and identifiable error checks."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.parsers import text as parser_module
from app.parsers.text import ParseError, parse_file

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "sample_docs"


def test_sample_markdown_preserves_headings_order_and_facts():
    sections = parse_file(SAMPLES / "a" / "A-EXP-001.md", document_id="A-EXP-001")
    assert [section.section_index for section in sections] == list(range(len(sections)))
    assert [section.heading_path for section in sections] == [
        ["演示数据｜A 库差旅报销规则"],
        ["演示数据｜A 库差旅报销规则", "适用范围"],
        ["演示数据｜A 库差旅报销规则", "住宿标准与材料"],
        ["演示数据｜A 库差旅报销规则", "提交期限与例外"],
    ]
    assert all(section.document_id == "A-EXP-001" for section in sections)
    assert [section.start_line for section in sections] == [3, 8, 12, 16]
    assert [section.end_line for section in sections] == [4, 8, 12, 16]
    assert sections[2].source_locator == "lines:12"
    assert "680 元" in sections[2].text
    assert "15 个自然日" in sections[3].text
    assert "3 个自然日" in sections[3].text
    assert "A-EXP-001" in sections[0].text


def test_sample_txt_and_utf8_bom_keep_numbers_and_order(tmp_path):
    source = SAMPLES / "a" / "A-API-005.txt"
    sections = parse_file(source)
    assert len(sections) == 5
    assert [section.start_line for section in sections] == [1, 5, 8, 11, 14]
    assert all(section.heading_path is None for section in sections)
    assert all(section.page_number is None for section in sections)
    assert "NX-210-P" in sections[1].text
    assert "HTTP 429 / RATE_LIMIT" in sections[2].text
    assert "60 次" in sections[2].text
    assert "HTTP 409 / REVISION_CONFLICT" in sections[3].text
    bom_file = tmp_path / "bom.txt"
    bom_file.write_bytes(b"\xef\xbb\xbf" + source.read_bytes())
    bom_sections = parse_file(bom_file)
    assert [section.text for section in bom_sections] == [
        section.text for section in sections
    ]
    assert bom_sections[0].text.startswith("演示数据")


def test_markdown_blocks_keep_list_code_and_nested_heading(tmp_path):
    source = tmp_path / "blocks.md"
    sentinel = tmp_path / "code-was-executed"
    source.write_text(
        "# 产品 NX-210\n\n"
        "## 费用\n"
        "每月 1,200 元，版本 NX-210-P。  \n"
        "第二行保留 HTTP 429。\n\n"
        "- 项目 1\n"
        "  - 子项 2\n"
        "- 项目 3\n\n"
        "```python\n"
        "print('不会执行')\n"
        f"__import__('pathlib').Path({str(sentinel)!r}).write_text('bad')\n"
        "# 代码里的井号不是标题\n"
        "```\n\n"
        "### 附注\n"
        "[链接](http://127.0.0.1:9/should-not-fetch) 仅保留为文本。\n"
        "## 其他\n"
        "结束。\n",
        encoding="utf-8",
    )
    sections = parse_file(source)
    assert [item.block_type for item in sections] == [
        "paragraph",
        "list",
        "code",
        "paragraph",
        "paragraph",
    ]
    assert [item.heading_path for item in sections] == [
        ["产品 NX-210", "费用"],
        ["产品 NX-210", "费用"],
        ["产品 NX-210", "费用"],
        ["产品 NX-210", "费用", "附注"],
        ["产品 NX-210", "其他"],
    ]
    assert "1,200 元" in sections[0].text
    assert "NX-210-P" in sections[0].text
    assert "- 项目 1\n  - 子项 2\n- 项目 3" == sections[1].text
    assert "print('不会执行')" in sections[2].text
    assert "# 代码里的井号不是标题" in sections[2].text
    assert "http://127.0.0.1:9/should-not-fetch" in sections[3].text
    assert sections[2].source_locator == "lines:11-15"
    assert not sentinel.exists()


@pytest.mark.parametrize(
    ("filename", "data", "code"),
    [
        ("missing.txt", None, "FILE_NOT_FOUND"),
        ("unsupported.pdf", b"%PDF-1.4", "UNSUPPORTED_FORMAT"),
        ("bad.txt", b"\xff\xfe\x00x", "UNSUPPORTED_ENCODING"),
        ("empty.txt", b" \n\t\n", "EMPTY_BODY"),
        ("headings.md", b"# Title\n\n## Subtitle\n", "EMPTY_BODY"),
        ("separator.md", b"---\n\n***\n", "EMPTY_BODY"),
        ("fence.md", b"# Title\n```python\nprint(1)\n", "UNCLOSED_CODE_FENCE"),
    ],
)
def test_identifiable_errors(tmp_path, filename, data, code):
    path = tmp_path / filename
    if data is not None:
        path.write_bytes(data)
    with pytest.raises(ParseError) as failure:
        parse_file(path)
    assert failure.value.code == code


def test_unexpected_parse_failure_has_stable_error_code(tmp_path, monkeypatch):
    source = tmp_path / "valid.txt"
    source.write_text("正文", encoding="utf-8")

    def fail(_source, _document_id):
        raise RuntimeError("internal parser failure")

    monkeypatch.setattr(parser_module, "_parse_txt", fail)
    with pytest.raises(ParseError) as failure:
        parse_file(source)
    assert failure.value.code == "PARSE_ERROR"
    assert isinstance(failure.value.__cause__, RuntimeError)


def test_cli_prints_json_and_error_code(tmp_path):
    valid = tmp_path / "example.txt"
    valid.write_text("演示数据\n\n编号 NX-210-P。\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "app.parsers.cli", str(valid)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert [item["text"] for item in json.loads(result.stdout)] == [
        "演示数据",
        "编号 NX-210-P。",
    ]
    missing = subprocess.run(
        [sys.executable, "-m", "app.parsers.cli", str(tmp_path / "missing.txt")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "FILE_NOT_FOUND" in missing.stderr
    assert not missing.stdout
