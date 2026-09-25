"""Print ParsedSection records for one local Markdown or TXT file."""

import argparse
import json
import sys
from dataclasses import asdict

from app.parsers.text import ParseError, parse_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Preview Markdown/TXT ParsedSection output"
    )
    parser.add_argument("path", help="Controlled local .md or .txt file path")
    parser.add_argument("--document-id", help="Optional database document ID")
    args = parser.parse_args(argv)
    try:
        sections = parse_file(args.path, document_id=args.document_id)
    except ParseError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps([asdict(item) for item in sections], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
