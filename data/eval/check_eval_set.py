"""Check draft evaluation records against the versioned sample corpus."""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = Path(__file__).resolve().parent
QUESTIONS = EVAL_DIR / "questions.json"
REVIEW = EVAL_DIR / "REVIEW.md"
FROZEN_TEST = EVAL_DIR / "test.freeze.sha256"
CORPUS = ROOT / "data" / "sample_docs"
MANIFEST = CORPUS / "manifest.json"

EXPECTED_COUNTS = {
    "direct_fact": 12,
    "paraphrase": 6,
    "cross_document": 6,
    "insufficient_evidence": 6,
}
REQUIRED = {
    "id",
    "question",
    "kb_id",
    "category",
    "answerable",
    "expected_facts",
    "gold_evidence",
    "split",
    "fact_group",
    "review_status",
}
OPTIONAL = {
    "paraphrase_of",
    "missing_information",
    "checked_document_ids",
    "reviewed_by",
    "reviewed_at",
}
HEADING = re.compile(r"^(?:## (?P<markdown>.+)|(?P<text>[一二三四五六七八九十]+、.+))$")


def section_texts(content: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in content.splitlines():
        match = HEADING.fullmatch(line)
        if match:
            current = match.group("markdown") or match.group("text")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines) for name, lines in sections.items()}


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read {path}: {exc}") from exc


def validate(records, manifest) -> list[str]:
    errors: list[str] = []
    if not isinstance(records, list):
        return ["questions.json must be a JSON array"]
    if not isinstance(manifest, dict) or not isinstance(
        manifest.get("documents"), list
    ):
        return ["sample document manifest is invalid"]

    kb_ids = {kb["key"] for kb in manifest.get("knowledge_bases", [])}
    sources = {doc["document_id"]: doc for doc in manifest["documents"]}
    if len(sources) != len(manifest["documents"]):
        errors.append("sample manifest has duplicate document IDs")
    source_sections: dict[str, dict[str, str]] = {}
    for document_id, doc in sources.items():
        path = (CORPUS / doc["path"]).resolve()
        if not path.is_relative_to(CORPUS.resolve()) or not path.is_file():
            errors.append(f"manifest {document_id}: source path is missing or unsafe")
            continue
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != doc["file_sha256"]:
            errors.append(f"manifest {document_id}: source SHA-256 differs")
        try:
            source_sections[document_id] = section_texts(raw.decode("utf-8"))
        except UnicodeDecodeError:
            errors.append(f"manifest {document_id}: source is not UTF-8")

    counts = Counter()
    splits = Counter()
    ids: set[str] = set()
    questions: set[tuple[str, str]] = set()
    group_splits: dict[str, set[str]] = defaultdict(set)
    by_id = {}
    for index, row in enumerate(records, start=1):
        label = f"row {index}"
        if not isinstance(row, dict):
            errors.append(f"{label}: record must be an object")
            continue
        missing = REQUIRED - row.keys()
        unknown = row.keys() - REQUIRED - OPTIONAL
        if missing:
            errors.append(f"{label}: missing {', '.join(sorted(missing))}")
        if unknown:
            errors.append(f"{label}: unexpected {', '.join(sorted(unknown))}")
        record_id = row.get("id")
        if not isinstance(record_id, str) or not re.fullmatch(
            r"E7B-[DPCU][0-9]{2}", record_id
        ):
            errors.append(f"{label}: id must match E7B-D01 style")
            continue
        if record_id in ids:
            errors.append(f"{label}: duplicate id {record_id}")
        ids.add(record_id)
        by_id[record_id] = row
        label = record_id

        kb_id = row.get("kb_id")
        if not isinstance(kb_id, str) or kb_id not in kb_ids:
            errors.append(f"{label}: unknown kb_id {kb_id!r}")
        question = row.get("question")
        if not isinstance(question, str) or not question.strip():
            errors.append(f"{label}: question must be nonempty")
        else:
            if isinstance(kb_id, str):
                key = (kb_id, question.strip())
                if key in questions:
                    errors.append(f"{label}: duplicate question in knowledge base")
                questions.add(key)

        category = row.get("category")
        if not isinstance(category, str) or category not in EXPECTED_COUNTS:
            errors.append(f"{label}: unknown category {category!r}")
        else:
            counts[category] += 1
        split = row.get("split")
        if not isinstance(split, str) or split not in {"dev", "test"}:
            errors.append(f"{label}: split must be dev or test")
        else:
            splits[split] += 1
        group = row.get("fact_group")
        if not isinstance(group, str) or not group.strip():
            errors.append(f"{label}: fact_group must be nonempty")
        elif isinstance(split, str) and split in {"dev", "test"}:
            group_splits[group].add(split)
        status = row.get("review_status")
        if not isinstance(status, str) or status not in {"draft", "reviewed"}:
            errors.append(f"{label}: review_status must be draft or reviewed")
        elif status == "reviewed" and any(
            not isinstance(row.get(field), str) or not row[field].strip()
            for field in ("reviewed_by", "reviewed_at")
        ):
            errors.append(f"{label}: reviewed record needs reviewer and date")

        answerable = row.get("answerable")
        if type(answerable) is not bool:
            errors.append(f"{label}: answerable must be boolean")
        facts = row.get("expected_facts")
        if not isinstance(facts, list) or any(
            not isinstance(fact, str) or not fact.strip() for fact in facts
        ):
            errors.append(f"{label}: expected_facts must be a list of nonempty strings")
        elif answerable is True and not facts:
            errors.append(f"{label}: answerable record needs expected_facts")
        elif answerable is False and facts:
            errors.append(f"{label}: insufficient record cannot assert known facts")

        evidence = row.get("gold_evidence")
        if not isinstance(evidence, list):
            errors.append(f"{label}: gold_evidence must be a list")
            continue
        if answerable is True and not evidence:
            errors.append(f"{label}: answerable record needs gold_evidence")
        evidence_docs: set[str] = set()
        for position, item in enumerate(evidence, start=1):
            if not isinstance(item, dict) or set(item) != {
                "document_id",
                "section",
                "quote",
            }:
                errors.append(
                    f"{label} evidence {position}: needs document_id, section, quote"
                )
                continue
            doc_id = item["document_id"]
            section = item["section"]
            quote = item["quote"]
            if not isinstance(doc_id, str) or doc_id not in sources:
                errors.append(
                    f"{label} evidence {position}: unknown document {doc_id!r}"
                )
                continue
            evidence_docs.add(doc_id)
            if sources[doc_id]["kb_key"] != kb_id:
                errors.append(
                    f"{label} evidence {position}: document belongs to another kb"
                )
            if (
                not isinstance(section, str)
                or not isinstance(quote, str)
                or not quote.strip()
            ):
                errors.append(
                    f"{label} evidence {position}: section/quote must be text"
                )
            elif section not in source_sections.get(doc_id, {}):
                errors.append(
                    f"{label} evidence {position}: section {section!r} is absent"
                )
            elif quote not in source_sections[doc_id][section]:
                errors.append(
                    f"{label} evidence {position}: quote is absent from section"
                )

        if category in ("direct_fact", "paraphrase") and len(evidence_docs) != 1:
            errors.append(f"{label}: this category needs evidence from one document")
        if category == "cross_document" and len(evidence_docs) < 2:
            errors.append(f"{label}: cross_document needs at least two documents")
        if category == "insufficient_evidence":
            if answerable is not False:
                errors.append(f"{label}: insufficient_evidence must be unanswerable")
            if (
                not isinstance(row.get("missing_information"), str)
                or not row["missing_information"].strip()
            ):
                errors.append(f"{label}: explain missing_information")
            checked = row.get("checked_document_ids")
            expected_scope = {
                doc_id for doc_id, doc in sources.items() if doc["kb_key"] == kb_id
            }
            if (
                not isinstance(checked, list)
                or any(not isinstance(doc_id, str) for doc_id in checked)
                or len(checked) != len(set(checked))
                or set(checked) != expected_scope
            ):
                errors.append(f"{label}: checked_document_ids must cover this kb")
        elif answerable is not True:
            errors.append(f"{label}: this category must be answerable")

    for row in records:
        if not isinstance(row, dict) or row.get("category") != "paraphrase":
            continue
        record_id = row.get("id", "unknown")
        paired_id = row.get("paraphrase_of")
        paired = by_id.get(paired_id) if isinstance(paired_id, str) else None
        if paired is None or paired.get("category") != "direct_fact":
            errors.append(f"{record_id}: paraphrase_of must name a direct_fact id")
        elif any(
            paired.get(field) != row.get(field)
            for field in ("kb_id", "split", "fact_group")
        ):
            errors.append(
                f"{record_id}: paired direct fact must share kb, split and group"
            )

    if len(records) != 30:
        errors.append(f"expected 30 records, got {len(records)}")
    if counts != Counter(EXPECTED_COUNTS):
        errors.append(f"category counts differ: {dict(counts)}")
    if splits != Counter({"dev": 15, "test": 15}):
        errors.append(f"split counts differ: {dict(splits)}")
    for group, assigned in group_splits.items():
        if len(assigned) != 1:
            errors.append(f"fact_group {group!r} leaks across dev/test")
    return errors


def render_review(records, digest: str) -> str:
    lines = [
        f"<!-- questions_sha256: {digest} -->",
        "# 第 7B 步评测集逐条复核清单",
        "",
        (
            "未复核样本标为 draft。勾选仅表示已阅读；正式使用前须由人工逐项"
            "核对题意、事实与来源，再另行记录审核结论。"
            "test 冻结，不依据其错误反复调参。"
        ),
        "",
    ]
    for split in ("dev", "test"):
        lines.extend([f"## {split}", ""])
        for row in records:
            if row["split"] != split:
                continue
            lines.append(
                f"- [ ] **{row['id']}** · {row['category']} · 库 {row['kb_id']} "
                f"· {row['review_status']}"
            )
            lines.append(f"  - 问题：{row['question']}")
            if row["answerable"]:
                for fact in row["expected_facts"]:
                    lines.append(f"  - 待核事实：{fact}")
                for item in row["gold_evidence"]:
                    lines.append(
                        f"  - 来源 {item['document_id']} / {item['section']}："
                        f"{item['quote']}"
                    )
            else:
                lines.append(f"  - 资料不足原因：{row['missing_information']}")
                lines.append(
                    "  - 核对资料范围：" + "、".join(row["checked_document_ids"])
                )
                for item in row["gold_evidence"]:
                    lines.append(
                        f"  - 范围提示 {item['document_id']} / {item['section']}："
                        f"{item['quote']}"
                    )
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", action="store_true", help="print the review checklist"
    )
    args = parser.parse_args()
    records = load_json(QUESTIONS)
    manifest = load_json(MANIFEST)
    errors = validate(records, manifest)
    if isinstance(records, list) and all(isinstance(row, dict) for row in records):
        test_records = [row for row in records if row.get("split") == "test"]
        canonical_test = json.dumps(
            test_records, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        actual_test_digest = hashlib.sha256(canonical_test).hexdigest()
        try:
            expected_test_digest = FROZEN_TEST.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            errors.append("test.freeze.sha256 is missing or unreadable")
        else:
            if actual_test_digest != expected_test_digest:
                errors.append("test split differs from its frozen SHA-256")
    digest = hashlib.sha256(QUESTIONS.read_bytes()).hexdigest()
    if not args.list:
        if not REVIEW.is_file():
            errors.append("REVIEW.md is missing; generate with --list")
        elif not REVIEW.read_text(encoding="utf-8").startswith(
            f"<!-- questions_sha256: {digest} -->\n"
        ):
            errors.append("REVIEW.md is stale; regenerate with --list")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    if args.list:
        sys.stdout.write(render_review(records, digest))
    else:
        print("OK: 30 draft records; 12 direct, 6 paraphrase, 6 cross, 6 insufficient")
        print("OK: dev 15 / test 15; IDs, groups, source hashes and quotes verified")
        print("OK: frozen test SHA-256 and review checklist are in sync")
        print("NOTE: missing evidence and factual interpretation need human review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
