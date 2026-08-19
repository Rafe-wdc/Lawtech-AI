"""Newacts agent evaluation set: 20 rounds x 3 turns = 60 prompts.

Each round is a multi-turn conversation (single thread_id) that simulates
how a real lawyer would use the system: exact section lookups, vague
topic queries, misnamed new-code confusion (BNSS vs BNS), multilingual
queries, code-switched Hinglish, extreme brevity, and long-form
comparison requests.

Scoring criteria per turn:
  - `expected_contains`: substrings that MUST appear in the response
    (case-insensitive). Pass threshold: 60% of them present.
  - `expected_not_contains`: substrings that MUST NOT appear. Any
    occurrence = fail.
  - `expected_agent`: default "Newacts" — response's `agents_used` must
    include this.
  - `should_note_mismatch`: for rounds where the user names the wrong
    new-code act (case B, case G), the response must acknowledge the
    mismatch. Verified via `mismatch_indicators` substring match.
"""

# Generic hallucination / refusal markers — any of these in a response
# means the LLM either refused or hedged, which is a fail for our tests.
_REFUSAL_MARKERS = [
    "cannot be generated",
    "cannot generate",
    "unable to find",
    "not available",
    "I am unable",
    "I cannot provide",
    "no information",
]

ROUNDS = [
    # ----------------------------------------------------------------
    # Round 1 — Basic exact-section lookup, English
    # ----------------------------------------------------------------
    {
        "id": 1,
        "description": "Basic exact-section lookup (IPC 302 murder)",
        "tags": ["en", "single-act", "exact-section"],
        "turns": [
            {
                "id": "1.1",
                "prompt": "What is Section 302 of IPC?",
                "expected_contains": ["302", "murder", "punishment", "death", "imprisonment"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "1.2",
                "prompt": "What's its equivalent in the new code?",
                "expected_contains": ["103", "BNS", "Bharatiya Nyaya Sanhita", "murder"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "1.3",
                "prompt": "Give me a comparison table between IPC 302 and BNS 103",
                "expected_contains": ["302", "103", "murder", "punishment"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 2 — THE INCIDENT CASE (case B — wrong new-code named)
    # ----------------------------------------------------------------
    {
        "id": 2,
        "description": "Incident case: user names BNSS but means BNS (substantive homicide)",
        "tags": ["en", "case-B", "cross-code-mismatch", "regression"],
        "turns": [
            {
                "id": "2.1",
                "prompt": "Give me the comparison between section 299 and section 300 of IPC along with the new sections of Bharatiya Nagarik Suraksha Sanhita 2023",
                "expected_contains": [
                    "299", "300", "100", "101",
                    "culpable homicide", "murder",
                ],
                "expected_not_contains": _REFUSAL_MARKERS,
                "should_note_mismatch": True,
                "mismatch_indicators": ["Nyaya Sanhita", "BNS"],
            },
            {
                "id": "2.2",
                "prompt": "Wait, I meant to ask about Bharatiya Nyaya Sanhita, not the Suraksha one",
                "expected_contains": ["100", "101", "BNS", "Nyaya Sanhita", "culpable homicide", "murder"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "2.3",
                "prompt": "Show me how the mens rea test changed between IPC 300 and BNS 101",
                "expected_contains": ["300", "101", "intention", "knowledge"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 3 — Vague topic query (private defence)
    # ----------------------------------------------------------------
    {
        "id": 3,
        "description": "Vague topic query (private defence of body)",
        "tags": ["en", "topic-vague", "cross-act"],
        "turns": [
            {
                "id": "3.1",
                "prompt": "What are the provisions for private defence of body?",
                "expected_contains": ["private defence", "96", "97", "IPC"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "3.2",
                "prompt": "Give me both IPC and BNS versions",
                "expected_contains": ["IPC", "BNS", "private defence", "96", "34"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "3.3",
                "prompt": "Which sections apply if I defend myself against a robbery?",
                "expected_contains": ["defence", "robbery"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 4 — Hindi query (CrPC 41 arrest procedure)
    # ----------------------------------------------------------------
    {
        "id": 4,
        "description": "Hindi query — CrPC 41 arrest → BNSS counterpart",
        "tags": ["hi", "procedural", "old-to-new"],
        "turns": [
            {
                "id": "4.1",
                "prompt": "CrPC की धारा 41 क्या है?",
                "expected_contains": ["41", "CrPC", "गिरफ्तार"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "4.2",
                "prompt": "इसका नया रूप BNSS में कहाँ है?",
                "expected_contains": ["35", "BNSS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "4.3",
                "prompt": "गिरफ्तारी के आधार दोनों कोड में बताइए",
                "expected_contains": ["गिरफ्तार", "41", "35"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 5 — Marathi query (IEA 65B → BSA)
    # ----------------------------------------------------------------
    {
        "id": 5,
        "description": "Marathi query — IEA 65B electronic records → BSA",
        "tags": ["mr", "evidence", "old-to-new"],
        "turns": [
            {
                "id": "5.1",
                "prompt": "IEA Section 65B म्हणजे काय?",
                "expected_contains": ["65B", "इलेक्ट्रॉनिक"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "5.2",
                "prompt": "बीएसए मध्ये त्याची तरतूद कुठे आहे?",
                "expected_contains": ["63", "BSA", "साक्ष्य"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "5.3",
                "prompt": "इलेक्ट्रॉनिक पुरावा नियम स्पष्ट करा",
                "expected_contains": ["इलेक्ट्रॉनिक", "पुरावा"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 6 — Multi-requirement single prompt (3 sections compared)
    # ----------------------------------------------------------------
    {
        "id": 6,
        "description": "Multi-requirement: compare 3 IPC sections with BNS counterparts",
        "tags": ["en", "multi-requirement", "multi-section", "cross-act"],
        "turns": [
            {
                "id": "6.1",
                "prompt": "Compare IPC 302, 304, 307 with their BNS counterparts. I need definition, ingredients, and punishment for each.",
                "expected_contains": [
                    "302", "304", "307",
                    "103", "105", "109",
                    "punishment",
                ],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "6.2",
                "prompt": "Now add IPC 34 to that comparison",
                "expected_contains": ["34", "3", "common intention"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "6.3",
                "prompt": "Which of these have stricter punishment in the new law?",
                "expected_contains": ["punishment"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 7 — Tamil query (IPC 375 rape → BNS)
    # ----------------------------------------------------------------
    {
        "id": 7,
        "description": "Tamil query — IPC 375 rape → BNS 63",
        "tags": ["ta", "substantive", "sensitive-topic"],
        "turns": [
            {
                "id": "7.1",
                "prompt": "IPC பிரிவு 375 என்ன?",
                "expected_contains": ["375", "IPC"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "7.2",
                "prompt": "BNS-ல் இதற்கு என்ன மாற்றம்?",
                "expected_contains": ["63", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "7.3",
                "prompt": "இணக்க வயது பற்றி விளக்குங்கள்",
                "expected_contains": ["18", "வயது"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 8 — Vague topic, no section named (cybercrime)
    # ----------------------------------------------------------------
    {
        "id": 8,
        "description": "Vague topic — cybercrime provisions",
        "tags": ["en", "topic-vague", "no-section-named"],
        "turns": [
            {
                "id": "8.1",
                "prompt": "What are the new provisions about cybercrime in Indian criminal law?",
                "expected_contains": ["electronic", "cyber"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "8.2",
                "prompt": "Any changes to the definition of theft in the digital age?",
                "expected_contains": ["theft"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "8.3",
                "prompt": "List some sections that mention electronic records",
                "expected_contains": ["electronic"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 9 — Cross-code correct pairing (case C — IPC 498A ↔ BNS 85)
    # ----------------------------------------------------------------
    {
        "id": 9,
        "description": "Cross-code with correct pairing (IPC 498A ↔ BNS 85)",
        "tags": ["en", "case-C", "cross-act-correct"],
        "turns": [
            {
                "id": "9.1",
                "prompt": "Compare IPC 498A with BNS 85",
                "expected_contains": ["498A", "85", "cruelty", "husband"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "9.2",
                "prompt": "What are the ingredients that changed between them?",
                "expected_contains": ["cruelty"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "9.3",
                "prompt": "Any new procedural requirements added?",
                "expected_contains": ["procedure"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 10 — New-code first (case D — BNS 111 organised crime)
    # ----------------------------------------------------------------
    {
        "id": 10,
        "description": "New-code first (BNS 111 organised crime — new provision)",
        "tags": ["en", "case-D", "new-code-first", "novel-provision"],
        "turns": [
            {
                "id": "10.1",
                "prompt": "What does BNS Section 111 say?",
                "expected_contains": ["111", "organised crime", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "10.2",
                "prompt": "Was there any equivalent in IPC?",
                "expected_contains": ["organised", "111"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "10.3",
                "prompt": "How does organised crime differ from ordinary criminal conspiracy?",
                "expected_contains": ["conspiracy", "organised"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 11 — Case G (IEA / BNS mixup — should route to BSA)
    # ----------------------------------------------------------------
    {
        "id": 11,
        "description": "Case G: user names IEA + BNS (wrong — should be BSA)",
        "tags": ["en", "case-G", "cross-code-mismatch"],
        "turns": [
            {
                "id": "11.1",
                "prompt": "What is the counterpart of IEA Section 3 in BNS?",
                "expected_contains": ["IEA", "3", "definition"],
                "expected_not_contains": _REFUSAL_MARKERS,
                "should_note_mismatch": True,
                "mismatch_indicators": ["BSA", "Sakshya Adhiniyam"],
            },
            {
                "id": "11.2",
                "prompt": "Show me the definitions section in both correctly",
                "expected_contains": ["IEA", "BSA", "definition"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "11.3",
                "prompt": "Explain 'fact in issue' vs 'relevant fact'",
                "expected_contains": ["fact in issue", "relevant fact"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 12 — Bengali query (BNS 63 rape → IPC 375)
    # ----------------------------------------------------------------
    {
        "id": 12,
        "description": "Bengali query — BNS 63 rape → IPC 375",
        "tags": ["bn", "substantive", "new-to-old"],
        "turns": [
            {
                "id": "12.1",
                "prompt": "BNS ধারা 63 কী বলে?",
                "expected_contains": ["63", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "12.2",
                "prompt": "IPC-তে এর পুরাতন সংস্করণ কোনটি?",
                "expected_contains": ["375", "IPC"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "12.3",
                "prompt": "সম্মতির ভূমিকা কী?",
                "expected_contains": ["সম্মতি"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 13 — Range query (BNS chapter on offences against women)
    # ----------------------------------------------------------------
    {
        "id": 13,
        "description": "Range query — BNS chapter on offences against women",
        "tags": ["en", "range", "chapter-overview"],
        "turns": [
            {
                "id": "13.1",
                "prompt": "List sections in the BNS chapter on offences against women",
                "expected_contains": ["63", "74", "85", "women"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "13.2",
                "prompt": "Compare with the corresponding IPC chapter",
                "expected_contains": ["375", "354", "498A", "IPC"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "13.3",
                "prompt": "Which ones have enhanced punishment in BNS?",
                "expected_contains": ["punishment", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 14 — Scenario-based (domestic violence)
    # ----------------------------------------------------------------
    {
        "id": 14,
        "description": "Scenario — husband habitually beats wife",
        "tags": ["en", "scenario", "no-section-named"],
        "turns": [
            {
                "id": "14.1",
                "prompt": "A husband habitually beats his wife — what sections apply?",
                "expected_contains": ["498A", "cruelty"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "14.2",
                "prompt": "In both old and new law please",
                "expected_contains": ["498A", "85", "IPC", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "14.3",
                "prompt": "What if the wife files an FIR — what's the procedure?",
                "expected_contains": ["FIR"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 15 — Procedural (CrPC 439 bail → BNSS)
    # ----------------------------------------------------------------
    {
        "id": 15,
        "description": "Procedural — CrPC 439 bail → BNSS counterpart",
        "tags": ["en", "procedural", "old-to-new"],
        "turns": [
            {
                "id": "15.1",
                "prompt": "What's the procedure for bail under CrPC 439?",
                "expected_contains": ["439", "bail", "Sessions"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "15.2",
                "prompt": "Has this procedure changed in BNSS?",
                "expected_contains": ["BNSS", "bail"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "15.3",
                "prompt": "Compare the time limits for bail in both codes",
                "expected_contains": ["bail"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 16 — Hinglish code-switching (IPC 420 cheating)
    # ----------------------------------------------------------------
    {
        "id": 16,
        "description": "Hinglish code-switching — IPC 420 cheating → BNS 318",
        "tags": ["hinglish", "code-switch", "substantive"],
        "turns": [
            {
                "id": "16.1",
                "prompt": "IPC 420 ka punishment kya hai?",
                "expected_contains": ["420", "cheating", "punishment"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "16.2",
                "prompt": "BNS mein iska equivalent kya hai?",
                "expected_contains": ["318", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "16.3",
                "prompt": "Both ke ingredients bataiye",
                "expected_contains": ["420", "318", "cheating"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 17 — Extreme brevity (IPC 375 → BNS)
    # ----------------------------------------------------------------
    {
        "id": 17,
        "description": "Extreme brevity — short follow-ups",
        "tags": ["en", "extreme-brief", "follow-up-heavy"],
        "turns": [
            {
                "id": "17.1",
                "prompt": "IPC 375",
                "expected_contains": ["375", "IPC"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "17.2",
                "prompt": "aur BNS?",
                "expected_contains": ["63", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "17.3",
                "prompt": "compare?",
                "expected_contains": ["375", "63"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 18 — Multi-act comparison (IPC 34, 149, 120B)
    # ----------------------------------------------------------------
    {
        "id": 18,
        "description": "Multi-act comparison — IPC 34, 149, 120B with BNS counterparts",
        "tags": ["en", "multi-section", "cross-act"],
        "turns": [
            {
                "id": "18.1",
                "prompt": "Compare IPC 34, IPC 149, and IPC 120B with their BNS counterparts",
                "expected_contains": ["34", "149", "120B", "BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "18.2",
                "prompt": "Which is the most changed in the new law?",
                "expected_contains": ["BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "18.3",
                "prompt": "Difference between common intention and criminal conspiracy in both codes",
                "expected_contains": ["common intention", "conspiracy"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 19 — Case E (section missing from mapping table)
    # ----------------------------------------------------------------
    {
        "id": 19,
        "description": "Case E — IPC 354D stalking (may not be in mapping table)",
        "tags": ["en", "case-E", "mapping-gap"],
        "turns": [
            {
                "id": "19.1",
                "prompt": "What is IPC Section 354D about stalking?",
                "expected_contains": ["354D", "stalking"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "19.2",
                "prompt": "Is there a corresponding BNS section?",
                "expected_contains": ["78", "BNS", "stalking"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "19.3",
                "prompt": "Compare the ingredients between the two",
                "expected_contains": ["354D", "78"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },

    # ----------------------------------------------------------------
    # Round 20 — Long-form comparison (homicide range 299-304A)
    # ----------------------------------------------------------------
    {
        "id": 20,
        "description": "Long-form comparison — IPC 299 to 304A vs BNS equivalents",
        "tags": ["en", "long-form", "range", "multi-section"],
        "turns": [
            {
                "id": "20.1",
                "prompt": "Give me a full comparison of homicide provisions — IPC 299 through 304A and their BNS equivalents",
                "expected_contains": [
                    "299", "300", "302", "304",
                    "100", "101", "103", "105",
                    "culpable homicide", "murder",
                ],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "20.2",
                "prompt": "Now organize this in a table with columns for definition, ingredients, and punishment",
                "expected_contains": ["definition", "punishment"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
            {
                "id": "20.3",
                "prompt": "Which of these sections have completely new elements added in BNS?",
                "expected_contains": ["BNS"],
                "expected_not_contains": _REFUSAL_MARKERS,
            },
        ],
    },
]


def total_turns() -> int:
    return sum(len(r["turns"]) for r in ROUNDS)


if __name__ == "__main__":
    print(f"{len(ROUNDS)} rounds, {total_turns()} total turns")
