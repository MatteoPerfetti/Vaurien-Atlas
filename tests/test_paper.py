import unittest
from dataclasses import replace
from uuid import uuid4

from vaurien_atlas.paper import AnalysisScope, Paper, ReadingStatus


class PaperTests(unittest.TestCase):
    def test_missing_metadata_and_independent_status(self):
        paper = Paper(reading_status=ReadingStatus.READ)
        self.assertIsNone(paper.title)
        self.assertEqual(paper.analysis_scope, AnalysisScope.NOT_ANALYZED)
        self.assertEqual(paper.source_files, ())

    def test_identity_survives_metadata_and_path_changes(self):
        paper = Paper(reading_status=ReadingStatus.QUEUED, source_files=("old.pdf",))
        updated = replace(paper, reading_status=ReadingStatus.READING,
                          title="Example", source_files=("new.pdf",))
        self.assertEqual(updated.id, paper.id)
        self.assertNotEqual(Paper(reading_status=ReadingStatus.QUEUED).id, paper.id)
        self.assertEqual(replace(updated, id=uuid4()).source_files, ("new.pdf",))

    def test_rejects_invalid_fields(self):
        for values in (
            {"id": "not-a-uuid"}, {"reading_status": "read"},
            {"analysis_scope": "full_text"}, {"title": "  "},
            {"doi": 42}, {"source_files": ["a.pdf"]},
            {"source_files": ("",)}, {"source_files": ("a.pdf", "a.pdf")},
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Paper(**({"reading_status": ReadingStatus.QUEUED} | values))


if __name__ == "__main__":
    unittest.main()
