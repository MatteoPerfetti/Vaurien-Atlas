"""Check per-citation review and preservation of legacy partial analyses."""

import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from vaurien_atlas.analyst import SECTIONS
from vaurien_atlas.evidence_review import decide_evidence, list_evidence, revise_matched_evidence, show_analysis, show_page
from vaurien_atlas.paper import Paper, ReadingStatus
from vaurien_atlas.storage import initialize_database, open_database, save_paper
from vaurien_atlas.cli import main


class EvidenceReviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = open_database(":memory:")
        self.addCleanup(self.conn.close)
        initialize_database(self.conn)
        paper = Paper(reading_status=ReadingStatus.READ)
        save_paper(self.conn, paper)
        self.run, fingerprint = uuid4(), "b" * 64
        analysis = {section: [] for section in SECTIONS}
        analysis.update(unknowns=["1 claim(s) excluded pending evidence review."], extraction_limitations=[])
        claim = {"text": "A claim", "origin": "paper_claim", "evidence": [
            {"page": 1, "quote": "valid quotation"}, {"page": 1, "quote": "invented quote"}]}
        # Legacy results kept the whole claim when only one quotation failed.
        result = {"analysis": analysis, "analysis_status": "partial", "unverified_claims": [
            {"section": "findings", "claim_index": 1, "claim": claim, "reason": "unverified"}]}
        with self.conn:
            self.conn.execute("INSERT INTO file_fingerprints VALUES (?, ?)", (fingerprint, str(paper.id)))
            self.conn.execute("INSERT INTO extractions VALUES (?, ?, ?)",
                              (fingerprint, "v1", json.dumps(["valid quotation and another passage"])))
            self.conn.execute("""INSERT INTO analysis_runs
                (id,paper_id,fingerprint,cache_key,model,instruction_version,status,started_at,result_json)
                VALUES (?, ?, ?, 'key', 'test', 'v1', 'completed', 'now', ?)""",
                (str(self.run), str(paper.id), fingerprint, json.dumps(result)))

    def saved(self):
        return json.loads(self.conn.execute("SELECT result_json FROM analysis_runs WHERE id=?", (str(self.run),)).fetchone()[0])

    def test_legacy_review_splits_evidence_and_preview_is_read_only(self):
        before = self.saved()
        pending = list_evidence(self.conn, self.run, "v1")
        self.assertEqual(pending[0]["pending_evidence"][0]["evidence_index"], 2)
        self.assertEqual(pending[0]["analysis_index"], 1)
        self.assertEqual(self.saved(), before)

    def test_replacement_is_checked_and_original_kept_in_history(self):
        args = dict(section="findings", claim_index=1, evidence_index=2, action="replace")
        with self.assertRaises(ValueError):
            decide_evidence(self.conn, self.run, "v1", **args, page=1, quote="not present")
        self.assertEqual(self.saved()["analysis_status"], "partial")
        decide_evidence(self.conn, self.run, "v1", **args, page=1, quote="another passage")
        result = self.saved()
        self.assertEqual(result["analysis_status"], "source_reviewed")
        self.assertEqual(result["unverified_claims"], [])
        self.assertEqual([e["quote"] for e in result["analysis"]["findings"][0]["evidence"]],
                         ["valid quotation", "another passage"])
        self.assertEqual(result["evidence_review_history"][0]["original"]["quote"], "invented quote")

    def test_reject_does_not_create_a_fake_citation(self):
        decide_evidence(self.conn, self.run, "v1", section="findings", claim_index=1,
                        evidence_index=2, action="reject")
        result = self.saved()
        self.assertEqual(result["analysis"]["findings"][0]["evidence"], [{"page": 1, "quote": "valid quotation"}])
        self.assertEqual(result["evidence_review_history"][0]["action"], "reject")

    def test_recorded_extraction_version_is_enforced(self):
        result = self.saved()
        result["extractor_version"] = "v2"
        with self.conn:
            self.conn.execute("UPDATE analysis_runs SET result_json=? WHERE id=?", (json.dumps(result), str(self.run)))
        with self.assertRaisesRegex(ValueError, "version differs"):
            list_evidence(self.conn, self.run, "v1")

    def test_show_page_returns_original_extracted_text(self):
        self.assertEqual(show_page(self.conn, self.run, "v1", 1), "valid quotation and another passage")
        with self.assertRaises(ValueError):
            show_page(self.conn, self.run, "v1", 0)

    def test_human_can_reject_a_text_match_that_does_not_support_claim(self):
        # Exact text presence does not guarantee that the passage supports the claim.
        preview = show_analysis(self.conn, self.run, "v1")
        self.assertEqual(preview["analysis"]["findings"][0]["evidence"][0]["quote"], "valid quotation")
        revise_matched_evidence(self.conn, self.run, "v1", section="findings", claim_index=1,
                                evidence_index=1, action="reject")
        result = self.saved()
        self.assertEqual(result["analysis"]["findings"], [])
        self.assertIsNone(result["unverified_claims"][0]["analysis_index"])
        decide_evidence(self.conn, self.run, "v1", section="findings", claim_index=1, evidence_index=2,
                        action="replace", page=1, quote="another passage")
        self.assertEqual(self.saved()["analysis"]["findings"][0]["evidence"][0]["quote"], "another passage")

    def test_source_matched_quote_can_be_replaced(self):
        revise_matched_evidence(self.conn, self.run, "v1", section="findings", claim_index=1,
                                evidence_index=1, action="replace", page=1, quote="another passage")
        result = self.saved()
        self.assertEqual(result["analysis"]["findings"][0]["evidence"][0]["quote"], "another passage")
        self.assertEqual(result["evidence_review_history"][0]["original"]["quote"], "valid quotation")

    def test_cli_inspects_and_rejects_without_a_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "review.sqlite3"
            target = open_database(path)
            try:
                self.conn.backup(target)
            finally:
                target.close()
            base = ["--db", str(path), "--run", str(self.run), "--extractor-version", "v1"]
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["evidence-list", *base]), 0)
                self.assertEqual(main(["evidence-decide", *base, "--section", "findings", "--claim", "1",
                                       "--evidence", "2", "--action", "reject"]), 0)
                self.assertEqual(main(["analysis-show", *base]), 0)
            self.assertIn("invented quote", output.getvalue())
            self.assertIn("Evidence review saved locally", output.getvalue())
            self.assertIn('"analysis_status": "source_reviewed"', output.getvalue())
