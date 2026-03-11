# Multilingual Indian Language Support — Implementation Plan

> Status: Phase 1 ✅ | Phase 2 ✅ | Phase 3 ✅ | Phase 4 ✅ | Complete 2026-03-11

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Architecture Decision](#2-architecture-decision)
3. [Supported Languages](#3-supported-languages)
4. [Phase 1 — Detection + Response Language](#4-phase-1--detection--response-language)
5. [Phase 2 — Query Normalization Expansion](#5-phase-2--query-normalization-expansion)
6. [Phase 3 — Drafting in Regional Languages](#6-phase-3--drafting-in-regional-languages)
7. [Phase 4 — Frontend Language Picker](#7-phase-4--frontend-language-picker)
8. [What NOT to Do](#8-what-not-to-do)
9. [Files Changed Per Phase](#9-files-changed-per-phase)

---

## 1. Current State

### What Exists (Partial, Hindi-only)

| Component | Current Behavior | Problem |
|-----------|-----------------|---------|
| `QUERY_NORMALIZE_PROMPT` | Translates Hindi/Hinglish → English before ES search | Only mentions Hindi — 21 other Indian languages ignored |
| Synthesis prompt | "if user asked in Hindi, respond in bilingual format" | Plain text hint — agents don't reliably follow it |
| Language detection | None — guessed inside LLM prompt | Unreliable, no structured `user_language` in state |
| Drafting | Always generated in English | Ignores user's language preference |
| Thread memory | No language preference stored | User must re-indicate language every turn |

### Conclusion

The system has the right skeleton (translate-in, process-in-English) but is incomplete, unreliable, and Hindi-only.

---

## 2. Architecture Decision

### Chosen: Translate-In → Process in English → Generate-Out in User Language

```
User Input (any Indian language)
        │
        ▼
   Language Detection (langdetect + heuristics)
        │  user_language = "ta" (Tamil)
        ▼
   Translate query → English  (Gemini Flash Lite, legal-aware)
        │  query = "How to file bail application under Section 483 BNSS?"
        ▼
   Elasticsearch / ChromaDB (English indices — unchanged)
        │  results in English
        ▼
   LLM Generation with language instruction injected into system prompt
        │  "Respond in Tamil. Legal citations remain in English."
        ▼
   Response in Tamil (with English citations)
```

### Why NOT full multilingual Elasticsearch

- All 7 ES indices are indexed in English — re-indexing requires weeks + parallel infra
- Gemini 2.5 Flash/Pro natively handles all 22 scheduled Indian languages at production quality
- Legal terminology (Section 302 IPC, Article 21, case citations) is universal regardless of response language
- Minimal pipeline changes, maximum language coverage

### Why NOT a separate Translation API (Google Translate / DeepL)

- Gemini already handles legal-context translation better (preserves section numbers, party names, act titles)
- Avoids extra API cost and latency hop
- Single model handles detect + translate + generate

---

## 3. Supported Languages

| Priority | Language | ISO Code | Script | Notes |
|----------|----------|----------|--------|-------|
| P1 | English | `en` | Latin | Current default |
| P1 | Hindi | `hi` | Devanagari + Roman | Already partially supported |
| P2 | Bengali | `bn` | Bengali | 2nd largest Indian language |
| P2 | Telugu | `te` | Telugu | 3rd largest |
| P2 | Marathi | `mr` | Devanagari | 4th largest |
| P2 | Tamil | `ta` | Tamil | 5th largest |
| P3 | Kannada | `kn` | Kannada | |
| P3 | Malayalam | `ml` | Malayalam | |
| P3 | Gujarati | `gu` | Gujarati | |
| P3 | Punjabi | `pa` | Gurmukhi | |
| P4 | Urdu | `ur` | Nastaliq (RTL) | Needs RTL CSS in frontend |
| P4 | Odia | `or` | Odia | |
| P4 | Assamese | `as` | Bengali script | |
| P4 | Konkani | `kok` | Devanagari | |
| P4 | Sindhi | `sd` | Devanagari / Perso-Arabic | |
| P4 | Sanskrit | `sa` | Devanagari | Legal maxims |

---

## 4. Phase 1 — Detection + Response Language

**Status: In Progress**
**Effort: ~1 day**
**Risk: Low**

### What Changes

#### `requirements.txt`
```
langdetect==1.0.9
```

#### `core/language.py` (NEW FILE)
```python
SUPPORTED_LANGUAGES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali",
    "te": "Telugu", "mr": "Marathi", "ta": "Tamil",
    "kn": "Kannada", "ml": "Malayalam", "gu": "Gujarati",
    "pa": "Punjabi", "ur": "Urdu", "or": "Odia",
    "as": "Assamese",
}

# Romanized Indian language keywords (Hinglish + other Roman-script usage)
ROMANIZED_HINTS = {
    "hi": ["kaise", "kya", "karo", "karein", "batao", "dijiye", "chahiye",
           "lagta", "hoga", "wala", "mujhe", "humko", "mere", "hamare",
           "iska", "uska", "yeh", "woh", "bhi", "aur", "lekin", "kyunki"],
    "mr": ["kasa", "aahe", "sangaa", "karayche", "mhanje"],
    "ta": ["eppadi", "enna", "sollunga", "pannum", "irukku"],
    "te": ["ela", "cheyali", "cheppandi", "undi", "kavali"],
}

def detect_language(text: str) -> str:
    """Detect language. Returns ISO 639-1 code, defaults to 'en'."""
    # 1. langdetect fast path
    # 2. Romanized script heuristic fallback
    # 3. Default to 'en'

def localize_prompt(base_prompt: str, lang: str) -> str:
    """Append language instruction to any system prompt."""
    if lang == "en":
        return base_prompt
    name = SUPPORTED_LANGUAGES.get(lang, "English")
    return (base_prompt +
            f"\n\nIMPORTANT: Respond in {name}. "
            "All legal citations (case names, section numbers, act titles, "
            "court names) must remain in English.")
```

#### `core/state.py`
```python
# Add to LegalAgentState:
user_language: str  # ISO 639-1 code, default "en"
```

#### `agents/memory.py`
```python
# After expand_abbreviations(), before query rewrite:
from core.language import detect_language
detected_lang = detect_language(state["original_query"])
# Store in state, persist per thread
```

#### All agent files (legislation, judgment, sci_judgment, newacts, scenario, constitution, drafting, document)
```python
# In each agent's system prompt build:
from core.language import localize_prompt
lang = state.get("user_language", "en")
system_prompt = localize_prompt(BASE_SYSTEM_PROMPT, lang)
```

#### `config/prompts.py` — synthesis prompt
```python
# Replace hardcoded Hindi hint with dynamic instruction
# Old: "9. If the user asked in Hindi/Hinglish, respond in bilingual format"
# New: instruction injected via localize_prompt() at call time
```

### Acceptance Criteria

- [ ] Tamil query → Tamil response with English citations
- [ ] Bengali query → Bengali response
- [ ] Hindi query → Hindi response (existing behavior preserved)
- [ ] English query → English response (no regression)
- [ ] Hinglish (Roman script) → Hindi response
- [ ] Language persists across turns in the same thread
- [ ] `user_language` visible in state debug logs

---

## 5. Phase 2 — Query Normalization Expansion

**Status: Planned**
**Effort: ~1 day**
**Depends on: Phase 1 complete**

### What Changes

#### `QUERY_NORMALIZE_PROMPT` in `agents/orchestrator.py`
- List all 22 scheduled Indian languages explicitly (not just Hindi/Hinglish)
- Add instruction: preserve section numbers, act names, party names verbatim
- Separate concerns: language detection OUT of this prompt (Phase 1 handles it)
- Add Romanized script handling: "bail kaise file kare" → "How to file bail application"

#### `agents/orchestrator.py` — per-agent query rewriting
- Pass `user_language` to per-agent rewrite prompts
- Agent queries always rewritten to English regardless of source language
- Verify legal terms are not mistranslated (e.g., "धारा 302" → "Section 302 IPC")

### Acceptance Criteria

- [ ] "धारा 302 भारतीय दंड संहिता के तहत जमानत" → correct ES query for bail under Section 302 IPC
- [ ] Tamil legal query correctly extracts section numbers in English
- [ ] "ஐபிசி பிரிவு 420" (IPC Section 420 in Tamil) → ES query with "Section 420 IPC"
- [ ] Romanized: "bail ke liye application" → proper English ES query

---

## 6. Phase 3 — Drafting in Regional Languages

**Status: Planned**
**Effort: ~2 days**
**Depends on: Phase 2 complete**

### What Changes

#### `agents/drafting.py`
- Pass `user_language` to outline generation
- `DRAFTING_SYSTEM_PROMPT`: body sections in regional language, citations in English
- Court headers: pre-translated for major languages (P1/P2)

#### Court Header Translations (P1 + P2 languages)

| Language | "IN THE HON'BLE HIGH COURT OF..." |
|----------|----------------------------------|
| Hindi | माननीय उच्च न्यायालय... |
| Bengali | মাননীয় উচ্চ আদালত... |
| Tamil | மதிப்பிற்குரிய உயர் நீதிமன்றம்... |
| Telugu | గౌరవనీయ హైకోర్టు... |
| Marathi | मा. उच्च न्यायालय... |

#### Handling Mixed Content
- Section titles → regional language
- Legal citations (`State v. Ram Kumar, (2024) 3 SCC 145`) → always English
- "Respectfully Showeth" / "Prayer" → regional language
- Cause title (party names) → original names + regional transliteration

### Acceptance Criteria

- [ ] Hindi bail application has Hindi section titles + English citations
- [ ] Tamil petition structured correctly with Tamil prose
- [ ] Party names preserved correctly (not translated)
- [ ] Court names in English (e.g., "High Court of Karnataka" not translated)

---

## 7. Phase 4 — Frontend Language Picker

**Status: Planned**
**Effort: ~1 day**
**Depends on: Phase 1 complete**

### What Changes

#### `frontend.html`
```html
<!-- Language selector in chat input area -->
<select id="langSelect" title="Response language">
  <option value="auto">🌐 Auto-detect</option>
  <option value="en">English</option>
  <option value="hi">हिन्दी</option>
  <option value="bn">বাংলা</option>
  <option value="te">తెలుగు</option>
  <option value="mr">मराठी</option>
  <option value="ta">தமிழ்</option>
  <option value="kn">ಕನ್ನಡ</option>
  <option value="ml">മലയാളം</option>
  <option value="gu">ગુજરાતી</option>
  <option value="pa">ਪੰਜਾਬੀ</option>
  <option value="ur">اردو</option>
</select>
```

#### `core/gateway.py`
```python
# Request body: preferred_language (optional)
# If provided → override detected language
# Store in thread metadata
```

#### RTL Support (Urdu)
```css
/* frontend.html */
.msg.ai[data-lang="ur"] { direction: rtl; text-align: right; font-family: "Noto Nastaliq Urdu", serif; }
```

### Acceptance Criteria

- [ ] Language selector visible in chat UI
- [ ] Selecting Tamil → response in Tamil even if query typed in English
- [ ] "Auto-detect" correctly identifies language
- [ ] Urdu text renders right-to-left
- [ ] Language preference persists across turns (stored in sessionStorage + thread)

---

## 8. What NOT to Do

| Idea | Why Not |
|------|---------|
| Re-index Elasticsearch in regional languages | Weeks of work, requires parallel infra, not needed |
| Use Google Translate API / DeepL | Extra cost, extra latency, worse legal context preservation |
| Translate legal citations (case names, sections) | Must stay in English — universal across Indian courts |
| Force one language per entire session | Users mix English legal terms with regional context naturally |
| Hardcode translations in prompts | Brittle, doesn't scale to 22 languages |
| Use separate multilingual embedding models | Current BM25 + kNN on English indices works fine |

---

## 9. Files Changed Per Phase

### Phase 1

| File | Change Type | Description |
|------|------------|-------------|
| `requirements.txt` | Add dependency | `langdetect==1.0.9` |
| `core/language.py` | **NEW** | Language detection + `localize_prompt()` |
| `core/state.py` | Modify | Add `user_language: str` field |
| `agents/memory.py` | Modify | Detect language, store in state |
| `agents/legislation.py` | Modify | Inject language instruction into system prompt |
| `agents/judgment.py` | Modify | Inject language instruction |
| `agents/sci_judgment.py` | Modify | Inject language instruction |
| `agents/newacts.py` | Modify | Inject language instruction |
| `agents/scenario.py` | Modify | Inject language instruction |
| `agents/constitution_maxim.py` | Modify | Inject language instruction |
| `agents/drafting.py` | Modify | Inject language instruction |
| `agents/document.py` | Modify | Inject language instruction |
| `agents/orchestrator.py` | Modify | Remove hardcoded Hindi hint, use `localize_prompt()` |
| `config/prompts.py` | Modify | Remove hardcoded Hindi line in synthesis prompt |

### Phase 2

| File | Change Type | Description |
|------|------------|-------------|
| `agents/orchestrator.py` | Modify | Expand `QUERY_NORMALIZE_PROMPT` to all 22 languages |
| `agents/orchestrator.py` | Modify | Per-agent rewrite always produces English queries |

### Phase 3

| File | Change Type | Description |
|------|------------|-------------|
| `agents/drafting.py` | Modify | Pass `user_language` to outline + section generation |
| `config/prompts.py` | Modify | `DRAFTING_SYSTEM_PROMPT` language-aware |

### Phase 4

| File | Change Type | Description |
|------|------------|-------------|
| `frontend.html` | Modify | Language selector UI + RTL CSS |
| `core/gateway.py` | Modify | Accept `preferred_language` in request |
| `core/state.py` | Modify | Gateway sets `user_language` if provided |

---

*Plan created 2026-03-11. Implementation starts Phase 1.*
