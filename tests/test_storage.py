import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from vaurien_atlas.paper import AnalysisScope, Paper, ReadingStatus
from vaurien_atlas.storage import get_paper, initialize_database, open_database, save_paper


class StorageTests(unittest.TestCase):
    def test_round_trip_across_connections_and_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            paper = Paper(reading_status=ReadingStatus.QUEUED, title="Example", doi="10/example",
                          arxiv_id="example", source_files=("a.pdf", "b.pdf"),
                          analysis_scope=AnalysisScope.ABSTRACT)
            with open_database(path) as connection:
                initialize_database(connection)
                initialize_database(connection)
                save_paper(connection, paper)
            connection.close()
            with open_database(path) as connection:
                self.assertEqual(get_paper(connection, paper.id), paper)
                self.assertIsNone(get_paper(connection, uuid4()))
                updated = replace(paper, reading_status=ReadingStatus.READ, source_files=("moved.pdf",))
                save_paper(connection, updated)
                self.assertEqual(get_paper(connection, paper.id), updated)
            connection.close()

    def test_failed_update_rolls_back_paper_and_sources(self):
        connection = open_database(":memory:")
        self.addCleanup(connection.close)
        initialize_database(connection)
        original = Paper(reading_status=ReadingStatus.QUEUED, source_files=("original.pdf",))
        save_paper(connection, original)
        connection.execute("""CREATE TRIGGER reject_path BEFORE INSERT ON paper_sources
                              WHEN NEW.path = 'bad.pdf' BEGIN SELECT RAISE(ABORT, 'rejected'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            save_paper(connection, replace(original, reading_status=ReadingStatus.READ,
                                           source_files=("bad.pdf",)))
        self.assertEqual(get_paper(connection, original.id), original)

    def test_rejects_unrecognized_databases(self):
        connection = open_database(":memory:")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE unrelated (id INTEGER)")
        with self.assertRaises(ValueError):
            initialize_database(connection)
        connection.execute("PRAGMA user_version = 99")
        with self.assertRaises(ValueError):
            initialize_database(connection)


if __name__ == "__main__":
    unittest.main()
