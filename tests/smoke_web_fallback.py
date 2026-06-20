"""Live smoke test for the niche-format web-fallback chain.

Hits the real drafting agent with two queries chosen to exercise different
branches of the new chain:

  1. "Draft an application under Section 9 of the Arbitration and
     Conciliation Act, 1996 for interim measures"
     → niche court_filing the corpus does not cover. Expected:
     selector grades 'none' → web fetch fires → validator accepts →
     selected_source = `<web:court_filing>` and grounding URLs in sources.

  2. "Draft a bail application under Section 483 BNSS"
     → existing good corpus coverage. Expected: selector grades 'good',
     no web fetch.

Output: fingerprint table + source attribution, no assertions. Used as a
visual spot-check after a change to the chain.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.drafting import drafting_node
from core.state import LegalAgentState


CASES = [
    {
        "label": "NICHE — arbitration Section 9 application",
        "query": (
            "Draft an application under Section 9 of the Arbitration and "
            "Conciliation Act, 1996 for interim measures before the "
            "Commercial Court at Pune"
        ),
    },
    {
        "label": "EXISTING — bail application S.483 BNSS",
        "query": "Draft a bail application under Section 483 BNSS",
    },
]


async def run_one(label: str, query: str) -> None:
    print()
    print("=" * 100)
    print(f"CASE: {label}")
    print(f"QUERY: {query}")
    print("=" * 100)

    state = LegalAgentState(
        original_query=query,
        query=query,
        agent_queries={"Drafting": query},
        user_language="en",
    )
    result = await drafting_node(state)
    r = result["agent_results"]["Drafting"]
    content = r.content or ""

    print(f"  error                : {r.error or 'None'}")
    print(f"  fallback_used        : {getattr(r, 'fallback_used', False)}")
    print(f"  content length       : {len(content)}")
    print()
    print("  --- sources ---")
    for s in r.sources or []:
        fn = getattr(s, "file_name", None)
        wu = getattr(s, "web_url", None)
        print(f"    file_name={fn}  web_url={wu}")
    print()
    print("  --- fingerprint ---")
    print(f"    IN THE COURT OF / BEFORE  : {'IN THE COURT OF' in content or 'BEFORE THE HON' in content}")
    print(f"    IN THE MATTER OF          : {'IN THE MATTER OF' in content}")
    print(f"    Versus                    : {'Versus' in content}")
    print(f"    Prayer / PRAYER           : {'Prayer' in content or 'PRAYER' in content}")
    print(f"    Verification              : {'Verification' in content or 'VERIFICATION' in content}")
    print(f"    Section 9 / Arbitration   : {'Section 9' in content or 'Arbitration' in content}")
    print(f"    BNSS / Section 483        : {'BNSS' in content or 'Section 483' in content}")
    print()
    print("  --- first 800 chars ---")
    print(content[:800])


async def main() -> None:
    for case in CASES:
        await run_one(case["label"], case["query"])


if __name__ == "__main__":
    asyncio.run(main())
