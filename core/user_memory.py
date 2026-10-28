"""User memory: a cross-session profile of the LAWYER using the assistant.

Design: docs/user_memory_design.md (approved 2026-10-27).

What this module owns
- The slot vocabulary and the profile rendering (`render_profile_block`).
- Gates that decide whether a turn is worth an extraction call.
- The extraction call (Gemini Flash Lite) and the server-side validation
  of its ADD / UPDATE / DELETE operations. The model is never trusted on
  its own: a hard-exclusion filter and the user_stated protection rule run
  on every operation regardless of what the model said.
- Explicit "remember that ..." / "forget that ..." handling.
- Resolving and verifying the user id from request headers.
- A request-scoped ContextVar carrying the rendered block so
  `core.clients.cacheable_system` can append it AFTER the cached static
  system prompt without threading a parameter through every drafting
  function.

Everything is gated on USER_MEMORY_ENABLED (default off). With the flag off
no table is read, no extraction runs, nothing is injected, and the review
API answers 403 (the status Node's frontend treats as "memory off").

Identity: Node's HS256 access token, forwarded by the frontend as
`Authorization: Bearer <jwt>` and verified here with `USER_MEMORY_JWT_SECRET`
(= user-service JWT_ACCESS_SECRET). No other signing mechanism exists.
Fail closed: with the flag on and no secret, no user id is ever resolved
(extraction and injection stay off) and the review API answers 403.
Rollout rule on chat / stream / search: no token -> the request runs
normally without memory (old clients keep working); a token that is
present but invalid or expired -> 401. USER_MEMORY_ALLOW_UNSIGNED=1 opts
local dev into a bare X-User-Id when no secret is set.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any, Mapping

from core.logger import get_logger, short_err

log = get_logger("UserMemory")


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------

def enabled() -> bool:
    return os.getenv("USER_MEMORY_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")


def _truthy(name: str) -> bool:
    return os.getenv(name, "0").strip().lower() in ("1", "true", "yes", "on")


def auth_mode() -> str:
    """How a user id may be proven on this deployment.

    "jwt"          USER_MEMORY_JWT_SECRET set: Node's HS256 access token as Bearer (prod).
    "unsigned"     USER_MEMORY_ALLOW_UNSIGNED=1 and no secret: bare X-User-Id (local dev only).
    "unconfigured" flag on, no secret, no opt-in: fail closed - no id is ever resolved.
    """
    if os.getenv("USER_MEMORY_JWT_SECRET", "").strip():
        return "jwt"
    if _truthy("USER_MEMORY_ALLOW_UNSIGNED"):
        return "unsigned"
    return "unconfigured"


def configured() -> bool:
    """Flag on AND a way to prove the user id. Everything that reads or
    writes a profile is gated on this, not on enabled() alone."""
    return enabled() and auth_mode() != "unconfigured"


def jwt_claims() -> tuple[str, ...]:
    """Claim(s) holding the user id, in order. Node (2026-10-28): the id is
    the numeric DB primary key in `userId`; there is no sub / iss / aud.
    USER_MEMORY_JWT_CLAIM overrides (comma-separated list accepted). No
    other claim is ever read: in particular the `username` claim never
    feeds the email slot or anything else in the profile."""
    raw = os.getenv("USER_MEMORY_JWT_CLAIM", "").strip()
    if raw:
        return tuple(c.strip() for c in raw.split(",") if c.strip())
    return ("userId",)


def jwt_leeway_s() -> float:
    """Clock-skew allowance on exp / nbf (Node asked for ~30 s)."""
    try:
        return max(0.0, float(os.getenv("USER_MEMORY_JWT_LEEWAY_S", "30")))
    except ValueError:
        return 30.0


def _extract_timeout_s() -> float:
    try:
        return max(5.0, float(os.getenv("USER_MEMORY_EXTRACT_TIMEOUT_S", "20")))
    except ValueError:
        return 20.0


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------

# slot -> (label shown on the review page, single-valued?, identity slot?)
# Identity slots fill the advocate's own details in documents. Only
# user_stated rows of an identity slot are ever injected; an inferred
# identity row is stored, shown on the review page as unconfirmed, and
# promoted to user_stated when the user confirms or edits it.
SLOTS: dict[str, tuple[str, bool, bool]] = {
    "advocate_name":       ("Advocate name",        True,  True),
    "designation":         ("Designation",          True,  True),
    "enrolment_no":        ("Enrolment number",     True,  True),
    "firm_name":           ("Firm name",            True,  True),
    "office_address":      ("Office address",       True,  True),
    "phone":               ("Phone",                True,  True),
    "email":               ("Email",                True,  True),
    "preferred_language":  ("Preferred draft language", True, False),
    "courts":              ("Regular courts",       False, False),
    "practice_areas":      ("Practice areas",       False, False),
    "drafting_preference": ("Drafting preference",  False, False),
    "note":                ("Note",                 False, False),
}
SINGLE_SLOTS = frozenset(s for s, (_l, single, _i) in SLOTS.items() if single)
MULTI_SLOTS = frozenset(s for s, (_l, single, _i) in SLOTS.items() if not single)
IDENTITY_SLOTS = frozenset(s for s, (_l, _s, ident) in SLOTS.items() if ident)
# Slots that are rendered into the model's context. `note` is review-page only.
INJECTED_SLOTS = frozenset(SLOTS) - {"note"}

SOURCE_USER = "user_stated"
SOURCE_INFERRED = "inferred"
OPS = ("ADD", "UPDATE", "DELETE")

MAX_VALUE_CHARS = 300
MAX_ROWS_PER_MULTI_SLOT = 8
MAX_ROWS_PER_USER = 40
MIN_INFERRED_CONFIDENCE = 0.70
MAX_BLOCK_CHARS = 2000          # ~500 tokens
EXTRACT_USER_TEXT_CHARS = 500

_LANG_NAMES = {
    "en": "English", "hi": "Hindi", "mr": "Marathi", "gu": "Gujarati",
    "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "bn": "Bengali",
    "ml": "Malayalam", "pa": "Punjabi", "ur": "Urdu", "or": "Odia",
}


def slot_label(slot: str) -> str:
    return SLOTS.get(slot, (slot, True, False))[0]


# ---------------------------------------------------------------------------
# Gates: is this turn worth an extraction call?
# ---------------------------------------------------------------------------

# English + romanised Hindi/Marathi first-person markers. Boundaries are
# explicit lookarounds (not \b) so the apostrophe forms work.
_FP_LATIN = (
    "i", "i'm", "i’m", "i've", "i’ve", "i am", "my", "me", "we", "our", "us",
    "mera", "meri", "mere", "hamara", "hamari", "hum", "main", "mujhe",
    "majha", "mazha", "majhi", "amcha", "amchi", "mala", "aamhi", "amhi",
)
_FP_LATIN_RE = re.compile(
    r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in sorted(_FP_LATIN, key=len, reverse=True))
    + r")(?![a-z])",
    re.IGNORECASE,
)
# Devanagari: match on a leading boundary only, because the trailing matra
# (ा/ी/े) varies and Python's \b does not treat combining marks as word chars.
_FP_DEVANAGARI = (
    "मैं", "मेरा", "मेरी", "मेरे", "मुझे", "हम", "हमारा", "हमारी", "हमें",
    "मी", "माझ", "मला", "आम्ही", "आमच",
)
_FP_DEVANAGARI_RE = re.compile(
    r"(?:^|[\s,.;:!?()\"'\-])(?:" + "|".join(_FP_DEVANAGARI) + r")"
)


def first_person_gate(text: str) -> bool:
    """True when the message talks about the speaker (worth an extraction)."""
    if not text:
        return False
    return bool(_FP_LATIN_RE.search(text) or _FP_DEVANAGARI_RE.search(text))


def first_person_span(text: str) -> int:
    """Offset of the first first-person marker, or -1."""
    m1 = _FP_LATIN_RE.search(text)
    m2 = _FP_DEVANAGARI_RE.search(text)
    starts = [m.start() for m in (m1, m2) if m]
    return min(starts) if starts else -1


_DOC_MARKERS_RE = re.compile(
    # Pleadings and court papers.
    r"IN THE (?:HON'?BLE )?(?:COURT|HIGH COURT|SUPREME COURT)|"
    r"\bWHEREAS\b|\bVERIFICATION\b|\bPRAYER\b|\bMOST RESPECTFULLY SHOWETH\b|"
    r"\bAFFIDAVIT\b|\bDEPONENT\b|\bVAKALATNAMA\b|^\s*#{1,3}\s|"
    # Correspondence the lawyer RECEIVED (a notice, letter or email pasted in).
    # Live negative test 2026-10-28: a short quoted notice in which opposing
    # counsel wrote "I, Adv. ..." passed the old gates and the extractor took
    # the other side's name, office, phone and email as the lawyer's own.
    r"\bLEGAL NOTICE\b|\bTAKE NOTICE\b|^\s*(?:From|To|Sub|Subject|Ref|Dated?)\s*:|"
    r"\bunder (?:the )?instructions? (?:from|of)\b|\byours (?:faithfully|sincerely|truly)\b|"
    r"\bdear (?:sir|madam)\b|\bcall upon you\b|\bthis is to inform you\b",
    re.IGNORECASE | re.MULTILINE,
)

# Quoted spans: "...", “...”, «...», and '...' when it is clearly a quotation
# (opening quote after start / space / colon). Spans under 24 chars are left
# alone so a quoted phrase in a preference ("Hon'ble Court") survives.
_QUOTED_SPAN_RE = re.compile(
    r"\"[^\"\n]{24,}?\"|\u201c[^\u201d\n]{24,}?\u201d|\u00ab[^\u00bb\n]{24,}?\u00bb"
    r"|(?:(?<=^)|(?<=[\s:(\[]))'[^'\n]{24,}?'(?=[\s.,;:!?)\]]|$)",
)
_QUOTED_PLACEHOLDER = " [quoted material omitted] "


def strip_quoted_spans(text: str) -> str:
    """Remove text the lawyer is quoting (a notice, an email, a clause).
    Whoever says "I" or "my" inside a quotation is not the lawyer, so the
    extractor never sees it. Independent of the model."""
    if not text:
        return text or ""
    out = _QUOTED_SPAN_RE.sub(_QUOTED_PLACEHOLDER, text)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def looks_like_pasted_document(text: str) -> bool:
    """A pasted draft / notice / judgment / letter, not a message about the
    lawyer. Checked on the ORIGINAL text, quotes included."""
    if not text:
        return False
    if len(text) > 1500:
        return True
    if text.count("\n") >= 8:
        return True
    return bool(_DOC_MARKERS_RE.search(text))


def extraction_text(text: str) -> str:
    """The user text handed to the extractor: quoted spans removed, then the
    first 500 chars, plus a short window around the first first-person
    marker when it lies past the cap (facts about the lawyer often come at
    the end of a long request)."""
    text = strip_quoted_spans((text or "").strip())
    if len(text) <= EXTRACT_USER_TEXT_CHARS:
        return text
    head = text[:EXTRACT_USER_TEXT_CHARS]
    pos = first_person_span(text)
    if pos >= EXTRACT_USER_TEXT_CHARS - 100:
        lo = max(0, pos - 60)
        hi = min(len(text), pos + 240)
        return head + " ... " + text[lo:hi]
    return head


# ---------------------------------------------------------------------------
# Hard exclusions on stored values (independent of the model)
# ---------------------------------------------------------------------------

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_CNR_RE = re.compile(r"\b[A-Z]{4}\d{2}-?\d{6}-?\d{4}\b|\b[A-Z]{4}\d{12}\b")
_CASE_NO_RE = re.compile(
    r"\b(?:no|nos|number)\.?\s*\d+\b"
    r"|\b\d{1,6}\s*/\s*(?:19|20)\d{2}\b"
    r"|\b(?:FIR|CNR|CC|CRL|WP|W\.P|OA|ITA|MA|RA|SLP|CA|OS|RCS|SCC|PIL|CRA|CRR|MCA)\b[\s.]*(?:no\.?\s*)?\d",
    re.IGNORECASE,
)
_AMOUNT_RE = re.compile(
    r"(?:₹|\brs\.?|\binr\b|\brupees\b)\s*\d"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:lakh|lakhs|lac|lacs|crore|crores|/-)",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b\d{1,2}[./-]\d{1,2}[./-](?:\d{2}|\d{4})\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)?\s+" + _MONTHS + r",?\s+(?:19|20)\d{2}\b"
    r"|\b" + _MONTHS + r"\s+\d{1,2},?\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_OTHER_PERSON_RE = re.compile(
    r"\b(?:client|accused|complainant|petitioner|respondent|plaintiff|defendant|"
    r"appellant|opposite party|deponent|witness)\b",
    re.IGNORECASE,
)


def hard_excluded(slot: str, value: str) -> str | None:
    """Return the reason a value must not be stored, or None if it is fine."""
    v = value or ""
    if _CNR_RE.search(v):
        return "cnr"
    # Dates before case numbers: "03/11/2026" is a date, not "11/2026".
    if _DATE_RE.search(v):
        return "date"
    # Enrolment numbers legitimately look like "MAH/1234/2015".
    if slot != "enrolment_no" and _CASE_NO_RE.search(v):
        return "case_number"
    if _AMOUNT_RE.search(v):
        return "amount"
    if slot in IDENTITY_SLOTS and _OTHER_PERSON_RE.search(v):
        return "other_person"
    return None


# ---------------------------------------------------------------------------
# Operation validation and application
# ---------------------------------------------------------------------------

def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _find_target(op: dict, rows: list[dict]) -> dict | None:
    """The existing row an op would modify, or None."""
    slot = op["slot"]
    same = [r for r in rows if r["slot"] == slot]
    if not same:
        return None
    if slot in SINGLE_SLOTS:
        return same[0]
    wanted = {_norm(op.get("old_value")).lower(), _norm(op.get("value")).lower()}
    for r in same:
        if _norm(r["value"]).lower() in wanted:
            return r
    return None


# Identity facts that do not change by a passing remark. From an implicit
# turn (post-turn extraction, as opposed to "remember that", "forget that"
# or the review page) an existing user_stated row in these slots can be
# neither updated nor deleted, whatever source the model claims. Office,
# phone, email, designation stay implicitly updatable ("our office has
# moved", approved example).
IMPLICIT_LOCKED_SLOTS = frozenset({"advocate_name", "enrolment_no", "firm_name"})


def validate_ops(raw_ops: Any, rows: list[dict], *, implicit: bool = False
                 ) -> tuple[list[dict], list[str]]:
    """Filter the model's operations down to the ones the server will apply.

    Rules (docs/user_memory_design.md section 3, as amended):
    - slot, op, source must be from the closed sets; value 1-300 chars.
    - user_stated ops carry confidence 1.0; inferred ops below 0.70 drop.
    - The hard-exclusion filter runs on every value.
    - An op may modify (UPDATE / DELETE / ADD-as-UPDATE on a single slot) a
      user_stated row only if the op is itself user_stated.
    - implicit=True (post-turn extraction): an existing user_stated row in
      IMPLICIT_LOCKED_SLOTS cannot be modified at all. The model's
      "user_stated" label is its own judgement, and a quoted notice can
      fool it; a name or enrolment number changes only on an explicit
      "remember that" / "forget that" or on the review page.
    Returns (clean_ops, reasons_for_dropped).
    """
    clean: list[dict] = []
    dropped: list[str] = []
    if not isinstance(raw_ops, list):
        return clean, ["ops_not_a_list"]
    for raw in raw_ops[:20]:
        if not isinstance(raw, dict):
            dropped.append("op_not_object")
            continue
        op = _norm(raw.get("op")).upper()
        slot = _norm(raw.get("slot")).lower()
        value = _norm(raw.get("value"))
        old_value = _norm(raw.get("old_value"))
        source = _norm(raw.get("source")).lower() or SOURCE_INFERRED
        if op not in OPS or slot not in SLOTS:
            dropped.append(f"bad_op_or_slot:{op}:{slot}")
            continue
        if source not in (SOURCE_USER, SOURCE_INFERRED):
            dropped.append(f"bad_source:{source}")
            continue
        if op != "DELETE" and not (1 <= len(value) <= MAX_VALUE_CHARS):
            dropped.append(f"bad_value_len:{slot}")
            continue
        if op == "DELETE" and not value and not old_value:
            dropped.append(f"delete_without_value:{slot}")
            continue
        try:
            conf = float(raw.get("confidence", 1.0 if source == SOURCE_USER else 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if source == SOURCE_USER:
            conf = 1.0
        elif conf < MIN_INFERRED_CONFIDENCE:
            dropped.append(f"low_confidence:{slot}:{conf:.2f}")
            continue
        conf = min(1.0, max(0.0, conf))
        if op != "DELETE":
            reason = hard_excluded(slot, value)
            if reason:
                dropped.append(f"hard_excluded:{reason}:{slot}")
                continue
        cand = {"op": op, "slot": slot, "value": value, "old_value": old_value,
                "source": source, "confidence": conf}
        target = _find_target(cand, rows)
        if target is not None and target.get("source") == SOURCE_USER and source != SOURCE_USER:
            dropped.append(f"protected_user_stated:{slot}")
            continue
        if (implicit and target is not None and slot in IMPLICIT_LOCKED_SLOTS
                and target.get("source") == SOURCE_USER
                and (op == "DELETE" or _norm(target.get("value")).lower() != value.lower())):
            dropped.append(f"implicit_identity_locked:{slot}")
            continue
        if op in ("UPDATE", "DELETE") and target is None:
            if op == "DELETE":
                dropped.append(f"delete_no_match:{slot}")
                continue
            # UPDATE with nothing to update behaves as ADD.
            cand["op"] = "ADD"
        if cand["op"] == "ADD" and slot in SINGLE_SLOTS and target is not None:
            cand["op"] = "UPDATE"
        cand["_target_id"] = target["id"] if target is not None else None
        clean.append(cand)
    return clean, dropped


async def apply_ops(user_id: str, thread_id: str, ops: list[dict], rows: list[dict]) -> list[dict]:
    """Apply validated ops against the store. Returns the ops actually applied."""
    from core.chat_store import chat_store

    applied: list[dict] = []
    live = [dict(r) for r in rows]
    for op in ops:
        slot, kind = op["slot"], op["op"]
        try:
            if kind == "DELETE":
                target = next((r for r in live if r["id"] == op["_target_id"]), None)
                if target is None:
                    continue
                await chat_store.delete_user_memory(target["id"], user_id)
                live = [r for r in live if r["id"] != target["id"]]
                applied.append({**op, "value": target["value"]})
            elif kind == "UPDATE":
                target = next((r for r in live if r["id"] == op["_target_id"]), None)
                if target is None:
                    continue
                await chat_store.update_user_memory(
                    target["id"], user_id, op["value"], op["source"], op["confidence"])
                target.update(value=op["value"], source=op["source"], confidence=op["confidence"])
                applied.append(op)
            else:  # ADD
                same = [r for r in live if r["slot"] == slot]
                if any(_norm(r["value"]).lower() == op["value"].lower() for r in same):
                    continue  # duplicate
                if slot in MULTI_SLOTS and len(same) >= MAX_ROWS_PER_MULTI_SLOT:
                    evict = sorted(
                        (r for r in same if r.get("source") == SOURCE_INFERRED),
                        key=lambda r: str(r.get("created_at", "")),
                    )
                    if not evict:
                        log.info("Multi slot full of user_stated rows; ADD dropped",
                                 slot=slot, user=user_id[:8])
                        continue
                    await chat_store.delete_user_memory(evict[0]["id"], user_id)
                    live = [r for r in live if r["id"] != evict[0]["id"]]
                if len(live) >= MAX_ROWS_PER_USER:
                    log.info("Profile at row cap; ADD dropped", slot=slot, user=user_id[:8])
                    continue
                new_id = await chat_store.insert_user_memory(
                    user_id, slot, op["value"], op["source"], op["confidence"], thread_id)
                live.append({"id": new_id, "slot": slot, "value": op["value"],
                             "source": op["source"], "confidence": op["confidence"],
                             "created_at": ""})
                applied.append(op)
        except Exception as e:
            log.warning("Memory op failed", op=kind, slot=slot, error=short_err(e))
    return applied


# ---------------------------------------------------------------------------
# Rendering (what the model sees)
# ---------------------------------------------------------------------------

def injectable_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        slot = r.get("slot", "")
        if slot not in INJECTED_SLOTS:
            continue
        if slot in IDENTITY_SLOTS and r.get("source") != SOURCE_USER:
            continue  # inferred identity is never injected
        out.append(r)
    return out


_BLOCK_HEADER = (
    "## ABOUT THE ADVOCATE USING THIS ASSISTANT (saved profile)\n"
    "These are the advocate's OWN details and standing preferences. Use them "
    "to fill the advocate's name, designation, enrolment number, firm, address, "
    "phone and email wherever a document needs them (signature block, \"From\" "
    "block, Vakalatnama, notice letterhead) instead of bracketed placeholders "
    "such as [Advocate Name] or [Advocate Address]. They are NOT facts of the "
    "matter: never present the advocate as a party, witness or deponent. If "
    "the current request gives a different value for any of these, the "
    "request wins.\n"
)


def render_profile_block(rows: list[dict]) -> str:
    """Render the injected block. Empty string when nothing is injectable."""
    rows = injectable_rows(rows)
    if not rows:
        return ""
    single = {r["slot"]: r["value"] for r in rows if r["slot"] in SINGLE_SLOTS}
    multi: dict[str, list[str]] = {}
    for r in rows:
        if r["slot"] in MULTI_SLOTS:
            multi.setdefault(r["slot"], []).append(r["value"])

    lines: list[str] = []
    if single.get("advocate_name") or single.get("designation") or single.get("enrolment_no"):
        who = single.get("advocate_name", "")
        if single.get("designation"):
            who = f"{who}, {single['designation']}" if who else single["designation"]
        if single.get("enrolment_no"):
            who = f"{who} (Enrolment {single['enrolment_no']})" if who else f"Enrolment {single['enrolment_no']}"
        lines.append(f"Advocate: {who}")
    if single.get("firm_name"):
        lines.append(f"Firm: {single['firm_name']}")
    if single.get("office_address"):
        lines.append(f"Office: {single['office_address']}")
    contact = " | ".join(p for p in (
        f"Phone: {single['phone']}" if single.get("phone") else "",
        f"Email: {single['email']}" if single.get("email") else "",
    ) if p)
    if contact:
        lines.append(contact)
    if multi.get("courts"):
        lines.append("Regular courts: " + "; ".join(multi["courts"]))
    if multi.get("practice_areas"):
        lines.append("Practice areas: " + "; ".join(multi["practice_areas"]))
    if single.get("preferred_language"):
        code = single["preferred_language"].strip().lower()
        lines.append("Preferred draft language: " + _LANG_NAMES.get(code, single["preferred_language"]))
    prefs = multi.get("drafting_preference") or []
    if prefs:
        lines.append("Drafting preferences:")
        lines.extend(f"- {p}" for p in prefs)

    block = _BLOCK_HEADER + "\n" + "\n".join(lines) + "\n"
    # Size cap: trim from the bottom (preferences first, then practice areas,
    # then courts) until it fits.
    while len(block) > MAX_BLOCK_CHARS and len(lines) > 1:
        lines.pop()
        block = _BLOCK_HEADER + "\n" + "\n".join(lines) + "\n"
    return block


# Request-scoped carrier read by core.clients.cacheable_system.
_profile_block_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "user_memory_profile_block", default="")


def set_request_profile_block(block: str) -> contextvars.Token:
    return _profile_block_var.set(block or "")


def reset_request_profile_block(token: contextvars.Token) -> None:
    try:
        _profile_block_var.reset(token)
    except ValueError:
        _profile_block_var.set("")


def current_profile_block() -> str:
    return _profile_block_var.get()


async def load_profile_block(user_id: str) -> str:
    """Rendered block for this user, or "" (flag off, no id, user opted out, empty)."""
    if not enabled() or not user_id:
        return ""
    try:
        from core.chat_store import chat_store
        if not await chat_store.get_user_memory_enabled(user_id):
            return ""
        rows = await chat_store.load_user_memory(user_id)
    except Exception as e:
        log.warning("Profile load failed; continuing without memory", error=short_err(e))
        return ""
    block = render_profile_block(rows)
    if block:
        log.info("Profile injected", user=user_id[:8], rows=len(injectable_rows(rows)),
                 chars=len(block))
    return block


# ---------------------------------------------------------------------------
# Extraction (Gemini Flash Lite)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """You maintain a short profile of the LAWYER who is using a legal drafting
assistant. Your only job is to decide what, if anything, in this
conversation turn is a DURABLE fact about the lawyer themself, and to
return the operations needed to keep the profile correct.

The profile is used to fill the advocate's own details in court documents
(signature block, "From" address, Vakalatnama) and to apply the lawyer's
standing preferences. It is NOT a record of the lawyer's cases.

## What to store (durable facts about the lawyer)
- Their own name, designation (Advocate, Senior Advocate, Notary, Partner),
  enrolment / bar council number, firm name, office address, phone, email.
- Courts and forums where they regularly practise.
- Practice areas.
- Preferred language for drafts.
- Standing drafting preferences ("always number prayer clauses", "use
  'Hon'ble Court'", "keep notices under two pages").
- Other stable personal-professional facts the lawyer states about
  themself (teaches, is a notary, is a senior counsel).

## What you MUST NOT store, under any circumstances
- Client names, opposite-party names, accused, complainant, witness,
  deponent, or any person who is not the lawyer.
- Facts of any matter: what happened, where, when, to whom.
- Case numbers, CNR numbers, FIR numbers, diary numbers, file numbers.
- Hearing dates, filing dates, limitation dates, or any date tied to a
  matter.
- Amounts: claim values, cheque amounts, fees, compensation, fines.
- Anything taken from an uploaded document or a pasted draft.
- Anything inside quotation marks, or that reads like a notice, letter,
  email or pleading the lawyer RECEIVED or is replying to. "I", "my",
  "we", "our" inside such text refer to its author (often the opposing
  counsel), never to the lawyer. A "[quoted material omitted]" marker
  shows where such text was removed; do not guess what it said.
- Judges' names, opposing counsel's names, opposing counsel's firm,
  office, phone or email.
- One-off instructions for the current draft ("keep it to one page",
  "make this one formal", "address it to the Collector"). Store a
  drafting preference only when it is stated as a general rule ("always",
  "in all my notices", "by default", "from now on").
- Anything about the current matter's court or forum when it is said
  only for this matter ("file this in Agra ITAT"). Store a court ONLY
  when the lawyer says they practise there regularly or asks to use it
  by default.
- Temporary states ("I am busy today", "I have a hearing tomorrow").
- Anything the lawyer asks you to forget.
If a fact is about a matter rather than about the lawyer, return no
operation for it. When in doubt, do not store.

## Slots (use exactly these names)
single-valued: advocate_name, designation, enrolment_no, firm_name,
               office_address, phone, email, preferred_language
multi-valued:  courts, practice_areas, drafting_preference, note

## Operations
- ADD: a new fact. For a single-valued slot this replaces any existing
  value.
- UPDATE: the lawyer corrected or changed an existing fact. Give the new
  value in "value" and the old one in "old_value".
- DELETE: the lawyer said a fact is wrong or no longer applies, or asked
  to forget it. Give the exact stored value.
- If nothing durable about the lawyer appears, return {"ops": []}.

## Source and confidence
- "user_stated": the lawyer said it about themself in plain words, or
  used "remember that". confidence 1.0.
- "inferred": a reasonable reading that the lawyer did not state
  outright (for example, their fourth draft in Bombay High Court
  suggests they practise there). confidence between 0.70 and 0.95.
  Below 0.70, return nothing.
Never UPDATE or DELETE a fact whose source is "user_stated" unless the
lawyer explicitly changes or withdraws it in this turn.

## Output
Return ONLY a JSON object, no prose, no code fence:
{"ops": [{"op": "ADD|UPDATE|DELETE", "slot": "<slot>", "value": "<text>",
          "old_value": "<text or omit>", "source": "user_stated|inferred",
          "confidence": <number>}]}
Values are plain text, at most 300 characters, written the way they
should appear in a document (for example "Adv. Priya S. Kulkarni", not
"the user is Priya"). preferred_language is an ISO 639-1 code (en, hi, mr).

## Examples
Turn: "Draft a bail application for my client Ramesh Yadav, FIR 112/2026
PS Hadapsar, hearing on 3 Nov. I'm Adv. Priya Kulkarni, enrolment
MAH/1234/2015, office at 201 Lex Towers, Pune."
-> ops: ADD advocate_name "Adv. Priya Kulkarni" (user_stated, 1.0);
        ADD enrolment_no "MAH/1234/2015" (user_stated, 1.0);
        ADD office_address "201 Lex Towers, Pune" (user_stated, 1.0).
   Nothing about Ramesh Yadav, the FIR, Hadapsar or 3 Nov.

Turn: "Reply to this legal notice. The notice demands Rs 4,50,000."
-> {"ops": []}

Turn: "Reply to this notice for me and deny liability for my client Anil
Deshmukh: "Under instructions from my client Suresh Patil, I, Adv. Rakesh
Mehta of Mehta Law Chambers, 12 Court Lane, Pune, phone +91 98765 43210,
call upon you to pay Rs 4,50,000." Keep it to one page."
-> {"ops": []}   (Rakesh Mehta is the opposing counsel speaking inside the
   quotation; "keep it to one page" is an instruction for this draft only)

Turn: "Our office has moved to 5th floor, Sadhu Vaswani Chowk, Pune."
Profile has office_address "201 Lex Towers, Pune".
-> ops: UPDATE office_address value "5th floor, Sadhu Vaswani Chowk,
        Pune", old_value "201 Lex Towers, Pune" (user_stated, 1.0).

Turn: "Forget my phone number."
Profile has phone "+91 98xxxxxx12".
-> ops: DELETE phone "+91 98xxxxxx12" (user_stated, 1.0).

Turn: "Always write the prayer clauses as (a), (b), (c), not 1, 2, 3."
-> ops: ADD drafting_preference "number prayer clauses (a), (b), (c)"
        (user_stated, 1.0).

Turn: "File this appeal in ITAT Agra."
-> {"ops": []}   (forum for this matter only)
"""

_REMEMBER_INSTRUCTION = (
    "\n## Explicit request\nThe lawyer began this message with \"remember that\" "
    "(or an equivalent). Every durable fact about the lawyer in it is "
    "user_stated with confidence 1.0. The exclusions above still apply: if "
    "the message is about a matter (a hearing, a client, a case number), "
    "return {\"ops\": []}.\n"
)
_FORGET_INSTRUCTION = (
    "\n## Explicit request\nThe lawyer asked to FORGET something. Return only "
    "DELETE operations (source user_stated, confidence 1.0) for the stored "
    "facts that match the request, using the exact stored values. If nothing "
    "matches, return {\"ops\": []}.\n"
)


def _profile_json(rows: list[dict]) -> str:
    return json.dumps(
        [{"slot": r["slot"], "value": r["value"], "source": r.get("source", "")} for r in rows],
        ensure_ascii=False,
    ) or "[]"


def build_extraction_message(rows: list[dict], user_text: str, mode: str = "turn") -> str:
    extra = _REMEMBER_INSTRUCTION if mode == "remember" else _FORGET_INSTRUCTION if mode == "forget" else ""
    return (
        EXTRACTION_PROMPT + extra
        + "\n## Current profile\n" + _profile_json(rows)
        + "\n\n## This turn\nUser: " + user_text.strip() + "\n"
    )


def parse_ops_json(text: str) -> list:
    """Tolerant parse of the extractor's output. Returns [] on any failure."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t).strip()
    if t.startswith("["):
        try:
            obj = json.loads(t)
        except (json.JSONDecodeError, ValueError):
            return []
    else:
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            obj = json.loads(t[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(obj, dict):
        ops = obj.get("ops", [])
        return ops if isinstance(ops, list) else []
    return obj if isinstance(obj, list) else []


def _extractor_llm():
    from core.clients import get_gemini_flash_lite
    return get_gemini_flash_lite(temperature=0.0, max_output_tokens=1024)


async def extract_ops(rows: list[dict], user_text: str, mode: str = "turn") -> list:
    """One Flash Lite call. Returns the raw ops list (unvalidated)."""
    from langchain.messages import HumanMessage
    llm = _extractor_llm()
    msg = build_extraction_message(rows, user_text, mode)
    response = await asyncio.wait_for(
        asyncio.to_thread(llm.invoke, [HumanMessage(content=msg)]),
        timeout=_extract_timeout_s(),
    )
    try:
        from core.token_tracker import record as _record_tokens
        _record_tokens("UserMemory", "extract", response)
    except Exception:
        pass
    text = getattr(response, "text", None) or getattr(response, "content", "") or ""
    if callable(text):
        text = text()
    return parse_ops_json(str(text))


async def after_turn_extraction(user_id: str, thread_id: str, user_text: str,
                                *, conversational: bool = False) -> list[dict]:
    """Post-turn extraction. Never raises; returns the ops applied.

    Gates, cheapest first: flag, user id, conversational ack, pasted
    document (original text), first-person marker (on the text with quoted
    spans removed), per-user setting, per-thread no_memory.
    """
    if not configured() or not user_id or not user_text:
        return []
    if conversational or looks_like_pasted_document(user_text):
        return []
    if not first_person_gate(strip_quoted_spans(user_text)):
        return []
    try:
        from core.chat_store import chat_store
        if not await chat_store.get_user_memory_enabled(user_id):
            return []
        if thread_id and await chat_store.get_thread_no_memory(thread_id):
            return []
        rows = await chat_store.load_user_memory(user_id)
        raw = await extract_ops(rows, extraction_text(user_text), "turn")
        ops, dropped = validate_ops(raw, rows, implicit=True)
        if dropped:
            log.info("Memory ops dropped by validation", user=user_id[:8], dropped=dropped[:8])
        if not ops:
            # Logged so a live run can tell "extractor ran, found nothing"
            # from "a gate skipped the turn" (silent).
            log.info("Memory extraction found nothing to store", user=user_id[:8],
                     raw_ops=len(raw) if isinstance(raw, list) else -1)
            return []
        applied = await apply_ops(user_id, thread_id, ops, rows)
        log.info("Memory updated from turn", user=user_id[:8],
                 applied=[f"{o['op']}:{o['slot']}" for o in applied])
        return applied
    except asyncio.TimeoutError:
        log.warning("Memory extraction timed out", user=user_id[:8])
    except Exception as e:
        log.warning("Memory extraction failed", user=user_id[:8], error=short_err(e))
    return []


# ---------------------------------------------------------------------------
# Explicit "remember that ..." / "forget that ..."
# ---------------------------------------------------------------------------

_REMEMBER_RE = re.compile(
    r"^\s*(?:please\s+|kindly\s+)?(?:remember|note|save|yaad\s+rakho|yaad\s+rakhna|"
    r"याद\s+रखो|याद\s+रखें|याद\s+रखना|लक्षात\s+ठेव|लक्षात\s+ठेवा)"
    r"\s*(?:that|कि|की)?\s*[:,-]?\s*(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_FORGET_RE = re.compile(
    r"^\s*(?:please\s+|kindly\s+)?(?:forget|delete|remove|erase|bhool\s+jao|bhul\s+jao|"
    r"भूल\s+जाओ|भूल\s+जाइए|भूल\s+जा|विसरा|विसरून\s+जा|विसर)"
    r"\s*(?:that|about|कि|की)?\s*[:,-]?\s*(.+?)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_FORGET_ALL_RE = re.compile(
    r"^(?:everything|all|all of it|everything about me|"
    r"(?:all (?:of )?)?my (?:whole |entire |saved )?(?:profile|memory|details|data|info|information)|"
    r"सब\s*कुछ|सब|सगळं|सगळे|सर्व|सारे)\s*[.!]*$",
    re.IGNORECASE,
)


def parse_direct_command(text: str) -> tuple[str, str] | None:
    """("remember"|"forget"|"forget_all", remainder) when the WHOLE message is
    a memory directive; None otherwise. A long drafting request that merely
    contains "remember that ..." mid-way goes through the normal pipeline."""
    if not text or len(text) > 600 or text.count("\n") > 3:
        return None
    m = _FORGET_RE.match(text)
    if m:
        rest = m.group(1).strip()
        if _FORGET_ALL_RE.match(rest):
            return ("forget_all", rest)
        return ("forget", rest)
    m = _REMEMBER_RE.match(text)
    if m:
        rest = m.group(1).strip()
        # "note" / "save" are common verbs; require some substance after them.
        if len(rest) < 3:
            return None
        return ("remember", rest)
    return None


_WHAT_MEMORY_KEEPS = (
    "Memory keeps only your own professional details and standing "
    "preferences (name, enrolment number, firm, office address, contact "
    "details, regular courts, practice areas, draft language, drafting "
    "style). It never keeps client names, case facts, case numbers, hearing "
    "dates or amounts."
)


def _fmt_rows(ops: list[dict]) -> str:
    return "\n".join(f"- {slot_label(o['slot'])}: {o['value']}" for o in ops)


async def handle_direct_command(user_id: str, thread_id: str, kind: str, remainder: str) -> str:
    """Save / delete per an explicit directive and return the confirmation text."""
    from core.chat_store import chat_store

    if not await chat_store.get_user_memory_enabled(user_id):
        return ("Memory is switched off for your account, so nothing was saved. "
                "You can turn it on from your profile page.")
    if thread_id and await chat_store.get_thread_no_memory(thread_id):
        return "Memory is off for this chat, so nothing was saved or changed."

    rows = await chat_store.load_user_memory(user_id)

    if kind == "forget_all":
        n = await chat_store.delete_user_memory_all(user_id)
        return (f"Cleared your saved profile ({n} item{'s' if n != 1 else ''} removed)."
                if n else "Your profile was already empty.")

    try:
        raw = await extract_ops(rows, remainder[:1200],
                                "remember" if kind == "remember" else "forget")
    except Exception as e:
        log.warning("Direct memory command extraction failed", error=short_err(e))
        return "Sorry, I could not update your profile just now. Please try again."

    # Explicit directives are user_stated by definition.
    for o in raw:
        if isinstance(o, dict):
            o["source"] = SOURCE_USER
            o["confidence"] = 1.0
            if kind == "forget":
                o["op"] = "DELETE"
    ops, dropped = validate_ops(raw, rows)
    applied = await apply_ops(user_id, thread_id, ops, rows)

    if kind == "forget":
        if not applied:
            return ("I did not find anything matching that in your saved profile, so "
                    "nothing was removed. You can see everything saved on your profile page.")
        return "Removed from your profile:\n" + _fmt_rows(applied)

    if not applied:
        excluded = any(d.startswith("hard_excluded") for d in dropped)
        why = (" That looks like a detail of a matter (a date, amount or case number), "
               "which memory does not keep." if excluded else "")
        return ("Nothing was saved." + why + " " + _WHAT_MEMORY_KEEPS)
    return ("Saved to your profile:\n" + _fmt_rows(applied)
            + "\n\nYou can see or change this any time from your profile page.")


# ---------------------------------------------------------------------------
# User id from headers
# ---------------------------------------------------------------------------

_USER_ID_RE = re.compile(r"^[A-Za-z0-9_.:@\-]{1,128}$")
_warned_bare_id = False
_warned_unconfigured = False


def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s.encode("ascii"))


def _verify_hs256_jwt(token: str, secret: str) -> str:
    """Return the user id claim of a valid HS256 JWT, else "".

    Equivalent to jwt.decode(token, secret, algorithms=["HS256"],
    leeway=30) without a PyJWT dependency: alg pinned to HS256 (so "none"
    and RS* are rejected), constant-time signature check, exp and nbf
    honoured when present with jwt_leeway_s() of skew, no iss / aud check
    (Node issues none), id from jwt_claims() cast to str (Node's userId is
    a numeric primary key).
    """
    try:
        h_b64, p_b64, s_b64 = token.split(".")
        header = json.loads(_b64url_decode(h_b64))
        if str(header.get("alg", "")) != "HS256":
            return ""
        expected = hmac.new(secret.encode("utf-8"), f"{h_b64}.{p_b64}".encode("ascii"),
                            hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(s_b64)):
            return ""
        payload = json.loads(_b64url_decode(p_b64))
        if not isinstance(payload, dict):
            return ""
        now = time.time()
        leeway = jwt_leeway_s()
        exp = payload.get("exp")
        if exp is not None and float(exp) + leeway < now:
            return ""
        nbf = payload.get("nbf")
        if nbf is not None and float(nbf) - leeway > now:
            return ""
        for claim in jwt_claims():
            v = payload.get(claim)
            if v is None or v == "" or isinstance(v, bool):
                continue
            v = str(v)  # numeric PK -> string user_id
            return v if _USER_ID_RE.match(v) else ""
    except Exception:
        return ""
    return ""


# Outcomes of verify_bearer(). Only "ok" carries a user id.
AUTH_OK = "ok"                    # verified token (or dev bare id)
AUTH_ABSENT = "absent"            # no credential sent: run without memory
AUTH_INVALID = "invalid"          # token present but forged / expired / malformed -> 401
AUTH_UNCONFIGURED = "unconfigured"  # flag on, no secret, no dev opt-in: fail closed -> 403
AUTH_OFF = "off"                  # USER_MEMORY_ENABLED not set -> 403 on memory routes


def verify_bearer(headers: Mapping[str, str]) -> tuple[str, str]:
    """(user_id, status) for this request's identity headers.

    jwt (prod)     Authorization: Bearer <HS256 access token>. If the
                   frontend also sends X-User-Id it must equal the claim.
    unsigned (dev) bare X-User-Id, only with USER_MEMORY_ALLOW_UNSIGNED=1.
    """
    global _warned_bare_id, _warned_unconfigured
    if not enabled():
        return "", AUTH_OFF
    mode = auth_mode()
    if mode == "unconfigured":
        if not _warned_unconfigured:
            _warned_unconfigured = True
            log.error("USER_MEMORY_ENABLED=1 but USER_MEMORY_JWT_SECRET is not set and "
                      "USER_MEMORY_ALLOW_UNSIGNED is not set: memory stays off (fail closed)")
        return "", AUTH_UNCONFIGURED
    get = lambda k: (headers.get(k) or headers.get(k.lower()) or headers.get(k.title()) or "")  # noqa: E731
    raw = get("X-User-Id").strip()
    if mode == "jwt":
        auth = get("Authorization").strip()
        if not auth:
            return "", AUTH_ABSENT
        if not auth.lower().startswith("bearer "):
            return "", AUTH_INVALID
        uid = _verify_hs256_jwt(auth[7:].strip(), os.getenv("USER_MEMORY_JWT_SECRET", "").strip())
        if not uid:
            return "", AUTH_INVALID
        if raw and raw != uid:
            log.warning("X-User-Id does not match the JWT userId claim", user=uid[:8])
            return "", AUTH_INVALID
        return uid, AUTH_OK
    # mode == "unsigned" (local dev)
    if not raw:
        return "", AUTH_ABSENT
    if not _USER_ID_RE.match(raw):
        return "", AUTH_INVALID
    if not _warned_bare_id:
        _warned_bare_id = True
        log.warning("USER_MEMORY_ALLOW_UNSIGNED=1: accepting bare X-User-Id (local dev only)")
    return raw, AUTH_OK


def resolve_user_id(headers: Mapping[str, str]) -> str:
    """Verified user id for this request, or "" (memory off for the request).
    See verify_bearer() for the status behind an empty result."""
    return verify_bearer(headers)[0]


def rows_for_review(rows: list[dict]) -> list[dict]:
    """Shape for GET /pyapi/memory."""
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "slot": r["slot"],
            "label": slot_label(r["slot"]),
            "value": r["value"],
            "source": r.get("source", ""),
            "confirmed": r.get("source") == SOURCE_USER,
            "injected": r["slot"] in INJECTED_SLOTS and (
                r["slot"] not in IDENTITY_SLOTS or r.get("source") == SOURCE_USER),
            "updated_at": str(r.get("updated_at") or ""),
        })
    return out
