"""One bounded, source-checked model call; outputs are proposals, not facts."""

import json
from importlib.resources import files
from typing import Any

INSTRUCTION_VERSION = "paper_analyst_v1"
SECTIONS = ("research_question", "contributions", "methods", "assumptions_limitations", "findings", "prerequisites")

def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

_text = {"type": "string"}
_evidence = _object({"page": {"type": "integer"}, "quote": _text})
_claim = _object({"text": _text, "origin": {"type": "string", "enum": ["paper_claim", "background", "interpretation"]},
                  "evidence": {"type": "array", "items": _evidence}})
ANALYSIS_SCHEMA = _object({**{name: {"type": "array", "items": _claim} for name in SECTIONS},
                           "unknowns": {"type": "array", "items": _text},
                           "extraction_limitations": {"type": "array", "items": _text}})


def validate_analysis(data: Any, pages: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != set(SECTIONS) | {"unknowns", "extraction_limitations"}:
        raise ValueError("invalid analysis sections")
    for name in ("unknowns", "extraction_limitations"):
        if not isinstance(data[name], list) or any(not isinstance(x, str) or not x.strip() for x in data[name]):
            raise ValueError(f"invalid {name}")
    for name in SECTIONS:
        if not isinstance(data[name], list):
            raise ValueError(f"invalid {name}")
        for claim in data[name]:
            if not isinstance(claim, dict) or set(claim) != {"text", "origin", "evidence"}:
                raise ValueError("invalid claim")
            if not isinstance(claim["text"], str) or not claim["text"].strip() or claim["origin"] not in ("paper_claim", "background", "interpretation"):
                raise ValueError("invalid claim text or origin")
            if not isinstance(claim["evidence"], list) or not claim["evidence"]:
                raise ValueError("claim requires evidence")
            for evidence in claim["evidence"]:
                if not isinstance(evidence, dict) or set(evidence) != {"page", "quote"}:
                    raise ValueError("invalid evidence")
                page, quote = evidence["page"], evidence["quote"]
                if type(page) is not int or not 1 <= page <= len(pages) or not isinstance(quote, str) or not quote.strip() or quote not in pages[page - 1]:
                    raise ValueError("evidence quote not found on stated page")
    return data


def analyze_paper(client: Any, *, model: str, pages: tuple[str, ...], scope: str,
                  extraction_limitations: list[str], max_input_chars: int = 200_000,
                  max_output_tokens: int = 4000) -> dict[str, Any]:
    """Call Responses once; caller supplies the OpenAI client and model explicitly.

    No automatic retries, truncation, persistence, or acceptance. The caller is
    responsible for consent to transmit the paper and for recording usage.
    """
    if not model or not pages or any(not isinstance(p, str) for p in pages):
        raise ValueError("model and extracted pages are required")
    if scope not in ("abstract", "partial_text", "full_text"):
        raise ValueError("explicit analysis scope is required")
    if not isinstance(extraction_limitations, list) or any(not isinstance(x, str) or not x.strip() for x in extraction_limitations):
        raise ValueError("invalid extraction limitations")
    if max_input_chars <= 0 or max_output_tokens <= 0 or sum(len(p) for p in pages) > max_input_chars:
        raise ValueError("input exceeds limit or bounds are invalid; no text was truncated")
    instructions = files("vaurien_atlas").joinpath("instructions/paper_analyst_v1.txt").read_text(encoding="utf-8")
    payload = json.dumps({"scope": scope, "extraction_limitations": extraction_limitations,
                          "pages": [{"page": i, "text": text} for i, text in enumerate(pages, 1)]}, ensure_ascii=False)
    response = client.responses.create(model=model, instructions=instructions, input=payload,
        max_output_tokens=max_output_tokens, store=False,
        text={"format": {"type": "json_schema", "name": "paper_analysis", "strict": True, "schema": ANALYSIS_SCHEMA}})
    if getattr(response, "status", None) != "completed" or not response.output_text:
        raise ValueError("model response incomplete or refused")
    try:
        analysis = validate_analysis(json.loads(response.output_text), pages)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid model JSON") from exc
    usage = getattr(response, "usage", None)
    return {"analysis": analysis, "scope": scope, "input_extraction_limitations": list(extraction_limitations),
            "model": model, "instruction_version": INSTRUCTION_VERSION,
            "usage": {"input_tokens": getattr(usage, "input_tokens", None),
                      "output_tokens": getattr(usage, "output_tokens", None)}}
