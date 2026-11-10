"""Inspect and explicitly accept or reject topic and relationship proposals."""

import sqlite3
from uuid import UUID

from vaurien_atlas.knowledge import ReviewStatus
from vaurien_atlas.storage import _require_foreign_keys, get_relationship, get_topic


def list_proposals(connection: sqlite3.Connection, limit: int = 50) -> dict:
    """Return pending objects with their definitions and supporting evidence."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    topics = connection.execute("SELECT id FROM topics WHERE review_status='proposed' ORDER BY name, id LIMIT ?", (limit,)).fetchall()
    relations = connection.execute("SELECT id FROM relationships WHERE review_status='proposed' ORDER BY id LIMIT ?", (limit,)).fetchall()
    return {"topics": [get_topic(connection, UUID(row[0])) for row in topics],
            "relationships": [get_relationship(connection, UUID(row[0])) for row in relations]}


def decide(connection: sqlite3.Connection, kind: str, item_id: UUID, decision: ReviewStatus) -> None:
    """Apply one explicit decision atomically; reviewed records are not overwritten."""
    _require_foreign_keys(connection)
    if connection.in_transaction:
        raise ValueError("review requires a connection without pending changes")
    if kind not in ("topic", "relationship") or not isinstance(item_id, UUID):
        raise ValueError("invalid review item")
    if decision not in (ReviewStatus.ACCEPTED, ReviewStatus.REJECTED) or not isinstance(decision, ReviewStatus):
        raise ValueError("decision must be accepted or rejected")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if kind == "topic":
            item = get_topic(connection, item_id)
            table = "topics"
        else:
            item = get_relationship(connection, item_id)
            table = "relationships"
            if item and decision == ReviewStatus.ACCEPTED:
                # A link cannot become accepted while any topic is unreviewed.
                for endpoint in (item.source, item.target):
                    if endpoint.kind.value == "topic":
                        topic = get_topic(connection, endpoint.id)
                        if not topic or topic.review_status != ReviewStatus.ACCEPTED:
                            raise ValueError("accept all linked topics before accepting a relationship")
        if item is None or item.review_status != ReviewStatus.PROPOSED:
            raise ValueError("item is missing or was already reviewed")
        connection.execute(f"UPDATE {table} SET review_status=? WHERE id=? AND review_status='proposed'",
                           (decision.value, str(item_id)))
