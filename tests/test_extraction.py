import contextlib
import hashlib
import io
import logging
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from vaurien_atlas.cli import main
from vaurien_atlas.extraction import ExtractionError, extract_pdf
from vaurien_atlas.ingestion import import_pdf
from vaurien_atlas.paper import ReadingStatus
from vaurien_atlas.storage import initialize_database, open_database


class Page:
    def __init__(self, text):
        self.text = text

    def extract_text(self):
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "paper.pdf"
        self.path.write_bytes(b"%PDF-1.4\nsynthetic")
        self.conn = open_database(":memory:")
        self.addCleanup(self.conn.close)
        initialize_database(self.conn)
        self.registration = import_pdf(self.conn, self.path, ReadingStatus.QUEUED)

    def fake(self, pages, version="test"):
        reader = lambda stream: types.SimpleNamespace(is_encrypted=False, pages=pages)
        return patch.dict("sys.modules", {"pypdf": types.SimpleNamespace(__version__=version, PdfReader=reader)})

    def test_cache_and_changed_content(self):
        with self.fake([Page("first page"), Page("")]):
            result = extract_pdf(self.conn, self.path, self.registration.sha256)
            self.assertEqual(result.pages, ("first page", ""))
            self.assertEqual(result.empty_pages, (2,))
            self.assertFalse(result.cached)
        with self.fake([Page(RuntimeError("should not run"))]):
            self.assertTrue(extract_pdf(self.conn, self.path, self.registration.sha256).cached)
            self.path.write_bytes(b"%PDF-changed")
            with self.assertRaises(ExtractionError):
                extract_pdf(self.conn, self.path, self.registration.sha256)

    def test_failure_does_not_cache_partial_results(self):
        with self.fake([Page("text"), Page(RuntimeError("broken"))]):
            with self.assertRaises(ExtractionError):
                extract_pdf(self.conn, self.path, self.registration.sha256)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0], 0)

    def test_reader_warning_is_not_cached(self):
        class WarningPage:
            def extract_text(self):
                logging.getLogger("pypdf").warning("content skipped")
                return "partial text"
        with self.fake([WarningPage()]):
            with self.assertRaisesRegex(ExtractionError, "content skipped"):
                extract_pdf(self.conn, self.path, self.registration.sha256)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0], 0)

    def test_cli_reports_new_and_cached(self):
        db = str(Path(self.directory.name) / "cli.sqlite3")
        output = io.StringIO()
        with self.fake([Page("short"), Page("")]), contextlib.redirect_stdout(output):
            self.assertEqual(main(["import-pdf", str(self.path), "--db", db, "--status", "queued"]), 0)
            self.assertEqual(main(["import-pdf", str(self.path), "--db", db, "--status", "read"]), 0)
        self.assertIn("new", output.getvalue())
        self.assertIn("duplicate", output.getvalue())
        self.assertIn("cached", output.getvalue())
        self.assertIn("Warning", output.getvalue())

    def test_cli_failure_keeps_registration(self):
        db = str(Path(self.directory.name) / "failure.sqlite3")
        with self.fake([Page(RuntimeError("broken"))]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["import-pdf", str(self.path), "--db", db, "--status", "queued"]), 2)
        conn = sqlite3.connect(db)
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM extractions").fetchone()[0], 0)
        finally:
            conn.close()

    def test_cli_analyze_imported_pdf_and_reuse(self):
        import json
        from vaurien_atlas.analyst import SECTIONS
        db = str(Path(self.directory.name) / "analysis.sqlite3")
        data = {name: [] for name in SECTIONS}
        data.update(unknowns=["Unknown"], extraction_limitations=["Figures absent"])
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(status="completed", output_text=json.dumps(data), usage=None)
        sdk = types.SimpleNamespace(OpenAI=lambda **kwargs: types.SimpleNamespace(responses=types.SimpleNamespace(create=create)))
        out = io.StringIO()
        with self.fake([Page("Some extracted text")]), patch.dict("sys.modules", {"openai": sdk}), contextlib.redirect_stdout(out):
            self.assertEqual(main(["import-pdf", str(self.path), "--db", db, "--status", "queued"]), 0)
            args = ["analyze-pdf", str(self.path), "--db", db, "--model", "test"]
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("cached", out.getvalue())
