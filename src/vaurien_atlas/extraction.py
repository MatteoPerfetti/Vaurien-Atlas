"""Extract and cache PDF text by content fingerprint and extractor version."""

import hashlib
import io
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from vaurien_atlas.storage import _require_foreign_keys


class ExtractionError(Exception):
    """Extraction failed; no result was cached."""


@dataclass(frozen=True)
class ExtractionResult:
    pages: tuple[str, ...]
    extractor_version: str
    cached: bool

    @property
    def empty_pages(self) -> tuple[int, ...]:
        return tuple(i for i, text in enumerate(self.pages, 1) if not text.strip())


def extract_pdf(connection: sqlite3.Connection, path: str | Path, sha256: str) -> ExtractionResult:
    """Extract all pages or raise ExtractionError; never cache partial results."""
    _require_foreign_keys(connection)
    if connection.in_transaction:
        raise ValueError("extraction requires a connection without pending changes")
    try:
        import pypdf
    except ImportError as exc:
        raise ExtractionError("pypdf is not installed; install project dependencies") from exc
    version = f"pypdf:{pypdf.__version__}:v2"
    try:
        content = Path(path).read_bytes()
    except OSError as exc:
        raise ExtractionError(f"cannot read PDF: {exc}") from exc
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ExtractionError("file content changed since registration; import again")
    cached = connection.execute("SELECT pages_json FROM extractions WHERE sha256=? AND extractor_version=?",
                                (sha256, version)).fetchone()
    if cached:
        return ExtractionResult(tuple(json.loads(cached[0])), version, True)
    warnings = []
    class CaptureWarnings(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                warnings.append(record.getMessage())
    handler = CaptureWarnings()
    logger = logging.getLogger("pypdf")
    logger.addHandler(handler)
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ExtractionError("encrypted PDF is not supported")
        pages = tuple(page.extract_text() or "" for page in reader.pages)
        if not pages:
            raise ExtractionError("PDF has no pages")
        if warnings:
            raise ExtractionError("PDF reader reported limitations; result not cached: " + "; ".join(dict.fromkeys(warnings)))
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(f"PDF extraction failed: {exc}") from exc
    finally:
        logger.removeHandler(handler)
    with connection:
        connection.execute("INSERT OR REPLACE INTO extractions VALUES (?, ?, ?)",
                           (sha256, version, json.dumps(pages, ensure_ascii=False)))
    return ExtractionResult(pages, version, False)
