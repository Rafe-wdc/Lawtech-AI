"""Shared Tools: Guardrail utilities (PII + hallucination only).

The prompt-injection detection tools that used to live here
(`validate_input`, `detect_injection_regex`, `detect_injection_llm`) were
removed on 2026-07-25 along with the corresponding live-path code in
agents/guardrail.py. They were a dormant duplicate of a rejected policy
(see agents/guardrail.py module docstring) and were never wired into
the graph.

What remains:
  detect_pii / redact_pii   — content-quality utilities for Aadhaar /
                              PAN / phone / email / bank / IFSC masking
  flag_hallucination        — post-hoc grounding check for LLM output
                              against retrieved source documents
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field
from langchain.tools import tool

from core.clients import get_gemini_flash_full
from core.logger import get_logger

log = get_logger("GuardrailTools")


# PII patterns for Indian legal context
PII_PATTERNS = {
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
    "phone": re.compile(r"\b(?:\+91[\s-]?)?[6-9]\d{9}\b"),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    "bank_account": re.compile(r"\b\d{9,18}\b"),
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),
}

PII_REDACTION_MAP = {
    "aadhaar": "[AADHAAR REDACTED]",
    "pan": "[PAN REDACTED]",
    "phone": "[PHONE REDACTED]",
    "email": "[EMAIL REDACTED]",
    "bank_account": "[ACCOUNT REDACTED]",
    "ifsc": "[IFSC REDACTED]",
}


# --- Structured Output Schemas ---

class HallucinationResult(BaseModel):
    has_suspicious_claims: bool = Field(..., description="True if suspicious unsupported claims found")
    suspicious_claims: list[str] = Field(default_factory=list, description="List of suspicious claim texts")


# --- Tool Functions ---

@tool
def detect_pii(text: str) -> dict:
    """Detect personally identifiable information (PII) in text.

    Checks for Indian-specific PII patterns: Aadhaar numbers, PAN cards,
    phone numbers, email addresses, bank account numbers, and IFSC codes.

    Args:
        text: Text to scan for PII (user query or LLM response)

    Returns:
        Dict with keys: has_pii (bool), entities (dict of type → list of matches)
    """
    entities: dict[str, list[str]] = {}

    for pii_type, pattern in PII_PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            entities[pii_type] = matches

    return {
        "has_pii": bool(entities),
        "entities": entities,
    }


@tool
def redact_pii(text: str) -> str:
    """Redact personally identifiable information from text.

    Replaces detected PII with type-specific redaction markers.
    Handles: Aadhaar, PAN, phone numbers, emails, bank accounts, IFSC codes.

    Args:
        text: Text containing PII to be redacted

    Returns:
        Text with PII replaced by redaction markers (e.g. [AADHAAR REDACTED])
    """
    redacted = text
    for pii_type, pattern in PII_PATTERNS.items():
        replacement = PII_REDACTION_MAP.get(pii_type, "[REDACTED]")
        redacted = pattern.sub(replacement, redacted)
    return redacted


@tool
def flag_hallucination(content: str, sources: list[str]) -> dict:
    """Check LLM response for potential hallucinations against source documents.

    Uses Gemini Pro to verify whether claims in the response are supported
    by the provided source documents. Flags specific unsupported claims.

    Args:
        content: The LLM-generated response to verify
        sources: List of source document texts used to generate the response

    Returns:
        Dict with keys: has_suspicious_claims (bool), suspicious_claims (list of strings)
    """
    if not content or not sources:
        return {"has_suspicious_claims": False, "suspicious_claims": []}

    sources_text = "\n\n---\n\n".join(s[:500] for s in sources[:5])

    try:
        llm = get_gemini_flash_full(temperature=0.0).with_structured_output(HallucinationResult)
        prompt = (
            "You are a legal accuracy checker. Compare the AI response against the source documents.\n"
            "Flag any claims, case citations, section numbers, or legal provisions in the response "
            "that are NOT supported by the source documents.\n\n"
            f"Source Documents:\n{sources_text}\n\n"
            f"AI Response:\n{content[:2000]}\n\n"
            "List any suspicious or unsupported claims. If all claims are supported, return empty list."
        )
        result = llm.invoke(prompt)
        return {
            "has_suspicious_claims": result.has_suspicious_claims,
            "suspicious_claims": result.suspicious_claims,
        }
    except Exception as e:
        log.error(f"[Guardrail] Hallucination check failed: {e}")
        return {"has_suspicious_claims": False, "suspicious_claims": []}
