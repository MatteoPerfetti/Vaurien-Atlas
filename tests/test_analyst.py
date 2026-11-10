"""Check analyst request and validation with simulated model responses."""

import json
import unittest
from types import SimpleNamespace

from vaurien_atlas.analyst import AnalysisValidationError, SECTIONS, analyze_paper, validate_analysis


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

    def test_invalid_quote_is_separate_and_incomplete_response_rejected(self):
        data = valid()
        data["findings"][0]["evidence"][0]["quote"] = "invented"
        result = analyze_paper(SimpleNamespace(responses=Responses(data)), model="test", pages=("text",),
                               scope="partial_text", extraction_limitations=[])
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(result["analysis"]["findings"], [])
        self.assertEqual(result["unverified_claims"][0]["claim"]["evidence"][0]["quote"], "invented")
        with self.assertRaises(ValueError):
            analyze_paper(SimpleNamespace(responses=Responses(valid(), "incomplete")), model="test", pages=("text",),
                          scope="partial_text", extraction_limitations=[])

    def test_no_truncation_or_model_call_when_too_large(self):
        responses = Responses(valid())
        with self.assertRaises(ValueError):
            analyze_paper(SimpleNamespace(responses=responses), model="test", pages=("long text",),
                          scope="abstract", extraction_limitations=[], max_input_chars=2)
        self.assertEqual(responses.calls, [])

    def test_whitespace_only_quote_is_replaced_by_exact_source(self):
        data = valid()
        source = ("Some supporting\n   text here.",)
        checked = validate_analysis(data, source)
        self.assertEqual(checked["findings"][0]["evidence"][0]["quote"], "supporting\n   text")
        self.assertEqual(data["findings"][0]["evidence"][0]["quote"], "supporting text")

    def test_wrong_words_or_page_are_never_repaired(self):
        data = valid()
        data["findings"][0]["evidence"][0]["quote"] = "supporting invented text"
        with self.assertRaisesRegex(AnalysisValidationError, r"section=findings.*page=1"):
            validate_analysis(data, ("supporting text",))
        data["findings"][0]["evidence"][0]["quote"] = "supporting text"
        with self.assertRaises(AnalysisValidationError):
            validate_analysis(data, ("other page", "supporting text"))

    def test_one_targeted_repair_replaces_quote_but_not_claim_or_page(self):
        draft = valid()
        draft["findings"][0]["evidence"][0]["quote"] = "incorrect quotation"
        repair = {"replacements": [{"section": "findings", "claim_index": 1,
                                    "evidence_index": 1, "quote": "supporting text"}]}
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            value = draft if len(calls) == 1 else repair
            return SimpleNamespace(status="completed", output_text=json.dumps(value),
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        result = analyze_paper(SimpleNamespace(responses=SimpleNamespace(create=create)), model="test",
                               pages=("Some supporting text here.",), scope="partial_text", extraction_limitations=[])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["analysis"]["findings"][0]["text"], "A finding")
        self.assertEqual(result["analysis"]["findings"][0]["evidence"], [{"page": 1, "quote": "supporting text"}])
        self.assertEqual(result["evidence_repair"]["count"], 1)
        self.assertEqual(result["usage"], {"input_tokens": 20, "output_tokens": 10})

    def test_repair_cannot_invent_or_omit_evidence(self):
        draft = valid()
        draft["findings"][0]["evidence"][0]["quote"] = "incorrect quotation"
        for quote in ("", "invented replacement"):
            repair = {"replacements": [{"section": "findings", "claim_index": 1,
                                        "evidence_index": 1, "quote": quote}]}
            responses = iter((draft, repair))
            client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs:
                SimpleNamespace(status="completed", output_text=json.dumps(next(responses)), usage=None)))
            with self.subTest(quote=quote):
                result = analyze_paper(client, model="test", pages=("Some supporting text here.",),
                                       scope="partial_text", extraction_limitations=[])
                self.assertEqual(result["analysis_status"], "partial")
                self.assertEqual(result["analysis"]["findings"], [])
                self.assertEqual(len(result["unverified_claims"]), 1)

    def test_partial_keeps_other_verified_claims_after_repair_failure(self):
        draft = valid()
        draft["research_question"] = [{"text": "Unsupported", "origin": "paper_claim",
                                       "evidence": [{"page": 1, "quote": "invented"}]}]
        calls = []
        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(status="completed", output_text=json.dumps(draft), usage=None)
            raise RuntimeError("provider detail must not be exposed")
        result = analyze_paper(SimpleNamespace(responses=SimpleNamespace(create=create)), model="test",
                               pages=("Some supporting text here.",), scope="partial_text", extraction_limitations=[])
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(len(result["analysis"]["findings"]), 1)
        self.assertEqual(result["analysis"]["research_question"], [])
        self.assertEqual(result["unverified_claims"][0]["section"], "research_question")
        self.assertEqual(result["evidence_repair"]["failure_type"], "RuntimeError")
        self.assertIsNone(result["usage"]["input_tokens"])

    def test_invalid_page_is_unverified_without_guessing_or_repair_call(self):
        draft = valid()
        draft["findings"][0]["evidence"][0]["page"] = 99
        responses = Responses(draft)
        result = analyze_paper(SimpleNamespace(responses=responses), model="test", pages=("supporting text",),
                               scope="partial_text", extraction_limitations=[])
        self.assertEqual(result["analysis_status"], "partial")
        self.assertEqual(result["unverified_claims"][0]["claim"]["evidence"][0]["page"], 99)
        self.assertEqual(len(responses.calls), 1)
        self.assertFalse(result["evidence_repair"]["attempted"])
