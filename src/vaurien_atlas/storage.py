"""SQLite persistence for paper records. Callers own and close connections."""

import sqlite3
from pathlib import Path
from uuid import UUID

from vaurien_atlas.paper import AnalysisScope, Paper, ReadingStatus
from vaurien_atlas.knowledge import EntityRef, EntityType, Evidence, Relationship, RelationType, ReviewStatus, Topic


def open_database(path: str | Path) -> sqlite3.Connection:
    """Open or create a database; its parent directory must already exist.

    Initialize a fresh database with initialize_database before using it.
    """
    connection = sqlite3.connect(str(path))
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    """Create schema version 6 or upgrade an earlier supported database.

    This commits schema initialization or migration.
    """
    # Refuse unknown schemas rather than guessing how to migrate their data.
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == 6:
        return
    if version not in (0, 1, 2, 3, 4, 5):
        raise ValueError(f"unsupported database schema version: {version}")
    if version == 0 and connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1").fetchone():
        raise ValueError("cannot initialize a nonempty unversioned database")
    if connection.in_transaction:
        raise ValueError("initialize with no pending transaction")
    # Run schema creation and upgrades together, rolling back on failure.
    with connection:
        connection.execute("BEGIN")
        # Each schema version adds tables required by a later workflow.
        if version == 0:
            connection.execute("""CREATE TABLE papers (
            id TEXT PRIMARY KEY, reading_status TEXT NOT NULL,
            title TEXT, doi TEXT, arxiv_id TEXT, analysis_scope TEXT NOT NULL
        )""")
            connection.execute("""CREATE TABLE paper_sources (
            paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            position INTEGER NOT NULL, path TEXT NOT NULL,
            PRIMARY KEY (paper_id, position), UNIQUE (paper_id, path)
        )""")
        if version < 2:
            _create_knowledge_schema(connection)
        if version < 3:
            connection.execute("""CREATE TABLE file_fingerprints (
            sha256 TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id)
        )""")
            connection.execute("""CREATE TABLE imported_paths (
            path TEXT PRIMARY KEY, sha256 TEXT NOT NULL REFERENCES file_fingerprints(sha256)
        )""")
        if version < 4:
            connection.execute("""CREATE TABLE extractions (
            sha256 TEXT NOT NULL REFERENCES file_fingerprints(sha256),
            extractor_version TEXT NOT NULL, pages_json TEXT NOT NULL,
            PRIMARY KEY (sha256, extractor_version)
        )""")
        if version < 5:
            connection.execute("""CREATE TABLE analysis_runs (
            id TEXT PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(id),
            fingerprint TEXT NOT NULL REFERENCES file_fingerprints(sha256),
            cache_key TEXT NOT NULL, model TEXT NOT NULL, instruction_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
            started_at TEXT NOT NULL, finished_at TEXT, duration_seconds REAL,
            result_json TEXT, failure_type TEXT
        )""")
            connection.execute("CREATE INDEX analysis_cache_idx ON analysis_runs(cache_key, status)")
        # Existing topics have never had an explicit review state; do not assume acceptance.
        connection.execute("""ALTER TABLE topics ADD COLUMN review_status TEXT NOT NULL
                           DEFAULT 'proposed' CHECK (review_status IN ('proposed', 'accepted', 'rejected'))""")
        connection.execute("PRAGMA user_version = 6")


def _create_knowledge_schema(connection: sqlite3.Connection) -> None:
        """Create topic, relation and evidence tables with reference checks."""
        connection.execute("""CREATE TABLE topics (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, definition TEXT NOT NULL
        )""")
        connection.execute("""CREATE TABLE topic_aliases (
            topic_id TEXT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
            position INTEGER NOT NULL, alias TEXT NOT NULL,
            PRIMARY KEY (topic_id, position)
        )""")
        connection.execute("""CREATE TABLE relationships (
            id TEXT PRIMARY KEY, paper_source TEXT REFERENCES papers(id),
            topic_source TEXT REFERENCES topics(id), target TEXT NOT NULL REFERENCES topics(id),
            relation_type TEXT NOT NULL, provenance TEXT NOT NULL, review_status TEXT NOT NULL,
            CHECK ((relation_type = 'covers' AND paper_source IS NOT NULL AND topic_source IS NULL)
                OR (relation_type IN ('related', 'prerequisite') AND paper_source IS NULL
                    AND topic_source IS NOT NULL AND topic_source != target)),
            CHECK (review_status IN ('proposed', 'accepted', 'rejected'))
        )""")
        connection.execute("""CREATE TABLE relationship_evidence (
            relationship_id TEXT NOT NULL REFERENCES relationships(id) ON DELETE CASCADE,
            position INTEGER NOT NULL, paper_id TEXT NOT NULL REFERENCES papers(id),
            passage TEXT NOT NULL, location TEXT NOT NULL,
            PRIMARY KEY (relationship_id, position)
        )""")


def save_paper(connection: sqlite3.Connection, paper: Paper) -> None:
    """Insert or replace a paper and its paths atomically; commits the transaction.

    Use a dedicated connection without unrelated pending changes. Same-ID saves
    replace the full record; no duplicate detection or concurrency merging occurs.
    """
    if not isinstance(paper, Paper):
        raise ValueError("paper must be a Paper")
    # Upsert the record and replace its ordered paths in the same transaction.
    with connection:
        connection.execute("""INSERT INTO papers
            (id, reading_status, title, doi, arxiv_id, analysis_scope)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET reading_status=excluded.reading_status,
              title=excluded.title, doi=excluded.doi, arxiv_id=excluded.arxiv_id,
              analysis_scope=excluded.analysis_scope""",
            (str(paper.id), paper.reading_status.value, paper.title, paper.doi,
             paper.arxiv_id, paper.analysis_scope.value))
        connection.execute("DELETE FROM paper_sources WHERE paper_id = ?", (str(paper.id),))
        connection.executemany("INSERT INTO paper_sources (paper_id, position, path) VALUES (?, ?, ?)",
                               ((str(paper.id), i, path) for i, path in enumerate(paper.source_files)))


def get_paper(connection: sqlite3.Connection, paper_id: UUID) -> Paper | None:
    """Read a paper by ID, returning None if it is absent."""
    if not isinstance(paper_id, UUID):
        raise ValueError("paper_id must be a UUID")
    row = connection.execute("""SELECT id, reading_status, title, doi, arxiv_id, analysis_scope
                                FROM papers WHERE id = ?""", (str(paper_id),)).fetchone()
    if row is None:
        return None
    paths = connection.execute("SELECT path FROM paper_sources WHERE paper_id = ? ORDER BY position",
                               (str(paper_id),)).fetchall()
    return Paper(id=UUID(row[0]), reading_status=ReadingStatus(row[1]), title=row[2],
                 doi=row[3], arxiv_id=row[4], analysis_scope=AnalysisScope(row[5]),
                 source_files=tuple(path[0] for path in paths))


def _require_foreign_keys(connection: sqlite3.Connection) -> None:
    """Require reference checks before writing dependent records."""
    if not connection.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise ValueError("foreign keys must be enabled; use open_database")


def save_topic(connection: sqlite3.Connection, topic: Topic) -> None:
    """Save the full topic and aliases atomically, committing the transaction."""
    _require_foreign_keys(connection)
    if not isinstance(topic, Topic):
        raise ValueError("topic must be a Topic")
    with connection:
        connection.execute("""INSERT INTO topics (id, name, definition, review_status) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET name=excluded.name, definition=excluded.definition,
            review_status=excluded.review_status""",
            (str(topic.id), topic.name, topic.definition, topic.review_status.value))
        connection.execute("DELETE FROM topic_aliases WHERE topic_id = ?", (str(topic.id),))
        connection.executemany("INSERT INTO topic_aliases VALUES (?, ?, ?)",
                               ((str(topic.id), i, alias) for i, alias in enumerate(topic.aliases)))


def get_topic(connection: sqlite3.Connection, topic_id: UUID) -> Topic | None:
    """Reconstruct a topic and its ordered aliases, or return None."""
    if not isinstance(topic_id, UUID):
        raise ValueError("topic_id must be a UUID")
    row = connection.execute("SELECT name, definition, review_status FROM topics WHERE id = ?", (str(topic_id),)).fetchone()
    if row is None:
        return None
    aliases = connection.execute("SELECT alias FROM topic_aliases WHERE topic_id = ? ORDER BY position",
                                 (str(topic_id),)).fetchall()
    return Topic(id=topic_id, name=row[0], definition=row[1], aliases=tuple(a[0] for a in aliases),
                 review_status=ReviewStatus(row[2]))


def search_topics(connection: sqlite3.Connection, query: str, limit: int = 10) -> tuple[Topic, ...]:
    """Find up to 50 accepted topics by literal name or alias substring.

    SQLite LIKE matches ASCII case-insensitively; non-ASCII case is exact.
    Results are candidates only and do not imply a conceptual relationship.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be nonblank")
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    # Escape SQL wildcards so user input is treated as literal text.
    term = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{term}%"
    rows = connection.execute("""SELECT id FROM topics t WHERE review_status='accepted' AND
        (name LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM topic_aliases a
        WHERE a.topic_id=t.id AND a.alias LIKE ? ESCAPE '\\'))
        ORDER BY name COLLATE NOCASE, id LIMIT ?""", (pattern, pattern, limit)).fetchall()
    return tuple(get_topic(connection, UUID(row[0])) for row in rows)


def save_relationship(connection: sqlite3.Connection, relationship: Relationship) -> None:
    """Save a relationship and evidence atomically; referenced records must exist."""
    _require_foreign_keys(connection)
    if not isinstance(relationship, Relationship):
        raise ValueError("relationship must be a Relationship")
    # Separate source columns let SQLite enforce paper and topic references.
    paper_source = str(relationship.source.id) if relationship.source.kind == EntityType.PAPER else None
    topic_source = str(relationship.source.id) if relationship.source.kind == EntityType.TOPIC else None
    with connection:
        connection.execute("""INSERT INTO relationships
            (id, paper_source, topic_source, target, relation_type, provenance, review_status)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
            paper_source=excluded.paper_source, topic_source=excluded.topic_source,
            target=excluded.target, relation_type=excluded.relation_type,
            provenance=excluded.provenance, review_status=excluded.review_status""",
            (str(relationship.id), paper_source, topic_source, str(relationship.target.id),
             relationship.relation_type.value, relationship.provenance, relationship.review_status.value))
        # Replace evidence together with the relationship; rollback preserves both on error.
        connection.execute("DELETE FROM relationship_evidence WHERE relationship_id = ?", (str(relationship.id),))
        connection.executemany("INSERT INTO relationship_evidence VALUES (?, ?, ?, ?, ?)",
            ((str(relationship.id), i, str(e.paper_id), e.passage, e.location)
             for i, e in enumerate(relationship.evidence)))


def get_relationship(connection: sqlite3.Connection, relationship_id: UUID) -> Relationship | None:
    """Reconstruct a relationship and its ordered evidence, or return None."""
    if not isinstance(relationship_id, UUID):
        raise ValueError("relationship_id must be a UUID")
    row = connection.execute("""SELECT paper_source, topic_source, target, relation_type, provenance,
        review_status FROM relationships WHERE id = ?""", (str(relationship_id),)).fetchone()
    if row is None:
        return None
    evidence_rows = connection.execute("""SELECT paper_id, passage, location FROM relationship_evidence
        WHERE relationship_id = ? ORDER BY position""", (str(relationship_id),)).fetchall()
    source = EntityRef(EntityType.PAPER, UUID(row[0])) if row[0] else EntityRef(EntityType.TOPIC, UUID(row[1]))
    return Relationship(id=relationship_id, source=source, target=EntityRef(EntityType.TOPIC, UUID(row[2])),
                        relation_type=RelationType(row[3]), provenance=row[4], review_status=ReviewStatus(row[5]),
                        evidence=tuple(Evidence(UUID(e[0]), e[1], e[2]) for e in evidence_rows))
