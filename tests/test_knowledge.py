"""Check topic aliases and evidence-backed relationship rules."""

import unittest
from dataclasses import replace
from uuid import uuid4

from vaurien_atlas.knowledge import (
    EntityRef, EntityType, Evidence, Relationship, RelationType, ReviewStatus, Topic,
)


class KnowledgeTests(unittest.TestCase):
    def test_topic_identity_and_alias_validation(self):
        topic = Topic("Control", "Study of dynamic systems", ("Control theory",))
        self.assertEqual(replace(topic, definition="Updated definition").id, topic.id)
        for aliases in ((" control ",), ("Theory", "theory"), (" ",)):
            with self.subTest(aliases=aliases), self.assertRaises(ValueError):
                Topic("Control", "Definition", aliases)

    def test_proposal_and_review_preserve_evidence(self):
        # Reviewing a proposal must retain its identity and supporting evidence.
        paper = EntityRef(EntityType.PAPER, uuid4())
        topic = EntityRef(EntityType.TOPIC, uuid4())
        evidence = Evidence(paper.id, "Supporting passage", "page 2")
        proposal = Relationship(paper, topic, RelationType.COVERS, (evidence,), "manual")
        self.assertEqual(proposal.review_status, ReviewStatus.PROPOSED)
        accepted = replace(proposal, review_status=ReviewStatus.ACCEPTED)
        self.assertEqual((accepted.id, accepted.evidence), (proposal.id, proposal.evidence))

    def test_rejects_invalid_relationships_and_evidence(self):
        topic = EntityRef(EntityType.TOPIC, uuid4())
        other = EntityRef(EntityType.TOPIC, uuid4())
        paper = EntityRef(EntityType.PAPER, uuid4())
        evidence = Evidence(paper.id, "Text", "page 1")
        for source, target, kind, items, provenance in (
            (topic, other, RelationType.COVERS, (evidence,), "manual"),
            (topic, topic, RelationType.PREREQUISITE, (evidence,), "manual"),
            (topic, other, RelationType.RELATED, (), "manual"),
            (topic, other, RelationType.RELATED, (evidence,), " "),
        ):
            with self.subTest(kind=kind, source=source, target=target), self.assertRaises(ValueError):
                Relationship(source, target, kind, items, provenance)
        with self.assertRaises(ValueError):
            Evidence(paper.id, "Text", " ")


if __name__ == "__main__":
    unittest.main()
