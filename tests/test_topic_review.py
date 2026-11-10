"""Verify topic review status, bounded search and grounded proposal checks."""

import json
import unittest
from dataclasses import replace
from uuid import uuid4

from vaurien_atlas.knowledge import EntityRef, EntityType, Evidence, Relationship, RelationType, ReviewStatus, Topic
from vaurien_atlas.paper import Paper, ReadingStatus
from vaurien_atlas.storage import get_topic, initialize_database, open_database, save_paper, save_topic, search_topics
from vaurien_atlas.topic_review import validate_proposal


class TopicReviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = open_database(":memory:")
        self.addCleanup(self.conn.close)
        initialize_database(self.conn)
        self.paper = Paper(reading_status=ReadingStatus.QUEUED)
        save_paper(self.conn, self.paper)
        self.sha = "a" * 64
        with self.conn:
            self.conn.execute("INSERT INTO file_fingerprints VALUES (?, ?)", (self.sha, str(self.paper.id)))
            self.conn.execute("INSERT INTO extractions VALUES (?, ?, ?)", (self.sha, "test", json.dumps(["Evidence for control."])))

    def test_status_and_search_only_accepted_candidates(self):
        topic = Topic("Control", "Definition", ("feedback",))
        save_topic(self.conn, topic)
        self.assertEqual(search_topics(self.conn, "feedback"), ())
        accepted = replace(topic, review_status=ReviewStatus.ACCEPTED)
        save_topic(self.conn, accepted)
        self.assertEqual(get_topic(self.conn, topic.id), accepted)
        self.assertEqual(search_topics(self.conn, "FEED", 1), (accepted,))
        self.assertEqual(search_topics(self.conn, "%"), ())
        with self.assertRaises(ValueError):
            search_topics(self.conn, "control", 51)

    def test_validate_proposal_and_reject_unsupported_evidence(self):
        topic = Topic("Control", "Definition")
        save_topic(self.conn, topic)
        evidence = Evidence(self.paper.id, "Evidence for control", "page 1")
        relation = Relationship(EntityRef(EntityType.PAPER, self.paper.id), EntityRef(EntityType.TOPIC, topic.id),
                                RelationType.COVERS, (evidence,), "analyst proposal")
        kwargs = dict(paper_id=self.paper.id, fingerprint=self.sha, extractor_version="test")
        self.assertIsNone(validate_proposal(self.conn, relation, **kwargs))
        for bad in (replace(evidence, passage="invented"), replace(evidence, location="page 2"),
                    replace(evidence, paper_id=uuid4())):
            with self.subTest(evidence=bad), self.assertRaises(ValueError):
                validate_proposal(self.conn, replace(relation, evidence=(bad,)), **kwargs)
        save_topic(self.conn, replace(topic, review_status=ReviewStatus.REJECTED))
        with self.assertRaises(ValueError):
            validate_proposal(self.conn, relation, **kwargs)

    def test_no_candidates_for_new_database(self):
        self.assertEqual(search_topics(self.conn, "marine"), ())

    def test_migration_keeps_existing_topics_unreviewed(self):
        # A version 5 database never recorded a topic decision: preserve it as proposed.
        conn = open_database(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE topics (id TEXT PRIMARY KEY, name TEXT NOT NULL, definition TEXT NOT NULL)")
        topic_id = str(uuid4())
        conn.execute("INSERT INTO topics VALUES (?, ?, ?)", (topic_id, "Existing", "Definition"))
        conn.execute("PRAGMA user_version = 5")
        conn.commit()
        initialize_database(conn)
        self.assertEqual(conn.execute("SELECT review_status FROM topics WHERE id=?", (topic_id,)).fetchone()[0], "proposed")
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 6)
