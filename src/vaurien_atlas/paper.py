"""A validated paper record, independent of storage and file access."""

from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4


class ReadingStatus(str, Enum):
    QUEUED = "queued"
    READING = "reading"
    READ = "read"
    DEFERRED = "deferred"


class AnalysisScope(str, Enum):
    NOT_ANALYZED = "not_analyzed"
    ABSTRACT = "abstract"
    PARTIAL_TEXT = "partial_text"
    FULL_TEXT = "full_text"


def _optional_text(name: str, value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} must be nonblank text or None")


@dataclass(frozen=True)
class Paper:
    """Identity remains unchanged when metadata, status or paths change.

    Paths are references only: construction does not inspect the filesystem.
    DOI and arXiv values are recorded as supplied, not verified or normalized.
    """

    reading_status: ReadingStatus
    id: UUID = field(default_factory=uuid4)
    title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    source_files: tuple[str, ...] = ()
    analysis_scope: AnalysisScope = AnalysisScope.NOT_ANALYZED

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("id must be a UUID")
        if not isinstance(self.reading_status, ReadingStatus):
            raise ValueError("reading_status must be a ReadingStatus")
        if not isinstance(self.analysis_scope, AnalysisScope):
            raise ValueError("analysis_scope must be an AnalysisScope")
        for name in ("title", "doi", "arxiv_id"):
            _optional_text(name, getattr(self, name))
        if not isinstance(self.source_files, tuple):
            raise ValueError("source_files must be a tuple of nonblank paths")
        if any(not isinstance(path, str) or not path.strip() for path in self.source_files):
            raise ValueError("source_files must contain only nonblank paths")
        if len(set(self.source_files)) != len(self.source_files):
            raise ValueError("source_files must not contain duplicate paths")
