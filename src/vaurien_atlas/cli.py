"""Manual import command."""

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from uuid import UUID

from vaurien_atlas.extraction import ExtractionError, extract_pdf
from vaurien_atlas.ingestion import import_pdf
from vaurien_atlas.paper import ReadingStatus
from vaurien_atlas.storage import initialize_database, open_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vaurien-atlas")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("import-pdf", help="register and extract one PDF")
    command.add_argument("pdf", help="path to the original PDF")
    command.add_argument("--db", required=True, help="SQLite database path; parent must exist")
    command.add_argument("--status", required=True, choices=[s.value for s in ReadingStatus])
    analyze = sub.add_parser("analyze-pdf", help="analyze a previously imported PDF using a model")
    analyze.add_argument("pdf", help="path previously registered with import-pdf")
    analyze.add_argument("--db", required=True, help="existing SQLite database path")
    analyze.add_argument("--model", required=True, help="model available to your API account")
    args = parser.parse_args(argv)
    try:
        with closing(open_database(args.db)) as connection:
            initialize_database(connection)
            if args.command == "analyze-pdf":
                from vaurien_atlas.analysis_workflow import run_analysis
                path = os.path.normcase(str(Path(args.pdf).resolve(strict=True)))
                row = connection.execute("""SELECT i.sha256, f.paper_id FROM imported_paths i
                    JOIN file_fingerprints f ON f.sha256=i.sha256 WHERE i.path=?""", (path,)).fetchone()
                if row is None:
                    raise ValueError("PDF has not been imported; run import-pdf first")
                try:
                    extraction = extract_pdf(connection, path, row[0])
                except ExtractionError as exc:
                    print(f"Extraction failed: {exc}", file=sys.stderr)
                    return 2
                limitations = ["Text-only extraction: formulas, figures and layout may be missing or inaccurate."]
                if extraction.empty_pages:
                    limitations.append("No extracted text on pages: " + ", ".join(map(str, extraction.empty_pages)))
                def client_factory():
                    try:
                        from openai import OpenAI
                    except ImportError as exc:
                        raise ValueError("OpenAI SDK not installed; install the analyst extra") from exc
                    return OpenAI(max_retries=0, timeout=120.0)
                try:
                    run_id, result, cached = run_analysis(connection, client_factory, paper_id=UUID(row[1]),
                        fingerprint=row[0], pages=extraction.pages, extractor_version=extraction.extractor_version,
                        model=args.model, scope="full_text", extraction_limitations=limitations)
                except Exception as exc:
                    print(f"Analysis failed ({type(exc).__name__}); see local run status and check configuration.", file=sys.stderr)
                    return 2
                print(f"Run: {run_id} ({'cached' if cached else 'new'})")
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            result = import_pdf(connection, args.pdf, ReadingStatus(args.status))
            print(f"Paper: {result.paper_id} ({'duplicate' if result.is_duplicate else 'new'})")
            print(f"SHA-256: {result.sha256}")
            try:
                extraction = extract_pdf(connection, result.path, result.sha256)
            except ExtractionError as exc:
                print(f"Extraction failed; paper registration retained: {exc}", file=sys.stderr)
                return 2
            print(f"Pages: {len(extraction.pages)} ({'cached' if extraction.cached else 'extracted'})")
            if extraction.empty_pages:
                print("Warning: no text extracted on pages " + ", ".join(map(str, extraction.empty_pages)))
            sparse = [str(i) for i, text in enumerate(extraction.pages, 1) if 0 < len(text.strip()) < 50]
            if sparse:
                print("Warning: little text extracted on pages " + ", ".join(sparse) + "; inspect manually")
            return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Import failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
