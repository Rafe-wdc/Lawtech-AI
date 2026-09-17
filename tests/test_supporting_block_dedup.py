"""Case-lookup append path: only genuinely new supporting content is appended.

Measured on the dev box 2026-09-16: when the primary agent is a case lookup,
synthesis concatenated every supporting agent's whole output, so 53-68% of four
probe answers was duplicate material — a statute block repeating the provision
shown in the PREVIOUS turn, and a second judgment agent's full essay carrying
its own duplicate "Landmark Case Law" list.

These tests pin the deterministic filter: anchors already covered by the
primary (or the previous turn) are dropped, new ones survive whole, an
over-long supporter is reduced to its structured citations (links intact), and
the primary answer itself is never modified.
"""

from __future__ import annotations

from agents.orchestrator import (
    _BLOCK_MIN_UNIQUE_TOKENS,
    _SUPPORTING_APPEND_MAX_CHARS,
    _case_name_keys,
    _citation_keys,
    _content_token_set,
    _filter_supporting_blocks,
    _known_keys,
    _provision_keys,
)


# ---------------------------------------------------------------------------
# Key extraction
# ---------------------------------------------------------------------------

def test_citation_keys_normalise_across_formats():
    k = _citation_keys("Vijay Kumar Ghai v. State of WB, (2022) 7 SCC 124; 2022 INSC 326")
    assert any("scc" in x for x in k) and any("insc" in x for x in k)
    # same citation written differently collapses to one key
    assert _citation_keys("(2022) 7 SCC 124") == _citation_keys("2022  7  SCC 124")
    assert _citation_keys("Neutral Citation 2026:JHHC:16661")
    assert _citation_keys("no citations here at all") == set()


def test_provision_keys_ignore_the_act_name():
    a = _provision_keys("Section 420 of the Indian Penal Code, 1860")
    b = _provision_keys("Section 420 IPC")
    c = _provision_keys("s. 420 of the Penal Code")
    assert a == b == c == {"section420"}
    assert _provision_keys("Section 318(4) BNS") == {"section318(4)"}
    assert _provision_keys("Article 226 of the Constitution") == {"article226"}


def test_case_name_keys_match_v_and_versus():
    assert _case_name_keys("Hridaya Ranjan Prasad Verma v. State of Bihar") == \
           _case_name_keys("Hridaya Ranjan Prasad Verma versus State of Bihar")
    assert _case_name_keys("nothing here") == set()


def test_known_keys_merges_primary_and_previous_turn():
    cites, provs, names = _known_keys(
        "Primary cites (2022) 7 SCC 124.",
        "Previous turn discussed Section 420 IPC and Sushil Sethi v. State of Arunachal Pradesh.",
    )
    assert cites and "section420" in provs and names


# ---------------------------------------------------------------------------
# Block filtering
# ---------------------------------------------------------------------------

PRIMARY = (
    "The Supreme Court has held that a mere breach of contract is not cheating.\n\n"
    "### Vijay Kumar Ghai v. State of West Bengal, (2022) 7 SCC 124\n"
    "Fraudulent intention must exist at the inception of the transaction.\n\n"
    "## PDF Links\n- [Vijay Kumar Ghai](https://api.sci.gov.in/vijay.pdf)\n"
)
PREV_TURN = (
    "Section 420 of the Indian Penal Code, 1860 corresponds to Section 318(4) "
    "of the Bharatiya Nyaya Sanhita, 2023. Whoever cheats and thereby "
    "dishonestly induces the person deceived to deliver any property..."
)


def _filter(content, primary=PRIMARY, prev=PREV_TURN):
    c, p, n = _known_keys(primary, prev)
    return _filter_supporting_blocks(content, c, p, n, _content_token_set(primary))


def test_statute_block_already_in_the_previous_turn_is_dropped():
    """The tester's exact case: Newacts re-emits the provision pair shown in
    turn 1 under '## Statutory Provisions Referenced'."""
    supporting = (
        "## Statutory Provisions Referenced\n\n"
        "### Old Provision: Section 420 of the Indian Penal Code, 1860\n"
        "Whoever cheats and thereby dishonestly induces the person deceived to "
        "deliver any property shall be punished.\n\n"
        "### New Provision: Section 318(4) of the Bharatiya Nyaya Sanhita, 2023\n"
        "Whoever cheats and thereby dishonestly induces the person deceived to "
        "deliver any property shall be punished.\n"
    )
    body, dropped = _filter(supporting)
    assert body == "", f"expected the whole block to be dropped, kept: {body[:120]!r}"
    assert len(dropped) >= 2


def test_a_provision_the_primary_lacks_survives_whole():
    supporting = (
        "## Statutory Provisions Referenced\n\n"
        "### Section 406 of the Indian Penal Code, 1860\n"
        "Criminal breach of trust is a distinct offence with different "
        "ingredients from cheating and must be pleaded separately.\n"
    )
    body, dropped = _filter(supporting)
    assert "Section 406" in body
    assert "Criminal breach of trust" in body
    # the agent's own label heading is not repeated — the append branch
    # prepends "## Statutory Provisions Referenced" itself
    assert not body.lstrip().startswith("## Statutory Provisions Referenced")
    assert dropped == []


def test_label_only_heading_is_dropped_silently():
    body, dropped = _filter("## Supporting Case Authority\n\n"
                            "### Sarabjit Kaur v. State of Punjab, (2023) 5 SCC 360\n"
                            "A promise broken later is not cheating at inception.\n")
    assert "Sarabjit Kaur" in body
    assert "## Supporting Case Authority" not in body
    assert dropped == [], "a bare label heading must not be logged as dropped content"


def test_duplicate_case_is_dropped_but_a_new_one_is_kept():
    supporting = (
        "## Supporting Case Authority\n\n"
        "### Vijay Kumar Ghai v. State of West Bengal, (2022) 7 SCC 124\n"
        "Fraudulent intention must exist at the inception of the transaction.\n\n"
        "### Sarabjit Kaur v. State of Punjab, (2023) 5 SCC 360\n"
        "A breach of a promise to sell does not by itself make out cheating.\n"
    )
    body, dropped = _filter(supporting)
    assert "Sarabjit Kaur" in body
    assert "Vijay Kumar Ghai" not in body
    assert any("Vijay" in d for d in dropped)


def test_self_dedup_within_one_supporter():
    """The same case twice in one supporting answer is appended once."""
    supporting = (
        "## Landmark Case Law\n\n"
        "### Sarabjit Kaur v. State of Punjab, (2023) 5 SCC 360\n"
        "A breach of a promise to sell does not by itself make out cheating "
        "unless dishonest intention at inception is shown on the record.\n\n"
        "## Landmark Case Law (restated)\n\n"
        "### Sarabjit Kaur v. State of Punjab, (2023) 5 SCC 360\n"
        "A breach of a promise to sell does not by itself make out cheating.\n"
    )
    body, dropped = _filter(supporting)
    assert body.count("Sarabjit Kaur") == 1
    assert dropped


def test_transitional_prose_with_no_anchor_is_dropped():
    body, dropped = _filter(
        "## Supporting Case Authority\n\nThe position is well settled and the "
        "courts have consistently taken this view.\n")
    assert body == "" and dropped


def test_substantive_analysis_without_a_citation_is_kept():
    long_new = ("## Additional Analysis\n\n" + " ".join(
        f"distinctword{i}" for i in range(_BLOCK_MIN_UNIQUE_TOKENS + 15)))
    body, dropped = _filter(long_new)
    assert body.startswith("## Additional Analysis") and dropped == []


def test_empty_content_is_safe():
    assert _filter("") == ("", [])


# ---------------------------------------------------------------------------
# The append branch's ceiling
# ---------------------------------------------------------------------------

def test_ceiling_is_smaller_than_the_essays_it_replaces():
    # The four probe cases appended 8.1k-12.0k chars per supporter.
    assert _SUPPORTING_APPEND_MAX_CHARS < 8_000
    assert _SUPPORTING_APPEND_MAX_CHARS >= 1_000  # still room for real gap-filling


# ---------------------------------------------------------------------------
# End-to-end through the real synthesis node.
#
# The case-lookup append branch makes no LLM call, so the whole path can be
# driven deterministically. This reproduces the reported conversation: turn 1
# explained Section 420 IPC / Section 318(4) BNS, turn 2 asked only for case
# law, and the answer came back with the statute pair repeated verbatim plus a
# second judgment agent's whole essay.
# ---------------------------------------------------------------------------

def test_reported_conversation_end_to_end():
    import asyncio
    from core.state import AgentResult, SourceMetadata
    from agents.orchestrator import orchestrator_synthesize_node

    turn1 = (
        "## Statutory Framework\n"
        "Section 420 of the Indian Penal Code, 1860 corresponds to Section 318(4) "
        "of the Bharatiya Nyaya Sanhita, 2023. Whoever cheats and thereby "
        "dishonestly induces the person deceived to deliver any property shall be "
        "punished with imprisonment which may extend to seven years."
    )
    sci_primary = (
        "The Supreme Court has consistently held that a mere breach of contract is "
        "not cheating unless dishonest intention existed at the inception.\n\n"
        "### Vijay Kumar Ghai v. State of West Bengal, (2022) 7 SCC 124\n"
        "Fraudulent intention must exist at the very inception of the transaction.\n\n"
        "## PDF Links\n"
        "- [Vijay Kumar Ghai](https://api.sci.gov.in/supremecourt/vijay.pdf)\n"
    )
    newacts_supporting = (
        "## Statutory Provisions Referenced\n\n"
        "### Old Provision: Section 420 of the Indian Penal Code, 1860\n"
        "Whoever cheats and thereby dishonestly induces the person deceived to "
        "deliver any property shall be punished with imprisonment which may extend "
        "to seven years, and shall also be liable to fine.\n\n"
        "### New Provision: Section 318(4) of the Bharatiya Nyaya Sanhita, 2023\n"
        "Whoever cheats and thereby dishonestly induces the person deceived to "
        "deliver any property shall be punished with imprisonment which may extend "
        "to seven years, and shall also be liable to fine.\n"
    )
    judgment_supporting = (
        "## 1. Statutory Framework: Dual-Law Pairing\n"
        "Section 420 IPC maps to Section 318(4) BNS.\n\n"
        "## 2. Doctrinal Distinction\n"
        "Cheating requires deception at inception.\n\n"
        "## 3. Landmark Case Law\n"
        "### Vijay Kumar Ghai v. State of West Bengal, (2022) 7 SCC 124\n"
        "Fraudulent intention must exist at inception.\n"
    ) + ("Further elaboration of the same settled position. " * 200)

    state = {
        "original_query": "Please provide case laws also",
        "query": "Please provide case laws also",
        "task": "SCI_Judgment",
        "tasks_planned": ["SCI_Judgment", "Newacts", "Judgment"],
        "previous_artifact_content": turn1,
        "agent_results": {
            "SCI_Judgment": AgentResult(agent_name="SCI_Judgment", content=sci_primary),
            "Newacts": AgentResult(agent_name="Newacts", content=newacts_supporting),
            "Judgment": AgentResult(
                agent_name="Judgment", content=judgment_supporting,
                sources=[SourceMetadata(
                    source_type="judgment", title="Sarabjit Kaur v. State of Punjab",
                    court_name="Supreme Court of India", year=2023,
                    doc_link="https://api.sci.gov.in/supremecourt/sarabjit.pdf",
                    agent_name="Judgment")],
            ),
        },
    }
    out = asyncio.run(orchestrator_synthesize_node(state))
    answer = out["final_response"]

    # The statute pair the user was already shown in turn 1 is gone.
    assert "Old Provision:" not in answer
    assert "New Provision:" not in answer
    # The second judgment agent's essay is not pasted in whole.
    assert "Further elaboration of the same settled position." not in answer
    assert len(answer) < len(sci_primary) + 3_000
    # The primary answer survives byte-for-byte, PDF links included.
    assert sci_primary.rstrip() in answer
    assert "https://api.sci.gov.in/supremecourt/vijay.pdf" in answer
    # A genuinely new case still reaches the user, with its link.
    assert "Sarabjit Kaur" in answer
    assert "sarabjit.pdf" in answer


def test_supporter_with_new_material_is_still_appended():
    """The filter must not silence a supporter that adds something."""
    import asyncio
    from core.state import AgentResult
    from agents.orchestrator import orchestrator_synthesize_node

    state = {
        "original_query": "cases on cheating",
        "query": "cases on cheating",
        "task": "Judgment",
        "tasks_planned": ["Judgment", "Legislation"],
        "previous_artifact_content": "",
        "agent_results": {
            "Judgment": AgentResult(
                agent_name="Judgment",
                content="### Vijay Kumar Ghai v. State of West Bengal, (2022) 7 SCC 124\n"
                        "Intention at inception governs.\n"),
            "Legislation": AgentResult(
                agent_name="Legislation",
                content="## Limitation\n### Article 113 of the Limitation Act, 1963\n"
                        "A suit for which no period is provided elsewhere must be filed "
                        "within three years of when the right to sue accrues.\n"),
        },
    }
    answer = asyncio.run(orchestrator_synthesize_node(state))["final_response"]
    assert "Article 113" in answer
    assert "Statutory Provisions Referenced" in answer
