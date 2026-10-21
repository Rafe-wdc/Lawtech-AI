"""User forbids unsupplied law / names an unresolved-item marker (2026-10-21).

A construction-agreement request said "Do not introduce invented statutory
provisions, case law, rules, notifications, or legal requirements" and
"Flag any unresolved ... detail with 'TO BE CONFIRMED BEFORE EXECUTION'".
The draft still cited Punjab construction-workers Rules 17 / 203, BIS and
labour Acts and invented Rs. 1,000/day liquidated damages. The rule now
travels as typed intent: a directive only when the user said so, and the
overlay's anchors plus the retrieved legal context withheld. Ordinary drafts
see exactly the prompt they saw before.
"""
from __future__ import annotations

import inspect

from config.intent import UserIntent
from core.language import _format_intent_directives


def test_defaults_add_nothing_to_ordinary_drafts():
    intent = UserIntent()
    assert intent.no_unsupplied_law is False and intent.unresolved_marker == ""
    block = _format_intent_directives(intent)
    assert "LEGAL-MATERIAL" not in block and "USER MARKER" not in block


def test_legal_material_rule_is_emitted_only_when_the_user_forbade_it():
    block = _format_intent_directives(UserIntent(no_unsupplied_law=True))
    assert "USER LEGAL-MATERIAL RULE" in block
    assert "NICHE OVERLAY" in block and "liquidated damages" in block


def test_user_marker_replaces_bracket_placeholders():
    block = _format_intent_directives(
        UserIntent(unresolved_marker="TO BE CONFIRMED BEFORE EXECUTION"))
    assert '"TO BE CONFIRMED BEFORE EXECUTION"' in block
    assert "LEGAL-MATERIAL" not in block


def test_anchor_blocks_are_stripped_from_every_overlay():
    import re
    from agents.drafting import _strip_statutory_anchors
    from config.drafting_niches import NICHE_OVERLAYS
    for key, overlay in NICHE_OVERLAYS.items():
        stripped = _strip_statutory_anchors(overlay)
        assert not re.search(r"(?m)^\*\*[^\n]*anchors", stripped), key
        if "**Do not do:**" in overlay:
            assert "**Do not do:**" in stripped, key
    contract = next(o for o in NICHE_OVERLAYS.values() if "COMMERCIAL CONTRACT" in o)
    stripped = _strip_statutory_anchors(contract)
    assert "Indian Stamp Act, 1899 + State Stamp Acts" not in stripped
    assert "IN-WITNESS-WHEREOF" in stripped                 # skeleton survives


def test_generate_draft_withholds_anchors_and_context_on_the_flag():
    import agents.drafting as d
    src = inspect.getsource(d._generate_draft)
    gate = src.index('getattr(user_intent, "no_unsupplied_law", False)')
    assert src.index("niche_overlay = get_niche_overlay(niche_key)") < gate
    assert "niche_overlay = _strip_statutory_anchors(niche_overlay)" in src[gate:]
    assert "gathered_context = None" in src[gate:]
