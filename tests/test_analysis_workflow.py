"""Check persisted analysis runs, cache reuse and failed-call handling."""

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from vaurien_atlas.analysis_workflow import run_analysis
from vaurien_atlas.analyst import SECTIONS
from vaurien_atlas.paper import Paper, ReadingStatus
from vaurien_atlas.storage import initialize_database, open_database, save_paper


class AnalysisWorkflowTests(unittest.TestCase):
    def setUp(self):
        # Use an isolated in-memory database and a synthetic source extraction.
        self.conn = open_database(":memory:")
        self.addCleanup(self.conn.close)
        initialize_database(self.conn)
        self.paper = Paper(reading_status=ReadingStatus.QUEUED)
        save_paper(self.conn, self.paper)
        self.sha = "a" * 64
        self.pages = ("Evidence on page one.",)
        with self.conn:
            self.conn.execute("INSERT INTO file_fingerprints VALUES (?, ?)", (self.sha, str(self.paper.id)))
            self.conn.execute("INSERT INTO extractions VALUES (?, ?, ?)", (self.sha, "reader-v1", json.dumps(self.pages)))
        self.kwargs = dict(paper_id=self.paper.id, fingerprint=self.sha, pages=self.pages,
                           extractor_version="reader-v1", model="test-model", scope="full_text",
                           extraction_limitations=["figures missing"])
        self.data = {s: [] for s in SECTIONS}
        self.data.update(unknowns=[], extraction_limitations=[])
        self.data["findings"] = [{"text": "A claim", "origin": "paper_claim",
                                  "evidence": [{"page": 1, "quote": "Evidence"}]}]
        self.calls = 0

    def factory(self):
        """Return a fake model client and count calls for cache assertions."""
        def create(**kwargs):
            self.calls += 1
            return SimpleNamespace(status="completed", output_text=json.dumps(self.data), usage=None)
        return SimpleNamespace(responses=SimpleNamespace(create=create))

    def test_persist_cache_and_invalidate_on_model_or_limitations(self):
        first, result, cached = run_analysis(self.conn, self.factory, **self.kwargs)
        self.assertFalse(cached)
        second, same, cached = run_analysis(self.conn, self.factory, **self.kwargs)
        self.assertTrue(cached)
        self.assertEqual((first, result), (second, same))
        self.assertEqual(self.calls, 1)
        run_analysis(self.conn, self.factory, **(self.kwargs | {"model": "another"}))
        run_analysis(self.conn, self.factory, **(self.kwargs | {"extraction_limitations": ["new limitation"]}))
        self.assertEqual(self.calls, 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM analysis_runs WHERE status='completed'").fetchone()[0], 3)

    def test_failure_recorded_and_never_reused(self):
        # Error messages may contain sensitive details, so only the type is saved.
        def broken():
            raise RuntimeError("sensitive details")
        with self.assertRaises(RuntimeError):
            run_analysis(self.conn, broken, **self.kwargs)
        row = self.conn.execute("SELECT status, failure_type, result_json FROM analysis_runs").fetchone()
        self.assertEqual(row, ("failed", "RuntimeError", None))
        _, _, cached = run_analysis(self.conn, self.factory, **self.kwargs)
        self.assertFalse(cached)

    def test_cache_is_separate_for_each_provider(self):
        # The same model name must not merge plan and API provenance.
        _, _, cached = run_analysis(self.conn, self.factory, **self.kwargs)
        self.assertFalse(cached)
        _, result, cached = run_analysis(self.conn, self.factory, **self.kwargs, provider="chatgpt-plan")
        self.assertFalse(cached)
        self.assertEqual(result["provider"], "chatgpt-plan")
        self.assertEqual(self.calls, 2)

    def test_rejects_mismatched_extracted_pages(self):
        with self.assertRaises(ValueError):
            run_analysis(self.conn, self.factory, **(self.kwargs | {"pages": ("other",)}))
        self.assertEqual(self.calls, 0)

    def test_partial_is_cached_and_can_be_explicitly_retried(self):
        self.data["findings"][0]["evidence"][0]["quote"] = "not found"
        _, result, cached = run_analysis(self.conn, self.factory, **self.kwargs)
        self.assertFalse(cached)
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(self.calls, 2)
        _, _, cached = run_analysis(self.conn, self.factory, **self.kwargs)
        self.assertTrue(cached)
        self.assertEqual(self.calls, 2)
        _, _, cached = run_analysis(self.conn, self.factory, **self.kwargs, retry_partial=True)
        self.assertFalse(cached)
        self.assertEqual(self.calls, 4)


if __name__ == "__main__":
    unittest.main()
