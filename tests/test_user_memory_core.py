"""User memory: pure logic (gates, hard exclusions, op validation, rendering,
direct-command parsing, user-id verification). docs/user_memory_design.md.
"""

import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import base64
import hashlib
import hmac
import json
import time

import pytest

from core import user_memory as um


# --- gates -----------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "I'm Adv. Priya Kulkarni, enrolment MAH/1234/2015",
    "Our office has moved to Sadhu Vaswani Chowk",
    "please address notices to me at my chamber",
    "hamara office Pune mein hai",
    "mera naam Priya hai",
    "majha office Pune la aahe",
    "amcha firm Kulkarni & Associates",
    "मी पुण्यात प्रॅक्टिस करते",
    "माझा पत्ता बदलला आहे",
    "मेरा दफ्तर नोएडा में है",
    "हमें मराठीत ड्राफ्ट चाहिए",
    "आम्ही मुंबई हायकोर्टात काम करतो",
])
def test_first_person_gate_passes(text):
    assert um.first_person_gate(text)


@pytest.mark.parametrize("text", [
    "what does section 138 NI Act say",
    "draft a legal notice for cheque dishonour",
    "Minimum wages in Maharashtra 2026",
    "summarise this judgment",
])
def test_first_person_gate_blocks(text):
    assert not um.first_person_gate(text)


def test_pasted_document_detection():
    assert um.looks_like_pasted_document("IN THE HON'BLE HIGH COURT OF BOMBAY\nWrit Petition")
    assert um.looks_like_pasted_document("x" * 1501)
    assert um.looks_like_pasted_document("\n".join(["line"] * 9))
    assert not um.looks_like_pasted_document("I'm Adv. Priya, draft a bail application")


def test_extraction_text_caps_and_windows():
    short = "I'm Adv. X"
    assert um.extraction_text(short) == short
    long = ("Facts of the matter. " * 40) + "By the way I'm Adv. Priya Kulkarni, enrolment MAH/1/2015."
    out = um.extraction_text(long)
    assert len(out) < len(long)
    assert "Adv. Priya Kulkarni" in out


# --- hard exclusions ------------------------------------------------------

@pytest.mark.parametrize("slot,value,reason", [
    ("note", "CNR MHPU010012342026", "cnr"),
    ("note", "FIR No. 112/2026 PS Hadapsar", "case_number"),
    ("note", "WP 4521/2024", "case_number"),
    ("note", "cheque of Rs 4,50,000", "amount"),
    ("note", "claim of 5 lakh", "amount"),
    ("note", "hearing on 03/11/2026", "date"),
    ("note", "filed on 3 November 2026", "date"),
    ("advocate_name", "Ramesh Yadav, accused", "other_person"),
])
def test_hard_excluded(slot, value, reason):
    assert um.hard_excluded(slot, value) == reason


def test_enrolment_number_is_not_a_case_number():
    assert um.hard_excluded("enrolment_no", "MAH/1234/2015") is None
    assert um.hard_excluded("office_address", "201, Lex Towers, Fergusson College Rd, Pune 411004") is None
    assert um.hard_excluded("phone", "+91 98230 12345") is None


# --- validate_ops ----------------------------------------------------------

def _row(id_, slot, value, source="user_stated", conf=1.0):
    return {"id": id_, "slot": slot, "value": value, "source": source,
            "confidence": conf, "created_at": f"2026-10-0{id_}"}


def test_validate_basic_add_and_low_confidence_drop():
    ops, dropped = um.validate_ops([
        {"op": "ADD", "slot": "advocate_name", "value": "Adv. Priya Kulkarni",
         "source": "user_stated", "confidence": 0.3},
        {"op": "ADD", "slot": "courts", "value": "Bombay High Court",
         "source": "inferred", "confidence": 0.5},
        {"op": "ADD", "slot": "bogus", "value": "x", "source": "inferred", "confidence": 0.9},
    ], [])
    assert [o["slot"] for o in ops] == ["advocate_name"]
    assert ops[0]["confidence"] == 1.0          # user_stated is forced to 1.0
    assert any(d.startswith("low_confidence") for d in dropped)
    assert any(d.startswith("bad_op_or_slot") for d in dropped)


def test_hard_exclusion_runs_regardless_of_model_claims():
    ops, dropped = um.validate_ops([
        {"op": "ADD", "slot": "note", "value": "hearing on 3 Nov 2026",
         "source": "user_stated", "confidence": 1.0},
    ], [])
    assert ops == []
    assert dropped == ["hard_excluded:date:note"]


def test_inferred_cannot_touch_user_stated_including_add_as_update():
    rows = [_row(1, "office_address", "201 Lex Towers, Pune", "user_stated"),
            _row(2, "courts", "Bombay High Court", "user_stated")]
    ops, dropped = um.validate_ops([
        # ADD on a single slot that already holds a user_stated row = UPDATE
        {"op": "ADD", "slot": "office_address", "value": "Somewhere else",
         "source": "inferred", "confidence": 0.9},
        {"op": "UPDATE", "slot": "office_address", "value": "Somewhere else",
         "old_value": "201 Lex Towers, Pune", "source": "inferred", "confidence": 0.9},
        {"op": "DELETE", "slot": "courts", "value": "Bombay High Court",
         "source": "inferred", "confidence": 0.9},
    ], rows)
    assert ops == []
    assert dropped.count("protected_user_stated:office_address") == 2
    assert "protected_user_stated:courts" in dropped


def test_user_stated_may_modify_user_stated():
    rows = [_row(1, "office_address", "201 Lex Towers, Pune", "user_stated")]
    ops, _ = um.validate_ops([
        {"op": "ADD", "slot": "office_address", "value": "5th floor, Sadhu Vaswani Chowk, Pune",
         "source": "user_stated", "confidence": 1.0},
    ], rows)
    assert len(ops) == 1
    assert ops[0]["op"] == "UPDATE" and ops[0]["_target_id"] == 1


def test_inferred_may_update_inferred_and_delete_needs_a_match():
    rows = [_row(1, "courts", "District Court, Pune", "inferred", 0.8)]
    ops, dropped = um.validate_ops([
        {"op": "UPDATE", "slot": "courts", "value": "District and Sessions Court, Pune",
         "old_value": "District Court, Pune", "source": "inferred", "confidence": 0.85},
        {"op": "DELETE", "slot": "courts", "value": "Not stored", "source": "inferred",
         "confidence": 0.9},
    ], rows)
    assert len(ops) == 1 and ops[0]["op"] == "UPDATE" and ops[0]["_target_id"] == 1
    assert "delete_no_match:courts" in dropped


# --- rendering ------------------------------------------------------------

def test_render_block_format_and_identity_rule():
    rows = [
        _row(1, "advocate_name", "Adv. Priya S. Kulkarni"),
        _row(2, "designation", "Advocate"),
        _row(3, "enrolment_no", "MAH/1234/2015"),
        _row(4, "firm_name", "Kulkarni & Associates"),
        _row(5, "office_address", "201, Lex Towers, Fergusson College Rd, Pune 411004"),
        _row(6, "phone", "+91 98230 12345", "inferred", 0.9),   # inferred identity: NOT injected
        _row(7, "email", "priya@kulkarnilaw.in"),
        _row(8, "courts", "Bombay High Court", "inferred", 0.85),
        _row(9, "courts", "District Court, Pune"),
        _row(1, "preferred_language", "mr"),
        _row(2, "drafting_preference", "number prayer clauses (a), (b), (c)"),
        _row(3, "note", "teaches at ILS Law College"),           # never injected
    ]
    block = um.render_profile_block(rows)
    assert block.startswith("## ABOUT THE ADVOCATE USING THIS ASSISTANT (saved profile)")
    assert "Advocate: Adv. Priya S. Kulkarni, Advocate (Enrolment MAH/1234/2015)" in block
    assert "Firm: Kulkarni & Associates" in block
    assert "Office: 201, Lex Towers, Fergusson College Rd, Pune 411004" in block
    assert "Email: priya@kulkarnilaw.in" in block
    assert "98230" not in block
    assert "Regular courts: Bombay High Court; District Court, Pune" in block
    assert "Preferred draft language: Marathi" in block
    assert "- number prayer clauses (a), (b), (c)" in block
    assert "ILS Law College" not in block
    assert len(block) <= um.MAX_BLOCK_CHARS


def test_render_empty_when_only_inferred_identity_or_note():
    assert um.render_profile_block([]) == ""
    assert um.render_profile_block([_row(1, "advocate_name", "X", "inferred", 0.9)]) == ""
    assert um.render_profile_block([_row(1, "note", "whatever")]) == ""


def test_render_caps_size():
    rows = [_row(i, "drafting_preference", f"preference number {i} " + "x" * 280,
                 "user_stated") for i in range(1, 9)]
    rows.append(_row(99, "advocate_name", "Adv. A"))
    block = um.render_profile_block(rows)
    assert len(block) <= um.MAX_BLOCK_CHARS
    assert "Advocate: Adv. A" in block


# --- direct commands ------------------------------------------------------

@pytest.mark.parametrize("text,kind,rest", [
    ("Remember that I'm Adv. Priya Kulkarni", "remember", "I'm Adv. Priya Kulkarni"),
    ("please remember my office is in Pune", "remember", "my office is in Pune"),
    ("याद रखो कि मेरा नाम प्रिया है", "remember", "मेरा नाम प्रिया है"),
    ("लक्षात ठेव माझा पत्ता पुणे आहे", "remember", "माझा पत्ता पुणे आहे"),
    ("Forget my phone number", "forget", "my phone number"),
    ("forget that I practise in Delhi", "forget", "I practise in Delhi"),
    ("Forget everything", "forget_all", "everything"),
    ("delete all my details", "forget_all", "all my details"),
    ("सब भूल जाओ", None, None),   # verb-last word order is not matched; falls to pipeline
])
def test_parse_direct_command(text, kind, rest):
    got = um.parse_direct_command(text)
    if kind is None:
        assert got is None
    else:
        assert got == (kind, rest)


def test_direct_command_requires_whole_message():
    long = "Draft a bail application for X. Remember that I'm Adv. Y. " + "facts " * 200
    assert um.parse_direct_command(long) is None
    assert um.parse_direct_command("note") is None
    assert um.parse_direct_command("Remember the hearing is on 3 Nov") == ("remember", "the hearing is on 3 Nov")


def test_parse_ops_json_tolerant():
    assert um.parse_ops_json('```json\n{"ops": [{"op": "ADD"}]}\n```') == [{"op": "ADD"}]
    assert um.parse_ops_json('Sure! {"ops": []} done') == []
    assert um.parse_ops_json("not json") == []
    assert um.parse_ops_json('[{"op": "ADD"}]') == [{"op": "ADD"}]


# --- user id resolution ---------------------------------------------------

def _jwt(payload, secret, alg="HS256"):
    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    h = b64(json.dumps({"alg": alg, "typ": "JWT"}).encode())
    p = b64(json.dumps(payload).encode())
    sig = b64(hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


def test_resolve_user_id_flag_off(monkeypatch):
    monkeypatch.setenv("USER_MEMORY_ENABLED", "0")
    assert um.resolve_user_id({"x-user-id": "u1"}) == ""


def test_resolve_user_id_fails_closed_when_unconfigured(monkeypatch):
    """Flag on, no secret, no USER_MEMORY_ALLOW_UNSIGNED: nothing resolves."""
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.delenv("USER_MEMORY_ID_SECRET", raising=False)
    monkeypatch.delenv("USER_MEMORY_JWT_SECRET", raising=False)
    monkeypatch.delenv("USER_MEMORY_ALLOW_UNSIGNED", raising=False)
    assert um.auth_mode() == "unconfigured"
    assert um.configured() is False
    assert um.resolve_user_id({"x-user-id": "user_42"}) == ""
    good = _jwt({"userId": "user_42", "exp": time.time() + 60}, "whatever")
    assert um.resolve_user_id({"authorization": f"Bearer {good}", "x-user-id": "user_42"}) == ""


def test_resolve_user_id_bare_dev_mode_needs_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.delenv("USER_MEMORY_ID_SECRET", raising=False)
    monkeypatch.delenv("USER_MEMORY_JWT_SECRET", raising=False)
    monkeypatch.setenv("USER_MEMORY_ALLOW_UNSIGNED", "1")
    assert um.auth_mode() == "unsigned" and um.configured() is True
    assert um.resolve_user_id({"x-user-id": "user_42"}) == "user_42"
    assert um.resolve_user_id({"x-user-id": "bad id with spaces"}) == ""
    assert um.resolve_user_id({}) == ""
    # A secret always wins over the dev opt-in.
    monkeypatch.setenv("USER_MEMORY_JWT_SECRET", "jwt-secret")
    assert um.auth_mode() == "jwt"
    assert um.resolve_user_id({"x-user-id": "user_42"}) == ""


def test_resolve_user_id_hmac(monkeypatch):
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.setenv("USER_MEMORY_ID_SECRET", "s3cret")
    monkeypatch.delenv("USER_MEMORY_JWT_SECRET", raising=False)
    sig = um.hmac_sign_user_id("user_42", "s3cret")
    assert um.resolve_user_id({"x-user-id": "user_42", "x-user-sig": sig}) == "user_42"
    assert um.resolve_user_id({"x-user-id": "user_42", "x-user-sig": sig.upper()}) == "user_42"
    assert um.resolve_user_id({"x-user-id": "user_42"}) == ""
    assert um.resolve_user_id({"x-user-id": "user_43", "x-user-sig": sig}) == ""


def test_resolve_user_id_jwt(monkeypatch):
    """Node's access token: HS256, `userId` = numeric DB PK, exp, no sub/iss/aud."""
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.setenv("USER_MEMORY_JWT_SECRET", "jwt-secret")
    monkeypatch.delenv("USER_MEMORY_ID_SECRET", raising=False)
    monkeypatch.delenv("USER_MEMORY_JWT_CLAIM", raising=False)
    monkeypatch.delenv("USER_MEMORY_JWT_LEEWAY_S", raising=False)
    good = _jwt({"userId": 4821, "username": "priya@kulkarnilaw.in", "exp": time.time() + 900}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {good}"}) == "4821"   # cast to str
    # exp: 30 s leeway, then rejected.
    recent = _jwt({"userId": 4821, "exp": time.time() - 10}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {recent}"}) == "4821"
    expired = _jwt({"userId": 4821, "exp": time.time() - 60}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {expired}"}) == ""
    monkeypatch.setenv("USER_MEMORY_JWT_LEEWAY_S", "0")
    assert um.resolve_user_id({"authorization": f"Bearer {recent}"}) == ""
    monkeypatch.delenv("USER_MEMORY_JWT_LEEWAY_S")
    # Signature and alg.
    forged = _jwt({"userId": 4821}, "other-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {forged}"}) == ""
    none_alg = _jwt({"userId": 4821}, "jwt-secret", alg="none")
    assert um.resolve_user_id({"authorization": f"Bearer {none_alg}"}) == ""
    # Only userId is read: a token with sub but no userId yields nothing.
    sub_only = _jwt({"sub": "4821", "exp": time.time() + 60}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {sub_only}"}) == ""
    # With JWT configured, a bare X-User-Id is not accepted on its own ...
    assert um.resolve_user_id({"x-user-id": "4821"}) == ""
    # ... is ignored when it agrees with the claim, and rejected when it does not.
    assert um.resolve_user_id({"authorization": f"Bearer {good}", "x-user-id": "4821"}) == "4821"
    assert um.resolve_user_id({"authorization": f"Bearer {good}", "x-user-id": "4822"}) == ""
    # HMAC headers are irrelevant on the JWT path.
    monkeypatch.setenv("USER_MEMORY_ID_SECRET", "s3cret")
    sig = um.hmac_sign_user_id("4821", "s3cret")
    assert um.resolve_user_id({"x-user-id": "4821", "x-user-sig": sig}) == ""
    monkeypatch.delenv("USER_MEMORY_ID_SECRET")
    # nbf in the future (beyond leeway), missing Bearer prefix, garbage.
    nbf = _jwt({"userId": 4821, "nbf": time.time() + 120}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {nbf}"}) == ""
    assert um.resolve_user_id({"authorization": good}) == ""
    assert um.resolve_user_id({"authorization": "Bearer not.a.jwt"}) == ""


def test_resolve_user_id_jwt_claim_pinned_by_env(monkeypatch):
    """USER_MEMORY_JWT_CLAIM names the claim Node puts the user id in; the
    default fallback list is then not consulted."""
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.setenv("USER_MEMORY_JWT_SECRET", "jwt-secret")
    monkeypatch.delenv("USER_MEMORY_ID_SECRET", raising=False)
    monkeypatch.delenv("USER_MEMORY_JWT_CLAIM", raising=False)
    assert um.jwt_claims() == ("userId",)
    monkeypatch.setenv("USER_MEMORY_JWT_CLAIM", "sub")
    tok = _jwt({"sub": "u-777", "userId": 1, "exp": time.time() + 60}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {tok}"}) == "u-777"
    tok = _jwt({"userId": 1, "exp": time.time() + 60}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {tok}"}) == ""
    monkeypatch.setenv("USER_MEMORY_JWT_CLAIM", "user.id, userId")
    assert um.jwt_claims() == ("user.id", "userId")
    assert um.resolve_user_id({"authorization": f"Bearer {tok}"}) == "1"


# --- quoted material and received correspondence (live negative test b2) ---

QUOTED_NOTICE = ('Reply to this notice for me and deny liability for my client Anil Deshmukh: '
                 '"Under instructions from my client Suresh Patil, I, Adv. Rakesh Mehta of Mehta Law '
                 'Chambers, 12 Court Lane, Shivajinagar, Pune 411005, phone +91 98765 43210, email '
                 'rakesh@mehtalaw.in, call upon you to pay Rs 4,50,000 within 15 days." Keep it to one page.')


def test_received_correspondence_counts_as_pasted_document():
    assert um.looks_like_pasted_document(QUOTED_NOTICE)                      # "under instructions from"
    assert um.looks_like_pasted_document("LEGAL NOTICE\nTo: Mr X\nSir, pay up.")
    assert um.looks_like_pasted_document("From: Adv. R. Mehta\nTo: Anil\nPlease pay.")
    assert um.looks_like_pasted_document("Dear Sir, this is to inform you that the cheque bounced.")
    assert um.looks_like_pasted_document("... failing which. Yours faithfully, Adv. R. Mehta")
    # The lawyer's own words are not a document.
    assert not um.looks_like_pasted_document("I'm Adv. Priya Kulkarni, enrolment MAH/1234/2015.")
    assert not um.looks_like_pasted_document("Draft a notice to the tenant for arrears of rent.")
    assert not um.looks_like_pasted_document("Our office has moved to Camp, Pune.")


def test_strip_quoted_spans_removes_what_the_lawyer_is_quoting():
    s = um.strip_quoted_spans('Reply to this for me: "I am Adv. Rakesh Mehta of 12 Court Lane, Pune '
                              'and I demand Rs 4,50,000 from your client." Keep it short.')
    assert "Rakesh" not in s and "Court Lane" not in s
    assert s.startswith("Reply to this for me:") and s.endswith("Keep it short.")
    assert "[quoted material omitted]" in s
    # Curly quotes and guillemets.
    assert "Mehta" not in um.strip_quoted_spans("He wrote \u201cI, Adv. Mehta, call upon you\u201d today.")
    assert "Mehta" not in um.strip_quoted_spans("He wrote \u00abI, Adv. Mehta, call upon you\u00bb today.")
    # Single quotes only when clearly a quotation; apostrophes survive.
    assert "Mehta" not in um.strip_quoted_spans("He wrote: 'I, Adv. Mehta of Pune, call upon you to pay' yesterday.")
    assert um.strip_quoted_spans("I'm Adv. Kulkarni and it's my firm's office") == \
        "I'm Adv. Kulkarni and it's my firm's office"
    # Short quoted terms (a defined term, a phrase) are left alone.
    assert um.strip_quoted_spans('always use "Hon\'ble Court" in my drafts') == \
        'always use "Hon\'ble Court" in my drafts'


def test_extraction_text_never_carries_quoted_spans():
    out = um.extraction_text('Reply to this for me: "I am Adv. Rakesh Mehta of 12 Court Lane, Pune '
                             'and I demand payment from your client within 15 days." Keep it short.')
    assert "Rakesh" not in out and "quoted material omitted" in out


def test_implicit_turn_cannot_change_a_confirmed_name_or_enrolment():
    rows = [
        {"id": 1, "slot": "advocate_name", "value": "Adv. Priya Kulkarni", "source": "user_stated"},
        {"id": 2, "slot": "enrolment_no", "value": "MAH/1234/2015", "source": "user_stated"},
        {"id": 3, "slot": "firm_name", "value": "Kulkarni & Associates", "source": "user_stated"},
        {"id": 4, "slot": "office_address", "value": "201 Lex Towers, Pune", "source": "user_stated"},
    ]
    ops = [
        {"op": "ADD", "slot": "advocate_name", "value": "Adv. Rakesh Mehta", "source": "user_stated", "confidence": 1.0},
        {"op": "UPDATE", "slot": "enrolment_no", "value": "MAH/9/2009", "old_value": "MAH/1234/2015", "source": "user_stated", "confidence": 1.0},
        {"op": "DELETE", "slot": "firm_name", "value": "Kulkarni & Associates", "source": "user_stated", "confidence": 1.0},
        {"op": "UPDATE", "slot": "office_address", "value": "5th floor, Camp, Pune", "old_value": "201 Lex Towers, Pune", "source": "user_stated", "confidence": 1.0},
        {"op": "ADD", "slot": "phone", "value": "+91 98230 12345", "source": "user_stated", "confidence": 1.0},
    ]
    clean, dropped = um.validate_ops(ops, rows, implicit=True)
    assert sorted(d.split(":")[1] for d in dropped if d.startswith("implicit_identity_locked")) == \
        ["advocate_name", "enrolment_no", "firm_name"]
    assert [(o["op"], o["slot"]) for o in clean] == [("UPDATE", "office_address"), ("ADD", "phone")]
    # Re-stating the same name is not a change and is not dropped.
    same = [{"op": "ADD", "slot": "advocate_name", "value": "adv. priya kulkarni", "source": "user_stated", "confidence": 1.0}]
    assert um.validate_ops(same, rows, implicit=True)[1] == []
    # Explicit paths (remember / forget / review page) keep the approved behaviour.
    clean, dropped = um.validate_ops(ops, rows)
    assert dropped == [] and len(clean) == 5
    # A first ADD into an empty locked slot is still allowed implicitly (prompt example).
    clean, dropped = um.validate_ops(
        [{"op": "ADD", "slot": "advocate_name", "value": "Adv. Priya Kulkarni", "source": "user_stated", "confidence": 1.0}],
        [], implicit=True)
    assert dropped == [] and clean[0]["op"] == "ADD"
