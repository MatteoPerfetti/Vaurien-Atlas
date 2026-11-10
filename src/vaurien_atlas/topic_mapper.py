"""Propose and stage evidence-backed paper-topic links for human review."""

import json
import sqlite3
from importlib.resources import files
from uuid import UUID, uuid4

from vaurien_atlas.analyst import SECTIONS, _object, prepare_evidence_review, validate_analysis
from vaurien_atlas.knowledge import ReviewStatus, Topic
from vaurien_atlas.storage import _require_foreign_keys

VERSION = "topic_mapper_v1"
_str = {"type": "string"}
SCHEMA = _object({"new_topics": {"type": "array", "items": _object({"name": _str, "definition": _str})},
                  "links": {"type": "array", "items": _object({"existing_id": _str, "new_name": _str,
                      "page": {"type": "integer"}, "quote": _str})}})


def propose_topics(client, *, model: str, analysis: dict, pages: tuple[str, ...], candidates: tuple[Topic, ...],
                   max_input_chars: int = 100_000, max_output_tokens: int = 3000) -> dict:
    """Make one bounded model call and validate all links against analyst quotes.

    This function does not save or approve anything. It does not verify semantic
    support; the caller must review each proposal before acceptance.
    """
    validate_analysis(analysis, pages)
    if not isinstance(model, str) or not model.strip() or len(candidates) > 50:
        raise ValueError("a model and at most 50 candidates are required")
    if any(not isinstance(t, Topic) or t.review_status != ReviewStatus.ACCEPTED for t in candidates):
        raise ValueError("candidates must be accepted topics")
    candidate_ids = {str(t.id) for t in candidates}
    if len(candidate_ids) != len(candidates):
        raise ValueError("candidate IDs must be unique")
    payload = json.dumps({"analysis": analysis, "candidates": [{"id": str(t.id), "name": t.name,
        "definition": t.definition, "aliases": t.aliases} for t in candidates]}, ensure_ascii=False)
    if max_input_chars <= 0 or max_output_tokens <= 0 or len(payload) > max_input_chars:
        raise ValueError("mapper input exceeds bounds; no text was truncated")
    instructions = files("vaurien_atlas").joinpath("instructions/topic_mapper_v1.txt").read_text(encoding="utf-8")
    response = client.responses.create(model=model, instructions=instructions, input=payload, store=False,
        max_output_tokens=max_output_tokens, text={"format": {"type": "json_schema", "name": "topic_proposals",
        "strict": True, "schema": SCHEMA}})
    if getattr(response, "status", None) != "completed" or not response.output_text:
        raise ValueError("mapper response incomplete or refused")
    try:
        result = json.loads(response.output_text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid mapper JSON") from exc
    return validate_topic_proposals(result, analysis=analysis, pages=pages, candidates=candidates)


def validate_topic_proposals(result: dict, *, analysis: dict, pages: tuple[str, ...], candidates: tuple[Topic, ...]) -> dict:
    """Validate a proposal against the analysis and the supplied candidate set."""
    validate_analysis(analysis, pages)
    if any(not isinstance(t, Topic) or t.review_status != ReviewStatus.ACCEPTED for t in candidates):
        raise ValueError("candidates must be accepted topics")
    candidate_ids = {str(t.id) for t in candidates}
    if not isinstance(result, dict) or set(result) != {"new_topics", "links"} or not all(isinstance(result[k], list) for k in result):
        raise ValueError("invalid mapper output")
    if len(result["new_topics"]) > 20 or len(result["links"]) > 30:
        raise ValueError("too many topic proposals")
    names = {}
    for topic in result["new_topics"]:
        if not isinstance(topic, dict) or set(topic) != {"name", "definition"}:
            raise ValueError("invalid new topic")
        # Topic construction validates nonblank names and definitions.
        new = Topic(topic["name"], topic["definition"])
        key = new.name.strip().casefold()
        if key in names:
            raise ValueError("duplicate new topic")
        names[key] = new.name
    supported = {(e["page"], e["quote"]) for section in SECTIONS for claim in analysis[section] for e in claim["evidence"]}
    used, seen = set(), set()
    for link in result["links"]:
        if not isinstance(link, dict) or set(link) != {"existing_id", "new_name", "page", "quote"}:
            raise ValueError("invalid link")
        existing, new = link["existing_id"], link["new_name"]
        if not isinstance(existing, str) or not isinstance(new, str) or bool(existing) == bool(new):
            raise ValueError("link must identify one existing or new topic")
        if type(link["page"]) is not int or not isinstance(link["quote"], str) or (link["page"], link["quote"]) not in supported:
            raise ValueError("link must cite evidence from the analysis")
        if existing and existing not in candidate_ids:
            raise ValueError("unknown candidate topic")
        if new and new.strip().casefold() not in names:
            raise ValueError("unknown new topic")
        identity = existing or new.strip().casefold()
        if identity in seen:
            raise ValueError("duplicate link")
        seen.add(identity)
        if new:
            used.add(new.strip().casefold())
    if used != set(names):
        raise ValueError("every new topic must have a link")
    return result


def stage_proposals(connection: sqlite3.Connection, *, paper_id: UUID, proposals: dict,
                    analysis: dict, pages: tuple[str, ...], candidates: tuple[Topic, ...], provenance: str,
                    analysis_run_id: UUID, extractor_version: str, allow_partial: bool = False) -> None:
    """Atomically stage validated new topics and paper links as proposed records.

    Never changes the review status of an existing topic or accepts a link.
    """
    _require_foreign_keys(connection)
    if connection.in_transaction:
        raise ValueError("staging requires a connection without pending changes")
    if not isinstance(paper_id, UUID) or not isinstance(provenance, str) or not provenance.strip():
        raise ValueError("paper ID and provenance are required")
    validate_topic_proposals(proposals, analysis=analysis, pages=pages, candidates=candidates)
    # Recheck referenced paper, candidate states and name conflicts in the write transaction.
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        # Bind citations to the stored completed analysis and its source extraction.
        row = connection.execute("""SELECT r.result_json, e.pages_json FROM analysis_runs r
            JOIN extractions e ON e.sha256=r.fingerprint WHERE r.id=? AND r.paper_id=?
            AND r.status='completed' AND e.extractor_version=?""",
            (str(analysis_run_id), str(paper_id), extractor_version)).fetchone()
        if not row or tuple(json.loads(row[1])) != pages:
            raise ValueError("analysis and pages do not match a completed run")
        saved_result = json.loads(row[0])
        if saved_result.get("extractor_version") and saved_result["extractor_version"] != extractor_version:
            raise ValueError("extraction version differs from the recorded run")
        saved_result = prepare_evidence_review(saved_result, pages)
        if saved_result["analysis"] != analysis:
            raise ValueError("analysis and pages do not match a completed run")
        if saved_result.get("analysis_status") == "partial" and not allow_partial:
            raise ValueError("analysis is partial; explicit consent is required to map source-matched claims only")
        if not connection.execute("SELECT 1 FROM papers WHERE id=?", (str(paper_id),)).fetchone():
            raise ValueError("paper not found")
        known = {row[0].strip().casefold() for row in connection.execute("SELECT name FROM topics")}
        known.update(row[0].strip().casefold() for row in connection.execute("SELECT alias FROM topic_aliases"))
        new_ids = {}
        for topic in proposals["new_topics"]:
            key = topic["name"].strip().casefold()
            if key in known:
                raise ValueError("proposed topic conflicts with existing name or alias")
            new_id = str(uuid4())
            connection.execute("INSERT INTO topics (id,name,definition,review_status) VALUES (?, ?, ?, 'proposed')",
                               (new_id, topic["name"], topic["definition"]))
            new_ids[key] = new_id
            known.add(key)
        candidate_ids = {str(t.id) for t in candidates}
        for link in proposals["links"]:
            target = link["existing_id"] or new_ids[link["new_name"].strip().casefold()]
            if link["existing_id"] and (target not in candidate_ids or not connection.execute(
                "SELECT 1 FROM topics WHERE id=? AND review_status='accepted'", (target,)).fetchone()):
                raise ValueError("candidate is no longer accepted")
            # Exact duplicates are not useful proposals, regardless of prior review state.
            if connection.execute("SELECT 1 FROM relationships WHERE paper_source=? AND target=? AND relation_type='covers'",
                                  (str(paper_id), target)).fetchone():
                raise ValueError("paper-topic relationship already exists")
            relation_id = str(uuid4())
            connection.execute("""INSERT INTO relationships (id,paper_source,topic_source,target,relation_type,provenance,review_status)
                VALUES (?, ?, NULL, ?, 'covers', ?, 'proposed')""", (relation_id, str(paper_id), target, provenance))
            connection.execute("INSERT INTO relationship_evidence VALUES (?, 0, ?, ?, ?)",
                               (relation_id, str(paper_id), link["quote"], f"page {link['page']}"))
