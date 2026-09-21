"""The output sanitizer must not re-wrap prose.

Advocate screenshot 2026-09-21 (writ petition): paragraph 5 rendered blank and
every later paragraph was numbered one too high. The draft itself was fine:
paragraph 4 was one 1,050-char line ending "... (Preparation and Maintenance)
Rules, 1971." `core.sanitize._fix_long_lines` then hard-wrapped every line over
500 chars at 120 chars, the last piece was the bare line "1971.", and markdown
reads a line opening with "<number>." as a new ordered-list item: an empty
item, rendered as "5.", pushing the real paragraph 5 to "6.".

Only an unbroken run (no spaces) actually overflows; the renderer wraps prose.
"""

from __future__ import annotations

import asyncio
import re

from core.sanitize import sanitize_output

# Same shape as the reported paragraph (synthetic parties, padded so the old
# 120-char word wrap leaves "1971." alone on the last line, as it did there).
PARA_4 = (
    "4. In the year 1985-1986, the predecessor of Respondent No. 9, Late Ramesh Kumar Patil, acting in clandestine "
    "collusion with the subordinate revenue staff of Taluka Haveli, engineered a fabricated proceeding styled as Case No. "
    "Tenancy / Vashi / Vahiwat / 1/86 (also referenced in contradictory official documents as Case No. Tenancy / Vashi / "
    "Vahiwat / 9/86). Late Ramesh Kumar Patil submitted an application alleging that he was in possession of the suit "
    "property and that the registered owner, Shri. Suresh Anant Joshi, resided at Shrirampur and did not cultivate the "
    "land in person. Based on this frivolous application, and without issuing statutory notices, conducting public spot "
    "inspections, or verifying registered instruments, an clearly clearly clearly ex-parte order dated 24.04.1986 was purportedly passed by the "
    "Tahsildar, Haveli, directing the entry of Ramesh Kumar Patil's name in Mode \"0\" (Vahiwat / possession) in Village Form "
    "14 under Rule 31 of the Maharashtra Land Revenue Record of Rights and Registers (Preparation and Maintenance) Rules, "
    "1971."
)
PARA_5 = "5. On the basis of the purported, unserved order dated 24.04.1986, the Circle Officer certified Mutation Entry No. 2562."
DRAFT = PARA_4 + "\n\n" + PARA_5

# A line markdown renders as an EMPTY ordered-list item.
_EMPTY_LIST_ITEM = re.compile(r"^[ \t]*\d+[.)][ \t]*$")


def test_reported_paragraph_is_not_rewrapped():
    out = sanitize_output(DRAFT)
    assert out == DRAFT
    assert not any(_EMPTY_LIST_ITEM.match(l) for l in out.splitlines())


def test_no_wrapped_line_opens_a_list_item_mid_paragraph():
    out = sanitize_output(DRAFT)
    numbered = [l.split(".")[0] for l in out.splitlines() if re.match(r"^\d+\.", l)]
    assert numbered == ["4", "5"]


def test_indented_long_prose_keeps_its_indentation():
    nested = "   " + ("A continuation paragraph nested under its clause. " * 15).strip()
    assert len(nested) > 500
    assert sanitize_output(nested) == nested


def test_unbroken_run_is_still_split():
    run = "x" * 1300
    out = sanitize_output("before " + run + " after")
    assert all(len(l) <= 130 for l in out.splitlines())
    assert out.replace("\n", "") == "before " + run + " after"


def test_long_url_is_left_whole():
    url = "https://api.sci.gov.in/supremecourt/" + "a" * 600 + ".pdf"
    line = "See " + url + " for the judgment. " * 3
    assert url in sanitize_output(line)


def test_guardrail_output_end_to_end():
    from agents.guardrail import guardrail_output_node
    state = {"final_response": DRAFT, "task": "Drafting", "tasks_planned": ["Drafting"],
             "original_query": "draft a writ petition", "query": "draft a writ petition"}
    out = asyncio.run(guardrail_output_node(state))["final_response"]
    assert not any(_EMPTY_LIST_ITEM.match(l) for l in out.splitlines())
    assert "Rules, 1971." in out


def test_drafting_escapes_a_year_alone_on_a_line():
    from agents.drafting import _escape_stranded_numbers
    out, n = _escape_stranded_numbers("4. Rules,\n1971.\n\n5. Next.")
    assert n == 1
    assert "1971\\." in out
