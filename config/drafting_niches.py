"""Niche overlays for the Drafting agent (senior-counsel mode).

Each overlay is a short prompt block appended after DRAFTING_SYSTEM_PROMPT
(single-pass) or DRAFTING_SECTION_PAIR_PROMPT (section-wise). The overlay
names the structural skeleton, mandatory statutory anchors, prayer form,
and verification form for one filing niche.

The overlay does NOT replace the reference draft — the reference remains
the structural anchor for shape / conventions / signature block. The
overlay adds the *niche-specific* discipline that the reference alone
does not guarantee (e.g. rejoinder replies must trace source paragraphs
one-for-one; anticipatory-bail applications must disclose custodial-
interrogation stance; s.138 NI Act notices must specify the 15-day
statutory period from receipt).

Selection is done by ``agents/drafting_niche.py`` — a Gemini Flash Lite
classifier picks the best niche key from ``NICHE_KEYS`` given the user
query + first ~2K chars of uploaded source docs, or returns ``"none"``
when no overlay applies. The dispatcher looks up ``NICHE_OVERLAYS[key]``
and passes it through as the ``## NICHE OVERLAY`` block.

Adding a niche:
  1. Add the key to ``NICHE_KEYS`` (kebab-case, stable — the selector
     picks by key name).
  2. Add the constant + docstring in the section below.
  3. Register in ``NICHE_OVERLAYS`` at the bottom.
  4. Extend the ``NICHE_SELECTOR_PROMPT`` in ``agents/drafting_niche.py``
     if the key needs a disambiguation hint (usually not needed — the
     key name plus the source is enough).
"""
from __future__ import annotations

# Stable identifiers used by the selector. Order matters only for the
# selector prompt — put the most-frequently-requested niches first so the
# LLM sees them in a natural priority order.
NICHE_KEYS: tuple[str, ...] = (
    "bail_application_regular",
    "bail_application_anticipatory",
    "quashing_petition_bnss528",
    "writ_petition_article_226",
    "writ_petition_article_32",
    "plaint_civil_suit",
    "written_statement",
    "rejoinder_pleading",
    "arbitration_statement_of_claim",
    "arbitration_rejoinder",
    "notice_ni_act_s138",
    "reply_to_legal_notice",
    "consumer_complaint",
    "cheque_bounce_complaint_s138",
    "matrimonial_petition_divorce",
    "nclt_oppression_petition",
    "mact_claim_petition",
    "review_petition",
    "transfer_petition",
    "contract_agreement_generic",
    "affidavit_generic",
)


# ---------------------------------------------------------------------------
# Bail — regular bail application under Section 480 BNSS (formerly s.437/439 CrPC)
# ---------------------------------------------------------------------------
BAIL_APPLICATION_REGULAR = """## NICHE OVERLAY — REGULAR BAIL APPLICATION (Section 480 BNSS / erstwhile Sections 437 & 439 CrPC)

**Structural skeleton (in this order):**
1. Cause title — Court name, "BAIL APPLICATION NO. ___ OF [YEAR]", "IN THE MATTER OF: FIR No. ___ / [Year] at P.S. [name], u/s [BNS/IPC sections]", Applicant vs State (through the PP).
2. Memo of parties — Applicant details, State represented by the concerned Public Prosecutor.
3. "MOST RESPECTFULLY SHOWETH" — opening line.
4. FACTS OF THE CASE — chronological, source-anchored, paragraph-numbered from 1.
5. GROUNDS FOR BAIL — enumerate 8-15 numbered grounds tailored to the matter (no criminal antecedents / roots in society / period of custody / co-accused parity / cooperation with investigation / no likelihood of tampering / delayed trial / medical / age / gender / statutory bail under s.187(3) BNSS / triple-test where applicable).
6. PREVIOUS BAIL APPLICATIONS — mandatory disclosure paragraph. If none, state so expressly. If prior applications made, give court, case number, date, outcome.
7. PRAYER — grant of bail on such terms and conditions the Hon'ble Court deems fit.
8. Place / Date / Signature block for Counsel + Applicant.
9. VERIFICATION — separate paragraphs of FACT (personal knowledge) from paragraphs of LEGAL SUBMISSION (advice of counsel).

**Mandatory statutory anchors:**
- Section 480 BNSS (bail powers of Court of Session / High Court) — primary enabling provision.
- Where in Magistrate's court: Section 478 BNSS.
- Statutory bail: Section 187(3) BNSS (formerly Section 167(2) CrPC).
- Cite the actual BNS/IPC sections invoked in the FIR (both, per statutory-currency Rule 4A of the base prompt).

**Doctrinal anchors (cite where on point — real citations only):**
- Sanjay Chandra v. CBI, (2012) 1 SCC 40 (economic offences ≠ automatic denial).
- P. Chidambaram v. Directorate of Enforcement, (2020) 13 SCC 791 (triple test — flight risk / tampering / influence).
- Arnesh Kumar v. State of Bihar, (2014) 8 SCC 273 (mandatory checklist before arrest for offences ≤ 7 years).
- Satender Kumar Antil v. CBI, (2022) 10 SCC 51 (bail categories / bond-and-surety norms).

**Prayer form:**
"In the premises aforesaid, it is most respectfully prayed that this Hon'ble Court may graciously be pleased to:
(a) allow the present application and grant bail to the Applicant in FIR No. ___ / [Year] registered at Police Station [name] under Sections [___] on such terms and conditions as this Hon'ble Court may deem fit and proper;
(b) pass any such other or further order(s) as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case in the interest of justice."

**Do not do:**
- Do not cite Section 439 CrPC as the operative enabling provision for offences on or after 01.07.2024 without also naming Section 480 BNSS.
- Do not omit the previous-bail-applications disclosure paragraph — Indian courts treat non-disclosure as a serious lapse.
- Do not cite the police report / FIR as if it were proved — say "as per the FIR" or "as alleged".
"""


# ---------------------------------------------------------------------------
# Bail — anticipatory bail under Section 482 BNSS (formerly s.438 CrPC)
# ---------------------------------------------------------------------------
BAIL_APPLICATION_ANTICIPATORY = """## NICHE OVERLAY — ANTICIPATORY BAIL APPLICATION (Section 482 BNSS / erstwhile Section 438 CrPC)

**Structural skeleton (in this order):**
1. Cause title — Court of Session / High Court, "ANTICIPATORY BAIL APPLICATION NO. ___ OF [YEAR]", "IN THE MATTER OF: Apprehended arrest in FIR No. ___ / [Year] at P.S. [name] u/s [BNS/IPC sections]".
2. Memo of parties.
3. "MOST RESPECTFULLY SHOWETH".
4. FACTS AND CIRCUMSTANCES — chronological, including the apprehension of arrest (notice u/s 41A CrPC / 35(3) BNSS received, allegations reaching applicant, or otherwise).
5. GROUNDS FOR ANTICIPATORY BAIL — 10-15 numbered grounds; MANDATORY subheadings: (i) no prima facie case / mala fide implication (ii) roots in society / no flight risk (iii) cooperation with investigation offered (iv) custodial interrogation NOT required — explain why written interrogation / notice u/s 179 BNSS / production of documents would suffice.
6. CUSTODIAL INTERROGATION — separate paragraph explaining why the applicant's custodial interrogation is not warranted. This is dispositive in most anticipatory-bail matters — do not omit.
7. PREVIOUS ANTICIPATORY BAIL APPLICATIONS — mandatory disclosure paragraph.
8. PRAYER — grant of anticipatory bail on terms u/s 482(2) BNSS.
9. Place / Date / Signature.
10. VERIFICATION separating fact from submission.

**Mandatory statutory anchors:**
- Section 482 BNSS (anticipatory bail) — primary enabling provision.
- Section 482(2) BNSS — conditions the Court may impose (attend investigation, no threat to witnesses, no leave country without leave).
- Sections 35 & 41A (notice of appearance) BNSS / CrPC — for cooperation ground.

**Doctrinal anchors (cite where on point):**
- Sushila Aggarwal v. State (NCT of Delhi), (2020) 5 SCC 1 (anticipatory bail need not be time-bound; can continue till end of trial).
- Siddharam Satlingappa Mhetre v. State of Maharashtra, (2011) 1 SCC 694 (parameters — nature and gravity, antecedents, likelihood of fleeing).
- Gurbaksh Singh Sibbia v. State of Punjab, (1980) 2 SCC 565 (foundational Constitution Bench).

**Do not do:**
- Do not skip the custodial-interrogation-not-required paragraph — courts routinely reject anticipatory bail on that ground alone.
- Do not conflate anticipatory bail with regular bail — different statutory scheme, different tests.
"""


# ---------------------------------------------------------------------------
# Quashing petition — Section 528 BNSS (formerly s.482 CrPC)
# ---------------------------------------------------------------------------
QUASHING_PETITION_BNSS528 = """## NICHE OVERLAY — QUASHING PETITION (Section 528 BNSS / erstwhile Section 482 CrPC)

**Structural skeleton:**
1. Cause title — "IN THE HIGH COURT OF [___] AT [___]", "CRIMINAL MISC. APPLICATION (QUASHING) NO. ___ OF [YEAR] UNDER SECTION 528 BNSS", "IN THE MATTER OF: FIR No. ___ / [Year] / P.S. [name] u/s [BNS/IPC sections]".
2. Memo of parties — Petitioner (accused), Respondent No. 1 State through PP, Respondent No. 2 the complainant (if a party is required).
3. Prefatory paragraph — statement that the petition is filed under Section 528 BNSS invoking the Court's inherent powers to prevent abuse of process and secure the ends of justice.
4. FACTS — chronology of the FIR / complaint / summoning order sought to be quashed.
5. GROUNDS FOR QUASHING — categorised: (i) allegations even if taken at face value do not constitute the offence charged (ii) allegations are absurd / inherently improbable (iii) proceedings are manifestly attended with mala fide (iv) civil dispute given criminal colour (v) settled between parties / compoundable (vi) territorial jurisdiction absent (vii) statutory bar (viii) any Bhajan Lal category on point.
6. PRAYER — quash the FIR / complaint / summoning order / entire proceedings.
7. Place / Date / Signature.
8. VERIFICATION.

**Mandatory statutory anchors:**
- Section 528 BNSS (formerly Section 482 CrPC) — primary enabling provision.
- Where a Magistrate's summoning order is challenged, cite the relevant Sections of BNSS on cognizance / process (Sections 223, 226 BNSS / former 190, 204 CrPC).

**Doctrinal anchors (cite by exact category invoked):**
- State of Haryana v. Bhajan Lal, 1992 Supp (1) SCC 335 — the seven Bhajan Lal categories are the gold standard; identify which of the seven applies to your matter and quote the language of that category.
- Rupan Deol Bajaj v. Kanwar Pal Singh Gill, (1995) 6 SCC 194 (quashing at threshold of prosecution).
- Gian Singh v. State of Punjab, (2012) 10 SCC 303 (quashing on settlement in non-heinous private disputes).
- Parbatbhai Aahir v. State of Gujarat, (2017) 9 SCC 641 (guidelines for quashing on settlement post-Gian Singh).

**Prayer form:**
"(a) quash and set aside the FIR No. ___ / [Year] registered at Police Station [name] as also all consequential proceedings arising therefrom in the interest of justice;
(b) pending disposal of the present petition, stay further proceedings including the investigation pursuant to FIR No. ___ / [Year];
(c) pass any such other or further order(s) as this Hon'ble Court may deem fit."

**Do not do:**
- Do not argue merits of defence at length — quashing is a "no offence made out even taken at face value" argument, not a mini-trial.
- Do not omit the Bhajan Lal category — courts want to see which category the petitioner invokes.
"""


# ---------------------------------------------------------------------------
# Writ — Article 226 (High Court)
# ---------------------------------------------------------------------------
WRIT_PETITION_ARTICLE_226 = """## NICHE OVERLAY — WRIT PETITION UNDER ARTICLE 226 (HIGH COURT)

**Structural skeleton:**
1. Cause title — "IN THE HIGH COURT OF [___] AT [___]", "WRIT PETITION (CIVIL / CRIMINAL) NO. ___ OF [YEAR]", "[UNDER ARTICLE 226 OF THE CONSTITUTION OF INDIA]".
2. Memo of parties — Petitioner, Respondents (State authorities named by designation, private respondents named individually).
3. Prefatory paragraph — statement that petition is filed under Article 226 seeking issuance of writ(s) of [certiorari / mandamus / prohibition / quo warranto / habeas corpus, as applicable] and other appropriate reliefs.
4. QUESTIONS OF LAW — 3-6 crisp questions the petition raises.
5. FACTS — chronological, source-anchored, paragraph-numbered.
6. GROUNDS — 8-15 numbered constitutional / statutory grounds; each ground pairs the specific right or statutory provision violated with the impugned act.
7. INTERIM RELIEF (if sought) — stay of impugned order / status quo / mandatory ad-interim direction pending disposal.
8. PRAYER — writ(s) sought, quashing of impugned order, positive mandamus, costs, omnibus.
9. Place / Date / Signature (Petitioner + Advocate).
10. VERIFICATION.
11. Affidavit-in-support (separately, on affidavit paper).
12. List of documents / Index of Annexures.

**Mandatory constitutional / statutory anchors:**
- Article 226 (writ jurisdiction of High Court) — primary anchor.
- Article 227 (superintendence) — cite alongside 226 where power of superintendence is separately invoked.
- Articles 14 / 19 / 21 / 300A — as applicable to the ground.
- If the impugned action is administrative — the parent statute + specific rule / notification.

**Doctrinal anchors (cite selectively on point):**
- Whirlpool Corporation v. Registrar of Trade Marks, (1998) 8 SCC 1 (alternative-remedy exceptions).
- Kaushal Kishor v. State of Uttar Pradesh, (2023) 4 SCC 1 (horizontal application, Article 21).
- Domain-specific landmark on the actual right / statute at issue (do NOT invent).

**Prayer form:**
"(a) issue a writ, order or direction in the nature of certiorari quashing the [impugned order / notification / action] dated [___] passed by Respondent No. [___];
(b) issue a writ, order or direction in the nature of mandamus directing Respondent No. [___] to [specific positive act];
(c) pending disposal of the present petition, stay the operation of the [impugned order / notification / action] dated [___];
(d) award costs of the petition to the Petitioner;
(e) pass any such other or further order(s) as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case."

**Do not do:**
- Do not omit the "QUESTIONS OF LAW" block — HC Registries in most states require it.
- Do not skip the affidavit-in-support reference — writ petitions must be verified on affidavit.
- Do not cite Article 32 as the enabling provision (that is the Supreme Court's jurisdiction).
"""


# ---------------------------------------------------------------------------
# Writ — Article 32 (Supreme Court)
# ---------------------------------------------------------------------------
WRIT_PETITION_ARTICLE_32 = """## NICHE OVERLAY — WRIT PETITION UNDER ARTICLE 32 (SUPREME COURT)

**Structural skeleton:**
1. Cause title — "IN THE SUPREME COURT OF INDIA", "CIVIL / CRIMINAL ORIGINAL JURISDICTION", "WRIT PETITION (CIVIL / CRIMINAL) NO. ___ OF [YEAR]", "[UNDER ARTICLE 32 OF THE CONSTITUTION OF INDIA]".
2. Memo of parties.
3. Prefatory paragraph — Article 32 invocation for enforcement of the Fundamental Right(s) named. Article 32 lies only for enforcement of Fundamental Rights — identify the right(s) upfront.
4. QUESTIONS OF LAW.
5. FACTS.
6. GROUNDS — each ground must trace back to a specific Fundamental Right or a companion right (Article 14 / 15 / 19 / 21 / 25 / 32 itself for right to constitutional remedies).
7. INTERIM RELIEF (if sought).
8. PRAYER.
9. Place / Date / Signature.
10. VERIFICATION on affidavit.
11. IA for exemption from filing certified copy / affidavit-in-support / list of dates and events (SC Rules 2013 requirements).

**Mandatory constitutional / statutory anchors:**
- Article 32 — primary enabling provision, "the right to move the Supreme Court by appropriate proceedings for the enforcement of the rights conferred by Part III".
- The specific Fundamental Right(s) allegedly violated.
- Order XXXVIII of the Supreme Court Rules, 2013 (writ petition format).

**Doctrinal anchors (cite selectively on point):**
- L. Chandra Kumar v. Union of India, (1997) 3 SCC 261 (Article 32 as basic-structure guarantee).
- Puttaswamy v. Union of India, (2017) 10 SCC 1 (right to privacy — where Article 21 privacy is invoked).
- Domain-specific landmark on the actual right at issue (do NOT invent).

**Do not do:**
- Do not use Article 32 for enforcement of a statutory right — it must be a Fundamental Right.
- Do not omit "LIST OF DATES AND EVENTS" — SC Registry requires it as a separate leaf.
- Do not cite Article 226 as the enabling provision.
"""


# ---------------------------------------------------------------------------
# Plaint — Civil Suit under CPC
# ---------------------------------------------------------------------------
PLAINT_CIVIL_SUIT = """## NICHE OVERLAY — PLAINT IN CIVIL SUIT (Code of Civil Procedure, 1908)

**Structural skeleton:**
1. Cause title — "IN THE COURT OF [___] AT [___]", "CIVIL SUIT NO. ___ OF [YEAR]", Plaintiff v. Defendant(s).
2. Party details — Plaintiff (name, age, occupation, address in the format Order VII Rule 1(a)-(b) requires); Defendant(s) same.
3. Prefatory paragraph — "The Plaintiff above named most respectfully begs to submit as under:—".
4. FACTS OF THE CASE — chronological, paragraph-numbered from 1 through the operative facts (parties, transaction, cause of action, breach / injury, notice, non-compliance).
5. CAUSE OF ACTION — a separate paragraph stating when and where the cause of action arose.
6. JURISDICTION — a separate paragraph invoking pecuniary and territorial jurisdiction (Sections 15-20 CPC), with the specific ground (defendant resides / carries on business / cause of action arose within jurisdiction).
7. LIMITATION — separate paragraph stating that the suit is within limitation, with the applicable Article of the Limitation Act, 1963.
8. VALUATION AND COURT FEE — separate paragraph valuing the suit and specifying the court fee paid, referring to the Court Fees Act / State Court Fees Act.
9. RELIEF / PRAYER — enumerated sub-lettered reliefs (a) main relief (b) interim relief if any (c) costs (d) omnibus.
10. Place / Date / Signature — of Plaintiff AND Advocate.
11. VERIFICATION under Order VI Rule 15 CPC — "I, [Name], the Plaintiff above named, do hereby verify that the contents of paragraphs [___] to [___] of the plaint are true to my personal knowledge and paragraphs [___] to [___] are true on advice received from counsel which I believe to be true. No part of it is false and no material has been concealed therefrom. Verified at [place] on this [day] of [month], [year]."
12. Schedule of Properties (in suits concerning immoveable property).
13. List of documents (Order XI CPC).

**Mandatory statutory anchors:**
- Order VII Rule 1 CPC (particulars of a plaint) — Party details paragraphs must satisfy every clause (a)-(k).
- Order VI Rule 15 CPC (verification) — Verification form is not optional and follows a fixed structure.
- Section 26 CPC read with Order IV Rule 1 (institution of suits).
- Sections 15-20 CPC (jurisdiction) — cite the specific section relied upon.
- The specific substantive Act invoked (Specific Relief Act, 1963 / Indian Contract Act, 1872 / Transfer of Property Act, 1882 / Sale of Goods Act, 1930 / etc.) — with exact sections.

**Do not do:**
- Do not skip the Cause of Action / Jurisdiction / Limitation / Valuation blocks — Order VII Rule 11 rejection is triggered by their absence.
- Do not conflate the verification form (Order VI Rule 15) with the affidavit-in-support of interim relief (Order XXXIX Rule 3 CPC).
- Do not cite Section 38 of the Specific Relief Act, 1963 for TEMPORARY injunction — that section governs PERMANENT injunctions only. For interim injunction cite Order XXXIX Rules 1 & 2 CPC + Section 94(c) CPC.
"""


# ---------------------------------------------------------------------------
# Written Statement — defendant's reply to a plaint
# ---------------------------------------------------------------------------
WRITTEN_STATEMENT = """## NICHE OVERLAY — WRITTEN STATEMENT (Defendant's Reply to Plaint, Order VIII CPC)

**Structural skeleton:**
1. Cause title — same as plaint, "WRITTEN STATEMENT ON BEHALF OF DEFENDANT NO. ___".
2. Prefatory paragraph — "The Defendant above named most respectfully submits his Written Statement in reply to the plaint of the Plaintiff as under:—".
3. PRELIMINARY OBJECTIONS — 3-8 numbered objections going to maintainability, jurisdiction, cause of action, limitation, non-joinder / mis-joinder of parties, absence of statutory notice, etc.
4. PARA-WISE REPLY TO THE PLAINT — walk EVERY paragraph of the plaint by its number and respond: "The contents of paragraph [n] of the plaint are DENIED / ADMITTED / NOT ADMITTED for want of knowledge / matters of record / a matter of law and require no reply." Where DENIED, add the Defendant's version in a follow-on sentence. This is Rule #1 of the niche — mirror the plaint's paragraph numbering one-for-one; do NOT collapse or renumber.
5. AFFIRMATIVE CASE OF THE DEFENDANT — the Defendant's own positive averments as a numbered narrative.
6. PRAYER — dismissal of the suit with costs; any counter-claim if applicable.
7. Place / Date / Signature — of Defendant AND Advocate.
8. VERIFICATION under Order VI Rule 15 CPC.

**Mandatory statutory anchors:**
- Order VIII Rule 1 CPC (time to file — 30 days from service of summons, extendable to 90 days for showing cause; 120 days in commercial suits) — always relevant background even if not cited in the WS itself.
- Order VIII Rules 2-5 CPC (specific denial of every allegation, effect of a general denial).
- Where a counter-claim is filed — Order VIII Rules 6A-6G CPC.

**Do not do:**
- Do not use a bare "not admitted" for every paragraph — Order VIII Rule 5 CPC deems any allegation not specifically denied to have been admitted.
- Do not omit any paragraph number from the para-wise reply — every plaint paragraph must be answered.
- Do not conflate the Written Statement with a Rejoinder (Rejoinder is the Plaintiff's reply to the WS, not the WS itself).
"""


# ---------------------------------------------------------------------------
# Rejoinder — Plaintiff's / Petitioner's reply to a Reply
# ---------------------------------------------------------------------------
RJOINDER_PLEADING = """## NICHE OVERLAY — REJOINDER PLEADING

**Structural skeleton:**
1. Cause title — same as main proceeding, "REJOINDER ON BEHALF OF THE PLAINTIFF / PETITIONER / CLAIMANT TO THE REPLY / WRITTEN STATEMENT / COUNTER-AFFIDAVIT OF RESPONDENT NO. ___".
2. Prefatory paragraph — "The Plaintiff / Petitioner / Claimant most respectfully submits his Rejoinder to the Reply / Written Statement / Counter-Affidavit filed by the Respondent as under:—".
3. PRELIMINARY SUBMISSIONS — the Petitioner's overarching stance on the Reply (evasive / non-responsive / suppresses material facts / raises inadmissible defences), with 3-6 numbered submissions.
4. PARA-WISE REPLY TO THE REPLY — walk EVERY paragraph of the Reply by its number, respond in the mirror form: "The contents of paragraph [n] of the Reply are DENIED / DENIED AS EVASIVE / DENIED AS INCORRECT / NOT DISPUTED to the extent [___]." Where denied, add the Rejoinder version and, if the Reply raised a new fact, respond to it directly by quoting or paraphrasing the specific assertion. This is Rule #1 of the niche.
5. REPLIES ARE NOT ADMISSIONS BLOCK — a paragraph clarifying that where a matter has not been separately replied to, the Petitioner reiterates and adopts the averments in the original plaint / petition / statement of claim and does not admit anything in the Reply.
6. PRAYER — reiterate the reliefs sought in the main proceeding; state that the Reply does not disclose any tenable defence and does not warrant deviation from those reliefs.
7. Place / Date / Signature.
8. VERIFICATION.

**Rule 1 — mirror the Reply's paragraph structure exactly.** A rejoinder that renumbers the Reply's paragraphs, collapses them, or replies out of order is a defective rejoinder. When your section responds to Reply Para 1, respond to it as "1." and start "The contents of paragraph 1 of the Reply are…". Continue for every numbered paragraph in the Reply, including sub-paragraphs.

**Do not do:**
- Do not raise wholly new grounds / reliefs in a Rejoinder — the pleading is a reply, not an amendment. Any new ground raised in the Reply may be addressed; new grounds not tied to the Reply belong in an amendment application.
- Do not repeat the entire plaint / petition — reference and adopt it.
- Do not cite case law in a Rejoinder unless the Reply raised a legal proposition that requires response.
"""


# ---------------------------------------------------------------------------
# Arbitration — Statement of Claim (Claimant's initial pleading before tribunal)
# ---------------------------------------------------------------------------
ARBITRATION_STATEMENT_OF_CLAIM = """## NICHE OVERLAY — STATEMENT OF CLAIM (Arbitration and Conciliation Act, 1996)

**Structural skeleton:**
1. Header — "BEFORE THE [SOLE ARBITRATOR / ARBITRAL TRIBUNAL] OF [names]", "IN THE MATTER OF: ARBITRATION UNDER [name of agreement / clause / statutory reference]", "STATEMENT OF CLAIM ON BEHALF OF THE CLAIMANT".
2. Memo of parties — Claimant, Respondent(s).
3. Introduction — parties, agreement giving rise to reference, arbitration clause / statutory provision under which reference lies.
4. STATEMENT OF FACTS — chronological, paragraph-numbered from 1; each contractual right, breach, and quantifiable head of loss is set out with its supporting document reference.
5. CAUSE OF ACTION / JURISDICTION — accrual of the disputes and the tribunal's mandate under the arbitration agreement + Section 16 A&C Act (Kompetenz-Kompetenz).
6. LIMITATION — Section 43 A&C Act read with Article 137 of the Limitation Act, 1963 for money claims; state date of accrual and date of invocation of arbitration.
7. CLAIMS AND HEADS OF LOSS — a numbered enumeration of each head (principal, damages, liquidated damages, interest, costs) with the quantum for each and the specific contractual / statutory basis.
8. INTEREST — pre-reference and pendente-lite interest under Section 31(7)(a) A&C Act; future interest under Section 31(7)(b).
9. COSTS — cost of the arbitration, tribunal's fees, and legal costs under Section 31A A&C Act.
10. RELIEFS SOUGHT — sub-lettered enumeration.
11. Place / Date / Signature.
12. VERIFICATION.
13. LIST OF DOCUMENTS relied upon.

**Mandatory statutory anchors:**
- Section 23 A&C Act (statements of claim and defence).
- Section 31(7)(a) and 31(7)(b) A&C Act (interest — pre-reference, pendente-lite, and future).
- Section 31A A&C Act (costs regime — mandatory to reason cost claims).
- Section 43 A&C Act (limitation applies as it would to a suit).
- The arbitration clause number and the specific paragraph of the underlying contract giving rise to each claim.

**Do not do:**
- Do not omit the interest and costs claims — Section 31(7) and 31A are default entitlements; if not claimed, the tribunal cannot award them.
- Do not conflate the Claimant's Statement of Claim with a Statement of Defence (Respondent's pleading) or a Rejoinder (Claimant's reply to the Statement of Defence).
- Do not cite Order VII CPC provisions — arbitration pleadings are governed by A&C Act + tribunal's procedural order, not the CPC.
"""


# ---------------------------------------------------------------------------
# Arbitration — Rejoinder (Claimant's reply to Statement of Defence / Counter-claim)
# ---------------------------------------------------------------------------
ARBITRATION_REJOINDER = """## NICHE OVERLAY — REJOINDER IN ARBITRATION

**Structural skeleton:**
1. Header — "BEFORE THE [SOLE ARBITRATOR / ARBITRAL TRIBUNAL]", "IN THE MATTER OF: [reference name]", "REJOINDER ON BEHALF OF THE CLAIMANT TO THE STATEMENT OF DEFENCE (AND COUNTER-CLAIM) OF THE RESPONDENT".
2. Prefatory paragraph.
3. PRELIMINARY SUBMISSIONS — 3-6 numbered submissions on the Respondent's defence (frivolous / dilatory / raises inadmissible defences).
4. PARA-WISE REPLY TO THE STATEMENT OF DEFENCE — walk EVERY paragraph of the SoD by its number, respond in mirror form: "The contents of paragraph [n] of the Statement of Defence are DENIED / DENIED AS EVASIVE / NOT DISPUTED to the extent [___]." Where denied, add the Claimant's version. Rule #1 of the niche.
5. REPLY TO COUNTER-CLAIM (if any) — treat the counter-claim as if it were a fresh Statement of Claim; para-wise reply + preliminary objections on maintainability, jurisdiction, limitation.
6. REITERATION OF PRAYER — reiterate the reliefs sought in the Statement of Claim; state that the Statement of Defence does not disclose any tenable defence.
7. Place / Date / Signature.
8. VERIFICATION.

**Rule 1 — mirror the Statement of Defence paragraph structure exactly.** Rejoinder is a reply, not an amendment. Every paragraph of the Statement of Defence must be answered by its own paragraph number.

**Mandatory statutory anchors:**
- Section 23(2A) A&C Act (parties may amend or supplement their statement unless the tribunal considers it inappropriate) — the Rejoinder is filed under the tribunal's procedural order in exercise of Section 23 read with Section 19 (procedural autonomy).
- Where interest is a live issue: reiterate Section 31(7)(a) A&C Act.

**Do not do:**
- Do not raise new heads of claim in a Rejoinder — those require an application to amend the Statement of Claim under Section 23(2A).
- Do not repeat the entire Statement of Claim — reference and adopt it.
- Do not omit the para-wise reply structure — a "general denial" is inadmissible in Indian arbitration practice.
"""


# ---------------------------------------------------------------------------
# Notice — Section 138 NI Act (statutory demand notice)
# ---------------------------------------------------------------------------
NOTICE_NI_ACT_S138 = """## NICHE OVERLAY — LEGAL NOTICE UNDER SECTION 138 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881

**Structural skeleton (this is a NOTICE, not a court pleading — no cause title, no verification, no prayer):**
1. Sender's letterhead / advocate's letterhead.
2. Date and reference number.
3. "By Registered Post AD / Speed Post AD / Electronic Mail" — mode of dispatch (mandatory for statutory limitation).
4. Addressee — the Drawer of the dishonoured cheque, full address (residential AND business, as available).
5. "Subject:" line — "STATUTORY DEMAND NOTICE UNDER SECTION 138 READ WITH SECTION 141 OF THE NEGOTIABLE INSTRUMENTS ACT, 1881".
6. Salutation — "Dear Sir / Madam".
7. Introductory paragraph — advocate's authority ("I have been instructed by my client [name], son / daughter of / carrying on business as [___], residing / carrying on business at [___] (hereinafter, my Client) to address the present notice to you as under:—").
8. NUMBERED FACT PARAGRAPHS — (i) source of the debt / liability; (ii) issuance of the cheque with cheque number, date, amount, drawee bank branch; (iii) presentation and dishonour with the specific date of the bank's return memo and the exact reason recorded on the memo (e.g. "Funds Insufficient", "Payment Stopped by Drawer", "Signature Differs", "Account Closed"); (iv) any prior demand and its outcome.
9. STATUTORY DEMAND paragraph — "In the circumstances aforesaid, my Client, through the undersigned, hereby demands from you the sum of Rs. [amount] being the cheque amount, together with interest at [rate]% per annum from the date of dishonour till realisation, WITHIN FIFTEEN (15) DAYS from the date of receipt of this notice, failing which my Client shall, without any further intimation, initiate criminal prosecution against you under Section 138 read with Sections 141 and 142 of the Negotiable Instruments Act, 1881, entirely at your risk as to costs and consequences."
10. Closing — "Yours faithfully".
11. Signature block — Advocate's name, enrolment number, address, contact.
12. "CC: my Client" (optional).

**Mandatory statutory anchors:**
- Section 138 NI Act, 1881 — the offence, ingredients (cheque, presented within validity, dishonoured, statutory notice within 30 days of receipt of bank memo, non-payment within 15 days of receipt of statutory notice).
- Section 138(b) — notice period of 30 days from receipt of the bank memo of dishonour to send the statutory demand notice.
- Section 138(c) — demand must give the drawer 15 days from receipt of notice to make payment. The notice MUST specify this period expressly.
- Section 141 NI Act — company / firm liability of directors / partners (cite where drawer is a company).
- Section 142 NI Act — cognizance and jurisdiction; complaint must be filed within one month of expiry of the 15-day payment period.

**Do not do:**
- Do not use a period other than "FIFTEEN (15) DAYS" — Section 138(c) is not extendable by the notice.
- Do not omit the specific dishonour reason from the bank memo — courts require the reason to be pleaded (Kusum Ingots v. Pennar Peterson, (2000) 2 SCC 745, on ingredients).
- Do not include a court-style Prayer or Verification — this is a demand notice, not a pleading.
"""


# ---------------------------------------------------------------------------
# Reply to a Legal Notice
# ---------------------------------------------------------------------------
REPLY_TO_LEGAL_NOTICE = """## NICHE OVERLAY — REPLY TO A LEGAL NOTICE

**Structural skeleton (this is a REPLY, not a court pleading):**
1. Letterhead of the replying advocate.
2. Date and reference number.
3. "By Registered Post AD / Speed Post AD / Electronic Mail" — mode of dispatch.
4. Addressee — the advocate who sent the original notice (name, address, enrolment number from the original notice); CC to the original client if named.
5. Subject line — "REPLY TO YOUR LEGAL NOTICE DATED [___] BEARING REFERENCE [___]".
6. Salutation — "Dear Sir / Madam".
7. Introductory paragraph — advocate's authority ("I have been instructed by my client [name] (hereinafter, my Client) to reply to your legal notice dated [___] served upon my Client on [___] as under:—").
8. PRELIMINARY SUBMISSIONS — 3-6 numbered submissions on the notice as a whole (baseless / misconceived / raises inadmissible claims / bad in law / concealed material facts).
9. PARA-WISE REPLY — walk EVERY numbered paragraph of the original notice and respond one-for-one: "The contents of paragraph [n] of the notice under reply are DENIED as false / DENIED as misleading / NOT ADMITTED for want of knowledge / a matter of record and require no reply." Where denied, give the correct version.
10. AFFIRMATIVE STANCE — my Client's positive version of the matter as a numbered narrative.
11. Closing paragraph — "In view of the foregoing, my Client denies the claims raised in the notice under reply, calls upon you to withdraw the notice unconditionally, and reserves the right to initiate appropriate legal proceedings in the event of any adverse action. This reply is without prejudice to my Client's rights and contentions."
12. Closing — "Yours faithfully".
13. Signature block — Advocate's name, enrolment number, address, contact.
14. "CC: my Client".

**Mandatory considerations:**
- Where the original notice invoked Section 138 NI Act (cheque dishonour), the reply MUST address each of the 138 ingredients (whether the cheque was issued, for what purpose, whether the debt is legally enforceable, whether payment was made prior to the 15-day period).
- Where the original notice invoked Section 8 of the Insolvency and Bankruptcy Code, 2016 (operational creditor demand notice), the reply MUST be within 10 days and MUST raise a pre-existing dispute if any (Mobilox Innovations v. Kirusa Software, (2018) 1 SCC 353).
- Where a limitation period runs against the client (e.g. 15 days u/s 138(c) NI Act), the reply should be dispatched before that period expires.

**Do not do:**
- Do not offer to pay unless the client so instructs — a partial acknowledgement in a reply can operate as an admission.
- Do not omit "without prejudice" where the reply contains any settlement-track discussion.
- Do not conflate the reply with a counter-claim — a reply denies; a counter-claim asserts. The two are separate.
"""


# ---------------------------------------------------------------------------
# Consumer Complaint — Consumer Protection Act, 2019
# ---------------------------------------------------------------------------
CONSUMER_COMPLAINT = """## NICHE OVERLAY — CONSUMER COMPLAINT (Consumer Protection Act, 2019)

**Structural skeleton:**
1. Cause title — "BEFORE THE [DISTRICT / STATE / NATIONAL] CONSUMER DISPUTES REDRESSAL COMMISSION AT [___]", "CONSUMER COMPLAINT NO. ___ OF [YEAR]", Complainant v. Opposite Party.
2. Memo of parties.
3. Introduction — statement that the Complainant is a "consumer" within the meaning of Section 2(7) of the Consumer Protection Act, 2019, and that the goods / services in question fall within Section 2(21) / 2(42).
4. JURISDICTION — pecuniary jurisdiction under Sections 34 / 47 / 58 CPA 2019 (District up to Rs. 50 lakh, State up to Rs. 2 crore, National above Rs. 2 crore); territorial jurisdiction under Section 34(2).
5. LIMITATION — Section 69 CPA 2019 (two years from the date on which the cause of action arose).
6. FACTS — chronological narrative, paragraph-numbered.
7. DEFICIENCY IN SERVICE / UNFAIR TRADE PRACTICE — separate paragraph aligning the OP's conduct with the statutory definitions in Sections 2(11) and 2(47) CPA 2019.
8. CAUSE OF ACTION — when and where the cause of action arose.
9. RELIEF — enumerated sub-lettered reliefs: (a) refund / replacement / removal of defect (b) compensation for loss / injury under Section 39 (c) punitive damages where warranted (d) costs (e) omnibus.
10. Place / Date / Signature — of Complainant AND Advocate.
11. VERIFICATION.
12. Affidavit-in-support (CPA 2019 requires the complaint be supported by an affidavit).
13. List of documents / Index of Annexures.

**Mandatory statutory anchors:**
- Section 2(7) CPA 2019 (definition of "consumer") — establish standing.
- Section 2(11) CPA 2019 ("deficiency") OR Section 2(47) ("unfair trade practice") — the specific mischief.
- Sections 35, 47, 58 CPA 2019 (jurisdiction of District / State / National Commission).
- Section 39 CPA 2019 (findings and reliefs the Commission may grant).
- Section 69 CPA 2019 (limitation).

**Do not do:**
- Do not file in the wrong forum — pecuniary jurisdiction is capped; over-invoicing to reach the State / National forum will lead to return / dismissal.
- Do not omit the affidavit — CPA 2019 mandates it.
- Do not conflate "deficiency" (post-transaction failure) with "unfair trade practice" (pre-transaction misrepresentation) — they attract different reliefs.
"""


# ---------------------------------------------------------------------------
# Cheque-bounce Criminal Complaint — Section 138 NI Act (post-notice)
# ---------------------------------------------------------------------------
CHEQUE_BOUNCE_COMPLAINT_S138 = """## NICHE OVERLAY — CRIMINAL COMPLAINT UNDER SECTION 138 NI ACT, 1881

**Structural skeleton:**
1. Cause title — "IN THE COURT OF [Ld. Metropolitan / Judicial] MAGISTRATE [___] AT [___]", "COMPLAINT CASE NO. ___ OF [YEAR]", Complainant v. Accused. If drawer is a company, name the company AND the directors sought to be prosecuted (Section 141 NI Act).
2. Memo of parties.
3. Prefatory paragraph — statement that the complaint is filed under Section 138 read with Sections 141 and 142 NI Act, 1881.
4. NUMBERED FACT PARAGRAPHS — (i) parties and relationship (ii) source of the legally enforceable debt (iii) issuance of the cheque with cheque number, date, amount, drawee bank branch (iv) presentation of the cheque with date of presentation (v) dishonour with date of the bank memo and the specific reason (vi) issuance of the statutory demand notice with date of dispatch, mode, and date of service (vii) expiry of the 15-day period without payment (viii) date of accrual of cause of action — the day after expiry of the 15 days.
5. CAUSE OF ACTION AND JURISDICTION — Section 142(1)(a) NI Act (cognizance on complaint); Section 142(2)(a) (territorial jurisdiction — location of the payee's bank branch, per amendment post-Dashrath Rupsingh).
6. LIMITATION — Section 142(1)(b) NI Act — complaint must be filed within one month of accrual of cause of action (subject to condonation under proviso).
7. PRAYER — take cognizance, issue summons to the Accused, try and convict for the offence under Section 138 NI Act, and impose the maximum fine (twice the cheque amount) plus compensation under Section 357 CrPC / 395 BNSS.
8. Place / Date / Signature — of Complainant AND Advocate.
9. VERIFICATION.
10. Affidavit-in-support u/s 145 NI Act (evidence on affidavit).
11. List of documents — original cheque, bank memo of dishonour, statutory notice, postal receipt, AD card / delivery report.

**Mandatory statutory anchors:**
- Section 138 NI Act — the offence; ALL ingredients pleaded (issuance, presentation within validity, dishonour, notice within 30 days of memo, 15-day period, non-payment).
- Section 138(b) — 30-day notice window from receipt of memo.
- Section 138(c) — 15-day payment window from receipt of notice.
- Section 141 NI Act — where drawer is a company, plead directorial liability.
- Section 142(1)(a) — cognizance on complaint of the payee.
- Section 142(2)(a) — territorial jurisdiction.
- Section 145 NI Act — evidence on affidavit.

**Doctrinal anchors:**
- Dashrath Rupsingh Rathod v. State of Maharashtra, (2014) 9 SCC 129 (superseded by 2015 amendment) → 2015 amendment restored payee-bank-branch jurisdiction; cite the amendment (Section 142(2) as substituted).
- K. Bhaskaran v. Sankaran Vaidhyan Balan, (1999) 7 SCC 510 (five ingredients of Section 138).

**Do not do:**
- Do not file beyond one month of accrual without a condonation application under the proviso to Section 142(1)(b).
- Do not omit the affidavit-in-support under Section 145 NI Act.
- Do not name a director without pleading the specific role of the director in the affairs of the company at the time of the offence (Section 141 requires this pleading — see S.M.S. Pharmaceuticals v. Neeta Bhalla, (2005) 8 SCC 89).
"""


# ---------------------------------------------------------------------------
# Matrimonial — Divorce Petition
# ---------------------------------------------------------------------------
MATRIMONIAL_PETITION_DIVORCE = """## NICHE OVERLAY — DIVORCE PETITION

**Structural skeleton:**
1. Cause title — "IN THE FAMILY COURT AT [___]" / "IN THE COURT OF THE DISTRICT JUDGE AT [___]", "H.M.A. PETITION NO. ___ OF [YEAR]" / equivalent, Petitioner v. Respondent.
2. Memo of parties — full names, ages, occupations, and CURRENT residential addresses of both spouses.
3. Prefatory paragraph — cite the correct personal-law statute: Hindu Marriage Act, 1955 (Section 13) / Muslim personal law / Special Marriage Act, 1954 (Section 27) / Indian Divorce Act, 1869 (Section 10) / Parsi Marriage and Divorce Act, 1936 / Foreign Marriage Act, 1969 — and state the specific ground(s) under the applicable section.
4. FACTS — (i) date and place of marriage; (ii) whether solemnised as per the personal-law form; (iii) place(s) of cohabitation with dates; (iv) issue of the marriage with names and dates of birth; (v) the specific facts constituting the ground(s) sought — dates, incidents, corroboration.
5. GROUND(S) FOR DIVORCE — a separate section naming the statutory ground(s) invoked: cruelty / desertion / adultery / conversion / unsoundness of mind / venereal disease / renunciation / presumption of death / mutual consent, with the specific sub-section, and the facts aligning with each.
6. JURISDICTION — Section 19 HMA / Section 31 SMA / equivalent — place of solemnisation of marriage / place where the parties last resided together / where the respondent resides / where the petitioner resides subject to conditions.
7. LIMITATION / COOLING PERIOD — where mutual-consent divorce, disclose the six-month cooling-off period under Section 13B(2) HMA and whether waiver is sought (Amardeep Singh v. Harveen Kaur, (2017) 8 SCC 746).
8. OTHER PROCEEDINGS — mandatory disclosure of any previous or pending matrimonial proceedings between the parties.
9. RELIEF — (a) decree of divorce dissolving the marriage; (b) custody of minor children if sought (this is generally a separate Guardian and Wards petition); (c) maintenance pendente lite under Section 24 HMA / equivalent; (d) permanent alimony under Section 25 HMA / equivalent; (e) costs; (f) omnibus.
10. Place / Date / Signature.
11. VERIFICATION.
12. Affidavit.

**Mandatory statutory anchors:**
- The correct personal-law statute AND section — cite them precisely; the choice depends on the parties' personal law and the form of marriage.
- Section 19 HMA (jurisdiction) or its equivalent under the applicable statute.
- Section 23 HMA — the court's duty to satisfy itself that no collusion / connivance / condonation.
- Where cruelty is a ground — cite Naveen Kohli v. Neelu Kohli, (2006) 4 SCC 558 and V. Bhagat v. D. Bhagat, (1994) 1 SCC 337 on mental cruelty.

**Do not do:**
- Do not name a personal-law statute the parties are not governed by — the wrong statute is fatal.
- Do not conflate mutual-consent divorce (Section 13B HMA) with contested divorce (Section 13 HMA) — they follow different procedures.
- Do not omit the "no collusion / connivance / condonation" averment in contested petitions — Section 23 HMA makes it a condition precedent to the decree.
"""


# ---------------------------------------------------------------------------
# NCLT — Section 241 / 242 Oppression and Mismanagement Petition
# ---------------------------------------------------------------------------
NCLT_OPPRESSION_PETITION = """## NICHE OVERLAY — PETITION UNDER SECTIONS 241 AND 242, COMPANIES ACT, 2013 (OPPRESSION AND MISMANAGEMENT)

**Structural skeleton:**
1. Cause title — "BEFORE THE NATIONAL COMPANY LAW TRIBUNAL, [___] BENCH AT [___]", "COMPANY PETITION NO. ___ OF [YEAR] UNDER SECTIONS 241 AND 242 OF THE COMPANIES ACT, 2013", Petitioner v. Respondents.
2. Memo of parties — Petitioner (shareholder(s)), Respondent No. 1 the Company, Respondents 2-N the majority shareholders / directors alleged to be oppressive.
3. STANDING / MAINTAINABILITY — Section 244 Companies Act, 2013 — the Petitioner satisfies the not-less-than 100 members OR not-less-than one-tenth of the members OR not-less-than one-tenth of the issued share capital OR the Tribunal has granted waiver under the proviso.
4. FACTS — chronological, paragraph-numbered from 1: (i) constitution of the Company (ii) shareholding of Petitioner and Respondents (iii) course of dealings between the parties (iv) the specific acts of oppression / mismanagement complained of, with dates and documents.
5. ACTS OF OPPRESSION — a numbered enumeration, each act aligned with the statutory language of Section 241(1)(a) ("prejudicial or oppressive to the Petitioner / prejudicial to the public interest / prejudicial to the interests of the Company") or 241(1)(b) (material change in ownership or control).
6. ACTS OF MISMANAGEMENT — separate numbered enumeration for conduct falling under Section 241(1)(a) but on the mismanagement limb.
7. JUST AND EQUITABLE — where winding-up would be just and equitable but relief under Section 242 is more appropriate, plead the ingredients.
8. RELIEFS — sub-lettered — regulation of the affairs of the Company; purchase of shares by other members or the Company (Section 242(2)(b)); termination or setting aside of prejudicial agreements; setting aside of transfers; removal of Managing Director / Manager; recovery of undue gains; costs; omnibus.
9. INTERIM RELIEF — status quo on shareholding, restraint on Board meetings, appointment of Interim Administrator / Observer.
10. Place / Date / Signature.
11. VERIFICATION.
12. Affidavit.

**Mandatory statutory anchors:**
- Section 241 Companies Act, 2013 (right to apply for relief).
- Section 242 Companies Act, 2013 (powers of the Tribunal to grant relief).
- Section 244 Companies Act, 2013 (right to apply — the standing threshold OR waiver).
- Rule 79 of the NCLT Rules, 2016 (form and content of the petition — Form NCLT-1 + accompanying documents).
- The specific provisions of the Articles of Association alleged to be breached.

**Do not do:**
- Do not file without meeting the Section 244 threshold OR without seeking waiver upfront — a threshold defect will lead to return.
- Do not conflate a Section 241 petition with a Section 213 investigation petition — different reliefs, different tests.
- Do not omit the "just and equitable" plea where the alternative is a Section 271 winding-up on that ground.
"""


# ---------------------------------------------------------------------------
# MACT Claim Petition — Motor Vehicles Act, 1988
# ---------------------------------------------------------------------------
MACT_CLAIM_PETITION = """## NICHE OVERLAY — MOTOR ACCIDENT CLAIM PETITION (Motor Vehicles Act, 1988)

**Structural skeleton:**
1. Cause title — "BEFORE THE MOTOR ACCIDENT CLAIMS TRIBUNAL AT [___]", "M.A.C.T. CLAIM PETITION NO. ___ OF [YEAR] UNDER SECTIONS 166 AND 168 OF THE MOTOR VEHICLES ACT, 1988", Claimant(s) v. Respondents (owner, driver, insurer).
2. Memo of parties — Claimant(s) (dependants of the deceased, or the injured), Respondent No. 1 the Owner of the offending vehicle, Respondent No. 2 the Driver, Respondent No. 3 the Insurer.
3. FACTS — (i) date, time, and place of the accident (ii) description of the accident (iii) offending vehicle details — make, registration, insurance policy number and validity (iv) the deceased / injured — age, occupation, monthly income at the time of the accident (v) FIR and police charge-sheet particulars (vi) injuries / death, treatment / post-mortem particulars.
4. CAUSE OF ACTION AND JURISDICTION — Section 166(2) MV Act — Tribunal within whose jurisdiction the accident occurred / Claimant resides / Respondent resides / carries on business.
5. RASH AND NEGLIGENT DRIVING — a separate paragraph attributing the accident to the rash and negligent driving of the Respondent driver of the offending vehicle, supported by the FIR / eye-witness account / MVI report.
6. HEADS OF DAMAGES — sub-lettered enumeration under the Sarla Verma / Pranay Sethi framework:
   (a) loss of dependency (income × dependency multiplier per Sarla Verma / Pranay Sethi; +40% for age <40 as future prospects for a permanent employee, +25% for age 40-50, +10% for age 50-60);
   (b) loss of consortium — Rs. 40,000 per dependant per Pranay Sethi;
   (c) loss of estate — Rs. 15,000 per Pranay Sethi;
   (d) funeral expenses — Rs. 15,000 per Pranay Sethi;
   (e) medical expenses (for injury claims) — with hospital bills;
   (f) pain and suffering / loss of amenities (for injury claims);
   (g) interest at the rate of 6-9% p.a. from the date of the claim till realisation.
7. INSURER'S LIABILITY — Section 149 MV Act (insurer's duty to satisfy the judgment).
8. PRAYER — grant of compensation quantified at Rs. [total], with interest, and costs; direction to Respondent No. 3 to satisfy the award.
9. Place / Date / Signature.
10. VERIFICATION.
11. Affidavit.
12. Annexures — copy of FIR, post-mortem / injury report, salary certificate, insurance policy.

**Mandatory statutory anchors:**
- Sections 166 and 168 MV Act, 1988.
- Section 149 MV Act (insurer's liability).
- Section 173 MV Act (appeal) — for background.

**Doctrinal anchors:**
- Sarla Verma v. Delhi Transport Corporation, (2009) 6 SCC 121 (multiplier method + future-prospects).
- National Insurance Co. Ltd. v. Pranay Sethi, (2017) 16 SCC 680 (Constitution Bench — future-prospects for self-employed, conventional heads at Rs. 15,000 / Rs. 40,000 / Rs. 15,000).
- Magma General Insurance Co. Ltd. v. Nanu Ram, (2018) 18 SCC 130 (per-dependant filial consortium).

**Do not do:**
- Do not use pre-Pranay Sethi conventional-head figures (Rs. 100 for funeral, Rs. 1,000 for consortium) — they were expressly overruled.
- Do not skip the future-prospects addition — Pranay Sethi mandates it as a matter of law.
- Do not omit interest — Tribunals routinely award 7.5-9% p.a. from the date of the claim petition.
"""


# ---------------------------------------------------------------------------
# Review Petition
# ---------------------------------------------------------------------------
REVIEW_PETITION = """## NICHE OVERLAY — REVIEW PETITION

**Structural skeleton:**
1. Cause title — "IN THE HIGH COURT / SUPREME COURT OF [___] AT [___]", "REVIEW PETITION (CIVIL / CRIMINAL) NO. ___ OF [YEAR] IN [name of decided proceeding and its number]", "[UNDER ARTICLE 137 CONSTITUTION OF INDIA / SECTION 114 & ORDER XLVII CPC / equivalent]".
2. Memo of parties — same as in the decided proceeding.
3. Prefatory paragraph — statement that the petition is filed for review of the judgment / order dated [___] passed by this Hon'ble Court in [___].
4. BRIEF FACTS — a short chronology of the decided proceeding (5-8 paragraphs).
5. GROUNDS FOR REVIEW — 3-6 numbered grounds, each strictly within the narrow scope of review:
   (i) discovery of new and important matter or evidence which, after the exercise of due diligence, was not within the knowledge of the Petitioner or could not be produced by him at the time when the order was made;
   (ii) mistake or error apparent on the face of the record;
   (iii) any other sufficient reason (analogous to (i) and (ii)).
6. ERROR APPARENT ON THE FACE OF THE RECORD — where invoked, identify the specific error with paragraph / line reference from the impugned order, and explain why it is apparent (i.e., not requiring reargument on the merits).
7. PRAYER — review of the impugned judgment / order dated [___]; recall / modification / setting aside as appropriate; costs.
8. Place / Date / Signature.
9. VERIFICATION.
10. Affidavit.
11. Certified copy of the impugned judgment / order.

**Mandatory statutory anchors:**
- Order XLVII Rule 1 CPC (grounds of review in civil matters before subordinate and High Courts).
- Section 114 CPC (right to apply for review).
- Article 137 of the Constitution (SC review jurisdiction).
- Order XLVII of the Supreme Court Rules, 2013 (SC review procedure).
- Section 362 CrPC / Section 403 BNSS (criminal review — court cannot alter or review its judgment except to correct clerical / arithmetical error) — cite where relevant.

**Doctrinal anchors:**
- Kamlesh Verma v. Mayawati, (2013) 8 SCC 320 (scope of review — narrow, not a rehearing).
- Northern India Caterers v. Lt. Governor of Delhi, (1980) 2 SCC 167 (error apparent on the face of the record).

**Do not do:**
- Do not rearrue the merits — review is not an appeal in disguise.
- Do not raise a new ground not argued in the original proceeding unless it falls squarely under Order XLVII Rule 1 (new and important matter with due diligence).
- Do not file beyond the limitation period — 30 days for HC review under the Limitation Act, 30 days for SC review under Order XLVII SC Rules, 2013.
"""


# ---------------------------------------------------------------------------
# Transfer Petition
# ---------------------------------------------------------------------------
TRANSFER_PETITION = """## NICHE OVERLAY — TRANSFER PETITION

**Structural skeleton:**
1. Cause title — "IN THE SUPREME COURT OF INDIA / HIGH COURT OF [___]", "TRANSFER PETITION (CIVIL / CRIMINAL) NO. ___ OF [YEAR]", "[UNDER SECTION 25 CPC / SECTION 447 BNSS (formerly SECTION 406 CrPC) / ARTICLE 139A CONSTITUTION]", Petitioner v. Respondent.
2. Memo of parties.
3. Prefatory paragraph — statement that the petition seeks transfer of [case name and number] from [transferor court] to [transferee court] on the grounds set out below.
4. FACTS — (i) parties and cause of action of the underlying proceeding; (ii) court in which the proceeding is currently pending; (iii) stage of the proceeding.
5. GROUNDS FOR TRANSFER — 3-6 numbered grounds, each fitting the statutory tests:
   For CPC transfer (Section 25 SC / Section 24 HC): expedient for the ends of justice — convenience of the parties and witnesses, avoidance of parallel proceedings, uniform adjudication.
   For CrPC / BNSS transfer (Section 447 BNSS): reasonable apprehension that a fair and impartial trial cannot be had in the transferor court, or expedient for the ends of justice.
   For matrimonial transfer petitions (SC): financial hardship, safety concerns, minor children with the transferor.
6. CONVENIENCE OF THE PARTIES — a separate paragraph analysing convenience of both parties, witnesses, and documents.
7. PRAYER — transfer of the case to the transferee court; interim stay of the proceedings before the transferor court pending disposal.
8. Place / Date / Signature.
9. VERIFICATION.
10. Affidavit.
11. Certified copy of the plaint / complaint / order sheet of the underlying proceeding.

**Mandatory statutory anchors:**
- Section 25 CPC (SC's power to transfer suits from one HC / subordinate court to another).
- Section 24 CPC (HC's power to transfer within its own jurisdiction).
- Section 447 BNSS / erstwhile Section 406 CrPC (SC's power to transfer criminal cases).
- Section 448 BNSS / erstwhile Section 407 CrPC (HC's power to transfer criminal cases).
- Article 139A of the Constitution (SC's power to transfer cases involving common questions of law from HCs to itself).

**Doctrinal anchors (cite selectively on point):**
- Krishna Veni Nagam v. Harish Nagam, (2017) 4 SCC 150 (matrimonial transfer petitions — video-conferencing option, before it was superseded).
- Santhini v. Vijaya Venketesh, (2018) 1 SCC 1 (superseded Krishna Veni's blanket video-conferencing mandate).

**Do not do:**
- Do not seek transfer merely because the Petitioner lost an interlocutory application in the transferor court — this is not a ground.
- Do not file a transfer petition where a remedy under the CPC / BNSS in the same court (e.g. change of Bench) is available.
- Do not confuse Section 25 CPC transfer with Section 24 CPC transfer — the former is between different High Courts / State jurisdictions; the latter is intra-State.
"""


# ---------------------------------------------------------------------------
# Contract / Agreement — Generic Commercial Contract (non-pleading)
# ---------------------------------------------------------------------------
CONTRACT_AGREEMENT_GENERIC = """## NICHE OVERLAY — COMMERCIAL CONTRACT / AGREEMENT

**Structural skeleton (this is NOT a court pleading — no cause title, no prayer, no verification):**
1. Title of the agreement (e.g. "MASTER SERVICES AGREEMENT", "SHARE PURCHASE AGREEMENT", "NON-DISCLOSURE AGREEMENT", "LEASE DEED").
2. Date of execution — "This [type] Agreement (this "Agreement") is executed on this [day] day of [month], [year]".
3. Parties block — full legal names, corporate identifiers (CIN / PAN / passport / Aadhaar as applicable), registered addresses, and defined-term abbreviations ("hereinafter referred to as the [Party 1]"), with the collective "the Parties" definition.
4. RECITALS ("WHEREAS clauses") — 3-8 numbered recitals establishing the commercial background, the parties' respective capacities, and the basis of the transaction.
5. "NOW THIS AGREEMENT WITNESSETH AS UNDER:—" — the operative preamble.
6. DEFINITIONS — a Clause 1 setting out defined terms in alphabetical order, each with a capital-initial definition.
7. OPERATIVE CLAUSES — numbered clauses on scope, consideration, payment terms, term and termination, representations and warranties, indemnity, limitation of liability, confidentiality, intellectual property, force majeure, dispute resolution, governing law and jurisdiction, notices, assignment, severability, entire agreement, amendment, counterparts.
8. DISPUTE RESOLUTION — a dedicated clause: (i) attempted amicable settlement; (ii) arbitration under the Arbitration and Conciliation Act, 1996, with number of arbitrators, seat and venue of arbitration, rules of arbitration, language; (iii) subject to arbitration, the courts of [seat] shall have exclusive jurisdiction (per Section 42 A&C Act, once a court is approached under the Act, all subsequent applications go to that court).
9. GOVERNING LAW — express choice of Indian law + specific State law where relevant.
10. NOTICES — mode, addresses of each Party, deemed-delivery timelines.
11. IN-WITNESS-WHEREOF and EXECUTION BLOCK — Party names, signatures, place of signature, name of witness, witness address, date.
12. SCHEDULES / ANNEXURES — separately paginated, cross-referenced from the operative clauses.

**Mandatory statutory anchors:**
- Indian Contract Act, 1872 — Sections 10 (essentials of a contract), 11 (competence), 23 (lawful consideration and object), 24-30 (specific voids), Sections 73-75 (damages).
- Sale of Goods Act, 1930 — for sale of goods contracts (Sections 2, 4, 15-17, 55, 57).
- Specific Relief Act, 1963 — Sections 10-14, 41 (specific performance and injunctions).
- Transfer of Property Act, 1882 — for lease deeds, sale deeds, mortgage deeds.
- Registration Act, 1908 — Sections 17 (documents requiring registration) and 49 (effect of non-registration) — CRITICAL for lease deeds > 12 months, sale deeds, mortgage deeds.
- Indian Stamp Act, 1899 + State Stamp Acts — Section 3 (documents chargeable) and the relevant Schedule I entry — CRITICAL for enforceability and admissibility.
- Arbitration and Conciliation Act, 1996 — Sections 7, 11, 20, 21 — for the dispute-resolution clause.

**Do not do:**
- Do not omit stamp-duty and registration considerations from a lease > 12 months / sale / mortgage — the deed is inadmissible in evidence without proper stamping (Section 35 Indian Stamp Act, 1899).
- Do not draft a court-styled document with a "Prayer" and "Verification" — this is a commercial contract, not a pleading.
- Do not conflate a "seat" of arbitration (juridical seat determining supervisory court) with a "venue" (physical place of hearing) — BGS SGS Soma JV v. NHPC, (2020) 4 SCC 234, on the distinction.
- Do not draft an exclusive-jurisdiction clause for a non-arbitrable matter without checking whether the chosen court has natural jurisdiction under Sections 15-20 CPC (an ouster to a court with no natural jurisdiction is void — Swastik Gases v. Indian Oil Corporation, (2013) 9 SCC 32).
"""


# ---------------------------------------------------------------------------
# Affidavit — Generic evidentiary or supporting affidavit
# ---------------------------------------------------------------------------
AFFIDAVIT_GENERIC = """## NICHE OVERLAY — AFFIDAVIT (Order XIX CPC / Section 297 BNSS, generic)

**Structural skeleton (this is a stand-alone AFFIDAVIT — no cause title unless it is filed in a proceeding, in which case use the proceeding's cause title):**
1. Cause title (if filed in a proceeding) — same as the main proceeding, "AFFIDAVIT ON BEHALF OF [name / Deponent's designation]".
2. AFFIDAVIT header — "I, [Full Name], [son / daughter / wife of Shri / Smt. ___], aged about [___] years, occupation [___], resident of [full residential address including PIN], do hereby solemnly affirm and state as under:—".
3. NUMBERED DEPOSITION PARAGRAPHS — starting from 1; each paragraph a single averment of fact within the Deponent's knowledge. Where the Deponent speaks on information believed to be true, state the source ("as informed to me by [___] which I believe to be true").
4. WITNESSING — a final numbered paragraph stating "The contents of paragraphs [___] to [___] of this affidavit are true to my personal knowledge and paragraphs [___] to [___] are true on information received from [source] which I believe to be true. Nothing material has been concealed therefrom."
5. Deponent's signature line — "DEPONENT" — with the Deponent's full name.
6. VERIFICATION — "Verified at [place] on this [day] day of [month], [year] that the contents of the above affidavit are true to my personal knowledge and information believed to be true, and that no part of it is false and nothing material has been concealed therefrom." — followed by Deponent's signature.
7. Attestation clause — where the affidavit is sworn before a Notary / Oath Commissioner / Judicial Magistrate, the attestation block ("Solemnly affirmed / sworn before me on this [day] day of [month], [year] at [place]") with the attesting officer's seal and signature.

**Mandatory statutory anchors:**
- Order XIX Rules 1-3 CPC (affidavits — form, contents, verification).
- Section 297 BNSS (formerly Section 297 CrPC) — affidavits in criminal proceedings.
- Sections 138-141 of the Indian Evidence Act, 1872 / Sections 145-148 BSA, 2023 (affidavit-based evidence).
- Notaries Act, 1952 — for notarised affidavits.
- Sections 191-193 IPC / Sections 227-229 BNS — perjury (false statement on oath).

**Do not do:**
- Do not mix argument or legal submission into the numbered deposition paragraphs — an affidavit deposes to FACTS. Legal submissions belong in a written note of arguments or the pleading itself.
- Do not sign the affidavit before it is attested — the deponent signs BEFORE the attesting authority.
- Do not omit the Deponent's residential address — a bare name-and-signature affidavit is defective.
- Do not omit the source of information for paragraphs deposed on information believed to be true — Order XIX Rule 3 CPC requires the source to be stated.
"""


# ---------------------------------------------------------------------------
# Registry — key → overlay lookup used by agents/drafting_niche.py
# ---------------------------------------------------------------------------
NICHE_OVERLAYS: dict[str, str] = {
    "bail_application_regular": BAIL_APPLICATION_REGULAR,
    "bail_application_anticipatory": BAIL_APPLICATION_ANTICIPATORY,
    "quashing_petition_bnss528": QUASHING_PETITION_BNSS528,
    "writ_petition_article_226": WRIT_PETITION_ARTICLE_226,
    "writ_petition_article_32": WRIT_PETITION_ARTICLE_32,
    "plaint_civil_suit": PLAINT_CIVIL_SUIT,
    "written_statement": WRITTEN_STATEMENT,
    "rejoinder_pleading": RJOINDER_PLEADING,
    "arbitration_statement_of_claim": ARBITRATION_STATEMENT_OF_CLAIM,
    "arbitration_rejoinder": ARBITRATION_REJOINDER,
    "notice_ni_act_s138": NOTICE_NI_ACT_S138,
    "reply_to_legal_notice": REPLY_TO_LEGAL_NOTICE,
    "consumer_complaint": CONSUMER_COMPLAINT,
    "cheque_bounce_complaint_s138": CHEQUE_BOUNCE_COMPLAINT_S138,
    "matrimonial_petition_divorce": MATRIMONIAL_PETITION_DIVORCE,
    "nclt_oppression_petition": NCLT_OPPRESSION_PETITION,
    "mact_claim_petition": MACT_CLAIM_PETITION,
    "review_petition": REVIEW_PETITION,
    "transfer_petition": TRANSFER_PETITION,
    "contract_agreement_generic": CONTRACT_AGREEMENT_GENERIC,
    "affidavit_generic": AFFIDAVIT_GENERIC,
}


def get_niche_overlay(niche_key: str | None) -> str:
    """Return the overlay for ``niche_key``, or empty string if unknown / none.

    Callers pass either a valid key from ``NICHE_KEYS`` or ``None`` /
    the sentinel ``"none"`` when the selector could not classify the
    matter. In all non-hit paths we return an empty string so the
    caller can safely concatenate without a special case.
    """
    if not niche_key or niche_key == "none":
        return ""
    return NICHE_OVERLAYS.get(niche_key, "")


__all__ = [
    "NICHE_KEYS",
    "NICHE_OVERLAYS",
    "get_niche_overlay",
]
