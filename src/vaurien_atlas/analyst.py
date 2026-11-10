"""One bounded, source-checked model call; outputs are proposals, not facts."""

import json
import re
from copy import deepcopy
from importlib.resources import files
from typing import Any

INSTRUCTION_VERSION = "paper_analyst_v2"
REPAIR_VERSION = "evidence_repair_v1"
ANALYSIS_POLICY_VERSION = "partial_review_v2"
SECTIONS = ("research_question", "contributions", "methods", "assumptions_limitations", "findings", "prerequisites")


class AnalysisValidationError(ValueError):
    """A controlled validation failure; messages never contain source text."""

def _object(properties: dict[str, Any]) -> dict[str, Any]:
    """Build a strict JSON Schema object with every supplied field required."""
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}

# Every claim has an origin and quotations pointing to numbered source pages.
_text = {"type": "string"}
_evidence = _object({"page": {"type": "integer"}, "quote": _text})
_claim = _object({"text": _text, "origin": {"type": "string", "enum": ["paper_claim", "background", "interpretation"]},
                  "evidence": {"type": "array", "items": _evidence}})
ANALYSIS_SCHEMA = _object({**{name: {"type": "array", "items": _claim} for name in SECTIONS},
                           "unknowns": {"type": "array", "items": _text},
                           "extraction_limitations": {"type": "array", "items": _text}})

# Repair responses identify existing evidence slots; they cannot rewrite claims or pages.
_repair_item = _object({"section": {"type": "string", "enum": list(SECTIONS)},
    "claim_index": {"type": "integer"}, "evidence_index": {"type": "integer"}, "quote": _text})
REPAIR_SCHEMA = _object({"replacements": {"type": "array", "items": _repair_item}})


def validate_analysis(data: Any, pages: tuple[str, ...], *, _missing: list | None = None) -> dict[str, Any]:
    """Reject malformed results and quotations absent from the stated page.

    Whitespace-only differences are matched on the same page and replaced by
    the exact source text. This does not prove the quote supports the claim.
    """
    if not isinstance(data, dict) or set(data) != set(SECTIONS) | {"unknowns", "extraction_limitations"}:
        raise AnalysisValidationError("invalid analysis sections")
    for name in ("unknowns", "extraction_limitations"):
        if not isinstance(data[name], list) or any(not isinstance(x, str) or not x.strip() for x in data[name]):
            raise AnalysisValidationError(f"invalid {name}")
    # Work on a copy; callers must use the returned, source-canonical result.
    data = deepcopy(data)
    # Empty sections are allowed, but every returned claim must have evidence.
    for name in SECTIONS:
        if not isinstance(data[name], list):
            raise AnalysisValidationError(f"invalid {name}")
        for claim_index, claim in enumerate(data[name], 1):
            if not isinstance(claim, dict) or set(claim) != {"text", "origin", "evidence"}:
                raise AnalysisValidationError("invalid claim")
            if not isinstance(claim["text"], str) or not claim["text"].strip() or claim["origin"] not in ("paper_claim", "background", "interpretation"):
                raise AnalysisValidationError("invalid claim text or origin")
            if not isinstance(claim["evidence"], list) or not claim["evidence"]:
                raise AnalysisValidationError("claim requires evidence")
            for evidence_index, evidence in enumerate(claim["evidence"], 1):
                if not isinstance(evidence, dict) or set(evidence) != {"page", "quote"}:
                    raise AnalysisValidationError("invalid evidence")
                page, quote = evidence["page"], evidence["quote"]
                if type(page) is not int or not 1 <= page <= len(pages) or not isinstance(quote, str) or not quote.strip():
                    if _missing is None:
                        raise AnalysisValidationError(f"invalid evidence location or quote (section={name}, claim={claim_index}, evidence={evidence_index})")
                    # Invalid locations cannot be repaired without choosing a page on the user's behalf.
                    _missing.append({"section": name, "claim_index": claim_index,
                        "evidence_index": evidence_index, "page": page, "claim": claim["text"],
                        "attempted_quote": quote, "repairable": False})
                    continue
                source = pages[page - 1]
                if quote not in source:
                    # Only whitespace may differ: never rewrite words, punctuation or page numbers.
                    pattern = r"\s+".join(re.escape(part) for part in re.split(r"\s+", quote.strip()))
                    matches = {match.group(0) for match in re.finditer(pattern, source)}
                    if len(matches) != 1:
                        if _missing is None:
                            raise AnalysisValidationError(f"evidence quote not found on stated page (section={name}, claim={claim_index}, evidence={evidence_index}, page={page})")
                        # Collect internally; unverified claims must never enter validated analysis.
                        _missing.append({"section": name, "claim_index": claim_index,
                            "evidence_index": evidence_index, "page": page,
                            "claim": claim["text"], "attempted_quote": quote, "repairable": True})
                    else:
                        evidence["quote"] = matches.pop()
    return data


def prepare_evidence_review(result: dict, pages: tuple[str, ...]) -> dict:
    """Separate each unresolved claim's matched and unmatched evidence.

    Older partial results stored the whole claim together. Reclassify those
    quotes against the supplied extraction without mutating the stored input.
    Source matching is not a semantic review.
    """
    result = deepcopy(result)
    analysis = result["analysis"]
    had_unverified = bool(result.get("unverified_claims", []))
    remaining = []
    for entry in result.get("unverified_claims", []):
        if "pending_evidence" in entry:
            remaining.append(entry)
            continue
        section = entry["section"]
        if section not in SECTIONS:
            raise AnalysisValidationError("invalid unverified claim section")
        wrapper = {name: [] for name in SECTIONS}
        wrapper.update(unknowns=[], extraction_limitations=[])
        wrapper[section] = [entry["claim"]]
        missing = []
        checked = validate_analysis(wrapper, pages, _missing=missing)[section][0]
        bad_indices = {issue["evidence_index"] for issue in missing}
        matched = [e for i, e in enumerate(checked["evidence"], 1) if i not in bad_indices]
        pending = [{"evidence_index": i, "evidence": evidence} for i, evidence in
                   enumerate(checked["evidence"], 1) if i in bad_indices]
        analysis_index = None
        if matched:
            # Keep source-matched evidence usable, while the claim stays a proposal.
            matched_claim = {**checked, "evidence": matched}
            analysis[section].append(matched_claim)
            analysis_index = len(analysis[section])
        if pending:
            remaining.append({**entry, "analysis_index": analysis_index, "pending_evidence": pending})
    result["unverified_claims"] = remaining
    if had_unverified:
        # Replace only progress notes generated by this application.
        analysis["unknowns"] = [note for note in analysis["unknowns"] if not re.fullmatch(
            r"\d+ claim\(s\) (?:excluded pending|have citations pending) evidence review\.", note)]
        if remaining:
            analysis["unknowns"].append(f"{len(remaining)} claim(s) have citations pending evidence review.")
        else:
            analysis["unknowns"].append("Previously flagged citations were source-matched; semantic review is still needed.")
    result["analysis"] = validate_analysis(analysis, pages)
    if result.get("analysis_status") == "partial" and had_unverified and not remaining:
        result["analysis_status"] = "complete"
    return result


def _repair_quotes(client: Any, *, model: str, draft: dict, pages: tuple[str, ...], issues: list,
                   max_output_tokens: int) -> tuple[dict, Any]:
    """Request one bounded repair and revalidate every resulting source quote."""
    if len(issues) > 20:
        raise AnalysisValidationError("too many unverified quotations for one repair")
    page_numbers = sorted({issue["page"] for issue in issues})
    payload = json.dumps({"issues": issues, "pages": [{"page": n, "text": pages[n - 1]} for n in page_numbers]}, ensure_ascii=False)
    if len(payload) > 100_000:
        raise AnalysisValidationError("evidence repair input exceeds limit; no text was truncated")
    instruction = files("vaurien_atlas").joinpath(f"instructions/{REPAIR_VERSION}.txt").read_text(encoding="utf-8")
    response = client.responses.create(model=model, instructions=instruction, input=payload,
        max_output_tokens=max_output_tokens, store=False,
        text={"format": {"type": "json_schema", "name": "evidence_repair", "strict": True, "schema": REPAIR_SCHEMA}})
    if getattr(response, "status", None) != "completed" or not response.output_text:
        raise AnalysisValidationError("evidence repair response incomplete or refused")
    try:
        result = json.loads(response.output_text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AnalysisValidationError("invalid evidence repair JSON") from exc
    if not isinstance(result, dict) or set(result) != {"replacements"} or not isinstance(result["replacements"], list):
        raise AnalysisValidationError("invalid evidence repair response")
    expected = {(x["section"], x["claim_index"], x["evidence_index"]) for x in issues}
    seen = set()
    for item in result["replacements"]:
        if not isinstance(item, dict) or set(item) != {"section", "claim_index", "evidence_index", "quote"}:
            raise AnalysisValidationError("invalid evidence repair entry")
        section, claim_index, evidence_index, quote = item["section"], item["claim_index"], item["evidence_index"], item["quote"]
        if not isinstance(section, str) or type(claim_index) is not int or type(evidence_index) is not int or not isinstance(quote, str):
            raise AnalysisValidationError("invalid evidence repair fields")
        key = (section, claim_index, evidence_index)
        if key not in expected or key in seen or not quote.strip():
            raise AnalysisValidationError("evidence repair missing, duplicated or unsupported")
        seen.add(key)
        # Only the quotation changes; the original claim and page remain unchanged.
        draft[section][claim_index - 1]["evidence"][evidence_index - 1]["quote"] = quote
    if seen != expected:
        raise AnalysisValidationError("evidence repair did not cover all unverified quotations")
    return validate_analysis(draft, pages), getattr(response, "usage", None)


def analyze_paper(client: Any, *, model: str, pages: tuple[str, ...], scope: str,
                  extraction_limitations: list[str], max_input_chars: int = 200_000,
                  max_output_tokens: int = 4000) -> dict[str, Any]:
    """Call Responses and optionally perform one targeted evidence repair.

    No automatic full-analysis retries, truncation, persistence, or acceptance. The caller is
    responsible for consent to transmit the paper and for recording usage.
    """
    # Bound the request without silently discarding paper content.
    if not model or not pages or any(not isinstance(p, str) for p in pages):
        raise ValueError("model and extracted pages are required")
    if scope not in ("abstract", "partial_text", "full_text"):
        raise ValueError("explicit analysis scope is required")
    if not isinstance(extraction_limitations, list) or any(not isinstance(x, str) or not x.strip() for x in extraction_limitations):
        raise ValueError("invalid extraction limitations")
    if max_input_chars <= 0 or max_output_tokens <= 0 or sum(len(p) for p in pages) > max_input_chars:
        raise ValueError("input exceeds limit or bounds are invalid; no text was truncated")
    # Instructions are kept in a versioned file; paper text is untrusted input.
    instructions = files("vaurien_atlas").joinpath(f"instructions/{INSTRUCTION_VERSION}.txt").read_text(encoding="utf-8")
    payload = json.dumps({"scope": scope, "extraction_limitations": extraction_limitations,
                          "pages": [{"page": i, "text": text} for i, text in enumerate(pages, 1)]}, ensure_ascii=False)
    # Make one structured request. The caller supplies the client and model.
    response = client.responses.create(model=model, instructions=instructions, input=payload,
        max_output_tokens=max_output_tokens, store=False,
        text={"format": {"type": "json_schema", "name": "paper_analysis", "strict": True, "schema": ANALYSIS_SCHEMA}})
    if getattr(response, "status", None) != "completed" or not response.output_text:
        raise AnalysisValidationError("model response incomplete or refused")
    try:
        issues = []
        analysis = validate_analysis(json.loads(response.output_text), pages, _missing=issues)
    except (json.JSONDecodeError, TypeError) as exc:
        raise AnalysisValidationError("invalid model JSON") from exc
    # One failed citation must not discard unrelated, source-checked claims.
    unverified_claims = []
    repair_status = "not_needed"
    repair_usage = None
    repair_failure_type = None
    repair_attempted = False
    if issues:
        try:
            # Repair a copy so a partially failed repair cannot alter the original draft.
            if any(not issue["repairable"] for issue in issues):
                raise AnalysisValidationError("some evidence locations cannot be repaired automatically")
            repair_attempted = True
            analysis, repair_usage = _repair_quotes(client, model=model, draft=deepcopy(analysis), pages=pages,
                                                    issues=issues, max_output_tokens=max_output_tokens)
            repair_status = "succeeded"
        except Exception as exc:
            # Keep unsupported claims separately for review, not as verified facts.
            repair_status = "failed"
            repair_failure_type = type(exc).__name__
            invalid = {(issue["section"], issue["claim_index"]) for issue in issues}
            for section in SECTIONS:
                retained = []
                for index, claim in enumerate(analysis[section], 1):
                    if (section, index) in invalid:
                        unverified_claims.append({"section": section, "claim_index": index,
                            "claim": claim, "reason": "quotation or page could not be verified"})
                    else:
                        retained.append(claim)
                analysis[section] = retained
            analysis["unknowns"].append(f"{len(unverified_claims)} claim(s) have citations pending evidence review.")
            analysis = validate_analysis(analysis, pages)
    # Missing usage remains unknown rather than being estimated.
    usage = getattr(response, "usage", None)
    def total_tokens(name):
        first = getattr(usage, name, None)
        second = getattr(repair_usage, name, None) if repair_attempted else 0
        return first + second if first is not None and second is not None else None
    result = {"analysis": analysis, "analysis_status": "partial" if unverified_claims else "complete",
            "unverified_claims": unverified_claims, "scope": scope, "input_extraction_limitations": list(extraction_limitations),
            "model": model, "instruction_version": INSTRUCTION_VERSION,
            "evidence_repair": {"attempted": repair_attempted, "status": repair_status, "count": len(issues),
                                "failure_type": repair_failure_type, "instruction_version": REPAIR_VERSION if issues else None},
            "usage": {"input_tokens": total_tokens("input_tokens"), "output_tokens": total_tokens("output_tokens")}}
    return prepare_evidence_review(result, pages) if unverified_claims else result
