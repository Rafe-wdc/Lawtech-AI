"""Pipeline-level citation grounding primitive.

`SourceRegistry` is the single source of truth for what the pipeline actually
retrieved during a request. Every retrieval-side agent (Legislation, Judgment,
SCI_Judgment, Newacts, Scenario, Constitution, Maxim, GST_Judgment) writes
into it before calling its generator LLM. Every downstream LLM call
(generator prompts, orchestrator merge, self_refine critic + refiner) reads
from it so citations, quoted statutory text, and PDF URLs are grounded to
what was actually retrieved — not fabricated from training memory.

The registry rides on `LegalAgentState["source_registry"]` and uses a
LangGraph reducer (`merge_source_registries` below) so parallel agent writes
merge cleanly instead of overwriting each other.

IDs are internal grounding tokens only. The LLM sees them so it can index
into the registry, but they are never emitted to the user — the model writes
normal case names, statutory citations, and clickable PDF markdown links.

See the citation-grounding shared prompt block
(`INDIAN_LEGAL_CITATION_GROUNDING` in `config/prompts.py`) for the exact
discipline the registry enforces at generation time, and the
`unretrieved_citation` category of `CRITIQUE_PROMPT` for the post-generation
safety net.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class RetrievedSource:
    """One authoritative record the pipeline actually retrieved.

    Every field the LLM might legitimately cite from lives here. The
    `canonical_citation` is the exact string a generator should reproduce
    when citing this record; `pdf_urls` are the real, verified links to
    include inline as clickable markdown.
    """
    id: str                              # stable slug e.g. "sci-44015", "leg-148", "hc-<hash>"
    agent: str                           # producer agent name ("SCI_Judgment", "Legislation", ...)
    type: str                            # "sci_judgment" | "judgment" | "legislation" |
                                         # "newacts" | "scenario_web" | "constitution" |
                                         # "maxim" | "gst_judgment"
    canonical_citation: str              # what the LLM should print, e.g.
                                         # "Union of India v. Rajeev Bansal, 2024 INSC 754"
    title: str = ""                      # short display title (party names, section name)
    pdf_urls: list[str] = field(default_factory=list)
    snippet: str = ""                    # 1-2 line excerpt for grounding context
    year: str = ""                       # judgment year / act year — helps disambiguate


class SourceRegistry:
    """Container for the request's retrieved sources.

    A plain dict-backed registry, keyed by `RetrievedSource.id`. Insertion is
    idempotent: `add()` on an existing id overwrites the older record (useful
    when a later retrieval step enriches an earlier metadata-only entry).
    """
    __slots__ = ("_records",)

    def __init__(self, records: dict[str, RetrievedSource] | None = None) -> None:
        self._records = dict(records) if records else {}

    def add(self, source: RetrievedSource) -> None:
        if not source.id:
            return
        self._records[source.id] = source

    def extend(self, sources: Iterable[RetrievedSource]) -> None:
        for s in sources:
            self.add(s)

    def get(self, source_id: str) -> RetrievedSource | None:
        return self._records.get(source_id)

    def all(self) -> list[RetrievedSource]:
        return list(self._records.values())

    def by_type(self, source_type: str) -> list[RetrievedSource]:
        return [s for s in self._records.values() if s.type == source_type]

    def by_agent(self, agent: str) -> list[RetrievedSource]:
        return [s for s in self._records.values() if s.agent == agent]

    def all_pdf_urls(self) -> list[str]:
        urls: list[str] = []
        seen: set[str] = set()
        for s in self._records.values():
            for u in s.pdf_urls:
                if u and u not in seen:
                    urls.append(u)
                    seen.add(u)
        return urls

    def __len__(self) -> int:
        return len(self._records)

    def __bool__(self) -> bool:
        return bool(self._records)

    # ------------------------------------------------------------------
    # Serialization for LLM prompts
    # ------------------------------------------------------------------

    def serialize_for_prompt(self, *, max_records: int = 40) -> str:
        """Compact block for GENERATION prompts.

        One line per record: `id | citation | pdf-url-if-any | snippet-if-any`.
        Capped at `max_records` because generation prompts are token-bounded
        and the top retrievals are what the LLM most needs to see.
        """
        if not self._records:
            return "(no sources retrieved yet — do not cite any case, statute quote, or URL)"

        records = list(self._records.values())[:max_records]
        lines = []
        for r in records:
            pdf = r.pdf_urls[0] if r.pdf_urls else ""
            snippet = r.snippet.replace("\n", " ").strip()
            if len(snippet) > 220:
                snippet = snippet[:217] + "..."
            parts = [r.id, r.canonical_citation or r.title or r.id]
            if pdf:
                parts.append(f"PDF: {pdf}")
            if snippet:
                parts.append(snippet)
            lines.append(" | ".join(parts))
        return "\n".join(lines)

    def serialize_for_critic(self) -> str:
        """Allowed-citation whitelist for the CRITIC prompt.

        The critic uses this to flag any citation, quoted statutory text, or
        PDF URL in the draft response that is NOT traceable to a registry
        entry.
        """
        if not self._records:
            return "(none — the pipeline retrieved no sources for this request)"

        lines = []
        for r in self._records.values():
            row = f"- {r.canonical_citation or r.title or r.id}"
            if r.year:
                row += f"  [year: {r.year}]"
            if r.pdf_urls:
                row += "  [pdf_urls: " + ", ".join(r.pdf_urls) + "]"
            lines.append(row)
        return "\n".join(lines)


def merge_source_registries(
    existing: SourceRegistry | None,
    new: SourceRegistry | None,
) -> SourceRegistry:
    """LangGraph reducer: merge two registries.

    Parallel domain agents each return a partial state containing their own
    registry writes. LangGraph invokes this reducer to merge those partials
    into the running state. Later writes for the same id overwrite earlier
    ones (matches `SourceRegistry.add` semantics).
    """
    if existing is None and new is None:
        return SourceRegistry()
    if existing is None:
        return new if isinstance(new, SourceRegistry) else SourceRegistry(new or {})
    if new is None:
        return existing

    # Handle callers who accidentally pass a raw dict (defensive).
    if not isinstance(existing, SourceRegistry):
        existing = SourceRegistry(existing or {})
    if not isinstance(new, SourceRegistry):
        new = SourceRegistry(new or {})

    merged = SourceRegistry(existing._records)
    for r in new.all():
        merged.add(r)
    return merged


# ----------------------------------------------------------------------
# Convenience adapters — build RetrievedSource records from the existing
# SourceMetadata dataclass so each agent's population code stays tiny.
# ----------------------------------------------------------------------

def source_from_sci(meta: object) -> RetrievedSource | None:
    """Build a registry record from an SCI_Judgment SourceMetadata."""
    db_id = getattr(meta, "db_id", None)
    parties = getattr(meta, "parties", None) or getattr(meta, "title", None) or ""
    if not db_id and not parties:
        return None

    date = getattr(meta, "judgment_date", "") or ""
    case_no = getattr(meta, "case_no", "") or ""
    year = ""
    if date and "-" in date:
        year = date.split("-")[-1] if len(date.split("-")[-1]) == 4 else ""

    citation_parts = [parties]
    if case_no and case_no != "- 0":
        citation_parts.append(case_no)
    if date:
        citation_parts.append(date)
    citation = ", ".join(p for p in citation_parts if p).strip(", ")

    pdf_links = getattr(meta, "pdf_links", []) or []
    pdf_urls = [p.get("url") for p in pdf_links if isinstance(p, dict) and p.get("url")]
    doc_link = getattr(meta, "doc_link", None)
    if doc_link and doc_link not in pdf_urls:
        pdf_urls.insert(0, doc_link)

    contents = getattr(meta, "content", None) or []
    snippet = (contents[0] if isinstance(contents, list) and contents else "") or ""

    return RetrievedSource(
        id=f"sci-{db_id}" if db_id else f"sci-{abs(hash(parties))}",
        agent="SCI_Judgment",
        type="sci_judgment",
        canonical_citation=citation,
        title=parties,
        pdf_urls=pdf_urls,
        snippet=snippet,
        year=year,
    )


def source_from_hc(meta: object) -> RetrievedSource | None:
    """Build a registry record from a High-Court Judgment SourceMetadata."""
    title = getattr(meta, "title", None) or ""
    petitioners = getattr(meta, "petitioner_names", []) or []
    respondents = getattr(meta, "respondent_names", []) or []
    if not title and (petitioners or respondents):
        title = " v. ".join([
            ", ".join(petitioners) if petitioners else "?",
            ", ".join(respondents) if respondents else "?",
        ])
    if not title:
        return None

    year = getattr(meta, "year", None)
    year_str = str(year) if year else ""
    court = getattr(meta, "court_name", "") or ""

    citation_parts = [title]
    if year_str:
        citation_parts.append(f"({year_str})")
    if court:
        citation_parts.append(court)
    citation = " ".join(citation_parts)

    pdf_urls = []
    doc_link = getattr(meta, "doc_link", None)
    if doc_link:
        pdf_urls.append(doc_link)
    web_url = getattr(meta, "web_url", None)
    if web_url and web_url not in pdf_urls:
        pdf_urls.append(web_url)

    contents = getattr(meta, "content", None) or []
    snippet = (contents[0] if isinstance(contents, list) and contents else "") or ""

    stable = title[:60].strip().lower().replace(" ", "_")
    return RetrievedSource(
        id=f"hc-{stable}-{abs(hash(citation)) % 100000}",
        agent="Judgment",
        type="judgment",
        canonical_citation=citation,
        title=title,
        pdf_urls=pdf_urls,
        snippet=snippet,
        year=year_str,
    )


def source_from_legislation(meta: object) -> RetrievedSource | None:
    """Build a registry record from a Legislation / Newacts SourceMetadata."""
    section = getattr(meta, "section_number", None) or ""
    act = getattr(meta, "act_name", None) or getattr(meta, "title", None) or ""
    if not section and not act:
        return None

    citation = f"Section {section}, {act}".strip(" ,") if section else act
    contents = getattr(meta, "content", None) or []
    snippet = (contents[0] if isinstance(contents, list) and contents else "") or ""

    stable = f"{act[:30]}-{section}".strip("- ").lower().replace(" ", "_")
    return RetrievedSource(
        id=f"leg-{stable}-{abs(hash(citation)) % 100000}",
        agent=getattr(meta, "agent_name", "Legislation") or "Legislation",
        type=getattr(meta, "source_type", "legislation") or "legislation",
        canonical_citation=citation,
        title=citation,
        pdf_urls=[],
        snippet=snippet,
        year="",
    )


def source_from_web(meta: object) -> RetrievedSource | None:
    """Build a registry record from a Scenario web-grounding SourceMetadata."""
    title = getattr(meta, "web_title", None) or getattr(meta, "title", None) or ""
    url = getattr(meta, "web_url", None) or ""
    if not title and not url:
        return None

    citation = title or url
    return RetrievedSource(
        id=f"web-{abs(hash(url or title)) % 1000000}",
        agent=getattr(meta, "agent_name", "Scenario") or "Scenario",
        type=getattr(meta, "source_type", "scenario_web") or "scenario_web",
        canonical_citation=citation,
        title=title,
        pdf_urls=[url] if url else [],
        snippet="",
        year="",
    )
