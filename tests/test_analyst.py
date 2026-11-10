"""Check analyst request and validation with simulated model responses."""

import json
import unittest
from types import SimpleNamespace

from vaurien_atlas.analyst import SECTIONS, analyze_paper


class Responses:
    """Record requests and return a predictable response without network calls."""
    def __init__(self, payload, status="completed"):
        self.payload, self.status, self.calls = payload, status, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(status=self.status, output_text=json.dumps(self.payload),
                               usage=SimpleNamespace(input_tokens=12, output_tokens=6))


def valid():
    """Build a small response with a verifiable quotation."""
    data = {name: [] for name in SECTIONS}
    data.update(unknowns=["Methods not found"], extraction_limitations=["Figures not extracted"])
    data["findings"] = [{"text": "A finding", "origin": "paper_claim",
                         "evidence": [{"page": 1, "quote": "supporting text"}]}]
    return data


class AnalystTests(unittest.TestCase):
    def test_one_call_with_evidence_and_usage(self):
        responses = Responses(valid())
        result = analyze_paper(SimpleNamespace(responses=responses), model="test-model",
            pages=("Some supporting text here.",), scope="full_text", extraction_limitations=["Figures missing"])
        self.assertEqual(result["analysis"]["findings"][0]["evidence"][0]["page"], 1)
        self.assertEqual(result["usage"]["input_tokens"], 12)
        self.assertEqual(len(responses.calls), 1)
        self.assertFalse(responses.calls[0]["store"])

    def test_invalid_quote_and_incomplete_response_rejected(self):
        data = valid()
        data["findings"][0]["evidence"][0]["quote"] = "invented"
        for response in (Responses(data), Responses(valid(), "incomplete")):
            with self.assertRaises(ValueError):
                analyze_paper(SimpleNamespace(responses=response), model="test", pages=("text",),
                              scope="partial_text", extraction_limitations=[])

    def test_no_truncation_or_model_call_when_too_large(self):
        responses = Responses(valid())
        with self.assertRaises(ValueError):
            analyze_paper(SimpleNamespace(responses=responses), model="test", pages=("long text",),
                          scope="abstract", extraction_limitations=[], max_input_chars=2)
        self.assertEqual(responses.calls, [])
