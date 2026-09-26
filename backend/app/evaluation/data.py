"""Reviewed sample loading, source identity and deterministic evidence positions."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from app.parsers.text import parse_file

ROOT = Path(__file__).resolve().parents[3]


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _locations(raw, parsed, gold):
    headings = []
    for number, line in enumerate(raw.splitlines(), 1):
        match = re.fullmatch(r"(?:## (.+)|([一二三四五六七八九十]+、.+))", line)
        if match:
            headings.append((number, match.group(1) or match.group(2)))
    ranges = [
        (
            line,
            headings[i + 1][0] if i + 1 < len(headings) else len(raw.splitlines()) + 1,
        )
        for i, (line, title) in enumerate(headings)
        if title == gold["section"]
    ]
    locations = []
    for section in parsed:
        if not any(low <= section.start_line < high for low, high in ranges):
            continue
        start = section.text.find(gold["quote"])
        while start >= 0:
            locations.append(
                {
                    "section_index": section.section_index,
                    "char_start": start,
                    "char_end": start + len(gold["quote"]),
                }
            )
            start = section.text.find(gold["quote"], start + 1)
    return locations


def load_dataset(path: Path, manifest_path: Path, split="dev"):
    raw = path.read_bytes()
    records = json.loads(raw)
    if not isinstance(records, list) or not records:
        raise ValueError("INVALID_DATASET")
    ids = set()
    for row in records:
        required = {
            "id",
            "question",
            "kb_id",
            "category",
            "answerable",
            "expected_facts",
            "gold_evidence",
            "split",
            "review_status",
        }
        if not isinstance(row, dict) or not required <= row.keys():
            raise ValueError("INVALID_SAMPLE")
        if not isinstance(row["id"], str) or row["id"] in ids:
            raise ValueError("DUPLICATE_ID")
        ids.add(row["id"])
        if (
            type(row["answerable"]) is not bool
            or not isinstance(row["gold_evidence"], list)
            or not isinstance(row["question"], str)
            or not row["question"].strip()
            or row["split"] not in ("dev", "test")
            or row["review_status"] not in ("draft", "reviewed")
        ):
            raise ValueError("INVALID_SAMPLE")
    selected = [row for row in records if row["split"] == split]
    if not selected:
        raise ValueError("EMPTY_SPLIT")
    if split == "test":
        freeze = path.parent / "test.freeze.sha256"
        if not freeze.exists() or freeze.read_text().strip() != digest(
            canonical(selected)
        ):
            raise ValueError("TEST_FREEZE_MISMATCH")
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    sources = {}
    for doc in manifest["documents"]:
        doc_id = doc["document_id"]
        if doc_id in sources:
            raise ValueError("DUPLICATE_DOCUMENT")
        source_path = (manifest_path.parent / doc["path"]).resolve()
        if not source_path.is_relative_to(manifest_path.parent.resolve()):
            raise ValueError("INVALID_SOURCE_PATH")
        source_raw = source_path.read_bytes()
        if digest(source_raw) != doc["file_sha256"]:
            raise ValueError("SOURCE_HASH_MISMATCH")
        sources[doc_id] = {
            **doc,
            "raw": source_raw.decode("utf-8"),
            "parsed": parse_file(source_path, document_id=doc_id),
        }
    blockers = []
    for row in selected:
        if row["kb_id"] not in {d["kb_key"] for d in sources.values()}:
            raise ValueError("UNKNOWN_KB")
        if row["review_status"] != "reviewed":
            blockers.append("UNREVIEWED_SAMPLES")
        else:
            if (
                not isinstance(row.get("reviewed_by"), str)
                or not row["reviewed_by"].strip()
            ):
                raise ValueError("MISSING_REVIEWER")
            try:
                datetime.fromisoformat(row["reviewed_at"])
            except (KeyError, ValueError, TypeError):
                raise ValueError("INVALID_REVIEW_DATE") from None
        for gold in row["gold_evidence"]:
            if not isinstance(gold, dict) or set(gold) != {
                "document_id",
                "section",
                "quote",
            }:
                raise ValueError("INVALID_GOLD")
            doc = sources.get(gold["document_id"])
            if doc is None or doc["kb_key"] != row["kb_id"]:
                raise ValueError("GOLD_KB_MISMATCH")
            if not isinstance(gold["quote"], str) or not gold["quote"].strip():
                raise ValueError("INVALID_GOLD")
            gold["locations"] = _locations(doc["raw"], doc["parsed"], gold)
            if not gold["locations"]:
                raise ValueError("GOLD_NOT_FOUND_IN_PARSED_SECTION")
    return {
        "questions": selected,
        "blockers": sorted(set(blockers)),
        "dataset_sha256": digest(raw),
        "manifest_sha256": digest(manifest_raw),
        "documents": manifest["documents"],
        "raw_dataset": raw.decode("utf-8"),
    }
