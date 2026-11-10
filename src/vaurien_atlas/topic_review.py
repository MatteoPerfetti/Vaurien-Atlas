"""Validate proposed links against stored paper evidence before human review."""

import json
import re
import sqlite3
from uuid import UUID

from vaurien_atlas.knowledge import EntityType, Relationship, ReviewStatus
from vaurien_atlas.storage import get_paper, get_topic


def validate_proposal(connection: sqlite3.Connection, relationship: Relationship, *,
                      paper_id: UUID, fingerprint: str, extractor_version: str) -> None:
    """Check references and exact page quotations without accepting the proposal.

    Presence of a quotation is not proof of semantic support. Review must still
    determine whether a specific link is justified and not overly generic.
    """
    if not isinstance(relationship, Relationship) or relationship.review_status != ReviewStatus.PROPOSED:
        raise ValueError("a proposed relationship is required")
    if not isinstance(paper_id, UUID) or get_paper(connection, paper_id) is None:
        raise ValueError("evidence paper must exist")
    if relationship.source.kind == EntityType.PAPER and relationship.source.id != paper_id:
        raise ValueError("paper source must match the evidence paper")
    # A rejected topic cannot be reused; proposed topics still need human review.
    for endpoint in (relationship.source, relationship.target):
        if endpoint.kind == EntityType.TOPIC:
            topic = get_topic(connection, endpoint.id)
            if topic is None or topic.review_status == ReviewStatus.REJECTED:
                raise ValueError("topic does not exist or has been rejected")
    row = connection.execute("""SELECT e.pages_json FROM extractions e
        JOIN file_fingerprints f ON f.sha256=e.sha256
        WHERE e.sha256=? AND e.extractor_version=? AND f.paper_id=?""",
        (fingerprint, extractor_version, str(paper_id))).fetchone()
    if row is None:
        raise ValueError("matching stored extraction not found")
    pages = json.loads(row[0])
    for evidence in relationship.evidence:
        # Restrict locations to explicit PDF page numbers for deterministic checks.
        match = re.fullmatch(r"page ([1-9][0-9]*)", evidence.location)
        if evidence.paper_id != paper_id or match is None:
            raise ValueError("evidence must refer to the paper and a page number")
        number = int(match.group(1))
        if number > len(pages) or evidence.passage not in pages[number - 1]:
            raise ValueError("evidence passage not found on the stated page")
