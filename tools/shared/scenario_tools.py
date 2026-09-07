"""Shared Tools: Scenario and web-grounded analysis operations.

Reusable @tool functions for web-grounded legal analysis, scenario evaluation,
provision citation, case finding, remedy suggestions, and legal news.

Used by the Scenario Agent (#8) for situational legal analysis.

Uses: Gemini 2.5 Pro with Google Search grounding, GPT-4o for structured extraction
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from langchain.tools import tool
from langchain_core.prompts import ChatPromptTemplate

from core.clients import get_genai_client, get_gemini_flash_full
from core.settings import GEMINI_MODELS, TIMEOUT_WEB_SEARCH_SEC
from core.logger import get_logger
from config.prompts import SCENARIO_SYSTEM_PROMPT

log = get_logger("Scenario")

_GENAI_TIMEOUT = int(TIMEOUT_WEB_SEARCH_SEC)  # seconds for Gemini API calls


def _safe_text(response) -> str:
    """Null-safe text extraction from Gemini response."""
    if not response.candidates or not response.candidates[0].content.parts:
        return ""
    return getattr(response.candidates[0].content.parts[0], "text", None) or ""


# --- Structured Output Schemas ---

class ProvisionCitation(BaseModel):
    act_name: str = Field(..., description="Name of the act/statute")
    section: str = Field(..., description="Section/Article number")
    relevance: str = Field(..., description="Why this provision is relevant")


class CaseSummary(BaseModel):
    case_name: str = Field(..., description="Case title (Petitioner vs Respondent)")
    court: str = Field(..., description="Court name")
    year: str = Field(..., description="Year of judgment")
    key_holding: str = Field(..., description="Key principle or holding")


class RemedySuggestion(BaseModel):
    remedy: str = Field(..., description="Legal remedy available")
    steps: list[str] = Field(default_factory=list, description="Steps to pursue this remedy")
    timeline: str = Field(default="", description="Expected timeline")


class ProvisionsResult(BaseModel):
    provisions: list[ProvisionCitation] = Field(default_factory=list)


class CasesResult(BaseModel):
    cases: list[CaseSummary] = Field(default_factory=list)


class RemediesResult(BaseModel):
    remedies: list[RemedySuggestion] = Field(default_factory=list)


# --- Tool Functions ---

@tool
def web_search_grounded(query: str, chat_history_text: str = "") -> dict:
    """Perform web-grounded legal analysis using Gemini 2.5 Pro with Google Search.

    Invokes Gemini with the Google Search tool for real-time legal information,
    case law references, and verified legal content from authorized sources.

    Args:
        query: The legal query to analyze with web grounding
        chat_history_text: Previous conversation context (optional)

    Returns:
        Dict with keys: content (str), tokens_consumed (int)
    """
    try:
        # Build prompt with system instructions and history
        parts = [SCENARIO_SYSTEM_PROMPT]
        if chat_history_text:
            parts.append(f"\nPrevious conversation:\n{chat_history_text}")
        parts.append(f"\n\n{query}")
        full_prompt = "\n".join(parts)

        client = get_genai_client()
        response = client.models.generate_content(
            model=GEMINI_MODELS["flash"],
            contents=[full_prompt],
            config={
                "tools": [{"google_search": {}}],
                "max_output_tokens": 8000,
                "temperature": 0.5,
                "top_p": 0.95,
                "http_options": {"timeout": _GENAI_TIMEOUT * 1000},
            },
        )

        content = _safe_text(response)
        if not content:
            log.warning("Web search grounded returned empty response")
        tokens = getattr(response.usage_metadata, "total_token_count", 0)

        return {"content": content, "tokens_consumed": tokens}

    except Exception as e:
        log.error(f"Web search grounded failed: {e}")
        return {"content": "", "tokens_consumed": 0}


@tool
def analyze_scenario(query: str, chat_history_text: str = "") -> dict:
    """Analyze a legal scenario and provide comprehensive legal advice.

    Uses Gemini 2.5 Pro with Google Search to provide deep legal analysis
    of situational queries, covering applicable laws, precedents, and remedies.

    Args:
        query: The user's situational legal query
        chat_history_text: Previous conversation context (optional)

    Returns:
        Dict with keys: analysis (str), tokens_consumed (int)
    """
    scenario_prompt = f"""Analyze this legal scenario comprehensively:

1. **Applicable Laws**: Identify all relevant Indian statutes and sections
2. **Legal Position**: Analyze the legal rights and obligations of each party
3. **Precedents**: Reference relevant case laws if applicable
4. **Remedies**: Suggest available legal remedies with step-by-step guidance
5. **Practical Advice**: Provide actionable next steps

Scenario: {query}"""

    try:
        client = get_genai_client()
        parts = [SCENARIO_SYSTEM_PROMPT]
        if chat_history_text:
            parts.append(f"\nPrevious conversation:\n{chat_history_text}")
        parts.append(f"\n\n{scenario_prompt}")

        response = client.models.generate_content(
            model=GEMINI_MODELS["flash"],
            contents=["\n".join(parts)],
            config={
                "tools": [{"google_search": {}}],
                "max_output_tokens": 8000,
                "temperature": 0.5,
                "top_p": 0.95,
                "http_options": {"timeout": _GENAI_TIMEOUT * 1000},
            },
        )

        content = _safe_text(response)
        if not content:
            log.warning("Scenario analysis returned empty response")
        tokens = getattr(response.usage_metadata, "total_token_count", 0)

        return {"analysis": content, "tokens_consumed": tokens}

    except Exception as e:
        log.error(f"Analysis failed: {e}")
        return {"analysis": "", "tokens_consumed": 0}


@tool
def cite_provisions(scenario: str) -> dict:
    """Identify and cite relevant legal provisions for a scenario.

    Uses GPT-4o to extract specific acts, sections, and articles
    that apply to the given legal scenario.

    Args:
        scenario: Description of the legal scenario or situation

    Returns:
        Dict with keys: provisions (list of {act_name, section, relevance})
    """
    try:
        llm = get_gemini_flash_full(temperature=0.2).with_structured_output(ProvisionsResult)
        prompt = ChatPromptTemplate.from_template(
            "You are an Indian legal expert. Identify ALL relevant legal provisions "
            "(acts, sections, articles) applicable to this scenario.\n\n"
            "For each provision, specify:\n"
            "- act_name: Full name of the act/statute\n"
            "- section: Section/Article number\n"
            "- relevance: Why this provision applies\n\n"
            "Scenario: {scenario}\n\n"
            "List the most relevant provisions (max 10)."
        )
        chain = prompt | llm
        result = chain.invoke({"scenario": scenario})
        return {
            "provisions": [
                {"act_name": p.act_name, "section": p.section, "relevance": p.relevance}
                for p in result.provisions
            ]
        }
    except Exception as e:
        log.error(f"Provision citation failed: {e}")
        return {"provisions": []}


@tool
def find_similar_cases(scenario: str) -> dict:
    """Find similar court cases relevant to a legal scenario.

    Uses Gemini Pro with Google Search to find real Indian court cases
    that are similar to the described legal scenario.

    Args:
        scenario: Description of the legal scenario to find similar cases for

    Returns:
        Dict with keys: cases (list of {case_name, court, year, key_holding})
    """
    try:
        client = get_genai_client()
        prompt = (
            "You are an Indian legal research expert. Find real, verifiable Indian court cases "
            "similar to this legal scenario. Only cite cases you are confident exist.\n\n"
            f"Scenario: {scenario}\n\n"
            "For each case, provide:\n"
            "- Case name (Petitioner vs Respondent)\n"
            "- Court (Supreme Court, High Court name, etc.)\n"
            "- Year of judgment\n"
            "- Key holding or principle established\n\n"
            "List up to 5 most relevant cases. If uncertain about a case, omit it."
        )

        response = client.models.generate_content(
            model=GEMINI_MODELS["flash"],
            contents=[prompt],
            config={
                "tools": [{"google_search": {}}],
                "max_output_tokens": 4000,
                "temperature": 0.3,
                "http_options": {"timeout": _GENAI_TIMEOUT * 1000},
            },
        )

        content = _safe_text(response)
        if not content:
            log.warning("Find similar cases returned empty response")
            return {"cases": [], "raw_response": "", "tokens_consumed": 0}

        # Try to parse structured cases from the response
        try:
            llm = get_gemini_flash_full(temperature=0.0).with_structured_output(CasesResult)
            result = llm.invoke(
                f"Extract the case citations from this text into structured format:\n\n{content}"
            )
            return {
                "cases": [
                    {"case_name": c.case_name, "court": c.court, "year": c.year, "key_holding": c.key_holding}
                    for c in result.cases
                ]
            }
        except Exception:
            return {"cases": [], "raw_response": content}

    except Exception as e:
        log.error(f"Case finding failed: {e}")
        return {"cases": []}


@tool
def suggest_remedies(scenario: str) -> dict:
    """Suggest available legal remedies for a given scenario.

    Analyzes the scenario and provides actionable remedies with
    step-by-step guidance and expected timelines.

    Args:
        scenario: Description of the legal situation requiring remedies

    Returns:
        Dict with keys: remedies (list of {remedy, steps, timeline})
    """
    try:
        llm = get_gemini_flash_full(temperature=0.3).with_structured_output(RemediesResult)
        prompt = ChatPromptTemplate.from_template(
            "You are an Indian legal advisor. Suggest all available legal remedies "
            "for this scenario. For each remedy, provide:\n"
            "- remedy: Name/description of the legal remedy\n"
            "- steps: Step-by-step actions to pursue this remedy\n"
            "- timeline: Expected timeline for this remedy\n\n"
            "Consider both civil and criminal remedies where applicable.\n\n"
            "Scenario: {scenario}\n\n"
            "List remedies in order of recommended priority."
        )
        chain = prompt | llm
        result = chain.invoke({"scenario": scenario})
        return {
            "remedies": [
                {"remedy": r.remedy, "steps": r.steps, "timeline": r.timeline}
                for r in result.remedies
            ]
        }
    except Exception as e:
        log.error(f"Remedy suggestion failed: {e}")
        return {"remedies": []}


@tool
def get_legal_news(topic: str) -> dict:
    """Search for latest legal news and updates on a topic.

    Uses Gemini with Google Search grounding to find recent legal
    developments, amendments, notifications, and news from
    authorized Indian legal sources.

    Args:
        topic: The legal topic to search news for (e.g. "right to privacy", "BNSS amendments")

    Returns:
        Dict with keys: news (str - formatted news summary), tokens_consumed (int)
    """
    try:
        client = get_genai_client()
        prompt = (
            f"Find the latest legal news, amendments, and developments regarding: {topic}\n\n"
            "Focus on:\n"
            "- Recent Supreme Court or High Court judgments\n"
            "- Legislative amendments or notifications\n"
            "- Important legal developments\n\n"
            "Only cite from authoritative sources:\n"
            "indiankanoon.org, barandbench.com, prsindia.org, legislative.gov.in, "
            "livelaw.in, scobserver.in, supremecourtofindia.nic.in\n\n"
            "Format as a structured news summary with dates and source references."
        )

        response = client.models.generate_content(
            model=GEMINI_MODELS["flash"],
            contents=[prompt],
            config={
                "tools": [{"google_search": {}}],
                "max_output_tokens": 4000,
                "temperature": 0.3,
                "http_options": {"timeout": _GENAI_TIMEOUT * 1000},
            },
        )

        content = _safe_text(response)
        tokens = getattr(response.usage_metadata, "total_token_count", 0)

        return {"news": content, "tokens_consumed": tokens}

    except Exception as e:
        log.error(f"Legal news search failed: {e}")
        return {"news": "", "tokens_consumed": 0}
