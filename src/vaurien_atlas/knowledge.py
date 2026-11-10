"""In-memory topics and evidence-backed relationship proposals."""

from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4


def _required_text(name: str, value: str) -> None:
    """Reject missing or blank text in knowledge records."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonblank text")


@dataclass(frozen=True)
class Topic:
    """A named concept with alternate names and an independent stable ID."""
    name: str
    definition: str
    aliases: tuple[str, ...] = ()
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("id must be a UUID")
        _required_text("name", self.name)
        _required_text("definition", self.definition)
        if not isinstance(self.aliases, tuple):
            raise ValueError("aliases must be a tuple")
        for alias in self.aliases:
            _required_text("alias", alias)
        # Compare aliases without case or surrounding spaces; preserve original spelling.
        names = [self.name.strip().casefold(), *(a.strip().casefold() for a in self.aliases)]
        if len(names) != len(set(names)):
            raise ValueError("name and aliases must be distinct")


class EntityType(str, Enum):
    """The kinds of records that can appear at a relationship endpoint."""
    PAPER = "paper"
    TOPIC = "topic"


class RelationType(str, Enum):
    """Allowed directed relationships and their endpoint conventions."""
    COVERS = "covers"  # paper -> topic
    PREREQUISITE = "prerequisite"  # prerequisite topic -> dependent topic
    RELATED = "related"  # topic -> topic


class ReviewStatus(str, Enum):
    """Whether a proposed relationship has been reviewed."""
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True)
class EntityRef:
    """Identify an endpoint without embedding the entire paper or topic."""
    kind: EntityType
    id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EntityType) or not isinstance(self.id, UUID):
            raise ValueError("entity reference requires an EntityType and UUID")


@dataclass(frozen=True)
class Evidence:
    """Record a supporting passage and where it appears in a paper."""
    paper_id: UUID
    passage: str
    location: str

    def __post_init__(self) -> None:
        if not isinstance(self.paper_id, UUID):
            raise ValueError("evidence paper_id must be a UUID")
        _required_text("passage", self.passage)
        _required_text("location", self.location)


@dataclass(frozen=True)
class Relationship:
    """A proposed or reviewed connection with evidence and provenance."""
    source: EntityRef
    target: EntityRef
    relation_type: RelationType
    evidence: tuple[Evidence, ...]
    provenance: str
    review_status: ReviewStatus = ReviewStatus.PROPOSED
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not isinstance(self.id, UUID):
            raise ValueError("id must be a UUID")
        if not isinstance(self.source, EntityRef) or not isinstance(self.target, EntityRef):
            raise ValueError("source and target must be entity references")
        if not isinstance(self.relation_type, RelationType):
            raise ValueError("relation_type must be a RelationType")
        if not isinstance(self.review_status, ReviewStatus):
            raise ValueError("review_status must be a ReviewStatus")
        # Enforce the direction of each supported relation before saving it.
        expected = ((EntityType.PAPER, EntityType.TOPIC) if self.relation_type == RelationType.COVERS
                    else (EntityType.TOPIC, EntityType.TOPIC))
        if (self.source.kind, self.target.kind) != expected:
            raise ValueError("invalid endpoint types for relation")
        if self.source == self.target:
            raise ValueError("a relationship cannot connect an entity to itself")
        # Evidence presence is structural validation, not proof of scientific support.
        if not isinstance(self.evidence, tuple) or not self.evidence or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            raise ValueError("at least one Evidence item is required")
        _required_text("provenance", self.provenance)
