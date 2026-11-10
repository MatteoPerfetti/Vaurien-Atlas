"""Review source quotations in a saved, explicitly partial analyst draft."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from uuid import UUID

from vaurien_atlas.analyst import SECTIONS, prepare_evidence_review, validate_analysis
from vaurien_atlas.storage import _require_foreign_keys


def _load(connection: sqlite3.Connection, run_id: UUID, extractor_version: str) -> tuple[dict, tuple]:
    """Load a run and the specified extraction without changing either record."""
    row = connection.execute("""SELECT r.result_json, e.pages_json FROM analysis_runs r
        JOIN extractions e ON e.sha256=r.fingerprint WHERE r.id=? AND r.status='completed'
        AND e.extractor_version=?""", (str(run_id), extractor_version)).fetchone()
    if not row:
        raise ValueError("completed run or extraction not found")
    result = json.loads(row[0])
    pages = tuple(json.loads(row[1]))
    recorded_version = result.get("extractor_version")
    if recorded_version and recorded_version != extractor_version:
        raise ValueError("extraction version differs from the recorded run")
    # Old runs lack an extraction-version field: verify their existing matched quotations.
    result["analysis"] = validate_analysis(result["analysis"], pages)
    return prepare_evidence_review(result, pages), pages


def list_evidence(connection: sqlite3.Connection, run_id: UUID, extractor_version: str) -> list:
    """Return unresolved citations, including source-matched evidence per claim."""
    result, _ = _load(connection, run_id, extractor_version)
    entries = result.get("unverified_claims", [])
    for entry in entries:
        index = entry.get("analysis_index")
        entry["source_matched_evidence"] = (result["analysis"][entry["section"]][index - 1]["evidence"]
                                            if index is not None else [])
    return entries


def show_analysis(connection: sqlite3.Connection, run_id: UUID, extractor_version: str) -> dict:
    """Show the source-matched subset and pending citations without modifying storage."""
    result, _ = _load(connection, run_id, extractor_version)
    return result


def show_page(connection: sqlite3.Connection, run_id: UUID, extractor_version: str, page: int) -> str:
    """Return the extracted text for one 1-based PDF page without changing it."""
    _, pages = _load(connection, run_id, extractor_version)
    if type(page) is not int or not 1 <= page <= len(pages):
        raise ValueError("page is outside the stored extraction")
    return pages[page - 1]


def decide_evidence(connection: sqlite3.Connection, run_id: UUID, extractor_version: str, *,
                    section: str, claim_index: int, evidence_index: int, action: str,
                    page: int | None = None, quote: str | None = None) -> None:
    """Replace an unverified citation with source text or reject it explicitly.

    The user decides semantic relevance. The program only checks that a
    replacement quotation occurs on the chosen extracted page.
    """
    _require_foreign_keys(connection)
    if connection.in_transaction:
        raise ValueError("review requires a connection without pending changes")
    if section not in SECTIONS or type(claim_index) is not int or type(evidence_index) is not int:
        raise ValueError("invalid claim or evidence identifier")
    if action not in ("replace", "reject"):
        raise ValueError("action must be replace or reject")
    if action == "reject" and (page is not None or quote is not None):
        raise ValueError("reject does not accept a replacement")
    if action == "replace" and (type(page) is not int or not isinstance(quote, str) or not quote.strip()):
        raise ValueError("replace requires a page and nonblank quotation")

    with connection:
        # Lock while reading and updating to prevent overwriting a concurrent review.
        connection.execute("BEGIN IMMEDIATE")
        result, pages = _load(connection, run_id, extractor_version)
        matches = [entry for entry in result.get("unverified_claims", [])
                   if entry["section"] == section and entry["claim_index"] == claim_index]
        if len(matches) != 1:
            raise ValueError("pending claim not found")
        entry = matches[0]
        items = [item for item in entry["pending_evidence"] if item["evidence_index"] == evidence_index]
        if len(items) != 1:
            raise ValueError("pending citation not found")
        item = items[0]
        replacement = None
        if action == "replace":
            # Reuse the same validation as the analyst, including page bounds.
            wrapper = {name: [] for name in SECTIONS}
            wrapper.update(unknowns=[], extraction_limitations=[])
            claim = entry["claim"]
            wrapper[section] = [{"text": claim["text"], "origin": claim["origin"],
                                 "evidence": [{"page": page, "quote": quote}]}]
            checked = validate_analysis(wrapper, pages)[section][0]
            replacement = checked["evidence"][0]
            index = entry["analysis_index"]
            if index is None:
                # Add the claim only after it has at least one source-matched citation.
                result["analysis"][section].append(checked)
                entry["analysis_index"] = len(result["analysis"][section])
            else:
                result["analysis"][section][index - 1]["evidence"].append(replacement)
        entry["pending_evidence"].remove(item)
        result.setdefault("evidence_review_history", []).append({"section": section, "claim_index": claim_index,
            "evidence_index": evidence_index, "action": action, "original": item["evidence"],
            "replacement": replacement, "reviewed_at": datetime.now(timezone.utc).isoformat()})
        if not entry["pending_evidence"]:
            result["unverified_claims"].remove(entry)
        if not result["unverified_claims"]:
            # All flagged citations were handled; this does not approve every claim.
            result["analysis_status"] = "source_reviewed"
        # Refresh our own progress note; preserve the model's unrelated unknowns.
        result["analysis"]["unknowns"] = [note for note in result["analysis"]["unknowns"] if not re.fullmatch(
            r"\d+ claim\(s\) (?:excluded pending|have citations pending) evidence review\.", note)]
        count = len(result["unverified_claims"])
        if count:
            result["analysis"]["unknowns"].append(f"{count} claim(s) have citations pending evidence review.")
        else:
            result["analysis"]["unknowns"].append("Evidence review recorded; rejected citations remain in review history.")
        result["extractor_version"] = extractor_version
        result["analysis"] = validate_analysis(result["analysis"], pages)
        connection.execute("UPDATE analysis_runs SET result_json=? WHERE id=?",
                           (json.dumps(result, ensure_ascii=False), str(run_id)))


def revise_matched_evidence(connection: sqlite3.Connection, run_id: UUID, extractor_version: str, *,
                            section: str, claim_index: int, evidence_index: int, action: str,
                            page: int | None = None, quote: str | None = None) -> None:
    """Replace or reject a source-matched citation after a human checks its meaning.

    Claim and citation indices refer to the current saved analysis, not to the
    original unverified-claim indices. Removing the last citation removes the
    claim from the source-matched subset; an audit record preserves the decision.
    """
    _require_foreign_keys(connection)
    if connection.in_transaction:
        raise ValueError("review requires a connection without pending changes")
    if section not in SECTIONS or type(claim_index) is not int or type(evidence_index) is not int:
        raise ValueError("invalid claim or evidence identifier")
    if action not in ("replace", "reject"):
        raise ValueError("action must be replace or reject")
    if action == "reject" and (page is not None or quote is not None):
        raise ValueError("reject does not accept a replacement")
    if action == "replace" and (type(page) is not int or not isinstance(quote, str) or not quote.strip()):
        raise ValueError("replace requires a page and nonblank quotation")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        result, pages = _load(connection, run_id, extractor_version)
        claims = result["analysis"][section]
        if not 1 <= claim_index <= len(claims):
            raise ValueError("source-matched claim not found")
        claim = claims[claim_index - 1]
        if not 1 <= evidence_index <= len(claim["evidence"]):
            raise ValueError("source-matched citation not found")
        original = claim["evidence"][evidence_index - 1]
        replacement = None
        if action == "replace":
            wrapper = {name: [] for name in SECTIONS}
            wrapper.update(unknowns=[], extraction_limitations=[])
            wrapper[section] = [{"text": claim["text"], "origin": claim["origin"],
                                 "evidence": [{"page": page, "quote": quote}]}]
            replacement = validate_analysis(wrapper, pages)[section][0]["evidence"][0]
            claim["evidence"][evidence_index - 1] = replacement
        else:
            claim["evidence"].pop(evidence_index - 1)
            if not claim["evidence"]:
                claims.pop(claim_index - 1)
                # Keep pending-claim pointers consistent when the list shifts.
                for pending in result.get("unverified_claims", []):
                    index = pending.get("analysis_index")
                    if pending["section"] == section and index is not None:
                        if index == claim_index:
                            pending["analysis_index"] = None
                        elif index > claim_index:
                            pending["analysis_index"] = index - 1
        result.setdefault("evidence_review_history", []).append({"section": section,
            "analysis_claim_index": claim_index, "evidence_index": evidence_index, "action": action,
            "claim_text": claim["text"], "original": original, "replacement": replacement,
            "reviewed_at": datetime.now(timezone.utc).isoformat()})
        result["extractor_version"] = extractor_version
        result["analysis"] = validate_analysis(result["analysis"], pages)
        connection.execute("UPDATE analysis_runs SET result_json=? WHERE id=?",
                           (json.dumps(result, ensure_ascii=False), str(run_id)))
