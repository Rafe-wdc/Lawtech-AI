"""Drafting niche selector — picks a filing-niche key for the current matter.

A single Gemini 2.5 Flash Lite call. Inputs: user query + first ~2K
characters of uploaded source docs (if any) + the canonical niche keys
from ``config/drafting_niches.py``. Output: a validated niche key or the
sentinel ``"none"`` when no overlay applies.

Design points:

* **Zero mechanical regex.** The classifier is a prompt — extension is
  through ``config/drafting_niches.NICHE_KEYS`` + prompt hints, never
  through a keyword table. Aligns with the project-level rule
  ``feedback_no_mechanical_patterns``.
* **Structured output.** ``with_structured_output(include_raw=True)``
  guarantees a validated pick or a clean fallback.
* **Fail-safe.** ANY error (timeout, invalid pick, empty picks list)
  returns ``None`` — the caller drops the overlay and the base
  DRAFTING_SYSTEM_PROMPT still produces a competent draft.
* **Cost-safe.** One Flash-Lite call. ~2K chars of source text max.
* **Token telemetry.** Recorded via ``core.token_tracker`` under
  ``("Drafting", "pick_niche")``.

Caller contract:

    niche_key = await pick_drafting_niche(query, user_facts)
    overlay = get_niche_overlay(niche_key)  # "" if key was None / "none"
    system_prompt = base_prompt + ("\\n\\n" + overlay if overlay else "")

Public API:

* ``pick_drafting_niche(query, user_facts, *, timeout_s=8) -> str | None``
* ``NICHE_SELECTOR_PROMPT`` — the prompt template used (exposed for tests).
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate

from config.drafting_niches import NICHE_KEYS
from core.logger import get_logger, log_time, short_err

log = get_logger("Drafting.NicheSelector")


# The classifier is instructed to return one of the NICHE_KEYS OR the
# literal string "none". Kept out of the prompt so a downstream test can
# assert the sentinel value.
NICHE_NONE_SENTINEL = "none"


NICHE_SELECTOR_PROMPT = """You are a senior Indian legal drafting assistant.

Your job: given the USER QUERY (and, when supplied, the first ~2000 chars
of UPLOADED SOURCE DOCUMENTS), pick the ONE filing-niche key from the
list below that best fits the document the user is asking us to produce.

If NONE of the keys fit — e.g. the user is asking for something outside
these niches (a first-information report, a will, a partnership deed
outside the generic contract slot, a police complaint that is not a
Section 138 NI Act complaint, a study memo, a note of arguments) —
return the literal string "{none_sentinel}". Do not force a bad fit.

Rules for the pick:

1. Match on the DOCUMENT the user wants us to draft, NOT on documents
   the user has uploaded as source material. A user who uploads a
   police FIR and asks for a bail application wants
   `bail_application_regular` or `bail_application_anticipatory`,
   not an FIR-niche key.
2. Prefer the more specific key over the generic one. If the matter
   is a Section 138 NI Act criminal complaint, use
   `cheque_bounce_complaint_s138` — not `plaint_civil_suit`.
3. When the user's request could reasonably be a "reply" or a
   "rejoinder", disambiguate by WHO is filing it: the party who
   receives a notice / plaint / statement of defence and replies to
   it is drafting a "reply" or "rejoinder"; the party who initiated
   the proceeding is drafting the initiating pleading.
4. For arbitration matters, `arbitration_statement_of_claim` is the
   claimant's first pleading; `arbitration_rejoinder` is the claimant's
   reply to the respondent's statement of defence. Choose accordingly.
5. When the user asks for a "quashing" of an FIR / complaint /
   summoning order, always use `quashing_petition_bnss528`.
6. When the user asks for a writ under Article 226 (High Court),
   use `writ_petition_article_226`; under Article 32 (Supreme Court),
   use `writ_petition_article_32`.
7. When the user asks for a review of a judgment / order, use
   `review_petition` regardless of whether the underlying proceeding
   was civil or criminal.
8. When the user asks for a transfer of a case, use `transfer_petition`.

Available niche keys:
{niche_keys}

USER QUERY:
{query}

UPLOADED SOURCE DOCUMENTS (first ~2000 characters, if any):
{source_preview}

Return your answer as a JSON object with two fields:
- niche_key: one of the keys above, or the literal string "{none_sentinel}"
- reasoning: one short sentence explaining the pick (<= 30 words)
"""


class _NichePick(BaseModel):
    """Structured output for the niche selector."""

    niche_key: str = Field(
        description=(
            "One of the canonical NICHE_KEYS from config/drafting_niches.py, "
            "or the literal string 'none' when no niche applies."
        )
    )
    reasoning: str = Field(
        default="",
        description="One short sentence explaining the pick (<= 30 words).",
    )


# Trimmed source preview cap — 2000 chars is enough to catch cause title,
# addressee block, first substantive paragraph in most document types.
_SOURCE_PREVIEW_CAP = 2000


def _validate_pick(raw_key: str | None) -> str | None:
    """Normalise and validate the raw pick.

    Returns:
      * the key (as-is) if it is in NICHE_KEYS
      * ``None`` if the pick is the sentinel ``"none"``, empty, or unknown.
    """
    if not raw_key:
        return None
    key = raw_key.strip().lower()
    if key == NICHE_NONE_SENTINEL:
        return None
    if key in NICHE_KEYS:
        return key
    # Common LLM foibles — with-dashes vs with-underscores, or an entirely
    # different phrasing. Fail closed rather than accept a hallucinated key.
    log.warning(
        "Niche selector returned unknown key; treating as none",
        picked=key,
    )
    return None


async def pick_drafting_niche(
    query: str,
    user_facts: str | None = None,
    *,
    timeout_s: float = 8.0,
) -> str | None:
    """Pick a niche key for this drafting matter, or return ``None``.

    ``None`` is returned when:
      * the selector could not classify the matter (returned the sentinel)
      * the LLM returned an unrecognised key
      * the call timed out, errored, or the query was empty

    The caller feeds the returned key into
    ``config.drafting_niches.get_niche_overlay`` which returns an empty
    string on ``None`` — so the caller never needs a special case.
    """
    if not query or not query.strip():
        return None

    try:
        from core.clients import get_gemini_flash_lite

        # Routed through the tier factory (2026-09-07). This was the last
        # hardcoded gemini-2.5 model left on a live path — it runs once per
        # drafting request (step `pick_niche`), so it silently kept the
        # retired 2.5 Flash-Lite in production after the 3.x migration.
        llm = get_gemini_flash_lite(
            temperature=0.0,
        ).with_structured_output(_NichePick, include_raw=True)

        source_preview = ""
        if user_facts and user_facts.strip():
            trimmed = user_facts.strip()
            if len(trimmed) > _SOURCE_PREVIEW_CAP:
                source_preview = trimmed[:_SOURCE_PREVIEW_CAP] + "\n\n[... truncated ...]"
            else:
                source_preview = trimmed
        else:
            source_preview = "(no source documents uploaded)"

        keys_block = "\n".join(f"- {k}" for k in NICHE_KEYS)

        prompt = ChatPromptTemplate.from_template(NICHE_SELECTOR_PROMPT)
        chain = prompt | llm

        with log_time(log, "Niche selector"):
            raw_and_parsed = await asyncio.wait_for(
                chain.ainvoke({
                    "query": query.strip()[:4000],
                    "source_preview": source_preview,
                    "niche_keys": keys_block,
                    "none_sentinel": NICHE_NONE_SENTINEL,
                }),
                timeout=timeout_s,
            )

        # Token telemetry — one call, cheap tier, but record it for the
        # cost audit trail. Follows the pattern in
        # `agents/drafting.py::_pick_reference_source`.
        try:
            from core.token_tracker import record as _record_tokens
            _record_tokens("Drafting", "pick_niche", raw_and_parsed.get("raw"))
        except Exception:
            pass

        pick: _NichePick | None = raw_and_parsed.get("parsed")
        if pick is None:
            log.warning("Niche selector returned unparsed response")
            return None

        validated = _validate_pick(pick.niche_key)
        log.info(
            "Niche selector",
            picked=validated or "none",
            raw=pick.niche_key,
            reasoning=(pick.reasoning or "")[:120],
        )
        return validated

    except asyncio.TimeoutError:
        log.warning(
            "Niche selector timed out; falling back to no overlay",
            timeout_s=timeout_s,
        )
        return None
    except Exception as e:
        log.warning(
            "Niche selector failed; falling back to no overlay",
            error=short_err(e),
        )
        return None


__all__ = [
    "pick_drafting_niche",
    "NICHE_SELECTOR_PROMPT",
    "NICHE_NONE_SENTINEL",
]
