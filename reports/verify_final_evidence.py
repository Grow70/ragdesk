"""Validate the step-30 not-run archive; never infer model quality from integrity."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def inside(base, relative):
    path = (base / relative).resolve()
    require(path.is_relative_to(base.resolve()), "Path escapes archive root")
    return path


def verify(base, *, working_tree=False, source_archive=False):
    sums = read(base / "SHA256SUMS.json")
    actual = {
        str(p.relative_to(base))
        for p in base.rglob("*")
        if p.is_file() and p.name != "SHA256SUMS.json"
    }
    require(actual == set(sums), "Archive file set differs from checksum inventory")
    for relative, expected in sums.items():
        require(
            digest(inside(base, relative)) == expected, f"Hash mismatch: {relative}"
        )
    audit = read(base / "audit.json")
    require(
        audit["experiment_status"] == "not_run"
        and audit["executed_n_by_arm"] == dict.fromkeys("ABCD", 0)
        and audit["paired_n"] == audit["human_answer_review_n"] == 0,
        "Audit invents completed experiments",
    )
    questions = read(base / "questions.snapshot.json")
    selected = [q for q in questions if q["split"] == "test"]
    frozen = hashlib.sha256(canonical(selected)).hexdigest()
    require(
        frozen == (base / "test.freeze.sha256").read_text().strip(),
        "Test freeze changed",
    )
    require(frozen == audit["test_canonical_sha256"], "Audit freeze mismatch")
    require(len(selected) == audit["test_n"], "Sample count mismatch")
    rows = [
        json.loads(line) for line in (base / "results.jsonl").read_text().splitlines()
    ]
    expected = {(q["id"], arm) for q in selected for arm in "ABCD"}
    require(len(rows) == len(expected), "Missing or extra result rows")
    require(
        {(r["id"], r["arm"]) for r in rows} == expected, "Duplicate/missing result IDs"
    )
    unmeasured = (
        "retrieved_chunks",
        "answer",
        "citations",
        "tool_call_count",
        "model_request_count",
        "retrieval_ms",
        "total_ms",
        "usage",
        "estimated_cost",
        "retrieval_metrics",
        "factual_correctness",
        "citation_support",
    )
    for row in rows:
        require(
            row["execution"] == "not_run" and row["split"] == "test",
            "Unexpected execution",
        )
        require(bool(row["not_run_reasons"]), "Missing not-run reason")
        require(
            all(row[k] is None for k in unmeasured),
            "Invented measurement in not_run row",
        )
    for arm in "ABC":
        manifest = read(base / f"{arm}-test-preflight/manifest.json")
        require(
            manifest["split"] == "test" and manifest["status"] == "not_run",
            "Raw scope changed",
        )
        raw = [
            json.loads(s)
            for s in (base / f"{arm}-test-preflight/results.jsonl")
            .read_text()
            .splitlines()
        ]
        require(
            {r["id"] for r in raw} == {q["id"] for q in selected}, "Raw IDs mismatch"
        )
        require(
            len(raw) == len(selected) and all(r["execution"] == "not_run" for r in raw),
            "Raw execution mismatch",
        )
    failures = read(base / "failures.json")
    require(
        failures["cases"] == [] and failures["executed_sample_n"] == 0,
        "Unbacked failure cases",
    )
    reviews = read(base / "human-review.template.json")
    require(len(reviews) == len(expected), "Review template count mismatch")
    require({(r["id"], r["arm"]) for r in reviews} == expected, "Review IDs mismatch")
    require(
        all(
            r["status"] == "pending_execution"
            and r["facts_correct"] is None
            and r["citations_support_conclusion"] is None
            and r["reviewed_by"] is None
            for r in reviews
        ),
        "Unbacked human review",
    )
    code = read(base / "code-version.json")
    if working_tree:
        for path, expected_hash in code["source_files"].items():
            require(
                digest(inside(ROOT, path)) == expected_hash,
                f"Working code changed: {path}",
            )
        require(
            digest(ROOT / "data/eval/questions.json") == audit["questions_file_sha256"],
            "Working questions changed",
        )
        corpus = read(base / "corpus-manifest.snapshot.json")
        require(
            digest(ROOT / "data/sample_docs/manifest.json")
            == digest(base / "corpus-manifest.snapshot.json"),
            "Corpus manifest changed",
        )
        for doc in corpus["documents"]:
            require(
                digest(inside(ROOT / "data/sample_docs", doc["path"]))
                == doc["file_sha256"],
                "Corpus file changed",
            )
    if source_archive:
        archive = code["baseline_archive"]
        require(
            digest(inside(ROOT, archive["path"])) == archive["sha256"],
            "Source archive changed",
        )
    return len(selected), len(rows), len(sums)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive", type=Path, default=ROOT / "reports/evidence/step30"
    )
    parser.add_argument("--check-working-tree", action="store_true")
    parser.add_argument("--check-source-archive", action="store_true")
    args = parser.parse_args()
    try:
        n, rows, files = verify(
            args.archive,
            working_tree=args.check_working_tree,
            source_archive=args.check_source_archive,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(
        f"OK: {files} files; test n={n}; {rows} not_run rows; real metrics remain unknown"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
