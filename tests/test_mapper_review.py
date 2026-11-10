"""Exercise mapper proposals and explicit review without external API calls."""

import json
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from vaurien_atlas.analyst import SECTIONS
from vaurien_atlas.knowledge import ReviewStatus, Topic
from vaurien_atlas.paper import Paper, ReadingStatus
from vaurien_atlas.review import decide, list_proposals
from vaurien_atlas.storage import initialize_database, open_database, save_paper, save_topic
from vaurien_atlas.topic_mapper import propose_topics, stage_proposals
from vaurien_atlas.cli import main


class MapperReviewTests(unittest.TestCase):
    def setUp(self):
        self.conn = open_database(":memory:")
        self.addCleanup(self.conn.close)
        initialize_database(self.conn)
        self.paper = Paper(reading_status=ReadingStatus.READ)
        save_paper(self.conn, self.paper)
        self.pages = ("Feedback stabilizes the system.",)
        self.analysis = {name: [] for name in SECTIONS}
        self.analysis.update(unknowns=[], extraction_limitations=[])
        self.analysis["findings"] = [{"text": "Feedback stabilizes", "origin": "paper_claim",
                                      "evidence": [{"page": 1, "quote": "Feedback stabilizes"}]}]
        self.run_id = uuid4()
        self.sha = "a" * 64
        with self.conn:
            self.conn.execute("INSERT INTO file_fingerprints VALUES (?, ?)", (self.sha, str(self.paper.id)))
            self.conn.execute("INSERT INTO extractions VALUES (?, ?, ?)", (self.sha, "test", json.dumps(self.pages)))
            self.conn.execute("""INSERT INTO analysis_runs
                (id,paper_id,fingerprint,cache_key,model,instruction_version,status,started_at,result_json)
                VALUES (?, ?, ?, 'key', 'test', 'v1', 'completed', 'now', ?)""",
                (str(self.run_id), str(self.paper.id), self.sha, json.dumps({"analysis": self.analysis})))
        self.proposals = {"new_topics": [{"name": "Feedback control", "definition": "Control using feedback"}],
                          "links": [{"existing_id": "", "new_name": "Feedback control", "page": 1,
                                     "quote": "Feedback stabilizes"}]}

    def fake_client(self, data):
        # Simulate a model response; this does not evaluate scientific accuracy.
        return SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs:
            SimpleNamespace(status="completed", output_text=json.dumps(data))))

    def test_empty_catalog_stages_and_requires_review(self):
        data = propose_topics(self.fake_client(self.proposals), model="test", analysis=self.analysis,
                              pages=self.pages, candidates=())
        stage_proposals(self.conn, paper_id=self.paper.id, proposals=data, analysis=self.analysis, pages=self.pages,
                        candidates=(), provenance="test", analysis_run_id=self.run_id, extractor_version="test")
        pending = list_proposals(self.conn)
        topic, link = pending["topics"][0], pending["relationships"][0]
        with self.assertRaises(ValueError):
            decide(self.conn, "relationship", link.id, ReviewStatus.ACCEPTED)
        decide(self.conn, "topic", topic.id, ReviewStatus.ACCEPTED)
        decide(self.conn, "relationship", link.id, ReviewStatus.ACCEPTED)
        self.assertEqual(list_proposals(self.conn), {"topics": [], "relationships": []})
        with self.assertRaises(ValueError):
            decide(self.conn, "topic", topic.id, ReviewStatus.REJECTED)

    def test_invented_quote_rejected_before_staging(self):
        self.proposals["links"][0]["quote"] = "invented"
        with self.assertRaises(ValueError):
            propose_topics(self.fake_client(self.proposals), model="test", analysis=self.analysis,
                           pages=self.pages, candidates=())
        self.assertEqual(list_proposals(self.conn), {"topics": [], "relationships": []})

    def test_existing_topic_can_be_reused_and_link_rejected(self):
        topic = Topic("Feedback control", "Control using feedback", review_status=ReviewStatus.ACCEPTED)
        save_topic(self.conn, topic)
        proposal = {"new_topics": [], "links": [{"existing_id": str(topic.id), "new_name": "",
                    "page": 1, "quote": "Feedback stabilizes"}]}
        result = propose_topics(self.fake_client(proposal), model="test", analysis=self.analysis,
                                pages=self.pages, candidates=(topic,))
        stage_proposals(self.conn, paper_id=self.paper.id, proposals=result, analysis=self.analysis,
                        pages=self.pages, candidates=(topic,), provenance="test", analysis_run_id=self.run_id,
                        extractor_version="test")
        link = list_proposals(self.conn)["relationships"][0]
        self.assertEqual(link.target.id, topic.id)
        decide(self.conn, "relationship", link.id, ReviewStatus.REJECTED)
        self.assertEqual(list_proposals(self.conn), {"topics": [], "relationships": []})

    def test_failed_staging_rolls_back_new_topic(self):
        # A mismatched source run must not leave partial topic records behind.
        with self.assertRaises(ValueError):
            stage_proposals(self.conn, paper_id=self.paper.id, proposals=self.proposals, analysis=self.analysis,
                            pages=self.pages, candidates=(), provenance="test", analysis_run_id=uuid4(), extractor_version="test")
        self.assertEqual(list_proposals(self.conn), {"topics": [], "relationships": []})

    def test_partial_analysis_requires_explicit_mapping_choice(self):
        # Partial results only contain source-matched claims in the analysis payload.
        with self.conn:
            self.conn.execute("UPDATE analysis_runs SET result_json=? WHERE id=?",
                              (json.dumps({"analysis": self.analysis, "analysis_status": "partial"}), str(self.run_id)))
        kwargs = dict(paper_id=self.paper.id, proposals=self.proposals, analysis=self.analysis, pages=self.pages,
                      candidates=(), provenance="test", analysis_run_id=self.run_id, extractor_version="test")
        with self.assertRaises(ValueError):
            stage_proposals(self.conn, **kwargs)
        self.assertEqual(list_proposals(self.conn), {"topics": [], "relationships": []})
        stage_proposals(self.conn, **kwargs, allow_partial=True)
        self.assertEqual(len(list_proposals(self.conn)["topics"]), 1)

    def test_mapper_cli_uses_luna_plan_without_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            target = open_database(path)
            try:
                self.conn.backup(target)
            finally:
                target.close()
            fake = self.fake_client(self.proposals)
            with patch("vaurien_atlas.chatgpt_plan.sign_in", return_value=fake) as login, \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["map-topics", "--db", str(path), "--run", str(self.run_id),
                             "--extractor-version", "test"])
            self.assertEqual(code, 0)
            login.assert_called_once_with("gpt-6-luna")

    def test_mapper_can_explicitly_use_matched_evidence_from_legacy_partial_run(self):
        # An older draft hid an entire claim when one of its citations was invalid.
        legacy_claim = {"text": "A claim", "origin": "paper_claim", "evidence": [
            {"page": 1, "quote": "Feedback stabilizes"}, {"page": 1, "quote": "not present"}]}
        empty = {name: [] for name in SECTIONS}
        empty.update(unknowns=[], extraction_limitations=[])
        result = {"analysis": empty, "analysis_status": "partial", "unverified_claims": [
            {"section": "findings", "claim_index": 1, "claim": legacy_claim, "reason": "unverified"}]}
        with self.conn:
            self.conn.execute("UPDATE analysis_runs SET result_json=? WHERE id=?", (json.dumps(result), str(self.run_id)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            target = open_database(path)
            try:
                self.conn.backup(target)
            finally:
                target.close()
            with patch("vaurien_atlas.chatgpt_plan.sign_in", return_value=self.fake_client(self.proposals)), \
                 contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                code = main(["map-topics", "--db", str(path), "--run", str(self.run_id),
                             "--extractor-version", "test", "--allow-partial"])
            self.assertEqual(code, 0)
            check = open_database(path)
            try:
                self.assertEqual(len(list_proposals(check)["relationships"]), 1)
                self.assertEqual(json.loads(check.execute("SELECT result_json FROM analysis_runs").fetchone()[0]), result)
            finally:
                check.close()
