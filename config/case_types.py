"""Suggested values for case intake fields.

These feed ``GET /pyapi/cases/meta`` so the frontend can offer a dropdown.
They are suggestions only: ``matter_type``, ``stage`` and ``our_side`` are
stored as free-text slugs and unknown values are accepted. Add a matter type
by adding a line, not by touching code paths. Lawyers own this list; the
playbooks (later phase) will key on ``value``.
"""
from __future__ import annotations

MATTER_TYPES: list[tuple[str, str]] = [
    ("cheque_dishonour_ni138", "Cheque dishonour (s.138 NI Act)"),
    ("criminal_fir_bail", "Criminal matter: FIR and bail stage"),
    ("criminal_trial", "Criminal trial"),
    ("consumer_complaint", "Consumer complaint"),
    ("civil_suit_property", "Civil suit: property / title / possession"),
    ("civil_suit_recovery", "Civil suit: recovery of money"),
    ("contract_dispute", "Contract dispute"),
    ("arbitration", "Arbitration"),
    ("matrimonial", "Matrimonial / family"),
    ("labour_employment", "Labour and employment"),
    ("motor_accident_claim", "Motor accident claim (MACT)"),
    ("writ_petition", "Writ petition"),
    ("gst_tax", "GST / tax"),
    ("company_insolvency", "Company / insolvency (NCLT)"),
    ("other", "Other"),
]

STAGES: list[tuple[str, str]] = [
    ("pre_litigation", "Pre-litigation / advice"),
    ("notice_sent", "Legal notice sent"),
    ("notice_received", "Legal notice received"),
    ("fir_registered", "FIR registered"),
    ("investigation", "Under investigation"),
    ("chargesheet_filed", "Chargesheet filed"),
    ("bail_stage", "Bail / anticipatory bail"),
    ("filed", "Filed, pre-admission"),
    ("pleadings", "Pleadings"),
    ("evidence", "Evidence"),
    ("arguments", "Arguments"),
    ("judgment_reserved", "Judgment reserved"),
    ("decided", "Decided"),
    ("appeal", "Appeal / revision"),
    ("execution", "Execution"),
    ("other", "Other"),
]

SIDES: list[tuple[str, str]] = [
    ("complainant", "Complainant"),
    ("petitioner", "Petitioner"),
    ("plaintiff", "Plaintiff"),
    ("applicant", "Applicant"),
    ("appellant", "Appellant"),
    ("claimant", "Claimant"),
    ("respondent", "Respondent"),
    ("accused", "Accused"),
    ("defendant", "Defendant"),
    ("opposite_party", "Opposite party"),
    ("other", "Other"),
]


def as_suggestions(pairs: list[tuple[str, str]]) -> list[dict]:
    return [{"value": v, "label": l} for v, l in pairs]
