"""Named-case lookups: no search-hit dumps, no grounding artifacts.

Lawyer report, prod 2026-09-22: "Provide a judgment of Vinay Kumar and other
versus Omprakash All LJ 1980". The case is not in the index. The High Court
agent answered from web search with Gemini's internal search objects pasted
in as citations, and the Supreme Court supporter appended 140 unrelated
judgments it had touched while searching two party names and the year 1980.
"""

import asyncio
import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

from core.grounding_artifacts import strip_grounding_artifacts
from core.state import AgentResult, SourceMetadata
from agents.orchestrator import (
    _SUPPORTING_CITATIONS_MAX,
    _discussed_sources,
    _mentions_case,
    _new_source_citations,
    _prefer_high_court_agent_for_hc_citation,
    _query_case_tokens,
    orchestrator_synthesize_node,
)

QUERY = "Provide a judgment of Vinay Kumar and other versus Omprakash All LJ 1980"

# Verbatim from the reported answer, including the object left unclosed.
REPORTED = (
    "The judgment in Vinai Kumar and others v. Om Prakash and another is reported at:\n\n"
    'Primary Citation: 1980 All LJ 524 [PerQueryResult(index="4.1.2", snippet="10 1980 ALL L.J. 524.")]\n'
    'Parallel Citations: 1980 ACC 24; 1980 ACrR 4 [PerQueryResult(index="4.1.3", '
    'snippet="Vinai Kumar v. Om Prakash 1980 A. C. C. 24", PerQueryResult(index="4.1.5", '
    'snippet="Vinai Kumar v. Om Prakash, 1980 ACrR 4")]\n'
    'Court: High Court of Judicature at Allahabad [PerQueryResult(index="4.1.1", '
    'snippet="Vinai Kumar v. Om Prakash(1)... Allahabad High Court", PerQueryResult(index="4.1.4", '
    'snippet="Vinai Kumar v. Om Prakash (1980 All LJ 524) | Allahabad High Court")]\n'
    "Subject Matter: Criminal Procedure Code, 1973 - Sections 145 & 146(1) "
    '[PerQueryResult(index="4.1.1", snippet="Proceedings under Section 145 Cr. P.C started..."), '
    'PerQueryResult(index="4.1.5", snippet="Reliance was placed upon the case of Vinai Kumar v. Om Prakash, 1980 ACrR 4 (a single Judge pronouncement)")].\n'
)


# --- grounding artifacts -------------------------------------------------------

def test_reported_answer_loses_every_perqueryresult_and_keeps_the_prose():
    cleaned, n = strip_grounding_artifacts(REPORTED)
    assert n == 7
    assert "PerQueryResult" not in cleaned
    assert "snippet=" not in cleaned
    assert "[" not in cleaned and "]" not in cleaned
    assert "Primary Citation: 1980 All LJ 524\n" in cleaned
    assert "Parallel Citations: 1980 ACC 24; 1980 ACrR 4\n" in cleaned
    assert "Court: High Court of Judicature at Allahabad\n" in cleaned
    assert "Sections 145 & 146(1).\n" in cleaned


def test_cite_tags_are_removed_too():
    cleaned, n = strip_grounding_artifacts("Bail is the rule [cite: 3, 4] and jail the exception[cite_start].")
    assert (cleaned, n) == ("Bail is the rule and jail the exception.", 2)


def test_clean_text_is_untouched():
    text = "Vijay Kumar Ghai v. State of WB, (2022) 7 SCC 124; [2022] 3 SCR 1. See Section 145 [old]."
    assert strip_grounding_artifacts(text) == (text, 0)
    assert strip_grounding_artifacts("") == ("", 0)


def test_stripping_is_idempotent():
    once, _ = strip_grounding_artifacts(REPORTED)
    assert strip_grounding_artifacts(once) == (once, 0)


def test_output_guardrail_strips_artifacts_from_the_final_answer():
    from agents.guardrail import guardrail_output_node
    out = asyncio.run(guardrail_output_node({"final_response": REPORTED, "task": "Judgment"}))
    assert "PerQueryResult" not in out["final_response"]
    assert "1980 All LJ 524" in out["final_response"]


# --- supporter citation lists --------------------------------------------------

def _sci_source(title, i):
    return SourceMetadata(source_type="sci_judgment", title=title, parties=title,
                          db_id=str(i), agent_name="SCI_Judgment",
                          doc_link=f"https://lawttorney.s3.ap-south-1.amazonaws.com/sc_pdfs/{i}.pdf")


# What the Supreme Court agent's tools returned for "Vinay Kumar", "Om Prakash"
# and the year 1980: sixty judgments, none of them the one asked for.
SCI_HITS = (
    ["VINAY KUMAR VS SAVITA", "HANUMANT DASS VS VINAY KUMAR & ORS.",
     "STATE OF U.P. VS VINAY KUMAR JAIN", "OM PRAKASH VS STATE OF U.P.",
     "SOM PRAKASH REKHI VS UNION OF INDIA & ANR.", "NORTHERN INDIA CATERERS (INDIA) LTD. VS LT. GOVERNOR OF DELHI"]
    + [f"PARTY {i} VS STATE OF HARYANA" for i in range(54)]
)
SCI_PROSE = (
    "The Supreme Court has considered the parties named in the query in unrelated matters.\n\n"
    "### Hanumant Dass v. Vinay Kumar & Ors.\n"
    "A matrimonial dispute with no bearing on Section 145 CrPC.\n\n"
    "### State of U.P. v. Vinay Kumar Jain\n"
    "Concerned service law.\n"
)


def _sci_supporter(prose=SCI_PROSE):
    return AgentResult(agent_name="SCI_Judgment", content=prose,
                       sources=[_sci_source(t, i) for i, t in enumerate(SCI_HITS)])


def test_only_cases_the_agent_discussed_are_listed():
    discussed = _discussed_sources(_sci_supporter())
    assert [s.title for s in discussed] == ["HANUMANT DASS VS VINAY KUMAR & ORS.",
                                            "STATE OF U.P. VS VINAY KUMAR JAIN"]
    rendered = _new_source_citations(_sci_supporter(), set(), set())
    assert rendered.count("\n") == 1 and "Hanumant" in rendered.title() and "Savita" not in rendered.title()
    assert "sc_pdfs/1.pdf" in rendered and "sc_pdfs/2.pdf" in rendered


def test_a_pdf_link_in_the_prose_counts_as_discussed():
    r = _sci_supporter(prose="See the order at https://lawttorney.s3.ap-south-1.amazonaws.com/sc_pdfs/3.pdf for context.")
    assert [s.title for s in _discussed_sources(r)] == ["OM PRAKASH VS STATE OF U.P."]


def test_citation_list_is_capped():
    prose = "\n".join(f"### Party {i} v. State of Haryana" for i in range(20))
    rendered = _new_source_citations(_sci_supporter(prose), set(), set())
    assert rendered.count("\n- ") + 1 == _SUPPORTING_CITATIONS_MAX


def test_indexed_agents_keep_their_retrieved_sources():
    # The High Court agent's sources are relevance-gated hits, not a search
    # history; an undiscussed one is still listed (as before), up to the cap.
    r = AgentResult(agent_name="Judgment", content="Analysis without naming the case.",
                    sources=[SourceMetadata(source_type="judgment", title="Sarabjit Kaur v. State of Punjab",
                                            court_name="Supreme Court of India", year=2023)])
    assert "Sarabjit Kaur" in _new_source_citations(r, set(), set())


def test_a_reactive_agent_with_no_prose_lists_nothing():
    assert _new_source_citations(_sci_supporter(prose=""), set(), set()) == ""


# --- the reported conversation, end to end through synthesis --------------------

WEB_PRIMARY = (
    "The decision in *Vinai Kumar and others v. Om Prakash and another*, reported at "
    "1980 All LJ 524, is an authority on the scope of a \"competent court\" under "
    "Section 146 of the Code of Criminal Procedure, 1973.\n\n"
    "### Key Legal Issues\n"
    "Whether a revenue court adjudicating mutation qualifies as a competent court.\n"
)


def _state(sci_supporter, primary=None):
    return {
        "original_query": QUERY, "query": QUERY,
        "task": "Judgment", "tasks_planned": ["Judgment", "SCI_Judgment"],
        "previous_artifact_content": "",
        "agent_results": {
            "Judgment": primary or AgentResult(agent_name="Judgment", content=WEB_PRIMARY,
                                               fallback_used=True),
            "SCI_Judgment": sci_supporter,
        },
    }


def test_reported_answer_ends_with_the_web_summary_and_nothing_else():
    answer = asyncio.run(orchestrator_synthesize_node(_state(_sci_supporter())))["final_response"]
    assert answer == WEB_PRIMARY.rstrip()
    assert "Related Supreme Court Authority" not in answer
    assert "Savita" not in answer and "sc_pdfs" not in answer


def test_supporter_that_actually_discusses_the_case_is_kept():
    prose = ("### Vinai Kumar v. Om Prakash, 1980 All LJ 524\n"
             "The Supreme Court later approved this reasoning in a Section 146 matter, "
             "holding that a revenue court deciding mutation is a competent court whose "
             "determination the Executive Magistrate must give effect to.\n")
    sci = AgentResult(agent_name="SCI_Judgment", content=prose,
                      sources=[_sci_source("VINAI KUMAR VS OM PRAKASH", 99)])
    answer = asyncio.run(orchestrator_synthesize_node(_state(sci)))["final_response"]
    assert "Related Supreme Court Authority" in answer
    assert "competent court whose" in answer


def test_indexed_primary_gets_a_discussed_only_citation_list_capped_at_five():
    indexed = AgentResult(agent_name="Judgment", content=(
        "### Ram Autar Singh v. State of U.P., 1985 All LJ 12\n"
        "Held that mutation entries bind the Magistrate.\n") + "More reasoning. " * 10)
    names = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf"]
    # Seven discussed cases, each with enough distinct prose to survive the
    # block filter; together far over the 2,500-char ceiling, so the
    # supporter is reduced to its citation list.
    prose = "".join(
        f"### {n} Traders v. State of Haryana\n"
        + " ".join(f"{n.lower()}{j}" for j in range(70)) + "\n\n"
        for n in names)
    sci = AgentResult(agent_name="SCI_Judgment", content=prose, sources=(
        [_sci_source(f"{n.upper()} TRADERS VS STATE OF HARYANA", 100 + i) for i, n in enumerate(names)]
        + [_sci_source(t, i) for i, t in enumerate(SCI_HITS)]))
    answer = asyncio.run(orchestrator_synthesize_node(_state(sci, primary=indexed)))["final_response"]
    assert "Related Supreme Court Authority" in answer
    assert answer.count("TRADERS VS STATE OF HARYANA") == _SUPPORTING_CITATIONS_MAX
    assert "ECHO TRADERS" in answer and "GOLF TRADERS" not in answer
    assert "SAVITA" not in answer and "PARTY 7" not in answer
    assert "alpha3" not in answer            # the prose itself is gone


# --- query parsing and the planner rule -------------------------------------------

def test_query_case_tokens_ignore_filler_and_citations():
    assert _query_case_tokens(QUERY) == ({"vinay", "kumar"}, {"omprakash"})
    assert _query_case_tokens("Summary of Dinesh Gupta v. State of UP, Criminal Appeal 214 of 2024") == \
        ({"dinesh", "gupta"}, {"state"})
    assert _query_case_tokens("cases on anticipatory bail") is None


def test_mentions_case_matches_spelling_variants_but_not_other_parties():
    case = _query_case_tokens(QUERY)
    assert _mentions_case("Reliance on Vinai Kumar v. Om Prakash, 1980 ACrR 4", case)
    assert _mentions_case("Om Prakash vs Vinay Kumar (appeal)", case)
    assert not _mentions_case("### Vinay Kumar v. Savita\nA matrimonial matter.", case)
    assert not _mentions_case("Om Prakash v. State of U.P.", case)


def test_high_court_reporter_citation_drops_the_supreme_court_agent():
    assert _prefer_high_court_agent_for_hc_citation(QUERY, "Judgment", ["Judgment", "SCI_Judgment"]) == \
        ("Judgment", ["Judgment"], True)
    # planner had them the other way round
    assert _prefer_high_court_agent_for_hc_citation(QUERY, "SCI_Judgment", ["SCI_Judgment", "Judgment", "Newacts"]) == \
        ("Judgment", ["Judgment", "Newacts"], True)


def test_planner_rule_leaves_other_plans_alone():
    keep = ["Judgment", "SCI_Judgment"]
    for q, task in [
        ("Vijay Kumar Ghai v. State of WB (2022) 7 SCC 124", "SCI_Judgment"),   # SC reporter
        ("Vinay Kumar vs Omprakash All LJ 1980 Supreme Court view", "Judgment"), # SC named
        ("Vinay Kumar vs Omprakash", "Judgment"),                                # no citation
        ("High Court cases on mutation as competent court 1980 All LJ", "Judgment"),  # no parties
    ]:
        assert _prefer_high_court_agent_for_hc_citation(q, task, list(keep)) == (task, keep, False)
    assert _prefer_high_court_agent_for_hc_citation(QUERY, "Judgment", ["Judgment"]) == ("Judgment", ["Judgment"], False)
    assert _prefer_high_court_agent_for_hc_citation(QUERY, "Drafting", list(keep)) == ("Drafting", keep, False)


def test_sci_prompt_forbids_year_browsing_for_a_named_case():
    from config.prompts import SCI_JUDGMENT_SYSTEM_PROMPT
    assert "Do NOT browse a whole year with search_by_date_range" in SCI_JUDGMENT_SYSTEM_PROMPT
