"""Register a PDF by content identity without copying or extracting it."""

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from vaurien_atlas.paper import ReadingStatus
from vaurien_atlas.storage import _require_foreign_keys


@dataclass(frozen=True)
class ImportResult:
    paper_id: UUID
    sha256: str
    is_duplicate: bool
    path: str


def import_pdf(connection: sqlite3.Connection, path: str | Path, status: ReadingStatus) -> ImportResult:
    """Register exact content and its path; commits on a dedicated connection.

    Exact matches reuse the paper ID and preserve its status. A path previously
    registered with different content is rejected for explicit reconciliation.
    """
    _require_foreign_keys(connection)
    if connection.in_transaction:
        raise ValueError("import requires a connection without pending changes")
    if not isinstance(status, ReadingStatus):
        raise ValueError("status must be a ReadingStatus")
    resolved = Path(path).resolve(strict=True)
    if resolved.suffix.lower() != ".pdf" or not resolved.is_file():
        raise ValueError("path must refer to a PDF file")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if stream.read(5) != b"%PDF-":
            raise ValueError("file does not begin with a PDF header")
        stream.seek(0)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
    current = resolved.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino) or (
        after.st_size, after.st_mtime_ns, after.st_ino
    ) != (current.st_size, current.st_mtime_ns, current.st_ino):
        raise ValueError("file changed while being read; retry after it is stable")
    fingerprint = digest.hexdigest()
    path_string = os.path.normcase(str(resolved))
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        existing_path = connection.execute("SELECT sha256 FROM imported_paths WHERE path = ?", (path_string,)).fetchone()
        if existing_path and existing_path[0] != fingerprint:
            raise ValueError("path was previously registered with different content")
        match = connection.execute("SELECT paper_id FROM file_fingerprints WHERE sha256 = ?", (fingerprint,)).fetchone()
        paper_id = UUID(match[0]) if match else uuid4()
        if not match:
            connection.execute("INSERT INTO papers (id, reading_status, analysis_scope) VALUES (?, ?, ?)",
                               (str(paper_id), status.value, "not_analyzed"))
            connection.execute("INSERT INTO file_fingerprints VALUES (?, ?)", (fingerprint, str(paper_id)))
        if not existing_path:
            connection.execute("INSERT INTO imported_paths VALUES (?, ?)", (path_string, fingerprint))
        source_exists = connection.execute("SELECT 1 FROM paper_sources WHERE paper_id = ? AND path = ?",
                                           (str(paper_id), path_string)).fetchone()
        if not source_exists:
            position = connection.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM paper_sources WHERE paper_id = ?",
                                          (str(paper_id),)).fetchone()[0]
            connection.execute("INSERT INTO paper_sources VALUES (?, ?, ?)", (str(paper_id), position, path_string))
    return ImportResult(paper_id, fingerprint, match is not None, path_string)
