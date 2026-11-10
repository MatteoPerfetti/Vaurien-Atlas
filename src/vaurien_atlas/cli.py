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
    """Parse a manual import or analysis command and report its result."""
    # Define arguments for each operation; neither command watches folders.
    parser = argparse.ArgumentParser(prog="vaurien-atlas")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("import-pdf", help="register and extract one PDF")
    command.add_argument("pdf", help="path to the original PDF")
    command.add_argument("--db", required=True, help="SQLite database path; parent must exist")
    command.add_argument("--status", required=True, choices=[s.value for s in ReadingStatus])
    analyze = sub.add_parser("analyze-pdf", help="analyze a previously imported PDF using a model")
    analyze.add_argument("pdf", help="path previously registered with import-pdf")
    analyze.add_argument("--db", required=True, help="existing SQLite database path")
    analyze.add_argument("--model", default="gpt-6-luna", help="model available to your account (default: gpt-6-luna)")
    analyze.add_argument("--provider", choices=("openai-api", "chatgpt-plan"), default="chatgpt-plan",
                         help="authentication route (default: chatgpt-plan)")
    analyze.add_argument("--retry-partial", action="store_true", help="rerun a cached partial analysis")
    pending = sub.add_parser("review-list", help="show pending topic and relationship proposals")
    pending.add_argument("--db", required=True)
    review = sub.add_parser("review-decide", help="accept or reject one pending proposal")
    review.add_argument("--db", required=True)
    review.add_argument("--kind", required=True, choices=("topic", "relationship"))
    review.add_argument("--id", required=True, help="proposal UUID")
    review.add_argument("--decision", required=True, choices=("accepted", "rejected"))
    evidence_list = sub.add_parser("evidence-list", help="inspect citations needing review in an analyst run")
    evidence_list.add_argument("--db", required=True)
    evidence_list.add_argument("--run", required=True)
    evidence_list.add_argument("--extractor-version", required=True)
    evidence_decide = sub.add_parser("evidence-decide", help="replace or reject one unverified citation")
    evidence_decide.add_argument("--db", required=True)
    evidence_decide.add_argument("--run", required=True)
    evidence_decide.add_argument("--extractor-version", required=True)
    evidence_decide.add_argument("--section", required=True)
    evidence_decide.add_argument("--claim", required=True, type=int)
    evidence_decide.add_argument("--evidence", required=True, type=int)
    evidence_decide.add_argument("--action", required=True, choices=("replace", "reject"))
    evidence_decide.add_argument("--page", type=int)
    evidence_decide.add_argument("--quote")
    show = sub.add_parser("analysis-show", help="show a saved analysis and review state")
    show.add_argument("--db", required=True)
    show.add_argument("--run", required=True)
    show.add_argument("--extractor-version", required=True)
    page_show = sub.add_parser("page-show", help="show one page of extracted source text")
    page_show.add_argument("--db", required=True)
    page_show.add_argument("--run", required=True)
    page_show.add_argument("--extractor-version", required=True)
    page_show.add_argument("--page", required=True, type=int)
    revise = sub.add_parser("evidence-revise", help="replace or reject a source-matched citation")
    revise.add_argument("--db", required=True)
    revise.add_argument("--run", required=True)
    revise.add_argument("--extractor-version", required=True)
    revise.add_argument("--section", required=True)
    revise.add_argument("--claim", required=True, type=int, help="1-based index in the current analysis section")
    revise.add_argument("--evidence", required=True, type=int, help="1-based index in the claim's current evidence")
    revise.add_argument("--action", required=True, choices=("replace", "reject"))
    revise.add_argument("--page", type=int)
    revise.add_argument("--quote")
    mapping = sub.add_parser("map-topics", help="propose topic links from a completed analysis")
    mapping.add_argument("--db", required=True)
    mapping.add_argument("--run", required=True, help="completed analysis run UUID")
    mapping.add_argument("--extractor-version", required=True, help="version used for the analysis source")
    mapping.add_argument("--model", default="gpt-6-luna")
    mapping.add_argument("--provider", choices=("openai-api", "chatgpt-plan"), default="chatgpt-plan")
    mapping.add_argument("--query", action="append", default=[], help="literal candidate search; may be repeated")
    mapping.add_argument("--allow-partial", action="store_true", help="explicitly map only source-matched claims from a partial analysis")
    args = parser.parse_args(argv)
    try:
        with closing(open_database(args.db)) as connection:
            initialize_database(connection)
            if args.command == "evidence-list":
                from vaurien_atlas.evidence_review import list_evidence
                print(json.dumps(list_evidence(connection, UUID(args.run), args.extractor_version),
                                 ensure_ascii=False, indent=2))
                return 0
            if args.command == "analysis-show":
                from vaurien_atlas.evidence_review import show_analysis
                print(json.dumps(show_analysis(connection, UUID(args.run), args.extractor_version),
                                 ensure_ascii=False, indent=2))
                return 0
            if args.command == "page-show":
                from vaurien_atlas.evidence_review import show_page
                print(show_page(connection, UUID(args.run), args.extractor_version, args.page))
                return 0
            if args.command == "evidence-revise":
                from vaurien_atlas.evidence_review import revise_matched_evidence
                revise_matched_evidence(connection, UUID(args.run), args.extractor_version, section=args.section,
                                        claim_index=args.claim, evidence_index=args.evidence, action=args.action,
                                        page=args.page, quote=args.quote)
                print("Source-matched evidence review saved locally.")
                return 0
            if args.command == "evidence-decide":
                from vaurien_atlas.evidence_review import decide_evidence
                decide_evidence(connection, UUID(args.run), args.extractor_version, section=args.section,
                                claim_index=args.claim, evidence_index=args.evidence, action=args.action,
                                page=args.page, quote=args.quote)
                print("Evidence review saved locally.")
                return 0
            if args.command == "review-list":
                from dataclasses import asdict
                from vaurien_atlas.review import list_proposals
                proposals = list_proposals(connection)
                print(json.dumps({name: [asdict(item) for item in items] for name, items in proposals.items()},
                                 ensure_ascii=False, indent=2, default=str))
                return 0
            if args.command == "review-decide":
                from vaurien_atlas.knowledge import ReviewStatus
                from vaurien_atlas.review import decide
                decide(connection, args.kind, UUID(args.id), ReviewStatus(args.decision))
                print(f"{args.kind} {args.id}: {args.decision}")
                return 0
            if args.command == "map-topics":
                from vaurien_atlas.storage import search_topics
                from vaurien_atlas.topic_mapper import VERSION, propose_topics, stage_proposals
                run_id = UUID(args.run)
                if len(args.query) > 5:
                    raise ValueError("at most five candidate queries are allowed")
                row = connection.execute("""SELECT r.paper_id, r.result_json, e.pages_json FROM analysis_runs r
                    JOIN extractions e ON e.sha256=r.fingerprint WHERE r.id=? AND r.status='completed'
                    AND e.extractor_version=?""", (str(run_id), args.extractor_version)).fetchone()
                if row is None:
                    raise ValueError("completed run or matching extraction not found")
                from vaurien_atlas.analyst import prepare_evidence_review
                pages = tuple(json.loads(row[2]))
                saved_result = json.loads(row[1])
                if saved_result.get("extractor_version") and saved_result["extractor_version"] != args.extractor_version:
                    raise ValueError("extraction version differs from the recorded run")
                saved_result = prepare_evidence_review(saved_result, pages)
                if saved_result.get("analysis_status") == "partial" and not args.allow_partial:
                    raise ValueError("analysis is partial; review unverified claims or use --allow-partial")
                analysis = saved_result["analysis"]
                # Retrieve only bounded candidates selected by explicit search terms.
                candidates = {}
                for query in args.query:
                    for topic in search_topics(connection, query, limit=10):
                        candidates[topic.id] = topic
                candidates = tuple(candidates.values())
                try:
                    if args.provider == "chatgpt-plan":
                        from vaurien_atlas.chatgpt_plan import sign_in
                        client = sign_in(args.model)
                    else:
                        from openai import OpenAI
                        client = OpenAI(max_retries=0, timeout=120.0)
                    proposals = propose_topics(client, model=args.model, analysis=analysis, pages=pages, candidates=candidates)
                    stage_proposals(connection, paper_id=UUID(row[0]), proposals=proposals, analysis=analysis,
                        pages=pages, candidates=candidates, provenance=f"{VERSION}; provider={args.provider}; model={args.model}; analysis={run_id}",
                        analysis_run_id=run_id, extractor_version=args.extractor_version, allow_partial=args.allow_partial)
                except ImportError:
                    print("Mapper failed: install the analyst extra.", file=sys.stderr)
                    return 2
                except Exception as exc:
                    print(f"Mapper failed ({type(exc).__name__}); no proposals were staged.", file=sys.stderr)
                    return 2
                print(f"Staged {len(proposals['new_topics'])} new topics and {len(proposals['links'])} links for review.")
                return 0
            if args.command == "analyze-pdf":
                # Only analyze a path that was already registered in this database.
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
                # The model must be told what text-only extraction can miss.
                limitations = ["Text-only extraction: formulas, figures and layout may be missing or inaccurate."]
                if extraction.empty_pages:
                    limitations.append("No extracted text on pages: " + ", ".join(map(str, extraction.empty_pages)))
                # Construct the external API client only if a cache miss requires it.
                def client_factory():
                    # The plan route never falls back to the API key or paid API.
                    if args.provider == "chatgpt-plan":
                        from vaurien_atlas.chatgpt_plan import sign_in
                        return sign_in(args.model)
                    try:
                        from openai import OpenAI
                    except ImportError as exc:
                        raise ValueError("OpenAI SDK not installed; install the analyst extra") from exc
                    return OpenAI(max_retries=0, timeout=120.0)
                try:
                    run_id, result, cached = run_analysis(connection, client_factory, paper_id=UUID(row[1]),
                        fingerprint=row[0], pages=extraction.pages, extractor_version=extraction.extractor_version,
                        model=args.model, scope="full_text", extraction_limitations=limitations,
                        provider=args.provider, retry_partial=args.retry_partial)
                except Exception as exc:
                    from vaurien_atlas.analyst import AnalysisValidationError
                    from vaurien_atlas.chatgpt_plan import PlanAuthError, PlanResponseError
                    # Only display messages from our controlled error classes, never raw provider errors.
                    if isinstance(exc, (AnalysisValidationError, PlanAuthError, PlanResponseError)):
                        print(f"Analysis failed ({type(exc).__name__}): {exc}", file=sys.stderr)
                    else:
                        print(f"Analysis failed ({type(exc).__name__}); see local run status and check configuration.", file=sys.stderr)
                    return 2
                print(f"Run: {run_id} ({'cached' if cached else 'new'})")
                print(f"Extractor version: {extraction.extractor_version}")
                if result.get("analysis_status") == "partial":
                    print("Partial analysis: some claims have unverified quotations; review unverified_claims.")
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 0
            # Import first; if extraction fails, the paper registration remains.
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
    # Convert the returned status to a shell exit code when run as a module.
    raise SystemExit(main())
