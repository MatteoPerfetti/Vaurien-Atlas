import tempfile
import unittest
from pathlib import Path

from vaurien_atlas.ingestion import import_pdf
from vaurien_atlas.paper import ReadingStatus
from vaurien_atlas.storage import get_paper, initialize_database, open_database


class ImportTests(unittest.TestCase):
    def test_duplicate_renamed_and_changed_path(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.pdf"
            second = Path(directory) / "second.pdf"
            first.write_bytes(b"%PDF-1.4\nexample")
            second.write_bytes(first.read_bytes())
            conn = open_database(Path(directory) / "test.sqlite3")
            try:
                initialize_database(conn)
                result = import_pdf(conn, first, ReadingStatus.QUEUED)
                again = import_pdf(conn, second, ReadingStatus.READ)
                self.assertFalse(result.is_duplicate)
                self.assertTrue(again.is_duplicate)
                self.assertEqual(result.paper_id, again.paper_id)
                paper = get_paper(conn, result.paper_id)
                self.assertEqual(paper.reading_status, ReadingStatus.QUEUED)
                self.assertEqual(len(paper.source_files), 2)
                first.write_bytes(b"%PDF-1.4\nchanged")
                with self.assertRaises(ValueError):
                    import_pdf(conn, first, ReadingStatus.READ)
                self.assertEqual(get_paper(conn, result.paper_id), paper)
                second.rename(Path(directory) / "moved.pdf")
                moved = import_pdf(conn, Path(directory) / "moved.pdf", ReadingStatus.READ)
                self.assertEqual(moved.paper_id, result.paper_id)
            finally:
                conn.close()

    def test_invalid_and_missing_files_create_no_record(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = open_database(":memory:")
            try:
                initialize_database(conn)
                bad = Path(directory) / "bad.pdf"
                bad.write_bytes(b"not a PDF")
                with self.assertRaises(ValueError):
                    import_pdf(conn, bad, ReadingStatus.QUEUED)
                with self.assertRaises(FileNotFoundError):
                    import_pdf(conn, Path(directory) / "missing.pdf", ReadingStatus.QUEUED)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0], 0)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
