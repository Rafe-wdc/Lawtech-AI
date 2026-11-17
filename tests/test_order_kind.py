"""Substantive judgments before routine procedural orders (core/order_kind.py).

Calibrated 2026-11-17 against 240 entries labelled substantive / procedural
(74% of procedural orders caught, 0 of 105 decisions demoted) and checked on
170 fresh ones (86% caught, 1 of 77 demoted, a borderline interim order).
The texts below are invented.
"""
from __future__ import annotations

from core.order_kind import asked_matters, classify, prefer_substantive

WITHDRAWN = ("Learned counsel for the applicant seeks leave to withdraw the present application with liberty "
             "to approach the trial court. Leave granted. The Bail Application is disposed of as withdrawn. "
             "[A. B. JUDGE, J.]")
REASONED_BAIL = (
    "The applicant is in custody since 12 March 2025 for offences under Sections 318 and 316 of the BNS. "
    "Heard learned counsel. Considering the nature of the allegations, the period of custody and the fact "
    "that the charge-sheet has been filed, I am of the view that further detention is not necessary. "
    "No case is made out for continued custody. The applicant shall be released on bail on furnishing a "
    "bond of Rs.25,000. The trial court to expedite the trial. Bail Application is allowed. [A. B. JUDGE, J.]")
COMPENSATION_DECISION = (
    "The reference court failed to consider the sale instances of comparable lands. For the reasons stated "
    "above, the market value is determined at Rs.450 per square metre and the claimants are entitled to "
    "enhanced compensation with statutory benefits. The appeal is partly allowed. The claimants are "
    "permitted to withdraw 50% of the compensation deposited in this Court. [C. D. JUDGE, J.]")
DELAY_ONLY = ("This civil application seeks condonation of delay of 312 days in filing the first appeal. "
              "Cause shown is sufficient. Delay is condoned subject to the appellant not claiming interest "
              "for the period of delay. Civil Application stands disposed of. [E. F. JUDGE, J.]")
LISTING = "Not on board. S. O. to 12th January 2026. [G. H. JUDGE, J.]"


def _hit(text, **kw):
    return {"content": text, **kw}


def test_a_routine_withdrawal_is_procedural():
    k = classify(_hit(WITHDRAWN))
    assert k.procedural and "withdrawal" in k.matters


def test_a_short_bail_order_with_reasons_is_substantive():
    assert not classify(_hit(REASONED_BAIL)).procedural


def test_withdrawing_money_is_not_withdrawing_the_case():
    assert not classify(_hit(COMPENSATION_DECISION)).procedural


def test_an_order_only_condoning_delay_is_procedural_despite_subject_keywords():
    hit = _hit(DELAY_ONLY, keywords=["land acquisition", "enhanced compensation", "market value",
                                     "reference court", "interest"],
               acts_or_sections_invoked=["Section 18 of the Land Acquisition Act"])
    assert classify(hit).procedural


def test_deciding_the_main_matter_outweighs_a_routine_line():
    succession = ("Delay is explained and the same is condoned. All legal formalities are complied with. "
                  "Hence, petition is granted. Office to issue Succession Certificate as prayed.")
    assert not classify(_hit(succession)).procedural
    side = ("The issue of limitation is a mixed question of law and fact. We dispose of the appeal setting "
            "aside the impugned order. C.M. No. 1234/2016 (Stay) Application is dismissed as infructuous.")
    assert not classify(_hit(side)).procedural


def test_a_partys_account_is_not_the_courts_disposal():
    text = ("Learned advocate for the intervenor submits that the suit filed by the applicant was dismissed "
            "for default. The interim order is confirmed and made absolute. Application disposed of.")
    assert not classify(_hit(text)).procedural


def test_listing_orders_and_stubs():
    assert classify(_hit(LISTING)).procedural
    assert classify(_hit("1. 2. 17-WP 719-24.DOCX 2 3")).procedural          # no decision text
    long_decision = REASONED_BAIL + " " + ("The material on record shows the role of the applicant. " * 30) \
        + " Stand over to 12th January 2026."
    assert not classify(_hit(long_decision)).procedural


def test_decisions_come_first_and_nothing_is_dropped():
    hits = [_hit(WITHDRAWN, n=1), _hit(LISTING, n=2), _hit(REASONED_BAIL, n=3), _hit(COMPENSATION_DECISION, n=4)]
    ordered = prefer_substantive("Bombay High Court judgments on bail in cheating cases", hits)
    assert [h["n"] for h in ordered] == [3, 4, 1, 2]
    assert ordered[0]["order_kind"] == "substantive" and ordered[-1]["order_kind"] == "procedural"
    assert all(h["order_reason"] for h in ordered)


def test_the_relevance_judge_sees_the_opening_and_the_operative_end():
    from agents.judgment import _judge_view
    titles = "WRIT PETITION NO. 5476 OF 2026 Sanstha v. State " * 200     # a grouped judgment's title list
    decision = "11. The Writ Petitions are accordingly disposed of on these terms."
    view = _judge_view([titles + decision, "short order"])
    assert view[0].startswith("WRIT PETITION NO. 5476") and view[0].endswith(decision)
    assert view[1] == "short order"


def test_an_order_on_the_procedure_asked_about_keeps_its_place():
    assert asked_matters("condonation of delay in land acquisition appeals") == {"delay"}
    hits = [_hit(DELAY_ONLY, n=1), _hit(WITHDRAWN, n=2), _hit(COMPENSATION_DECISION, n=3)]
    ordered = prefer_substantive("High Court orders on condonation of delay in land acquisition appeals", hits)
    assert [h["n"] for h in ordered] == [1, 3, 2]
    assert ordered[0]["order_kind"] == "procedural-asked"
    assert asked_matters("Bombay High Court judgments of 2026 on anticipatory bail") == set()
