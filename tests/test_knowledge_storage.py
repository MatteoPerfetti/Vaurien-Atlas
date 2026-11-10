"""Check topic and relationship persistence, references and schema migration."""

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from vaurien_atlas.knowledge import EntityRef, EntityType, Evidence, Relationship, RelationType, ReviewStatus, Topic
from vaurien_atlas.paper import Paper, ReadingStatus
from vaurien_atlas.storage import (get_paper, get_relationship, get_topic, initialize_database,
                                   open_database, save_paper, save_relationship, save_topic)


class KnowledgeStorageTests(unittest.TestCase):
    def test_round_trip_and_review_after_reopening(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            conn = open_database(path)
            try:
                initialize_database(conn)
                paper = Paper(reading_status=ReadingStatus.QUEUED)
                topic = Topic("Control", "Dynamic systems", ("Control theory",))
                save_paper(conn, paper)
                save_topic(conn, topic)
                relationship = Relationship(EntityRef(EntityType.PAPER, paper.id),
                    EntityRef(EntityType.TOPIC, topic.id), RelationType.COVERS,
                    (Evidence(paper.id, "passage", "page 2"),), "manual")
                save_relationship(conn, relationship)
            finally:
                conn.close()
            conn = open_database(path)
            try:
                self.assertEqual(get_topic(conn, topic.id), topic)
                self.assertEqual(get_relationship(conn, relationship.id), relationship)
                accepted = replace(relationship, review_status=ReviewStatus.ACCEPTED)
                save_relationship(conn, accepted)
                self.assertEqual(get_relationship(conn, accepted.id), accepted)
                self.assertIsNone(get_topic(conn, uuid4()))
                self.assertIsNone(get_relationship(conn, uuid4()))
            finally:
                conn.close()

    def test_missing_references_and_failed_update_roll_back(self):
        conn = open_database(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn)
        paper = Paper(reading_status=ReadingStatus.READ)
        topic = Topic("Topic", "Definition")
        save_paper(conn, paper)
        save_topic(conn, topic)
        relation = Relationship(EntityRef(EntityType.PAPER, paper.id), EntityRef(EntityType.TOPIC, topic.id),
                                RelationType.COVERS, (Evidence(paper.id, "text", "page 1"),), "manual")
        save_relationship(conn, relation)
        bad = replace(relation, evidence=(Evidence(uuid4(), "text", "page 1"),),
                      review_status=ReviewStatus.ACCEPTED)
        with self.assertRaises(sqlite3.IntegrityError):
            save_relationship(conn, bad)
        self.assertEqual(get_relationship(conn, relation.id), relation)
        with self.assertRaises(sqlite3.IntegrityError):
            save_relationship(conn, replace(relation, target=EntityRef(EntityType.TOPIC, uuid4())))
        self.assertEqual(get_relationship(conn, relation.id), relation)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM papers WHERE id = ?", (str(paper.id),))
        conn.rollback()

    def test_upgrade_version_one_preserves_paper(self):
        # Recreate the old paper-only schema and check migration retains its data.
        conn = open_database(":memory:")
        self.addCleanup(conn.close)
        conn.execute("CREATE TABLE papers (id TEXT PRIMARY KEY, reading_status TEXT NOT NULL, title TEXT, doi TEXT, arxiv_id TEXT, analysis_scope TEXT NOT NULL)")
        conn.execute("CREATE TABLE paper_sources (paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE, position INTEGER NOT NULL, path TEXT NOT NULL, PRIMARY KEY (paper_id, position), UNIQUE (paper_id, path))")
        conn.execute("PRAGMA user_version = 1")
        paper = Paper(reading_status=ReadingStatus.QUEUED, source_files=("a.pdf",))
        save_paper(conn, paper)
        initialize_database(conn)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 5)
        self.assertEqual(get_paper(conn, paper.id), paper)
        save_topic(conn, Topic("Example", "Definition"))

    def test_requires_foreign_keys(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        initialize_database(conn)
        with self.assertRaises(ValueError):
            save_topic(conn, Topic("Example", "Definition"))


if __name__ == "__main__":
    unittest.main()
