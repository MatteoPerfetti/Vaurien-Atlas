"""Run, record and reuse proposed analyses in local SQLite."""

import hashlib
import json
import time
from datetime import datetime, timezone
from importlib.resources import files
from uuid import UUID, uuid4

from vaurien_atlas.analyst import ANALYSIS_SCHEMA, INSTRUCTION_VERSION, analyze_paper, validate_analysis
from vaurien_atlas.storage import _require_foreign_keys


def run_analysis(connection, client_factory, *, paper_id: UUID, fingerprint: str, pages: tuple[str, ...],
                 extractor_version: str, model: str, scope: str, extraction_limitations: list[str],
                 max_input_chars: int = 200_000, max_output_tokens: int = 4000):
    """Return (run_id, result, cached); client_factory is only called on cache miss."""
    _require_foreign_keys(connection)
    if connection.in_transaction:
        raise ValueError("analysis requires a connection without pending changes")
    if not isinstance(paper_id, UUID):
        raise ValueError("paper_id must be a UUID")
    # Ensure the source and extraction belong to the paper being analyzed.
    row = connection.execute("SELECT paper_id FROM file_fingerprints WHERE sha256=?", (fingerprint,)).fetchone()
    if not row or row[0] != str(paper_id):
        raise ValueError("fingerprint does not belong to this paper")
    extraction = connection.execute("SELECT pages_json FROM extractions WHERE sha256=? AND extractor_version=?",
                                    (fingerprint, extractor_version)).fetchone()
    if not extraction or tuple(json.loads(extraction[0])) != pages:
        raise ValueError("pages do not match the stored extraction")
    # Hash every input that can affect the analysis to prevent stale cache hits.
    instructions = files("vaurien_atlas").joinpath("instructions/paper_analyst_v1.txt").read_text(encoding="utf-8")
    inputs = {"paper_id": str(paper_id), "fingerprint": fingerprint, "pages": pages,
              "extractor_version": extractor_version, "model": model, "scope": scope,
              "limitations": extraction_limitations, "instruction_version": INSTRUCTION_VERSION,
              "instructions": instructions, "schema": ANALYSIS_SCHEMA,
              "max_input_chars": max_input_chars, "max_output_tokens": max_output_tokens}
    key = hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    row = connection.execute("SELECT id, result_json FROM analysis_runs WHERE cache_key=? AND status='completed' ORDER BY rowid DESC LIMIT 1", (key,)).fetchone()
    if row:
        # Revalidate cached quotations against the supplied source pages.
        result = json.loads(row[1])
        validate_analysis(result["analysis"], pages)
        return UUID(row[0]), result, True
    # Persist the running state before making a potentially long model call.
    run_id = uuid4()
    started = datetime.now(timezone.utc).isoformat()
    with connection:
        connection.execute("INSERT INTO analysis_runs (id,paper_id,fingerprint,cache_key,model,instruction_version,status,started_at) VALUES (?,?,?,?,?,?,?,?)",
                           (str(run_id), str(paper_id), fingerprint, key, model, INSTRUCTION_VERSION, "running", started))
    begin = time.monotonic()
    try:
        result = analyze_paper(client_factory(), model=model, pages=pages, scope=scope,
                               extraction_limitations=extraction_limitations, max_input_chars=max_input_chars,
                               max_output_tokens=max_output_tokens)
        encoded = json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        # Record only the error type, avoiding sensitive exception details in SQLite.
        with connection:
            connection.execute("UPDATE analysis_runs SET status='failed',finished_at=?,duration_seconds=?,failure_type=? WHERE id=?",
                               (datetime.now(timezone.utc).isoformat(), time.monotonic()-begin, type(exc).__name__, str(run_id)))
        raise
    # A completed result remains a proposal; this does not accept its claims.
    with connection:
        connection.execute("UPDATE analysis_runs SET status='completed',finished_at=?,duration_seconds=?,result_json=? WHERE id=?",
                           (datetime.now(timezone.utc).isoformat(), time.monotonic()-begin, encoded, str(run_id)))
    return run_id, result, False
