"""Agent #8 — Supreme Court of India Judgment Agent

Searches 44,000+ SC judgments using a ReAct agent with 7 specialized tools.
Unlike other domain agents that use deterministic pipelines, this agent
uses multi-step tool calling (2-4 searches + case detail reads) via
create_react_agent from langgraph.prebuilt.

Handles: "SC cases on right to privacy", "Find Supreme Court judgment by
Justice Chandrachud", "Kesavananda Bharati case details", etc.

Uses: GPT-4o (ReAct reasoning + tool calling)
Data Source: Elasticsearch "supreme_court_judgement" index
"""

from __future__ import annotations

import asyncio
import re

from langchain_core.messages import SystemMessage
from langgraph.prebuilt import create_react_agent

from core.state import LegalAgentState, AgentResult, SourceMetadata
from core.clients import get_gemini_flash_full
from core.language import localize_prompt
from core.logger import get_logger, log_time, short_err
from core.progress import progress
from config.prompts import SCI_JUDGMENT_SYSTEM_PROMPT
from tools.shared import AGENT_TOOLS, search_by_topic

log = get_logger("SCI_Judgment")


# --- case-name lookup gate ----------------------------------------------
# Cost audit 2026-10-08: a case-name lookup ("Laxman B. Kamble vs MHADA")
# whose parties are not in the Supreme Court index still ran the full ReAct
# research loop, 10 to 14 calls and $0.10 to $0.28, and produced surname
# matches. One AND-match on the `parties` field decides it for free.
_CASE_NAME_RE = re.compile(
    r"(?P<pre>(?:[^\s]+\s+){0,5}?[^\s]+)\s+(?:vs?\.?|versus|v\.)\s+(?P<post>[^\s]+(?:\s+[^\s]+){0,5})",
    re.IGNORECASE,
)
# Words that end the petitioner on the left ("Supreme Court judgment in X vs Y")
# and the respondent on the right ("X vs Y 2021 Supreme Court judgment on bail").
_STOP_BEFORE = frozenset((
    "in", "judgment", "judgement", "case", "the", "court", "courts", "supreme", "high", "on",
    "regarding", "ruling", "decision", "by", "matter", "law", "laws", "citation", "cite", "find",
    "search", "about", "please", "me", "give", "show", "order", "for", "latest", "landmark", "a",
))
_STOP_AFTER = frozenset((
    "supreme", "court", "courts", "judgment", "judgement", "on", "regarding", "about", "case", "appeal",
    "under", "section", "sections", "with", "for", "ruling", "decision", "order", "law", "laws",
    "arrest", "guidelines", "principles", "citation", "pdf", "link", "summary", "summarise", "summarize",
))
_PARTY_STOP = frozenset((
    "and", "ors", "anr", "others", "another", "the", "of", "vs", "v", "versus", "shri", "smt",
    "mr", "mrs", "ms", "dr", "m/s", "ltd", "pvt", "co", "sh", "state", "union", "india", "govt",
    "government", "through", "thr", "its", "etc", "anor", "sri", "kum", "ku",
    "judgment", "judgement", "case", "court", "supreme", "high", "ruling", "order", "decision",
    "justice", "honble", "hon", "ble", "mr", "late",
))
# Not party names: statute references and bare numbers.
_NOT_A_PARTY = re.compile(r"(?i)\b(?:section|sections|article|rule|act|ipc|crpc|bns|bnss|bsa|cpc|\d{1,4})\b")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


_GOVT_PARTY_RE = re.compile(r"(?i)^\s*(?:the\s+)?(?:state|union|govt|government|commissioner|collector|secretary|cbi|central bureau|u\.?o\.?i)\b")


def _significant_party_tokens(name: str) -> list[str]:
    toks = re.findall(r"[a-z]{3,}", (name or "").lower())
    return [t for t in toks if t not in _PARTY_STOP]


def _trim_before(words: list[str]) -> list[str]:
    """Keep the words after the last stop word ("Supreme Court judgment in X" -> X)."""
    out: list[str] = []
    for w in words:
        if w.lower().strip(".,:;") in _STOP_BEFORE:
            out = []
        else:
            out.append(w)
    while out and out[0].lower().strip(".,:;") in ("of", "the", "in", "for", "and"):
        out = out[1:]
    return out


def _trim_after(words: list[str]) -> list[str]:
    """Keep the words up to the first stop word or year ("Y 2021 Supreme Court" -> Y)."""
    out: list[str] = []
    for w in words:
        bare = w.strip(".,:;()")
        if bare.lower() in _STOP_AFTER:
            break
        if _YEAR_RE.fullmatch(bare):
            break
        out.append(w)
    return out


def _looks_like_a_name(part: str, has_capitals: bool) -> bool:
    if not part or _NOT_A_PARTY.search(part):
        return False
    if has_capitals and not any(w[:1].isupper() for w in part.split()):
        return False
    return True


def _case_name_lookup(query: str) -> tuple[str, str] | None:
    """Return (petitioner, respondent) when the query contains a case name.

    Works on a bare "X vs Y 2021" and on sentences such as "Supreme Court
    judgment in X vs Y on arrest guidelines" (advocate test 2026-10-09: the
    wrapped form bypassed the gate and ran 17 research calls on a case that
    does not exist). Lowercase topic phrases ("dowry death vs cruelty") and
    statute comparisons ("Section 420 vs Section 318 BNS") are not names and
    are left alone.
    """
    if not query or "\n" in query:
        return None
    m = _CASE_NAME_RE.search(query)
    if not m:
        return None
    pet = " ".join(_trim_before(m.group("pre").split())).strip(" .,:;")
    resp = " ".join(_trim_after(m.group("post").split())).strip(" .,:;")
    has_caps = any(ch.isupper() for ch in query)
    if not (_looks_like_a_name(pet, has_caps) and _looks_like_a_name(resp, has_caps)):
        return None
    if not has_caps and not _GOVT_PARTY_RE.match(resp) and not _GOVT_PARTY_RE.match(pet):
        # "dowry death vs cruelty" / "anticipatory bail vs regular bail": a
        # lowercase pair with no State / Union party is a topic, not a case.
        return None
    if len(pet.split()) > 6 or len(resp.split()) > 6:
        return None
    return pet, resp


def _respondent_key(respondent: str) -> str | None:
    """At most ONE token from the respondent. Trailing topic words in a query
    ("State of Kerala basic structure", "CBI bail guidelines") are not part of
    the name; the index spells government parties many ways ("GOVT.OF U.P.",
    "STATE (NCT OF DELHI)", "CENTRAL BUREAU OF INVESTIGATION"). A government
    party contributes the state name after "of" when it is a real word, else
    nothing; a private party contributes its first significant token."""
    words = [w.strip(".,:;()").lower() for w in (respondent or "").split()]
    if _GOVT_PARTY_RE.match(respondent or ""):
        if "of" in words:
            for w in words[words.index("of") + 1:]:
                if len(w) >= 3 and w not in _PARTY_STOP and w not in ("nct",):
                    return w
                if len(w) >= 3:
                    break
        return None
    toks = _significant_party_tokens(respondent)
    return toks[0] if toks else None


def _other_party_is_government(parties: str, pet_tokens: list[str]) -> bool | None:
    """In an index `parties` string "A VS B", find the side that is NOT the
    petitioner and say whether it is a government party. None when unclear."""
    sides = [x.strip() for x in re.split(r"\s+v(?:s?\.?|ersus)\s+", parties or "", maxsplit=1, flags=re.IGNORECASE)]
    if len(sides) != 2:
        return None
    def has_pet(side):
        low = side.lower()
        return all(t in low for t in pet_tokens)
    other = sides[1] if has_pet(sides[0]) and not has_pet(sides[1]) else sides[0] if has_pet(sides[1]) else None
    if other is None:
        return None
    return bool(_GOVT_PARTY_RE.match(other) or re.search(r"(?i)\b(?:govt|government|state|union of india|u\.?o\.?i|cbi|central bureau|nct)\b", other))


def _party_in_sci_index(petitioner: str, respondent: str) -> tuple[bool | None, list[str]]:
    """(present, related). present is True / False when the index can answer,
    None when it cannot (fail open). related lists up to three real cases that
    share the distinctive party when the title is absent.

    Stage 1: petitioner tokens AND the respondent key. Stage 2 on a miss:
    petitioner tokens alone; if the matching case's other party is the same
    kind as the query's respondent (government vs private) the title is
    treated as present (spelling variants such as "GOVT.OF U.P."); otherwise
    it is a different case and the gate fires with the related list.
    """
    pet, resp = petitioner, respondent
    if _GOVT_PARTY_RE.match(pet or "") and not _GOVT_PARTY_RE.match(resp or ""):
        pet, resp = resp, pet                                   # gate on the private party
    pet_tokens = _significant_party_tokens(pet)
    if not pet_tokens:
        return None, []
    resp_key = _respondent_key(resp)
    resp_is_govt = bool(_GOVT_PARTY_RE.match(resp or ""))
    try:
        from core.clients import get_es_client
        from core.settings import ES_INDICES
        es = get_es_client()
        index = ES_INDICES["sci_judgments"]

        def _hits(tokens, size):
            r = es.search(index=index, body={
                "size": size, "_source": ["parties", "judgment_date", "case_no"],
                "query": {"match": {"parties": {"query": " ".join(tokens), "operator": "and"}}},
            }, request_timeout=15)
            return r["hits"]["hits"]

        if resp_key and _hits(pet_tokens + [resp_key], 1):
            return True, []
        hits = _hits(pet_tokens, 3)
        if not hits:
            # The title is not a party in the index. If the name occurs in the
            # TEXT of other judgments (a landmark case the corpus lacks but later
            # cases cite), research can still ground an answer on those: do not
            # gate. A truly unknown name occurs nowhere and the gate fires.
            r = es.search(index=index, body={"size": 1, "_source": False,
                "query": {"match_phrase": {"full_text": {"query": " ".join(pet_tokens), "slop": 1}}}}, request_timeout=15)
            t = r["hits"]["total"]
            if (t["value"] if isinstance(t, dict) else t) > 0:
                return None, []
            return False, []
        related = []
        for h in hits:
            src = h.get("_source", {})
            parties = (src.get("parties") or "").strip()
            kind = _other_party_is_government(parties, pet_tokens)
            if kind is None or kind == resp_is_govt:
                return True, []                                 # same title, variant spelling
            tail = ", ".join(x for x in (src.get("case_no"), src.get("judgment_date")) if x)
            related.append(parties + (f" ({tail})" if tail else ""))
        return False, related
    except Exception as e:  # noqa: BLE001
        log.warning("Case-name party gate skipped (ES error)", error=short_err(e))
        return None, []


def _not_found_result(petitioner: str, respondent: str, related: list[str] | None = None) -> AgentResult:
    msg = (
        f"No Supreme Court judgment titled \"{petitioner} vs {respondent}\" was found in the "
        "Lawttorney database. The party names may be spelt differently on the record, or the "
        "matter may be a High Court or unreported order. A case number, citation or date would "
        "allow an exact lookup."
    )
    if related:
        msg += "\n\nSupreme Court cases in the database with the same party name:\n" + "\n".join(f"- {r}" for r in related)
    return AgentResult(agent_name="SCI_Judgment", content=msg, sources=[], tokens_consumed=0)


async def sci_judgment_node(state: LegalAgentState) -> dict:
    """Search Supreme Court of India judgments using ReAct agent.

    Flow:
    1. Build ReAct sub-agent with 7 SCI tools + system prompt
    2. Invoke with user query (agent decides which tools to call)
    3. Agent performs multi-step research (2-4 searches + case reads)
    4. Extract final response and wrap in AgentResult
    """
    agent_queries = state.get("agent_queries", {})
    # Prefer agent-specific query > normalized English query > original (for multilingual support)
    query = agent_queries.get("SCI_Judgment", state.get("query", state.get("original_query", "")))
    user_context = state.get("user_context", "")
    user_language = state.get("user_language", "en")
    log.info("Agent started", query=query[:100],
             has_user_context=bool(user_context),
             using_agent_query="SCI_Judgment" in agent_queries)
    # For long queries: include pasted content for the ReAct agent
    if user_context:
        query = f"User's document/context:\n{user_context}\n\nUser's question:\n{query}"

    try:
        # Build ReAct agent with SCI tools
        progress("sci_judgment", "Preparing Supreme Court search...", step="prepare")
        _lookup = None if user_context else (
            _case_name_lookup(state.get("original_query") or "") or _case_name_lookup(query)
        )
        if _lookup:
            _present, _related = await asyncio.to_thread(_party_in_sci_index, *_lookup)
            if _present is False:
                log.info("Case-name lookup: parties absent from SCI index, skipping research",
                         petitioner=_lookup[0][:60], respondent=_lookup[1][:60])
                progress("sci_judgment", "No matching Supreme Court case found", step="results")
                return {"agent_results": {"SCI_Judgment": _not_found_result(*_lookup, related=_related)}}
        # max_output_tokens capped at 8192 (~32k chars) to bound the ReAct
        # agent's final response and prevent runaway markdown-table padding.
        llm = get_gemini_flash_full(temperature=0, max_output_tokens=8192)
        tools = AGENT_TOOLS["sci_judgment"]
        log.debug("Building ReAct agent", tools_count=len(tools))

        # Gap #1: append uploaded-document text to the ReAct agent's system
        # prompt so SCI_Judgment sees the user's matter (upload SCI order +
        # ask "cases citing this?" — the order text now influences retrieval
        # tool selection). Empty when no files attached — flow unchanged.
        from core.state import FileContextData as _FileContextData
        from core.file_context import format_file_context_prefix as _fmt_files
        _sci_system_prompt = localize_prompt(
            SCI_JUDGMENT_SYSTEM_PROMPT,
            user_language,
            state.get("user_intent"),
        )
        _file_prefix = _fmt_files(_FileContextData.from_state(state))
        if _file_prefix:
            _sci_system_prompt = _sci_system_prompt + "\n\n" + _file_prefix
            log.info("SCI_Judgment: uploaded file context injected",
                     prefix_len=len(_file_prefix))
        agent = create_react_agent(
            llm,
            tools,
            prompt=SystemMessage(content=_sci_system_prompt),
        )

        # Invoke the ReAct sub-agent. Bounded because a hung ReAct
        # trajectory can otherwise consume the whole 180-300s gateway
        # budget with no local ceiling.
        progress("sci_judgment", "Running multi-step research (ReAct agent)...", step="react")
        with log_time(log, "ReAct agent execution"):
            result = await asyncio.wait_for(
                agent.ainvoke({"messages": [("user", query)]}),
                timeout=90,
            )

        # Extract final AI message content and tools used
        messages = result.get("messages", [])
        answer = ""
        tools_used = []
        sources = []

        for msg in messages:
            # Track tool calls
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name:
                        tools_used.append(name)

            # Get the last AI message with content as the answer
            if hasattr(msg, "type") and msg.type == "ai" and msg.text:
                raw = msg.text
                # Gemini may return content as list of parts; normalize to string
                if isinstance(raw, list):
                    answer = "\n".join(
                        p.get("text", str(p)) if isinstance(p, dict) else str(p)
                        for p in raw
                    )
                else:
                    answer = raw

        if not answer and messages:
            last_msg = messages[-1]
            if hasattr(last_msg, "content") and last_msg.text:
                raw = last_msg.text
                if isinstance(raw, list):
                    answer = "\n".join(
                        p.get("text", str(p)) if isinstance(p, dict) else str(p)
                        for p in raw
                    )
                else:
                    answer = raw

        if tools_used:
            progress("sci_judgment", f"Completed {len(tools_used)} search steps", found=len(tools_used), substep=True, step="react")
        log.info("ReAct agent completed",
                 tools_used=tools_used, tool_calls=len(tools_used),
                 response_len=len(answer),
                 total_messages=len(messages))

        # Fallback: if ReAct agent didn't call any tools, force a semantic search
        if not tools_used:
            log.warning("ReAct agent returned 0 tool calls, forcing semantic search fallback",
                        query=query[:100])
            try:
                fallback_result = await asyncio.to_thread(
                    search_by_topic.invoke, {"query": query}
                )
                if fallback_result and "No matching" not in fallback_result:
                    # Parse sources from the direct fallback result
                    fallback_text = fallback_result
                    case_blocks = re.split(r'\n\n(?=\*\*)', fallback_text)
                    for block in case_blocks:
                        if not block.strip() or "No matching" in block:
                            continue
                        parties_m = re.search(r'\*\*(.+?)\*\*\s*\(DB ID:\s*(\S+)\)', block)
                        case_no_m = re.search(r'Case No:\s*(.+)', block)
                        date_m = re.search(r'Date:\s*(.+)', block)
                        bench_m = re.search(r'Bench:\s*(.+)', block)
                        judge_m = re.search(r'Judgment By:\s*(.+)', block)
                        pdf_m = re.search(r'PDF:\s*(https?://\S+)', block)
                        if parties_m:
                            p_title = parties_m.group(1).strip()
                            p_dbid = parties_m.group(2).strip()
                            pdf_url = pdf_m.group(1).strip() if pdf_m else None
                            sources.append(SourceMetadata(
                                source_type="sci_judgment",
                                title=p_title,
                                db_id=p_dbid,
                                parties=p_title,
                                case_no=case_no_m.group(1).strip() if case_no_m else None,
                                judgment_date=date_m.group(1).strip() if date_m else None,
                                bench=bench_m.group(1).strip() if bench_m else None,
                                judgment_by=judge_m.group(1).strip() if judge_m else None,
                                doc_link=pdf_url,
                                pdf_links=[{"label": "Judgment PDF", "url": pdf_url}] if pdf_url and pdf_url != "N/A" else [],
                                agent_name="SCI_Judgment",
                            ))
                    log.info("Fallback sources parsed", source_count=len(sources))

                    # Re-invoke ReAct with the search results as context
                    with log_time(log, "ReAct retry with fallback results"):
                        retry_result = await asyncio.wait_for(
                            agent.ainvoke(
                                {"messages": [
                                    ("user", query),
                                    ("assistant", f"I searched for cases and found these results:\n\n{fallback_result}\n\nLet me present these findings to the user."),
                                ]}
                            ),
                            timeout=60,
                        )
                    retry_messages = retry_result.get("messages", [])
                    # Fold the retry trajectory into `messages` so the token-
                    # summing loop below feeds BOTH ReAct calls into the
                    # tracker. Without this, retry LLM calls were silently
                    # dropped from `token_usage.total_tokens`, under-charging
                    # the user's wallet on every fallback-triggered request.
                    messages = list(messages) + list(retry_messages)
                    answer = ""
                    tools_used = ["search_by_topic (fallback)"]
                    for msg in retry_messages:
                        if hasattr(msg, "type") and msg.type == "ai" and msg.text:
                            answer = msg.text
                    if not answer:
                        answer = f"Here are relevant Supreme Court cases found:\n\n{fallback_result}"
                    log.info("Fallback search completed",
                             response_len=len(answer))
            except Exception as fb_err:
                log.error("Fallback search failed", error=short_err(fb_err))

        # Sum token usage across the ReAct trajectory's AI messages
        from core.token_tracker import record as _record_tokens
        tokens = 0
        for i, msg in enumerate(messages):
            tokens += _record_tokens("SCI_Judgment", f"react_msg_{i}", msg)

        # Parse tool response messages to extract structured case data
        for msg in messages:
            if hasattr(msg, "type") and msg.type == "tool" and msg.text:
                text = msg.text
                # Split on case headers: **Parties** (DB ID: 123)
                case_blocks = re.split(r'\n\n(?=\*\*)', text)
                for block in case_blocks:
                    if not block.strip() or "No matching" in block:
                        continue
                    parties_m = re.search(r'\*\*(.+?)\*\*\s*\(DB ID:\s*(\S+)\)', block)
                    case_no_m = re.search(r'Case No:\s*(.+)', block)
                    date_m = re.search(r'Date:\s*(.+)', block)
                    bench_m = re.search(r'Bench:\s*(.+)', block)
                    judge_m = re.search(r'Judgment By:\s*(.+)', block)
                    pdf_m = re.search(r'PDF:\s*(https?://\S+)', block)

                    if parties_m:
                        p_title = parties_m.group(1).strip()
                        p_dbid = parties_m.group(2).strip()
                        pdf_url = pdf_m.group(1).strip() if pdf_m else None
                        sources.append(SourceMetadata(
                            source_type="sci_judgment",
                            title=p_title,
                            db_id=p_dbid,
                            parties=p_title,
                            case_no=case_no_m.group(1).strip() if case_no_m else None,
                            judgment_date=date_m.group(1).strip() if date_m else None,
                            bench=bench_m.group(1).strip() if bench_m else None,
                            judgment_by=judge_m.group(1).strip() if judge_m else None,
                            doc_link=pdf_url,
                            pdf_links=[{"label": "Judgment PDF", "url": pdf_url}] if pdf_url and pdf_url != "N/A" else [],
                            agent_name="SCI_Judgment",
                        ))

        # Deduplicate by db_id
        seen_ids = set()
        unique_sources = []
        for s in sources:
            if s.db_id and s.db_id in seen_ids:
                continue
            if s.db_id:
                seen_ids.add(s.db_id)
            unique_sources.append(s)
        sources = unique_sources

        if sources:
            progress("sci_judgment", f"Found {len(sources)} Supreme Court judgments", found=len(sources), step="results")
        progress("sci_judgment", "Generating response...", step="generate")
        log.info("Sources parsed from tool messages",
                 source_count=len(sources))

        # Web-search last-resort. The SC corpus is broad but not
        # exhaustive; when the ReAct trajectory returns empty content
        # and topic-search also produced nothing, fall to Gemini +
        # Google Search grounding so the user sees a scoped answer with
        # citations instead of a blank content field (which then trips
        # the orchestrator's all-empty branch → generic web fallback).
        if (not answer or not answer.strip()) and not sources:
            progress("sci_judgment",
                     "No corpus results — falling back to web search...",
                     step="fallback", substep=True)
            try:
                from core.agent_fallback import web_search_fallback
                # RAW prompt — web_search_fallback localizes the assembled
                # prompt itself, so localizing here too just duplicates the
                # (long) directive block.
                fb = await web_search_fallback(
                    query,
                    "SCI_Judgment",
                    SCI_JUDGMENT_SYSTEM_PROMPT,
                    user_language=user_language,
                    intent=state.get("user_intent"),
                )
                if fb.content:
                    log.info("SCI web fallback produced answer",
                             answer_len=len(fb.content),
                             fb_sources=len(fb.sources or []))
                    result = AgentResult(
                        agent_name="SCI_Judgment",
                        content=fb.content,
                        sources=fb.sources or [],
                        tokens_consumed=(tokens or 0) + (fb.tokens_consumed or 0),
                        fallback_used=True,
                    )
                    return {"agent_results": {"SCI_Judgment": result}}
            except Exception as web_err:
                log.warning("SCI web fallback failed",
                            error=short_err(web_err))

        result = AgentResult(
            agent_name="SCI_Judgment",
            content=answer,
            sources=sources,
            tokens_consumed=tokens,
        )

    except Exception as e:
        from core.metrics import record_agent_error
        record_agent_error("SCI_Judgment", e)
        log.error("Agent failed", error=short_err(e), exc_info=True)
        result = AgentResult(
            agent_name="SCI_Judgment",
            content="",
            sources=[],
            tokens_consumed=0,
            error=short_err(e),
        )

    return {"agent_results": {"SCI_Judgment": result}}
