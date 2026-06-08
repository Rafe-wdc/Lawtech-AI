"""Agent #1 — Orchestrator Agent

The brain of the system. Two phases:
1. PLAN: Analyze query → classify task → decide which agents to invoke
2. SYNTHESIZE: Merge results from domain agents into final response

Uses: Gemini 2.5 Flash Lite for task classification, planning, and synthesis.
"""

from __future__ import annotations

import asyncio
from pydantic import BaseModel, Field
from typing import Literal
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from core.state import LegalAgentState, AgentResult, FileContextData
from core.clients import get_gemini_flash, get_gemini_flash_full, get_gemini_pro, get_drafting_llm
from core.language import localize_prompt
from core.logger import get_logger, log_time
from core.progress import progress
from config.prompts import (
    TASK_CLASSIFICATION_PROMPT, SYNTHESIS_PROMPT, SYNTHESIS_TABLE_PROMPT,
    DRAFT_SYNTHESIS_PROMPT, DRAFT_CITATION_PROMPT,
)

log = get_logger("Orchestrator")

import re

# --- Response Instructions Sanitization ---

_INJECTION_PATTERNS = re.compile(
    r"(?i)(ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|rules?|prompts?)"
    r"|disregard\s+(everything|all|the)\b"
    r"|you\s+are\s+now\b"
    r"|system\s*:\s*"
    r"|new\s+instructions?\s*:"
    r"|override\s+(all|previous|the)\b"
    r"|do\s+not\s+follow\b"
    r"|forget\s+(all|your|previous)\b)",
)

_MAX_INSTRUCTIONS_LEN = 500


def _sanitize_response_instructions(instructions: str) -> str:
    """Strip prompt-injection patterns and cap length of LLM-extracted instructions."""
    if not instructions:
        return ""
    cleaned = _INJECTION_PATTERNS.sub("", instructions).strip()
    if len(cleaned) > _MAX_INSTRUCTIONS_LEN:
        cleaned = cleaned[:_MAX_INSTRUCTIONS_LEN]
    return cleaned


# --- Internal cite-marker stripper (BUG-09) ---
# The drafting agent emits [CITE: brief description] markers as placeholders
# for case law that the citation-injection step is supposed to fill in. After
# we switched to append-only synthesis (BUG-03), unfilled markers leaked to
# the user. This stripper removes any unfilled marker before the draft is
# returned. Keep markers that look like real legal citations alone.
_INTERNAL_CITE_MARKER_RE = re.compile(
    # [CITE: anything that's not a closing bracket]
    r"\s*\[CITE:[^\]\n]*\]\s*",
    re.IGNORECASE,
)


# --- Table-format intent detection ---
# When the user's response_instructions or query indicate they want a comparison
# table (not a prose explanation), the synth node switches to SYNTHESIS_TABLE_PROMPT
# which constrains the LLM to produce ONLY a table — no preamble, no postamble.
_TABLE_INTENT_RE = re.compile(
    r"\b("
    r"table\s+format|format\s+of\s+(a\s+)?table|"
    r"in\s+a\s+table|as\s+a\s+table|"
    r"comparison\s+table|comparative\s+table|"
    r"tabular(?:ly|\s+(?:form|format|comparison))?|"
    r"side[- ]by[- ]side|"
    r"compare(?:\s+and\s+contrast)?(?:\s+the\s+\w+){0,3}\s+in\s+(?:a\s+)?(?:table|tabular|tabulated)|"
    r"differences?\s+between\s+.*\bin\s+(?:a\s+)?table"
    r")\b",
    re.IGNORECASE,
)


def _wants_table_format(response_instructions: str, query: str = "") -> bool:
    """Return True iff the user has explicitly asked for tabular output.

    Checks response_instructions first (extracted by the query analyzer) and
    falls back to the raw query. Be conservative — the synth_table prompt is
    strict (table only), so a false positive produces a worse result than a
    false negative.
    """
    if response_instructions and _TABLE_INTENT_RE.search(response_instructions):
        return True
    if query and _TABLE_INTENT_RE.search(query):
        return True
    return False


def _strip_internal_cite_markers(text: str) -> str:
    """Remove unfilled `[CITE: ...]` placeholder markers from the final draft.

    These are internal scaffolding emitted by the section-generation prompt
    rule #9. After synthesis, any marker that survived was never matched to
    a real citation — surfacing it to users is noise.
    """
    if not text:
        return text
    cleaned = _INTERNAL_CITE_MARKER_RE.sub(" ", text)
    # Tidy stray whitespace/punctuation introduced by the substitution
    cleaned = re.sub(r" +([.,;:!?])", r"\1", cleaned)  # " ." -> "."
    cleaned = re.sub(r"  +", " ", cleaned)              # double spaces
    cleaned = re.sub(r"\n +", "\n", cleaned)            # leading line spaces
    return cleaned.strip()


# --- Drafting Intent Detection ---
# Used to decide whether to add the Drafting agent to the plan when a file is
# attached. Matches only when the user *explicitly* asks for a document to be
# produced — never on bare document-noun mentions like "what's in this plaint?"
# or substring collisions like "plaint" inside "plaintiff".

# Document-type nouns that name a kind of legal document.
_DOC_NOUN_PATTERN = (
    r"plaint|petition|affidavit|notice|legal\s+notice|agreement|contract|"
    r"m\.?o\.?u\.?|deed|bail\s+application|written\s+statement|application|"
    r"complaint|response|reply|rejoinder|caveat|counter|writ|appeal|memo|"
    r"will|power\s+of\s+attorney|partnership\s+deed|sale\s+deed|gift\s+deed|"
    r"lease\s+deed|rental\s+agreement|nda|non[\s-]disclosure|"
    r"settlement\s+deed|divorce\s+petition|legal\s+document|"
    r"plea|pleading|brief|summons|subpoena|injunction\s+application"
)

# (1) Strong drafting verbs in any form — almost always indicate drafting intent
# in a legal-AI chat context (e.g. "Draft a plaint", "Drafting an affidavit").
_DRAFTING_VERB_RE = re.compile(
    r"\b(draft|drafts|drafted|drafting|redraft|redrafts|redrafted|redrafting)\b",
    re.IGNORECASE,
)

# (2) Verb + (article + optional adjective) + document-noun = drafting intent
# e.g. "write a plaint", "prepare a bail application", "compose an MOU",
# "give me a written statement", "I need a notice for property dispute",
# "give me a sample plaint" (article + adjective + doc-noun).
_DRAFTING_INTENT_RE = re.compile(
    r"\b(?:write|prepare|create|generate|compose|draw\s+up|"
    r"give\s+me|i\s+(?:need|want|require)|need|want|provide|build|produce|"
    r"craft|formulate|put\s+together|help\s+me\s+(?:write|prepare|create|make)|"
    r"could\s+you\s+(?:write|prepare|draft|make|create))\s+"
    r"(?:a|an|the|me\s+a|me\s+an|us\s+a|us\s+an|one|some|"
    r"(?:a|an|the)\s+\w+(?:\s+\w+)?)"
    r"\s+"  # whitespace between article (group) and document noun
    r"(?:" + _DOC_NOUN_PATTERN + r")\b",
    re.IGNORECASE,
)

# (3) Format / sample / template requests
# e.g. "format of bail application", "sample affidavit", "template for plaint".
_DRAFTING_FORMAT_RE = re.compile(
    r"\b(?:format|sample|specimen|template|model|standard\s+form|proforma)"
    r"(?:\s+(?:of|for|to|to\s+make))?\s+"
    r"(?:a|an|the)?\s*"
    r"(?:" + _DOC_NOUN_PATTERN + r")\b",
    re.IGNORECASE,
)

# (4) "Drafting/preparation of X"
_DRAFTING_OF_RE = re.compile(
    r"\b(?:drafting|preparation|preparing)\s+(?:of\s+)?(?:a|an|the)?\s*"
    r"(?:" + _DOC_NOUN_PATTERN + r")\b",
    re.IGNORECASE,
)

# (5) Negative signals — Q&A intent that should NEVER trigger drafting even
# when paired with document nouns. Used to short-circuit before regex checks.
_QA_INTENT_RE = re.compile(
    r"\b(?:summari[sz]e|summary\s+of|explain|describe|what\s+is|what\s+are|"
    r"what\s+does|what's|whats|who\s+is|who\s+are|when\s+(?:is|was|did)|"
    r"why\s+(?:is|did|does)|how\s+(?:is|does|did|much|many)|where\s+(?:is|was)|"
    r"which|tell\s+me\s+about|analy[sz]e|review|critique|extract|"
    r"list\s+(?:the|all|out)|find|identify|cite|enumerate|"
    r"is\s+the\s+(?:suit|case|claim|petition|plaint)|"
    r"are\s+the|does\s+the|did\s+the|can\s+(?:i|you|the))\b",
    re.IGNORECASE,
)


def _wants_drafting(query: str) -> bool:
    """Return True iff the query *explicitly* asks for a legal document to be drafted.

    This must NEVER trigger on:
      - Bare document-noun mentions ("what's in this plaint?")
      - Substring collisions ("plaintiff", "complaint", "explaining")
      - Q&A about an existing document ("who wrote the petition?")
      - Reference to a draft ("review my draft")  — handled by Q&A short-circuit

    Triggers when the query contains:
      (1) An explicit drafting verb: "draft", "drafting", "redraft" (any tense)
      (2) Verb + document-noun pattern: "write a plaint", "prepare an affidavit"
      (3) Format / sample / template request: "format of bail application"
      (4) "Drafting of X" / "preparation of X"
    """
    q = (query or "").strip()
    if not q:
        return False

    has_explicit_draft_verb = bool(_DRAFTING_VERB_RE.search(q))
    has_format_request = (
        bool(_DRAFTING_FORMAT_RE.search(q))
        or bool(_DRAFTING_OF_RE.search(q))
    )
    has_qa_framing = bool(_QA_INTENT_RE.search(q))

    # Format/template/sample requests are inherently drafting requests, even
    # when phrased as a question (e.g. "What is the format of a bail
    # application?", "Show me a sample plaint"). Override Q&A short-circuit.
    if has_format_request:
        return True

    # Explicit drafting verb wins over everything else.
    # ("Draft this", "Please draft a notice", "Drafting needed".)
    if has_explicit_draft_verb:
        return True

    # Pure Q&A framing without any drafting signal — never trigger drafting.
    # Catches "who is the plaintiff?", "summarize the plaint", "what's in
    # this petition?" etc. The document-noun substring is incidental.
    if has_qa_framing:
        return False

    # Verb + article + document-noun = drafting intent.
    # ("write a plaint", "prepare a bail application", "compose an MOU".)
    if _DRAFTING_INTENT_RE.search(q):
        return True

    return False


# --- Long Query Extraction ---
# When users paste 20-30K chars (e.g. a contract + question), we separate
# the concise question from the pasted context. Classification/routing uses
# only the question; the generating agent gets the full text as user_context.

_LONG_QUERY_THRESHOLD = 5000  # chars — below this, treat as normal query

_QUERY_EXTRACT_PROMPT = """You are a legal AI assistant. The user has sent a very long message that likely contains a pasted document (contract, notice, agreement, judgment) along with their actual question.

Your task: Extract the user's actual QUESTION or INSTRUCTION from the text. The question is usually at the beginning or end of the message.

If the entire text IS the document with no explicit question, infer the most likely intent (e.g., "Review this document and identify key legal issues").

User message (first 3000 chars):
{text_start}

---
User message (last 2000 chars):
{text_end}

Return JSON:
{{"question": "<the user's actual question/instruction in 1-3 sentences>", "document_type": "<contract|notice|agreement|judgment|legislation|petition|affidavit|other>"}}"""


class QueryExtraction(BaseModel):
    question: str = Field(..., description="The user's actual question or instruction")
    document_type: str = Field("other", description="Type of document pasted")


def _extract_question_from_long_query(query: str) -> tuple[str, str]:
    """Extract concise question from a long query containing pasted content.

    Returns (concise_question, document_type).
    Falls back to first 500 chars if extraction fails.
    """
    try:
        with log_time(log, "Long query extraction"):
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                QueryExtraction, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_template(_QUERY_EXTRACT_PROMPT)
            chain = prompt | llm
            raw_and_parsed = chain.invoke({
                "text_start": query[:3000],
                "text_end": query[-2000:],
            })
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "extract_long_query", raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]

        log.info("Question extracted from long query",
                 question_len=len(result.question),
                 doc_type=result.document_type,
                 original_len=len(query))
        return result.question, result.document_type

    except Exception as e:
        log.warning("Long query extraction failed, using truncated query",
                    error=str(e))
        return query[:500], "other"


# --- Task Classification (migrated from v1 task_identifer.py) ---

class IdentifyTaskSchema(BaseModel):
    task: Literal[
        "Drafting", "Judgment", "Legislation", "Constitution",
        "Scenario", "Maxim", "Newacts", "Legal_Concepts",
        "SCI_Judgment", "GST_Judgment", "Document",
        "Non_legal", "Other",
    ] = Field(..., description="The primary legal task type")


class ClassifyAndPlan(BaseModel):
    """Merged classification + planning result — single LLM call."""
    task: Literal[
        "Drafting", "Judgment", "Legislation", "Constitution",
        "Scenario", "Maxim", "Newacts", "Legal_Concepts",
        "SCI_Judgment", "GST_Judgment", "Document",
        "Non_legal", "Other",
    ] = Field(..., description="The primary legal task type")
    agents: list[str] = Field(
        ...,
        description="List of agent names to invoke (1-3): Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment, GST_Judgment, Document",
    )
    reasoning: str = Field(..., description="Brief reasoning for task type and agent selection")


def _classify_task_regex_fallback(query: str) -> str:
    """Keyword-based task classification fallback when LLM is unavailable."""
    q = query.lower().strip()
    # Greeting / non-legal short-circuit (check before any legal keywords)
    _greeting_tokens = {"hello", "hi", "hey", "hii", "helo", "hola", "namaste", "namaskar",
                        "good morning", "good afternoon", "good evening", "good night",
                        "how are you", "how r u", "what's up", "whats up", "sup",
                        "who are you", "what are you", "what can you do"}
    if q in _greeting_tokens or any(q.startswith(g) for g in _greeting_tokens):
        return "Non_legal"
    # GST AAAR short-circuit (must run BEFORE generic act/section keywords so
    # queries like "GST advance ruling on Section 17(5)" don't fall into Legislation)
    if any(k in q for k in (
        "advance ruling", "aaar", " aar ", "appellate authority for advance ruling",
        "gst appeal", "gst appellate", "gst ruling", "gst classification",
    )) or (("gst" in q or "cgst" in q or "sgst" in q or "igst" in q) and any(k in q for k in (
        "appeal", "ruling", "classification", "itc", "input tax credit", "valuation",
        "exemption", "hsn", "notification",
    ))):
        return "GST_Judgment"
    # Drafting detection: defer to the shared `_wants_drafting` helper which
    # already has explicit-verb + verb-noun + format-noun patterns with a Q&A
    # negative regex. The previous naive substring check ("notice", "plaint",
    # "petition", "format", "agreement") false-positively routed pure-Q&A
    # queries like "What is the format of a written statement?",
    # "Section 80 CPC notice requirements", or "Discuss the petition under
    # Article 32" to Drafting whenever the LLM classifier had to fall back
    # (timeout / rate-limit). _wants_drafting requires intent, not just a
    # document-noun substring.
    if _wants_drafting(query):
        return "Drafting"
    if any(k in q for k in ("bns", "bnss", "bsa", "ipc", "crpc", "iea",
                              "bharatiya nyaya", "bharatiya nagarik", "bharatiya sakshya",
                              "penal code", "criminal procedure", "evidence act")):
        return "Newacts"
    if any(k in q for k in ("judgment", "judgement", "case law", "citation", "held that",
                              "vs.", " v. ", "high court", "hc", "bench")):
        return "Judgment"
    if any(k in q for k in ("supreme court", "sc judgment", "sc case", "sc ruling",
                              "hon'ble sc", "apex court", "article 136",
                              "puttaswamy", "maneka gandhi", "kesavananda",
                              "navtej", "vishaka", "indra sawhney")):
        return "SCI_Judgment"
    if any(k in q for k in ("article ", "fundamental right", "directive principle",
                              "constitution", "constitutional")):
        return "Constitution"
    if any(k in q for k in ("maxim", "audi alteram", "res judicata", "estoppel",
                              "nemo judex", "caveat emptor", "actus reus", "mens rea")):
        return "Maxim"
    if any(k in q for k in ("scenario", "situation", "what happens if", "can i",
                              "what should", "legal opinion", "advise", "rights")):
        return "Scenario"
    if any(k in q for k in ("section ", "act ", "rule ", "regulation", "provision",
                              "statute", "law ", " act,", " act.")):
        # Avoid misclassifying constitutional queries that mention "section"
        if not any(k in q for k in ("constitution", "constitutional", "article ",
                                     "fundamental right", "directive principle")):
            return "Legislation"
    return "Legal_Concepts"


def _classify_task(query: str, chat_summary: str | None = None) -> str:
    """Classify query into a task type using GPT-4o structured output.

    Falls back to keyword-based classification if the LLM call fails
    (rate-limit, timeout, structured-output parse error, etc.).
    """
    try:
        with log_time(log, "Task classification"):
            prompt = PromptTemplate.from_template(TASK_CLASSIFICATION_PROMPT)
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                IdentifyTaskSchema, include_raw=True,
            )
            formatted = prompt.format(query=query, chat_summary=chat_summary or "")
            raw_and_parsed = llm.invoke(formatted)
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "classify_task", raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]
        log.info("Task classified", task=result.task, query=query[:80])
        return result.task
    except Exception as e:
        fallback = _classify_task_regex_fallback(query)
        log.warning("Task classification LLM failed, using regex fallback",
                    error=str(e), fallback=fallback, query=query[:80])
        return fallback


# --- Multi-Agent Planning ---

class AgentPlan(BaseModel):
    agents: list[str] = Field(
        ...,
        description="List of agent names to invoke: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment, GST_Judgment, Document",
    )
    reasoning: str = Field(..., description="Brief reasoning for agent selection")


PLAN_PROMPT = """You are a legal query planner. Given a query and its primary task type, determine which agents should handle it.

Available agents:
- Legislation: Central/state law sections and provisions (all acts EXCEPT the 6 below)
- Judgment: Court case laws, citations, precedents (general / High Court / unspecified courts)
- Newacts: ONLY these 6 acts: BNS/IPC, BNSS/CrPC, BSA/IEA
- Drafting: ONLY when the user explicitly asks the AI to CREATE / WRITE / PREPARE / DRAFT a legal document (verbs: draft, write, prepare, create, generate, compose, draw up, "give me a [doc]", "I need a [doc]"). NEVER for questions ABOUT documents (format, structure, essential elements, requirements, how to file, when to use, difference between X and Y) — those go to Legal_Concepts / Legislation / Scenario.
- Scenario: Situational analysis, legal advice, remedies, web search
- Constitution: Constitutional provisions, fundamental rights, Articles
- Maxim: Legal maxims and doctrines (Latin phrases like res judicata, audi alteram partem, estoppel)
- Legal_Concepts: General legal explanations (use only when no specific category applies)
- SCI_Judgment: Supreme Court of India case search
- GST_Judgment: GST Appellate Authority for Advance Ruling (AAAR) orders. Use for any query about GST/CGST/SGST/IGST advance rulings, AAR, AAAR, GST classification appeals, GST ITC disputes, GST valuation rulings, or state-level GST appellate orders.
- Document: Answers questions about user-uploaded documents (PDFs, images, DOCX). Use when user has uploaded files.

Rules:
1. Most queries need only the PRIMARY agent matching the task type.
2. Use MULTIPLE agents when the query explicitly or implicitly asks for different types of information:
   - "Draft bail application with relevant case laws" → [Drafting, Judgment]
   - "Section 438 BNSS with SC precedents" → [Newacts, SCI_Judgment]
   - "Arguments on behalf of plaintiff and defendant" → [Scenario, Judgment]
   - "Explain Article 21 and related case laws" → [Constitution, Judgment]
3. COMPLEX SCENARIO QUERIES: When a query describes a factual situation AND asks for arguments, defences, legal provisions, citations, or remedies, use MULTIPLE agents:
   - Scenario (for analysis/arguments/remedies) + Judgment (for case laws) + Legislation/Newacts (for statutory provisions)
   - Example: "A doctor operated on wrong patient. What are the legal arguments and relevant case laws and statutory provisions?" → [Scenario, Judgment, Legislation]
   - Example: "My landlord locked me out. Arguments with citations and relevant IPC sections" → [Scenario, Judgment, Newacts]
4. When a query mentions BOTH a constitutional concept AND a legal maxim/doctrine → [Constitution, Maxim]
5. When a query references a named SC landmark case alongside a constitutional topic → include SCI_Judgment.
6. For drafting requests: ONLY include Drafting when the user explicitly asks the AI to CREATE / WRITE / PREPARE / DRAFT a legal document. Questions ABOUT documents (format, structure, essential elements, ingredients, requirements, how to file, when to use, difference between X and Y) are NOT drafting requests — route those to Legal_Concepts, Legislation, or Scenario. "What legal options" or "how can I" is NOT drafting.
7. Never use more than 3 agents.
8. "Other" always maps to Scenario.

Query: {query}
Primary Task: {task}

Return the list of agents and brief reasoning."""


CLASSIFY_AND_PLAN_PROMPT = """You are an expert AI assistant specialized in Indian legal domain analysis.

Perform TWO tasks in one step:

## Task 1: Classify the query
Identify the PRIMARY legal task type. Choose EXACTLY ONE:

- **Newacts** → ONLY for these 6 acts: BNS/IPC, BNSS/CrPC, BSA/IEA (and their old/new equivalents). NOT for any other acts.
  - This includes ALL variants and full names: "IPC" / "Indian Penal Code" / "Penal Code"; "CrPC" / "Cr.P.C" / "Code of Criminal Procedure" / "Criminal Procedure Code"; "IEA" / "Indian Evidence Act" / "Evidence Act"; "BNS" / "Bharatiya Nyaya Sanhita"; "BNSS" / "Bharatiya Nagarik Suraksha Sanhita"; "BSA" / "Bharatiya Sakshya Adhiniyam".
  - Examples: "Section 125 of CrPC" → [Newacts]; "Section 125 of Code of Criminal Procedure 1973" → [Newacts]; "Section 302 IPC" → [Newacts]; "Section 65B Indian Evidence Act" → [Newacts]; "Section 438 BNSS" → [Newacts]. NEVER pair these queries with Legislation -- Newacts already covers both the old and new statute text.
- **Legislation** → ALL other central/state acts and statutes NOT listed under Newacts.
  - Examples: "Section 138 NI Act" → [Legislation]; "Section 7 Hindu Marriage Act" → [Legislation]; "Section 482 Companies Act" → [Legislation].
- **Drafting** → User explicitly asks the AI to **CREATE a standalone legal document** that could be filed in court or signed by parties: plaints, petitions, written statements, bail applications, affidavits, legal notices, agreements, contracts, deeds, MOUs, wills, divorce petitions, etc. Trigger only on explicit production verbs: "draft", "write", "prepare", "create", "generate", "compose", "draw up", "redraft", "give me a [document]", "I need a [document]" — **paired with a court-filing-ready document noun**. DO NOT trigger on questions ABOUT documents (format, structure, essential elements, ingredients, requirements, how to file, when to use, difference between X and Y). DO NOT trigger on tactical / strategic outputs like cross-examination questions, arguments, defences, strategies, analyses, opinions, briefs of advice — those go to **Scenario** (situational legal analysis), even when the user uses the words "draft" or "prepare".
  - DO Drafting: "Draft a plaint for partition", "Prepare a bail application", "Give me a sample MOU", "Write a legal notice for property dispute", "Generate a divorce petition".
  - DO NOT route to Drafting (route elsewhere):
    - "Essential elements of a partnership agreement" → Legal_Concepts (theory)
    - "What is the format of a bail application?" → Legal_Concepts (structural explanation)
    - "How to file a writ petition under Article 32?" → Scenario (procedure)
    - "Discuss petition under Article 32" → Constitution (concept)
    - "Notice under Section 138 NI Act — requirements" → Legislation (statutory rule)
    - "Section 80 CPC notice requirements" → Legislation
    - "Plaint requirements under Order VII CPC" → Legislation
    - "Difference between agreement and contract" → Legal_Concepts
    - "What is a written statement?" → Legal_Concepts
    - "Prepare a cross-examination strategy for an NDPS case" → Scenario (tactical output)
    - "Draft arguments for the accused / for the prosecution" → Scenario (advocacy strategy)
    - "Give me cross-examination questions for the IO" → Scenario (litigation prep)
    - "Prepare a brief on bail under Section 37 NDPS" → Scenario (legal analysis)
    - "What defences are available against Section 498A IPC" → Scenario (situational advice)
- **Constitution** → Constitutional provisions, fundamental rights/duties, Articles of Constitution.
- **Scenario** → Situational legal query, real-life legal situation analysis, legal advice.
- **Judgment** → Case law, court decisions, precedents (general / High Court / unspecified courts).
- **SCI_Judgment** → Supreme Court of India cases. Use when user explicitly mentions "Supreme Court" or "SC", or names a landmark SC case.
- **GST_Judgment** → GST Appellate Authority for Advance Ruling (AAAR) orders. Use when the query is about: GST/CGST/SGST/IGST advance rulings, AAR or AAAR orders, GST classification appeals, GST input tax credit (ITC) disputes, GST valuation rulings, HSN classification under GST, or state-level GST appellate decisions.
- **Maxim** → Legal maxims, Latin phrases, legal doctrines (res judicata, estoppel, etc.).
- **Legal_Concepts** → General legal explanations that don't fit above categories.
- **Document** → Questions about uploaded files/documents.
- **Non_legal** → Non-legal queries: greetings, casual chat, non-legal topics, bot identity questions.
- **Other** → Legal-adjacent queries that don't fit other categories.

## Task 2: Plan which agents to invoke
Available agents: Legislation, Judgment, Newacts, Drafting, Scenario, Constitution, Maxim, Legal_Concepts, SCI_Judgment, GST_Judgment, Document

Rules:
1. Most queries need only the PRIMARY agent matching the task type.
2. Use MULTIPLE agents when the query explicitly asks for different types of information:
   - "Draft bail application with relevant case laws" → [Drafting, Judgment]
   - "Section 438 BNSS with SC precedents" → [Newacts, SCI_Judgment]
   - "Explain Article 21 and related case laws" → [Constitution, Judgment]
3. COMPLEX SCENARIO QUERIES: When a query describes a factual situation AND asks for arguments, defences, provisions, or citations, use MULTIPLE agents:
   - Scenario + Judgment + Legislation/Newacts as appropriate
4. When a query mentions BOTH constitutional concept AND legal maxim → [Constitution, Maxim]
5. When a query references a named SC landmark case alongside a constitutional topic → include SCI_Judgment.
6. For drafting requests: ONLY include Drafting when the user explicitly asks the AI to CREATE / WRITE / PREPARE / DRAFT a legal document. Questions ABOUT documents (format, structure, essential elements, requirements, how to file, difference between X and Y) are NOT drafting — route to Legal_Concepts / Legislation / Scenario.
7. Never use more than 3 agents.
8. "Other" task always maps to Scenario agent.
9. For Non_legal: agents should be ["Non_legal"].

User Query: {query}
Chat Summary (Optional): {chat_summary}

Return the task type, list of agents, and brief reasoning."""


def _classify_and_plan(query: str, chat_summary: str | None = None) -> tuple[str, list[str]]:
    """Classify query AND plan agents in a single LLM call.

    Returns (task, agents_list).
    Falls back to regex classification + single-agent plan on failure.
    """
    try:
        with log_time(log, "Classify + plan (merged)"):
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                ClassifyAndPlan, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_template(CLASSIFY_AND_PLAN_PROMPT)
            chain = prompt | llm
            raw_and_parsed = chain.invoke({
                "query": query,
                "chat_summary": chat_summary or "",
            })
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "classify_and_plan", raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]

        task = result.task
        agents = result.agents[:3]  # cap at 3
        if not agents:
            agents = [task]

        # Telemetry: cross-check LLM's Drafting decision against the regex
        # helper. If the LLM picked Drafting but _wants_drafting disagrees,
        # that's the exact misroute pattern we're hunting — surface it as a
        # WARN so prod logs are greppable for future investigations. (We do
        # NOT override the LLM here — only observe; overriding would risk
        # breaking legitimate edge cases the regex doesn't know about.)
        drafting_disagreement = (
            (task == "Drafting" or "Drafting" in agents)
            and not _wants_drafting(query)
        )
        if drafting_disagreement:
            log.warning("Classify+plan picked Drafting but intent regex disagrees",
                        task=task, agents=agents,
                        query=query[:120],
                        reasoning=result.reasoning[:160])

        log.info("Classify+plan completed",
                 task=task, agents=agents,
                 source="llm",
                 reasoning=result.reasoning[:120])
        return task, agents

    except Exception as e:
        fallback_task = _classify_task_regex_fallback(query)
        log.warning("Classify+plan LLM failed, using regex fallback",
                    error=str(e), fallback=fallback_task, source="regex_fallback")
        return fallback_task, [fallback_task]


def _select_citation_agents(query: str) -> list[str]:
    """Select which agents should provide citations for a draft.

    Always returns at least [Judgment, Legislation/Newacts].
    """
    agents = ["Judgment"]  # always include general case laws
    query_lower = query.lower()

    # Check for specific acts in Newacts scope (BNS/BNSS/BSA/IPC/CrPC/IEA)
    newacts_keywords = [
        "bns", "bnss", "bsa", "ipc", "crpc", "iea",
        "penal code", "criminal procedure", "evidence act",
        "bharatiya nyaya", "bharatiya nagarik", "bharatiya sakshya",
    ]
    if any(kw in query_lower for kw in newacts_keywords):
        agents.append("Newacts")
    else:
        agents.append("Legislation")

    # Check for Supreme Court references
    sci_keywords = [
        "supreme court", " sc ", "puttaswamy", "maneka gandhi",
        "kesavananda", "vishaka", "navtej", "mohd. ahmed khan",
    ]
    if any(kw in query_lower for kw in sci_keywords):
        agents.append("SCI_Judgment")

    return agents


def _detect_multi_intent(query: str, task: str) -> list[str]:
    """Keyword-based multi-intent detection as safety net.

    Ensures complex queries that mention citations, arguments, provisions etc.
    get routed to multiple agents even if the LLM planner returns only one.
    Returns additional agents to add (may be empty).
    """
    q = query.lower()
    extra = []

    # Detect requests for case laws / citations / judgments
    wants_cases = any(k in q for k in (
        "case law", "case laws", "citation", "citations", "judgment", "judgement",
        "precedent", "precedents", "court decision", "landmark case",
        "relevant case", "supporting case", "judicial",
    ))

    # Detect requests for statutory provisions / sections / acts
    wants_statutes = any(k in q for k in (
        "section", "provision", "provisions", "statutory", "statute",
        "act ", " act,", " act.", "legal provision", "under which law",
        "applicable law", "relevant law", "penal", "ipc", "bns", "crpc", "bnss",
    ))

    # Detect requests for arguments / defences / remedies (scenario analysis)
    wants_analysis = any(k in q for k in (
        "argument", "arguments", "defence", "defense", "remedy", "remedies",
        "legal option", "legal options", "on behalf of", "what can",
        "how to fight", "how to defend", "legal recourse", "legal action",
        "advice", "advise",
    ))

    # Detect drafting intent. Use the strict verb+document-noun patterns so we
    # only add Drafting when the user actually asks for a court-filing-ready
    # document to be produced — plaint, petition, affidavit, bail application,
    # MOU, deed, etc. The previous naive substring check ("draft", "prepare",
    # "application for") false-positively added Drafting on tactical-output
    # queries like "Prepare a cross-examination strategy and draft arguments"
    # which should be served by Scenario. The downstream cite-appendix-OFF
    # logic strips all non-drafting agents once Drafting is in the plan, so
    # this false-positive completely hijacked Scenario/Newacts/Judgment
    # primary routings.
    wants_draft = bool(
        _DRAFTING_INTENT_RE.search(query)
        or _DRAFTING_FORMAT_RE.search(query)
        or _DRAFTING_OF_RE.search(query)
    )

    # Add missing agents based on detected intents
    if wants_cases and task not in ("Judgment", "SCI_Judgment"):
        extra.append("Judgment")
    if wants_statutes and task not in ("Legislation", "Newacts"):
        # Decide between Legislation and Newacts
        newacts_acts = ("bns", "bnss", "bsa", "ipc", "crpc", "iea",
                        "penal code", "criminal procedure", "evidence act")
        if any(a in q for a in newacts_acts):
            extra.append("Newacts")
        else:
            extra.append("Legislation")
    if wants_analysis and task not in ("Scenario",):
        extra.append("Scenario")
    if wants_draft and task not in ("Drafting",):
        extra.append("Drafting")

    return extra


def _plan_agents(query: str, task: str) -> list[str]:
    """Determine which domain agents to invoke for this query.

    For simple queries: returns just the primary task agent.
    For complex queries: returns multiple agents to run in parallel.
    For Drafting: always adds citation agents (Judgment + Legislation/Newacts).
    """
    # Short-circuit for simple task types
    if task == "Non_legal":
        log.debug("Routing to non_legal agent", task=task)
        return ["Non_legal"]

    # "Other" always maps to Scenario (as per PLAN_PROMPT contract)
    if task == "Other":
        log.debug("Mapping 'Other' task to Scenario")
        task = "Scenario"

    # Try multi-agent planning with LLM
    try:
        with log_time(log, "Multi-agent planning"):
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                AgentPlan, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_template(PLAN_PROMPT)
            chain = prompt | llm
            raw_and_parsed = chain.invoke({"query": query, "task": task})
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "plan_agents", raw_and_parsed.get("raw"))
        plan = raw_and_parsed["parsed"]
        agents = plan.agents[:3]  # max 3 agents
        log.info("Agent plan created",
                 agents=agents, reasoning=plan.reasoning[:120])
        agents = agents if agents else [task]
    except Exception as e:
        log.error("Planning failed, falling back to single agent",
                  error=str(e), fallback=task)
        agents = [task]

    # Safety net: keyword-based multi-intent detection
    extra = _detect_multi_intent(query, task)
    for agent in extra:
        if agent not in agents:
            agents.append(agent)
    if extra:
        log.info("Multi-intent detection added agents",
                 extra=extra, all_agents=agents)

    # For Drafting: ALWAYS add citation agents for court-filing quality
    has_drafting = task == "Drafting" or "Drafting" in agents
    if has_drafting:
        if "Drafting" not in agents:
            agents.insert(0, "Drafting")
        citation_agents = _select_citation_agents(query)
        for ca in citation_agents:
            if ca not in agents:
                agents.append(ca)
        agents = agents[:4]  # allow up to 4 agents for drafting
        log.info("Drafting citation agents added",
                 agents=agents, citation_agents=citation_agents)
    else:
        agents = agents[:3]  # cap at 3 agents for non-drafting

    return agents


# --- Per-Agent Query Rewriting ---

import json as _json

AGENT_QUERY_REWRITE_PROMPT = """You are a legal query optimizer. Rewrite the user's query into specialized search queries for each assigned agent.

Each agent has a different database and purpose:
- Judgment: Searches court case law database. Query should focus on: legal topic keywords, cause of action, type of case (e.g. "medical negligence", "consumer complaint", "property dispute"). NEVER include user-provided party names (they are fictional and won't match any real cases). Use generic terms like "doctor negligence hospital compensation" instead.
- Legislation: Searches Indian act/statute database by full-text match. IMPORTANT: Focus on the SINGLE most relevant act and its specific sections. Do NOT list multiple acts — the search engine will match the first act name it finds. Example: "Consumer Protection Act 2019 Section 2 definition of consumer deficiency in service medical negligence" (not "Indian Contract Act; IPC; Consumer Protection Act").
- Newacts: Searches BNS/IPC, BNSS/CrPC, BSA/IEA database. Query should mention the specific criminal code sections or topics.
- Drafting: Creates legal documents. Query should specify: document type, parties, key facts, relief sought.
- Scenario: Performs web-grounded legal analysis. Query should include: full factual situation, what analysis is needed (arguments, remedies, forum).
- Constitution: Searches constitutional provisions database. Query should specify: Article numbers, fundamental rights, constitutional principles.
- Maxim: Searches legal maxims database. Query should specify: maxim name, doctrine, Latin phrase.
- Legal_Concepts: General legal explanation. Query should be the legal concept to explain.
- SCI_Judgment: Searches Supreme Court database. Query should focus on: SC-specific case names, constitutional questions, landmark rulings.
- GST_Judgment: Searches GST AAAR (Appellate Authority for Advance Ruling) order database. Query should focus on: GST issue (classification / ITC / valuation / exemption), HSN code, GST section/notification, applicant name, state. Do NOT include unrelated tax topics (income tax, customs).

Rules:
1. Each rewritten query must be self-contained and optimized for that agent's search database.
2. Keep queries concise (under 100 words each). Scenario can be longer.
3. Extract and include specific legal terms: act names, section numbers, doctrines, party types.
4. For Legislation: pick the SINGLE most relevant act for the user's primary legal issue. Do NOT list multiple acts.
5. For Judgment: NEVER include fictional party names from the user's scenario. Use only legal topics and case type keywords.
6. Return valid JSON object mapping agent name to rewritten query.
7. CRITICAL: All rewritten queries MUST be in English, regardless of the input language. If the user query is in Hindi, Tamil, or any other language, translate the intent to English for every agent query.

User Query: {query}
Agents: {agents}

Return JSON object like: {{"Judgment": "...", "Legislation": "..."}}"""


class AgentQueries(BaseModel):
    queries: dict[str, str] = Field(
        ..., description="Map of agent name to its optimized query"
    )


# --- User Expectation Extraction + Query Normalization ---

QUERY_NORMALIZE_PROMPT = """You are a legal query analyzer. Analyze the user's query and extract two things:

1. **Normalized Query** (in English): Rewrite the user's query into clear, professional English.
   - If the query is in any Indian language (Hindi, Bengali, Tamil, Telugu, Marathi, Kannada, Malayalam, Gujarati, Punjabi, Urdu, Odia, Assamese, etc.) or in Romanized/transliterated form (Hinglish, "kaise", "batao", etc.), translate it to English.
   - Preserve all legal details verbatim: section numbers, act names (IPC, BNS, CrPC, BNSS, IEA, BSA), party names, case numbers, dates.
   - Do NOT translate proper nouns: court names, party names, act abbreviations.
   - Add explicit mention of what the user is asking for.

2. **Response Instructions**: Extract what FORMAT and TYPE of response the user expects. Look for:
   - Output type: draft/document, explanation, advice/opinion, comparison table, list, summary, step-by-step guide
   - Specific format requests: table format, bullet points, numbered list, formal legal language
   - Specific expectations: "on behalf of plaintiff", "with case laws", "with sections", "arguments and counter-arguments"
   - Relief/remedy focus: compensation, bail, injunction, etc.

If the user has no special format preference, return "Standard legal response with proper citations and markdown formatting."

User Query: {query}

Return JSON:
{{"normalized_query": "...", "response_instructions": "..."}}"""


class QueryAnalysis(BaseModel):
    normalized_query: str = Field(..., description="Query rewritten in clear English with expectations embedded")
    response_instructions: str = Field(..., description="What format/type of response the user expects")


def _analyze_and_normalize_query(query: str) -> tuple[str, str]:
    """Analyze user query: translate to English + extract response expectations.

    Returns (normalized_query, response_instructions).
    """
    try:
        with log_time(log, "Query analysis & normalization"):
            llm = get_gemini_flash(temperature=0.1).with_structured_output(
                QueryAnalysis, include_raw=True,
            )
            prompt = ChatPromptTemplate.from_template(QUERY_NORMALIZE_PROMPT)
            chain = prompt | llm
            raw_and_parsed = chain.invoke({"query": query})
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "normalize_query", raw_and_parsed.get("raw"))
        result = raw_and_parsed["parsed"]

        log.info("Query normalized",
                 original_len=len(query),
                 normalized_len=len(result.normalized_query),
                 instructions_len=len(result.response_instructions))
        return result.normalized_query, _sanitize_response_instructions(result.response_instructions)

    except Exception as e:
        log.warning("Query normalization failed, using original",
                    error=str(e))
        return query, ""


def _rewrite_queries_for_agents(query: str, agents: list[str]) -> dict[str, str]:
    """Generate per-agent optimized queries using LLM.

    For single-agent plans, returns empty dict (agent uses original query).
    For multi-agent plans, rewrites the query for each agent's domain.
    """
    if len(agents) <= 1:
        return {}

    try:
        with log_time(log, "Per-agent query rewriting"):
            llm = get_gemini_flash(temperature=0.1)
            prompt = ChatPromptTemplate.from_template(AGENT_QUERY_REWRITE_PROMPT)
            chain = prompt | llm

            response = chain.invoke({
                "query": query,
                "agents": ", ".join(agents),
            })
        from core.token_tracker import record as _record_tokens
        _record_tokens("Orchestrator", "rewrite_per_agent_queries", response)

        # Parse JSON from response
        text = response.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        parsed = _json.loads(text)
        # Validate: only keep queries for planned agents
        agent_queries = {k: v for k, v in parsed.items() if k in agents and isinstance(v, str)}

        log.info("Per-agent queries generated",
                 agents=list(agent_queries.keys()),
                 query_lengths={k: len(v) for k, v in agent_queries.items()})
        return agent_queries

    except Exception as e:
        log.warning("Per-agent query rewriting failed, agents will use original query",
                    error=str(e))
        return {}


# --- Signal-Based Plan Validation ---
# Instead of scattered keyword pre-checks, this single function validates
# the LLM's plan against keyword signals in the query and adds missing agents.
# Each rule maps keyword patterns → required agent(s) that should be in the plan.

_PLAN_SIGNALS: list[tuple[tuple[str, ...], str, int, tuple[str, ...]]] = [
    # (keywords, required_agent, max_plan_size_to_add, agents_to_remove)
    # — max_plan_size_to_add: only add if plan has ≤ N agents (prevents 4+ agent bloat).
    # — agents_to_remove: agents that become redundant once required_agent fires
    #   (e.g. Newacts already covers IPC/CrPC/IEA texts, so Legislation would
    #   only re-fetch the same content from a less-curated index and trigger
    #   wasteful web-search fallback). Empty tuple = no veto.

    # Judgment signals: "cases", "case law", "precedent", "ruling" → need Judgment
    ((" cases", "case law", "case laws", "precedent", "court decision",
      "judgments on ", "judgement on ", "rulings on "), "Judgment", 2, ()),

    # SCI signals (backup for pre-check): "supreme court", "SC" → need SCI_Judgment
    (("supreme court", "apex court"), "SCI_Judgment", 2, ()),

    # GST AAAR signals
    (("advance ruling", "aaar", "appellate authority for advance ruling",
      "gst appeal", "gst appellate", "gst ruling", "gst classification"),
     "GST_Judgment", 2, ()),

    # Old/new act names for the 6 codes that live in Newacts (BNS/IPC,
    # BNSS/CrPC, BSA/IEA). Newacts indexes BOTH the old and new statute text,
    # so we strip Legislation -- otherwise Legislation re-fetches the same
    # text from a less-curated index and frequently falls back to a 7-second
    # Google web search for canonical sections (verified on "Section 125 CrPC").
    (("ipc", "crpc", "cr.p.c", "cr pc", "iea", "bns", "bnss", "bsa",
      "indian penal code", "penal code",
      "code of criminal procedure", "criminal procedure code",
      "indian evidence act", "evidence act",
      "bharatiya nyaya", "bharatiya nagarik", "bharatiya sakshya"),
     "Newacts", 3, ("Legislation",)),

    # Freshness signals → need Scenario for web-grounded info
    (("latest", "recent changes", "recent amendments", "recent court",
      "recent ruling", "current status", "new changes", "updated",
      "is it legal", "is cryptocurrency", "is crypto"), "Scenario", 3, ()),
]


def _validate_and_enrich_plan(
    tasks_planned: list[str],
    original_query: str,
    normalized_query: str,
    log,
) -> list[str]:
    """Validate plan against keyword signals and add (or veto) agents.

    Scans both the original and normalized query for keyword patterns. For
    each matching signal:
    - adds `required_agent` if missing AND plan is not already too large,
    - removes any agents listed in `agents_to_remove` (the "veto" list) once
      the keyword fires, regardless of whether `required_agent` was newly
      added or already present -- both cases mean the veto'd agent is now
      redundant.

    Returns the (possibly modified) plan.
    """
    combined = (original_query.lower() + " " + normalized_query.lower())

    for keywords, required_agent, max_size, also_remove in _PLAN_SIGNALS:
        if not any(k in combined for k in keywords):
            continue

        # Add the required agent (if missing AND plan has headroom)
        if required_agent not in tasks_planned and len(tasks_planned) <= max_size:
            tasks_planned.append(required_agent)
            log.info("Plan enriched by signal validator",
                     added=required_agent, signal_match=True,
                     plan=tasks_planned)

        # Veto: remove agents that would now be redundant. Runs even when
        # `required_agent` was already in the plan, since the veto'd agent
        # is wrong either way once this keyword fired.
        for veto_agent in also_remove:
            if veto_agent in tasks_planned:
                tasks_planned.remove(veto_agent)
                log.info("Plan veto by signal validator",
                         removed=veto_agent,
                         reason=f"{required_agent} covers this content",
                         plan=tasks_planned)

    return tasks_planned


# --- Agent Nodes ---

async def orchestrator_plan_node(state: LegalAgentState) -> dict:
    """Phase 1 — Classify task and create execution plan.

    Steps:
    0. Normalize query (translate to English + extract user expectations)
    1. Classify query into task type
    2. Handle Non_legal rejection
    3. Plan which agents to invoke (single or multi-agent)
    4. Rewrite query per-agent for multi-agent plans
    5. Return task + tasks_planned + agent_queries + response_instructions
    """
    query = state.get("query", state["original_query"])
    _original_query = state.get("original_query", query)
    summary = state.get("summary_text", "")
    user_language = state.get("user_language", "en")
    user_context = ""  # long-form pasted content, empty for normal queries
    log.info("Plan phase started", query=query[:100],
             has_summary=bool(summary), query_len=len(query))

    progress("orchestrator", "Understanding your question...", step="classify")

    # --- Long query extraction: separate question from pasted content ---
    if len(query) > _LONG_QUERY_THRESHOLD:
        log.info("Long query detected, extracting question",
                 query_len=len(query), threshold=_LONG_QUERY_THRESHOLD)
        progress("orchestrator", "Analyzing your document...", step="extract")
        try:
            extracted_question, doc_type = await asyncio.wait_for(
                asyncio.to_thread(_extract_question_from_long_query, query),
                timeout=15,
            )
            user_context = query  # full text preserved for generating agent
            query = extracted_question  # route/classify on concise question
            log.info("Long query split",
                     question=extracted_question[:100],
                     doc_type=doc_type,
                     context_len=len(user_context))
        except asyncio.TimeoutError:
            log.warning("Long query extraction timed out, using first 500 chars for routing")
            user_context = query
            query = query[:500]
        except Exception as extract_err:
            log.warning("Long query extraction failed", error=str(extract_err))
            user_context = query
            query = query[:500]

    # --- Fast pre-checks (no LLM calls, uses original query) ---
    _GREETING_PREFIXES = (
        "hello", "hey", "hii", "helo", "hola", "namaste", "namaskar",
        "good morning", "good afternoon", "good evening", "good night",
        "how are you", "how r u", "what's up", "whats up",
        "who are you", "what are you", "how's it going", "hows it going",
    )
    _GREETING_EXACT = ("hi", "sup")
    _orig_stripped = _original_query.lower().strip().rstrip("!?,.")
    is_greeting = (
        _orig_stripped in _GREETING_PREFIXES
        or _orig_stripped in _GREETING_EXACT
        or any(_orig_stripped.startswith(g + " ") for g in _GREETING_PREFIXES)
        or any(_orig_stripped.startswith(g + " ") and len(_orig_stripped) < 30 for g in _GREETING_EXACT)
    )

    _SCI_STRONG_KEYWORDS = (
        "supreme court judgment", "supreme court case", "supreme court ruling",
        "supreme court ruled", "supreme court held", "supreme court order",
        "sc judgment", "sc case", "sc ruling", "hon'ble sc",
        "article 136", "supreme court of india", "apex court judgment",
        "apex court ruling", "supreme court bench",
    )
    _orig_lower = _original_query.lower()
    is_sci = any(k in _orig_lower for k in _SCI_STRONG_KEYWORDS)

    fc = FileContextData.from_state(state)

    # --- Short-circuit: greeting or SCI pre-check resolved ---
    if is_greeting:
        log.info("Greeting detected, short-circuiting classification",
                 original=_original_query[:60])
        task = "Non_legal"
        tasks_planned = ["Non_legal"]
        response_instructions = ""

        # Override to Document if files attached
        if fc and fc.has_content:
            log.info("Non-legal overridden to Document due to file context")
            task = "Document"
            tasks_planned = ["Document"]

    elif is_sci:
        log.info("SCI pre-check triggered, skipping LLM classification",
                 query=query[:80])
        task = "SCI_Judgment"
        tasks_planned = ["SCI_Judgment"]
        response_instructions = ""

    else:
        # --- Change 3: Skip normalization for English queries ---
        _FORMAT_KEYWORDS = (
            "table format", "bullet", "in hindi", "in marathi", "in tamil",
            "in telugu", "in bengali", "in kannada", "in malayalam",
            "in gujarati", "in punjabi", "in urdu", "in odia",
            "hindi mein", "hindi me", "batao", "samjhao", "kaise",
        )
        skip_normalize = (
            user_language == "en"
            and len(query) < 500
            and not any(k in _orig_lower for k in _FORMAT_KEYWORDS)
        )

        if skip_normalize:
            log.info("Skipping normalization (English, short query)")
            response_instructions = ""
            normalized_query = query
        else:
            normalized_query = None  # will be set by parallel normalization

        # --- Change 2: Parallelize normalization + classify-plan ---
        # Build classify query with file hint
        classify_query = query
        if fc and fc.has_content:
            file_hint = f" [User has uploaded files: {', '.join(fc.file_names)}. This query is about the uploaded document(s).]"
            classify_query = query + file_hint
            log.info("File context hint added for classification", file_names=fc.file_names)

        if skip_normalize:
            # Only need classify+plan (normalization skipped)
            try:
                task, tasks_planned = await asyncio.wait_for(
                    asyncio.to_thread(
                        _classify_and_plan, classify_query,
                        chat_summary=summary if summary else None,
                    ),
                    timeout=30,
                )
            except asyncio.TimeoutError:
                task = _classify_task_regex_fallback(classify_query)
                tasks_planned = [task]
                log.warning("Classify+plan timed out, using regex fallback",
                            task=task, query=query[:80])
        else:
            # Run normalization + classify-plan in parallel
            normalize_coro = asyncio.wait_for(
                asyncio.to_thread(_analyze_and_normalize_query, query),
                timeout=10,
            )
            classify_coro = asyncio.wait_for(
                asyncio.to_thread(
                    _classify_and_plan, classify_query,
                    chat_summary=summary if summary else None,
                ),
                timeout=30,
            )
            results = await asyncio.gather(
                normalize_coro, classify_coro, return_exceptions=True,
            )

            # Process normalization result
            if isinstance(results[0], Exception):
                log.warning("Query normalization failed/timed out, using original",
                            error=str(results[0]))
                response_instructions = ""
            else:
                normalized_query, response_instructions = results[0]
                if normalized_query and normalized_query != query:
                    log.info("Query normalized",
                             original=query[:80], normalized=normalized_query[:80])
                    query = normalized_query

            # Process classify+plan result
            if isinstance(results[1], Exception):
                task = _classify_task_regex_fallback(classify_query)
                tasks_planned = [task]
                log.warning("Classify+plan failed/timed out, using regex fallback",
                            error=str(results[1]), task=task)
            else:
                task, tasks_planned = results[1]
                # NOTE: Drafting false-positive strip lives downstream — after
                # _detect_multi_intent runs — so both the LLM-added and
                # safety-net-added Drafting cases are caught in one spot.

        # Handle non-legal with file context
        if task == "Non_legal" and fc and fc.has_content:
            log.info("Non-legal overridden to Document due to file context")
            task = "Document"
            tasks_planned = ["Document"]

    # For Document task with file context, ensure Document agent is primary
    if task == "Document" and fc and fc.has_content and tasks_planned != ["Document"]:
        tasks_planned = ["Document"]
        log.info("Document task with file context — using Document agent directly",
                 file_names=fc.file_names)

    # File context: ensure Document agent is in plan if files attached
    if fc and fc.has_content and "Document" not in tasks_planned:
        tasks_planned.append("Document")
        log.info("Document agent added for file context (multi-intent support)",
                 file_names=fc.file_names)

    # Draft + file detection: if user wants to DRAFT from an uploaded document,
    # route through Drafting pipeline (not just Document Q&A).
    # Inject file content into Drafting agent's query for context.
    #
    # IMPORTANT: must use intent detection (verb + document noun, or explicit
    # drafting verb), NOT raw substring matching. A naive substring approach
    # mis-routes pure Q&A queries — e.g. "summarize the attached plaint" or
    # "who is the plaintiff?" both contain "plaint" but neither asks for a draft.
    if fc and fc.has_content and _wants_drafting(_original_query):
        if "Drafting" not in tasks_planned:
            tasks_planned.insert(0, "Drafting")
            log.info("Draft-from-file detected — adding Drafting agent",
                     file_names=fc.file_names, plan=tasks_planned)

    progress("orchestrator", f"Identified: {', '.join(tasks_planned)}", substep=True, detail=task, step="classify")

    # Post-processing: multi-intent detection safety net
    if task not in ("Non_legal", "Document"):
        extra = _detect_multi_intent(query, task)
        for agent in extra:
            if agent not in tasks_planned:
                tasks_planned.append(agent)
        if extra:
            log.info("Multi-intent detection added agents",
                     extra=extra, all_agents=tasks_planned)

    # Drafting false-positive strip (post multi-intent): if the primary task
    # is not Drafting but Drafting ended up in tasks_planned (either from the
    # LLM planner or from _detect_multi_intent's safety net), drop it. The
    # cite-appendix-OFF logic below otherwise strips ALL non-drafting agents
    # the moment Drafting is in the plan, which silently hijacks the primary
    # task agent's execution. Trust the LLM's primary task. The file-attached
    # drafting path (line ~1198) re-injects Drafting if the user explicitly
    # asked to draft from an attached document, so that flow is preserved.
    if (
        task != "Drafting"
        and "Drafting" in tasks_planned
        and not (fc and fc.has_content and _wants_drafting(_original_query))
    ):
        tasks_planned = [a for a in tasks_planned if a != "Drafting"]
        if not tasks_planned:
            tasks_planned = [task]
        log.info("Drafting stripped (post multi-intent) — primary task is non-drafting",
                 task=task, agents=tasks_planned)

    # Drafting citation agents
    # Phase 1: gated behind cite_appendix flag (per-request) + env default.
    # Default OFF — citations doubled response length and ~50% of token spend
    # without making the draft itself more file-ready. Callers that want the
    # appendix pass `cite_appendix=true`.
    has_drafting = task == "Drafting" or "Drafting" in tasks_planned
    if has_drafting:
        if "Drafting" not in tasks_planned:
            tasks_planned.insert(0, "Drafting")
        from core.settings import DRAFTING_CITE_APPENDIX_DEFAULT
        _flag = state.get("cite_appendix")
        cite_appendix_on = _flag if _flag is not None else DRAFTING_CITE_APPENDIX_DEFAULT
        if cite_appendix_on:
            citation_agents = _select_citation_agents(query)
            for ca in citation_agents:
                if ca not in tasks_planned:
                    tasks_planned.append(ca)
            tasks_planned = tasks_planned[:4]
            log.info("Drafting citation appendix enabled",
                     citation_agents=citation_agents,
                     source="request" if _flag is not None else "env_default")
        else:
            tasks_planned = [t for t in tasks_planned if t == "Drafting" or t == "Document"][:3]
            log.info("Drafting citation appendix skipped",
                     source="request" if _flag is not None else "env_default")
    else:
        tasks_planned = tasks_planned[:3]

    # Signal-based plan validation
    tasks_planned = _validate_and_enrich_plan(
        tasks_planned, _original_query, query, log,
    )

    # Step 4: Per-agent query rewriting (only for multi-agent plans)
    agent_queries = {}
    if len(tasks_planned) > 1:
        progress("orchestrator", f"Preparing search queries for {len(tasks_planned)} agents...", found=len(tasks_planned), step="plan")
        try:
            agent_queries = await asyncio.wait_for(
                asyncio.to_thread(_rewrite_queries_for_agents, query, tasks_planned),
                timeout=15,
            )
        except asyncio.TimeoutError:
            log.warning("Per-agent query rewriting timed out")

    # NOTE: Previously this block concatenated 30K chars of PDF text into
    # agent_queries["Drafting"] so the drafting agent would receive the
    # uploaded-document content. That approach had three flaws (BUG-02, BUG-05,
    # BUG-16): (a) it bloated the BM25 template-search query and biased
    # selection toward irrelevant templates, (b) it buried the facts at the
    # end of the prompt where the LLM ignored them, and (c) it forced the
    # entire pipeline through one giant string. The drafting agent now reads
    # file_context directly via FileContextData.from_state(state) and passes
    # the document text as an explicit FACTS field to outline + section
    # generation. The agent_queries["Drafting"] entry now holds only the
    # user's clean question (used for template search and selection).
    if "Drafting" in tasks_planned and fc and fc.has_content and fc.inline_text:
        log.info("Drafting will receive uploaded document via file_context",
                 file_text_len=len(fc.inline_text),
                 file_names=fc.file_names)

    log.info("Plan phase completed",
             task=task, agents_planned=tasks_planned,
             agent_count=len(tasks_planned),
             agent_queries_generated=len(agent_queries),
             has_response_instructions=bool(response_instructions))

    result = {
        "query": query,  # normalized English query replaces original
        "task": task,
        "tasks_planned": tasks_planned,
        "agent_queries": agent_queries,
        "response_instructions": response_instructions,
    }
    if user_context:
        result["user_context"] = user_context
    return result


async def orchestrator_synthesize_node(state: LegalAgentState) -> dict:
    """Phase 2 — Merge results from all domain agents into final response.

    Single agent → pass through directly.
    Multiple agents → LLM synthesis into coherent, unified response.
    """
    agent_results: dict[str, AgentResult] = state.get("agent_results", {})
    query = state.get("query", state["original_query"])
    response_instructions = state.get("response_instructions", "")
    user_language = state.get("user_language", "en")

    # Inject file context into query for synthesis
    fc = FileContextData.from_state(state)
    if fc and fc.inline_text:
        query = f"{query}\n\n--- Uploaded File Content ---\n{fc.inline_text[:50000]}"
        log.info("File context injected into synthesis",
                 inline_chars=len(fc.inline_text))

    log.info("Synthesize phase started",
             agents_received=list(agent_results.keys()),
             has_response_instructions=bool(response_instructions))

    progress("orchestrator", f"Merging results from {len(agent_results)} agents...", found=len(agent_results), step="synthesize")

    if not agent_results:
        log.warning("No agent results to synthesize")
        return {
            "final_response": "No results were found for your query. Please try rephrasing.",
            "source_metadata": [],
            "tokens_consumed": 0,
        }

    # Filter out empty/errored results
    valid_results = {
        name: r for name, r in agent_results.items()
        if r.content and not r.error
    }
    errored = {
        name: r.error for name, r in agent_results.items()
        if r.error
    }
    empty = [
        name for name, r in agent_results.items()
        if not r.content and not r.error
    ]

    if errored:
        log.warning("Some agents returned errors", errored_agents=errored)
    if empty:
        log.debug("Some agents returned empty results", empty_agents=empty)

    if not valid_results:
        log.warning("All agents empty — invoking web search last resort")
        from core.agent_fallback import web_search_fallback
        fallback = await web_search_fallback(
            query, "Orchestrator",
            "You are Lawttorney, an Indian legal AI assistant. "
            "Answer the user's legal question comprehensively with citations.",
        )
        if fallback.content:
            return {
                "final_response": fallback.content,
                "source_metadata": _serialize_sources(fallback),
                "tokens_consumed": fallback.tokens_consumed,
            }
        return {
            "final_response": "The agents could not find relevant information. Please try a different query.",
            "source_metadata": [],
            "tokens_consumed": 0,
        }

    # Single agent — pass through directly (with auto-citation for Drafting)
    # BUT if file context has inline text, always synthesize so file content is used
    # If file context has inline text but single result is NOT from Document agent,
    # force synthesis so file content gets incorporated. Otherwise pass through.
    #
    # Also force synthesis when the user explicitly asked for a comparison table —
    # pass-through would deliver the raw agent prose, bypassing SYNTHESIS_TABLE_PROMPT
    # and leaving the user without the table they requested.
    has_unprocessed_file = (fc is not None and fc.inline_text
                           and "Document" not in valid_results)
    _wants_table_single = _wants_table_format(response_instructions, query)
    if len(valid_results) == 1 and not has_unprocessed_file and not _wants_table_single:
        name, result = next(iter(valid_results.items()))

        # Drafting solo: previously this called _auto_cite_draft which used an
        # LLM to rewrite the draft and add citations. That LLM rewrite would
        # routinely strip facts the drafting agent had carefully extracted
        # from the user's uploaded file (BUG-03). Pass the draft through
        # unmodified, just stripping internal [CITE: No matching case] markers.
        if name == "Drafting":
            cleaned = _strip_internal_cite_markers(result.content)
            log.info("Drafting solo — passing through unmodified (BUG-03 fix)",
                     draft_len=len(result.content), cleaned_len=len(cleaned))
            return {
                "final_response": cleaned,
                "source_metadata": _serialize_sources(result),
                "tokens_consumed": result.tokens_consumed,
            }

        log.info("Single agent pass-through",
                 agent=name, content_len=len(result.content),
                 tokens=result.tokens_consumed)
        return_dict = {
            "final_response": result.content,
            "source_metadata": _serialize_sources(result),
            "tokens_consumed": result.tokens_consumed,
        }
        return return_dict

    # --- Draft-Aware Synthesis ---
    # When Drafting is one of the agents, the drafting agent's output is the
    # primary content. Other agents' results become citation references that
    # we APPEND in a separate block — we MUST NOT rewrite the draft via an LLM
    # (BUG-03: an earlier _inject_citations_into_draft LLM call routinely
    # stripped real names/dates/amounts and replaced them with template
    # placeholders, losing all the file-grounded facts the drafting agent
    # had carefully extracted).
    if "Drafting" in valid_results:
        drafting_result = valid_results.pop("Drafting")
        citation_results = valid_results  # Judgment, Legislation, Newacts, Document, ...

        # When the user attached a file, the Document agent's analysis is
        # redundant with the drafting agent's content (drafting already
        # consumed the file via case_facts + raw text). Including it in the
        # citation block can dilute the response with template-style language.
        fc_check = FileContextData.from_state(state)
        if fc_check and fc_check.has_content and "Document" in citation_results:
            log.info("Drafting+file: dropping Document from citation block "
                     "(content already incorporated into the draft)")
            citation_results = {k: v for k, v in citation_results.items() if k != "Document"}

        log.info("Draft-aware synthesis starting",
                 draft_len=len(drafting_result.content),
                 citation_agents=list(citation_results.keys()))

        all_serialized_sources = _serialize_sources(drafting_result)
        total_tokens = drafting_result.tokens_consumed

        citations_text = ""
        for name, result in citation_results.items():
            if result.content:
                citations_text += f"\n\n### {name.upper()} CITATIONS:\n{result.content}"
                total_tokens += result.tokens_consumed
                all_serialized_sources.extend(_serialize_sources(result))

        # APPEND-ONLY synthesis: preserve the draft verbatim, strip internal
        # [CITE: ...] placeholder markers (BUG-09), append a References section.
        try:
            enriched = _strip_internal_cite_markers(drafting_result.content)
            if citations_text.strip():
                enriched += "\n\n---\n\n## REFERENCES & CITATIONS\n" + citations_text

            log.info("Draft synthesis completed (append-only)",
                     enriched_len=len(enriched),
                     citation_agents=list(citation_results.keys()),
                     total_tokens=total_tokens)

            return {
                "final_response": enriched,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

        except Exception as e:
            log.error("Draft synthesis failed, returning raw draft", error=str(e))
            return {
                "final_response": drafting_result.content,
                "source_metadata": all_serialized_sources,
                "tokens_consumed": total_tokens,
            }

    # --- Judgment-Aware Synthesis ---
    # Mirrors the draft-aware pattern: when Judgment is among multiple agents,
    # Judgment's response is the primary content. JUDGMENT_SYSTEM_PROMPT shapes
    # that response with a specific structure (direct answer first → "I think
    # this information will help you more" → Key Legal Issues → Detailed
    # Narrative → As per the High Court). If we feed it through the generic
    # SYNTHESIS_PROMPT, the synthesizer LLM reorganises everything by legal
    # argument and the user-facing structure is lost.
    #
    # Append-only: emit Judgment verbatim, then append each supporting agent's
    # content under a labelled heading. No LLM rewrite, no token cost.
    #
    # Skip if the user asked for a comparison table (SYNTHESIS_TABLE_PROMPT
    # owns the layout there) or if there is unprocessed file context that only
    # the synthesizer LLM will read.
    _wants_table_judgment = _wants_table_format(response_instructions, query)
    if (
        "Judgment" in valid_results
        and not has_unprocessed_file
        and not _wants_table_judgment
    ):
        judgment_result = valid_results.pop("Judgment")
        supporting_results = valid_results

        log.info("Judgment-aware synthesis starting",
                 primary_len=len(judgment_result.content),
                 supporting_agents=list(supporting_results.keys()))

        _SUPPORTING_HEADINGS = {
            "Newacts": "## Statutory Provisions Referenced",
            "Legislation": "## Statutory Provisions Referenced",
            "SCI_Judgment": "## Related Supreme Court Authority",
            "Constitution": "## Constitutional Provisions Referenced",
            "Maxim": "## Legal Maxims & Doctrines Referenced",
            "Scenario": "## Additional Analysis",
            "Document": "## From Your Uploaded Document",
        }

        primary = judgment_result.content.rstrip()
        all_serialized_sources = _serialize_sources(judgment_result)
        total_tokens = judgment_result.tokens_consumed
        appendix_parts: list[str] = []

        for name, result in supporting_results.items():
            if not result.content:
                continue
            heading = _SUPPORTING_HEADINGS.get(name, f"## {name} Notes")
            appendix_parts.append(f"\n\n---\n\n{heading}\n\n{result.content.strip()}")
            total_tokens += result.tokens_consumed
            all_serialized_sources.extend(_serialize_sources(result))

        final_response = primary + "".join(appendix_parts)

        log.info("Judgment-aware synthesis completed (append-only)",
                 final_len=len(final_response),
                 supporting_agents=list(supporting_results.keys()),
                 total_tokens=total_tokens)

        return {
            "final_response": final_response,
            "source_metadata": all_serialized_sources,
            "tokens_consumed": total_tokens,
        }

    # --- Generic Multi-Agent Synthesis (non-drafting) ---
    progress("orchestrator", "Composing final response...", step="synthesize")
    log.info("Multi-agent synthesis starting",
             agents=list(valid_results.keys()),
             content_lengths={n: len(r.content) for n, r in valid_results.items()})

    agent_results_text = ""
    total_tokens = 0
    all_serialized_sources = []

    _MAX_AGENT_CONTENT = 8000  # per-agent cap to prevent token explosion
    for name, result in valid_results.items():
        content = result.content
        if len(content) > _MAX_AGENT_CONTENT:
            content = content[:_MAX_AGENT_CONTENT] + "\n\n[... truncated for synthesis]"
        agent_results_text += f"\n\n### {name.upper()} AGENT RESULTS:\n{content}"
        total_tokens += result.tokens_consumed
        all_serialized_sources.extend(_serialize_sources(result))

    # Pick prompt template: table-mode if user asked for a comparison table,
    # else the general synthesis prompt. SYNTHESIS_TABLE_PROMPT constrains output
    # to a single markdown table with no preamble/postamble.
    wants_table = _wants_table_format(response_instructions, query)
    synth_template = SYNTHESIS_TABLE_PROMPT if wants_table else SYNTHESIS_PROMPT
    log.info("Synthesis prompt selected",
             template="SYNTHESIS_TABLE_PROMPT" if wants_table else "SYNTHESIS_PROMPT",
             response_instructions=response_instructions[:120] if response_instructions else "")

    try:
        with log_time(log, "LLM synthesis"):
            # Synthesis is pure generation (merge → format), not analysis. Disable
            # thinking_budget to reclaim the full 65K-token output budget for visible
            # content. Without this, Gemini 2.5 Flash can silently spend thousands
            # of tokens on hidden reasoning while emitting only a handful of visible
            # chars (observed in production logs).
            # Use temperature=0 for table mode (deterministic formatting).
            # max_output_tokens capped at 12288 (~48k chars) to bound the
            # synthesis output. Default 65535 allowed runaway markdown-table
            # column-padding loops that produced 140k-char responses with a
            # single 125k-char dash-only table separator row (see incident
            # thread 73a59cc4-fbfa-4d7b-b493-948a974c1496). 12k tokens is
            # comfortably above any legitimate synthesis of 3 agent outputs.
            llm = get_gemini_flash_full(
                temperature=0.0 if wants_table else 0.2,
                max_output_tokens=12288,
                thinking_budget=0,
            )
            prompt = ChatPromptTemplate.from_template(
                localize_prompt(synth_template, user_language)
            )
            chain = prompt | llm

            from core.streaming import stream_chain_response
            response = await stream_chain_response(chain, {
                "query": query,
                "agent_results": agent_results_text,
                "response_instructions": response_instructions or "Standard legal response with proper citations and markdown formatting.",
            }, timeout=180)  # Multi-agent synthesis needs more time

        synthesized = response.content
        from core.token_tracker import record as _record_tokens
        synth_tokens = _record_tokens("Orchestrator", "synthesize", response)

        # Defense-in-depth cap: at 65K-token output ceiling (~260K chars at
        # ~4 chars/token) the LLM cannot legitimately exceed ~260K chars. We
        # cap at 250K to allow full-budget responses but still catch upstream
        # streaming bugs (e.g. cumulative-content chunk double-counting).
        _MAX_SYNTHESIS_LEN = 250_000
        if len(synthesized) > _MAX_SYNTHESIS_LEN:
            log.warning("Synthesis output too large, truncating",
                        original_len=len(synthesized), cap=_MAX_SYNTHESIS_LEN)
            synthesized = synthesized[:_MAX_SYNTHESIS_LEN] + "\n\n*[Response truncated for length]*"

        total_tokens += synth_tokens
        log.info("Synthesis completed",
                 synthesized_len=len(synthesized),
                 synthesis_tokens=synth_tokens, total_tokens=total_tokens)

    except Exception as e:
        log.error("LLM synthesis failed, concatenating results", error=str(e))
        parts = []
        for name, result in valid_results.items():
            parts.append(result.content)
        synthesized = "\n\n---\n\n".join(parts)

    return {
        "final_response": synthesized,
        "source_metadata": all_serialized_sources,
        "tokens_consumed": total_tokens,
    }


def _serialize_sources(result: AgentResult) -> list[dict]:
    """Serialize all SourceMetadata objects from an agent result into dicts."""
    if not result.sources:
        return []

    serialized = []
    for s in result.sources:
        d = {
            "source_type": s.source_type,
            "title": s.title,
            "content": s.content,
            "doc_link": s.doc_link,
            "file_name": s.file_name,
            "agent_name": s.agent_name or result.agent_name,
            "relevance_score": s.relevance_score,
        }
        # Add type-specific fields only when populated
        for field_name in [
            "court_name", "year", "petitioner_names", "respondent_names",
            "keywords", "acts_or_sections_invoked",
            "case_no", "judgment_date", "bench", "judgment_by",
            "pdf_links", "parties", "db_id",
            "state_ut", "brief_of_order", "ar_order_no_date",
            "section_number", "act_name",
            "template_type",
            "web_url", "web_title",
        ]:
            val = getattr(s, field_name, None)
            if val is not None and val != "" and val != []:
                d[field_name] = val
        serialized.append(d)
    return serialized


# --- Draft Citation Helpers ---

async def _inject_citations_into_draft(
    query: str, draft: str, citations_text: str, user_language: str = "en",
) -> tuple[str, int]:
    """Preserve full draft and inject real citations from ALL agents.

    Uses Gemini 2.5 Flash for fast citation injection.
    Returns: (enriched_content, tokens_consumed)
    """
    with log_time(log, "Draft citation injection"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_template(
            localize_prompt(DRAFT_SYNTHESIS_PROMPT, user_language)
        )
        chain = prompt | llm

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "draft": draft,
            "citations": citations_text,
        }, timeout=180)  # Citation injection on full draft needs more time

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens("Orchestrator", "inject_citations", response)
    log.info("Citation injection completed",
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content, tokens


async def _auto_cite_draft(
    query: str, draft: str, response_instructions: str = "", user_language: str = "en",
) -> tuple[str, int]:
    """Add AI-generated citations when no database agents provided results.

    Uses Gemini 2.5 Flash for fast auto-citation.
    Returns: (enriched_content, tokens_consumed)
    """
    with log_time(log, "Draft auto-citation"):
        llm = get_drafting_llm()
        prompt = ChatPromptTemplate.from_template(
            localize_prompt(DRAFT_CITATION_PROMPT, user_language)
        )
        chain = prompt | llm

        instructions_text = ""
        if response_instructions:
            instructions_text = f"User's format preferences: {response_instructions}"

        from core.streaming import stream_chain_response
        response = await stream_chain_response(chain, {
            "query": query,
            "draft": draft,
            "response_instructions": instructions_text,
        }, timeout=180)  # Auto-citation on full draft needs more time

    from core.token_tracker import record as _record_tokens
    tokens = _record_tokens("Orchestrator", "auto_cite", response)
    log.info("Auto-citation completed",
             draft_len=len(draft), enriched_len=len(response.content),
             tokens=tokens)
    return response.content, tokens
