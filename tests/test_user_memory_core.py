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


def test_resolve_user_id_bare_dev_mode(monkeypatch):
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.delenv("USER_MEMORY_ID_SECRET", raising=False)
    monkeypatch.delenv("USER_MEMORY_JWT_SECRET", raising=False)
    assert um.resolve_user_id({"x-user-id": "user_42"}) == "user_42"
    assert um.resolve_user_id({"x-user-id": "bad id with spaces"}) == ""
    assert um.resolve_user_id({}) == ""


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
    monkeypatch.setenv("USER_MEMORY_ENABLED", "1")
    monkeypatch.setenv("USER_MEMORY_JWT_SECRET", "jwt-secret")
    monkeypatch.delenv("USER_MEMORY_ID_SECRET", raising=False)
    good = _jwt({"sub": "abc123", "exp": time.time() + 60}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {good}"}) == "abc123"
    expired = _jwt({"sub": "abc123", "exp": time.time() - 60}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {expired}"}) == ""
    forged = _jwt({"sub": "abc123"}, "other-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {forged}"}) == ""
    none_alg = _jwt({"sub": "abc123"}, "jwt-secret", alg="none")
    assert um.resolve_user_id({"authorization": f"Bearer {none_alg}"}) == ""
    # With JWT configured, a bare X-User-Id is not accepted.
    assert um.resolve_user_id({"x-user-id": "abc123"}) == ""
    mongo = _jwt({"_id": "66f1a2b3c4d5e6f7a8b9c0d1"}, "jwt-secret")
    assert um.resolve_user_id({"authorization": f"Bearer {mongo}"}) == "66f1a2b3c4d5e6f7a8b9c0d1"
