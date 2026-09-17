# bhc_quash_smoke

**Endpoint:** `http://localhost:5000/pyapi/chat`  
**Thread ID:** `0a467917-4436-4be3-a4c5-ce6fd418e5b0`  
**Elapsed:** 106.29s  
**TTFB:** 980 ms  
**Answer length:** 30,297 chars (4,827 words)  
**Raw SSE events:** 364  

## Prompt

```
Complete details on quashing of criminal complaint at Bombay high court and step by step guide
```

## Event-type counts

| Event type | Count |
|---|---:|
| `token` | 321 |
| `progress` | 28 |
| `status` | 8 |
| `agents_planned` | 1 |
| `context` | 1 |
| `done` | 1 |
| `followup_suggestions` | 1 |
| `response` | 1 |
| `sources` | 1 |
| `thread_id` | 1 |

**Agents planned:** `['Judgment', 'Legislation', 'SCI_Judgment']`  
**Unique progress steps (17):** ['classify', 'fallback', 'finalize', 'generate', 'history', 'language', 'metadata', 'parse', 'plan', 'prepare', 'react', 'relevance_check', 'results', 'rewrite', 'search', 'synthesize', 'validate']  

## Internal steps (chronological, token events collapsed)

| # | Elapsed | Type | Summary |
|---:|---:|---|---|
|    1 |    0.98s | `thread_id` | data='0a467917-4436-4be3-a4c5-ce6fd418e5b0' |
|    2 |    0.98s | `progress` | step='validate' message='Validating query...' agent='guardrail' |
|    3 |    1.38s | `status` | message='Validating query...' agent='guardrail_input' |
|    4 |    1.38s | `progress` | step='language' message='Detecting language...' agent='memory' |
|    5 |    1.38s | `progress` | step='language' message='Detected: en' agent='memory' |
|    6 |    1.38s | `progress` | step='history' message='Loading conversation history...' agent='memory' |
|    7 |    1.39s | `progress` | step='history' message='Found 0 previous turns' agent='memory' |
|    8 |    1.40s | `progress` | step='rewrite' message='Rewriting follow-up query...' agent='memory' |
|    9 |    1.40s | `status` | message='Loading context...' agent='memory' |
|   10 |    1.40s | `context` |  |
|   11 |    1.40s | `progress` | step='classify' message='Understanding your question...' agent='orchestrator' |
|   12 |    5.92s | `progress` | step='classify' message='Identified: Judgment, Legislation' agent='orchestrator' |
|   13 |    5.92s | `progress` | step='plan' message='Preparing search queries for 3 agents...' agent='orchestrator' |
|   14 |    8.18s | `status` | message='Planning search strategy...' agent='orchestrator_plan' |
|   15 |    8.19s | `agents_planned` | agents=["Judgment", "Legislation", "SCI_Judgment"] |
|   16 |    8.20s | `progress` | step='metadata' message='Extracting case details from query...' agent='judgment' |
|   17 |    8.20s | `progress` | step='search' message='Searching court judgments (2 strategies in parallel)...' agent='judgment' |
|   18 |    8.20s | `progress` | step='parse' message='Parsing query for section references...' agent='legislation' |
|   19 |    8.20s | `progress` | step='parse' message='Detected sections: 482' agent='legislation' |
|   20 |    8.20s | `progress` | step='search' message='Searching legislation database...' agent='legislation' |
|   21 |    8.20s | `progress` | step='prepare' message='Preparing Supreme Court search...' agent='sci_judgment' |
|   22 |    8.20s | `progress` | step='react' message='Running multi-step research (ReAct agent)...' agent='sci_judgment' |
|   23 |   21.46s | `progress` | step='search' message='Found 5 judgments via case_type:quashing' agent='judgment' |
|   24 |   21.46s | `progress` | step='search' message='Top match: Prakash Piniomal Haswani vs State of Maharashtra' agent='judgment' |
|   25 |   21.46s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='judgment' |
|   26 |   21.46s | `progress` | step='search' message='Found 5 matching provisions' agent='legislation' |
|   27 |   21.46s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='legislation' |
|   28 |   24.30s | `progress` | step='generate' message='Generating response with citations...' agent='judgment' |
|   29 |   25.54s | `progress` | step='fallback' message="Retrieved provisions don't match the query — searching the web..." agent='legislation' |
|  101 |   40.20s | `status` | message='Searching court judgments...' agent='judgment' |
|  352 |   46.78s | `status` | message='Searching legislation...' agent='legislation' |
|  353 |   82.79s | `progress` | step='react' message='Completed 10 search steps' agent='sci_judgment' |
|  354 |   82.79s | `progress` | step='results' message='Found 5 Supreme Court judgments' agent='sci_judgment' |
|  355 |   82.79s | `progress` | step='generate' message='Generating response...' agent='sci_judgment' |
|  356 |   82.80s | `status` | message='Searching Supreme Court judgments...' agent='sci_judgment' |
|  357 |   82.82s | `progress` | step='synthesize' message='Merging results from 3 agents...' agent='orchestrator' |
|  358 |   82.82s | `status` | message='Injecting citations into draft...' agent='orchestrator_synthesize' |
|  359 |   82.83s | `progress` | step='finalize' message='Finalizing response...' agent='guardrail' |
|  360 |   82.83s | `status` | message='Finalizing...' agent='guardrail_output' |
|  361 |   83.99s | `response` |  |
|  362 |   83.99s | `sources` | data=[{"source_type": "judgment", "title": "Prakash Piniomal Haswani vs State of Maharashtra", "content": ["16-wp-2676-2014\nIN THE HIGH COURT OF JUDICATURE AT BOMBAY\nCRIMINAL APPELLATE JURISDICTION\nCRIMINAL WRIT PETITIO... |
|  363 |  106.24s | `followup_suggestions` | data=["What are common grounds for quashing?", "What documents are needed for filing?", "How long does the process take?"] |
|  364 |  106.29s | `done` |  |

## Final response

The Bombay High Court has the power to quash criminal complaints under Article 226 of the Constitution of India and
Section 482 of the Code of Criminal Procedure, 1973. This power is not limited by the restrictions imposed on a
Magistrate under Section 320 of the Code of Criminal Procedure, which deals with the compounding of offences. The High
Court can exercise its inherent powers to quash criminal proceedings, FIRs, or complaints, even for non-compoundable
offences, if it is satisfied that the matter has been amicably settled between the parties and that quashing the
complaint would be in the interest of justice, the complainant, and/or the victim. However, the Court exercises due care
and caution, especially in cases involving offences like Section 498A of the Indian Penal Code, 1860, which are
considered to be against society at large, and will not quash a complaint merely on consent if it is not satisfied that
it is in the interest of the complainant and/or victim.

I think this information will help you more to understand the case:

### Case: *Prakash Piniomal Haswani v. State of Maharashtra & Ors.*, Criminal Writ Petition No. 2676 of 2014 (Bombay High Court, 2014)

#### Key Legal Issues
- Whether a criminal complaint can be quashed under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, 1973, when the trial has commenced and is at its final stage.
- Whether an undertaking given by the complainant in a previous court proceeding to consent to the quashing of a criminal complaint is binding, especially if the application for dropping the proceeding was unconditionally withdrawn by the petitioner.
- The scope of the High Court's inherent powers to quash criminal proceedings, particularly in cases involving Section 498A of the Indian Penal Code, 1860.

#### Detailed Narrative
The petitioner and respondent No. 2 were married but could not live together, leading to a divorce petition in the
Family Court, Bandra, which granted a decree of divorce on January 12, 2004. An appeal against this order was filed by
the wife in the High Court. A criminal complaint was also filed, and an FIR was registered on December 16, 2002, for
offences under Sections 232, 420, 406, 497, 498A, 506(ii) read with Section 34 of the Indian Penal Code, 1860. During
the appeal proceedings, consent terms were filed, agreeing that Rs. 10,00,000/- would be paid to the wife as full and
final settlement, and she undertook to give no objection for quashing the criminal complaint. The petitioner alleged
that the respondent-wife violated this undertaking by assisting the Public Prosecutor and giving evidence against him.
The petitioner also stated that he could not attend the Metropolitan Magistrate Court due to his advocate's advice,
spondylosis, and his brother's death. The respondent-wife, appearing in person, submitted that the petitioner had
unconditionally withdrawn an application for dropping the proceeding based on the consent terms, which caused her
harassment. She claimed she was compelled to accept the settlement due to her financial circumstances as a housewife.
She further argued that the case had proceeded for six years and was at its fag end, and therefore, the High Court
should not interfere.

#### As per the Court
The Bombay High Court observed that since the petitioner had unconditionally withdrawn the application for dropping the
proceeding based on the consent terms, it was not open for him to approach the Court under Article 226 of the
Constitution of India read with Section 482 of the Code of Criminal Procedure, 1973, for quashing the complaint,
especially when the trial had commenced and reached its final stage. The Court reiterated the settled position that once
a trial commences, it must be concluded either in acquittal or conviction. The Court also held that merely because an
undertaking was given by a party in a former proceeding to consent to the quashing of a complaint, it does not
automatically entitle the accused to have the criminal complaint quashed. Relying on *B.S. Joshi v. State of Haryana,
2003(4) SCC 675*, the Court acknowledged its inherent powers to quash criminal proceedings but emphasized that such
power should be exercised only if it is in the best interest of the wife, family, and children. The Court noted that an
offence under Section 498A of the Indian Penal Code, 1860, is not merely a personal offence but is against society at
large, and therefore, due care and caution must be exercised before quashing such complaints. The Court also considered
the allegations of harassment and the financial vulnerability of the wife. The Court further noted the delay of six
years in filing the petition after the undertaking was given, and that a contempt petition would be time-barred under
Section 20 of the Contempt of Court Act, 1971.

#### Judgment & Direction
The Writ Petition was dismissed, as the Court found it impossible to quash the proceeding at its final stage, taking an overall view of the matter.

### Case: *Michelle M. Desai v. Shirin R. Majai & Anr.*, Criminal Writ Petition No. 1279 of 2005 (Bombay High Court, 2005)

#### Key Legal Issues
- Whether the High Court can quash a criminal complaint under Section 482 of the Code of Criminal Procedure, 1973, even if some of the offences are non-compoundable under Section 320 of the Code, when the parties have amicably settled the dispute.

#### Detailed Narrative
The petitioner, the original complainant, lodged an FIR on June 1, 1996, for offences under Sections 324, 341, 342, 506
(Part 2), and 114 of the Indian Penal Code, 1860, against respondent No. 1, who was her paternal aunt. After the
complaint, charges were framed under Sections 324 and 506 (Part 2) of the Indian Penal Code, 1860. Two civil suits filed
on the Original Side of the High Court were settled out of court by filing consent terms. Subsequently, an application
was made to the Metropolitan Magistrate for compounding the offence. However, the Magistrate refused permission to
compound the offence under Sections 324 and 34 of the Indian Penal Code, 1860, because the offence under Section 506
(Part 2) is not compoundable as per Section 320 of the Code of Criminal Procedure, 1973. The petitioner then approached
the High Court under Section 482 of the Code of Criminal Procedure, 1973, to quash the criminal complaint.

#### As per the Court
The Bombay High Court reiterated the well-settled law that the inherent power of the High Court under Section 482 of the
Code of Criminal Procedure, 1973, is not curtailed or limited by the provisions of Section 320 of the Code. The Court
referred to *B.S. Joshi v. State of Haryana, JT 2003(3) SC 277*, which held that the High Court, in exercise of its
inherent powers, can quash criminal proceedings or FIR or complaint, and Section 320 of the Code does not limit or
affect these powers. Since the parties had amicably settled the matter and did not wish to proceed with the criminal
litigation, the Court found it a fit case for quashing the criminal complaint.

#### Judgment & Direction
The criminal writ petition was allowed. Criminal case No. 551/P/1996 pending before the Additional Chief Metropolitan Magistrate, 9th Court, Bandra, Mumbai, was quashed, and FIR No. 271/1996 lodged by the petitioner on June 1, 1996, was also quashed and set aside.

### Case: *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.*, Writ Petition No. 4386 of 2014 (Bombay High Court, 2014)

#### Key Legal Issues
- Whether a criminal complaint should be quashed when the original complainant seeks to quash it, alleging it was falsely registered, but the accused has a history of similar complaints.

#### Detailed Narrative
The original complainant (petitioner) filed this petition seeking to quash a complaint he had filed against Dattatray
Ramchandra Jadhav for offences punishable under Sections 387 and 326 of the Indian Penal Code, 1860. The petitioner
claimed that the complaint was falsely registered at the instance of the complainant, pursuant to an interview given by
the Superintendent of Police, and that he had never filed such a complaint. He also submitted that a senior advocate was
sought to be prosecuted based on this complaint. The learned APP, on the other hand, submitted that several complaints
had been pending against Dattatray Jadhav for the last 10 years, and there was no reason for the police to lodge a false
case at the instance of the petitioner.

#### As per the Court
The Bombay High Court observed that the accused, Dattatray Jadhav, appeared to be a history-sheeter with numerous
pending cases against him. The Court was not inclined to quash the complaint filed by the petitioner because there was a
possibility of the petitioner being pressurized by the accused to file the petition for quashing the complaint. The
Court found no reason for the police to file a false complaint through the petitioner or any other person. Therefore,
the Court was not inclined to exercise its jurisdiction under Article 226 of the Constitution of India or Section 482 of
the Code of Criminal Procedure, 1973.

#### Judgment & Direction
The Writ Petition was dismissed.

### Case: *Freddy Sunderdas Thomas v. The State of Maharashtra and others*, Criminal Writ Petition No. 1080 of 2005 (Bombay High Court, 2005)

#### Key Legal Issues
- Whether a Magistrate can pass an order under Section 156(3) of the Code of Criminal Procedure, 1973, directing investigation when the complainant has already applied for withdrawal of the complaint due to a settlement.
- The High Court's inherent powers under Section 482 of the Code of Criminal Procedure, 1973, to quash a criminal complaint in a non-compoundable case where the parties have settled the dispute.

#### Detailed Narrative
The petitioner, original accused No. 1, had received finance from respondent No. 2 (M/s. Ashok Leyland Finance Company)
for purchasing two trucks. After paying five monthly instalments, the petitioner defaulted. The company filed a
complaint with the Additional Chief Metropolitan Magistrate, 47th Court, Esplanade, Mumbai, seeking an order under
Section 156(3) of the Code of Criminal Procedure, 1973. In the meantime, an arbitrator was appointed, and the dispute
was settled. The amount due to respondent No. 2 was paid by respondent No. 4, who purchased the truck, and the
petitioner gave his no-objection for the transfer of ownership. Subsequently, the complainant company applied to the
Magistrate for permission to withdraw the complaint, stating that the matter was settled. However, the Magistrate
dismissed the application and proceeded to pass an order under Section 156(3) of the Code of Criminal Procedure, 1973,
on December 9, 2004. Consequently, the Cuffe Parade Police Station registered a complaint (MECR/1/2005) against the
petitioner. The petitioner and another accused were arrested and remanded to police custody, then judicial custody. On
March 17, 2005, the complainant company again submitted a letter for withdrawal, confirming payment of Rs. 15,02,000/-.
The petitioners were then released on bail, leading to the filing of the present petition for quashing the complaint.

#### As per the Court
The Bombay High Court held that the learned Magistrate clearly erred in passing an order under Section 156(3) of the
Code of Criminal Procedure, 1973, when the complainant had already applied for withdrawal of the complaint and
explicitly stated that the matter was settled. The Court noted that while a Magistrate does not have the power to
compound a non-compoundable offence under Section 320 of the Code of Criminal Procedure, 1973, there was no necessity to
direct an investigation under Section 156(3) in such circumstances. Relying on *B.S. Joshi and Ors. v. State of Haryana
and Anr., 2003 Cri. L.J. 2028*, the Court reiterated that its inherent powers under Section 482 of the Code of Criminal
Procedure, 1973, are not curtailed by Section 320, allowing it to quash criminal proceedings even in non-compoundable
cases where a settlement has been reached. Considering the peculiar facts and circumstances, the Court found the
complaint liable to be quashed.

#### Judgment & Direction
The Writ Petition was allowed. The criminal complaint filed by respondent No. 2 in the Court of the Metropolitan Magistrate (MECR/1/2005 dated February 7, 2005) registered by Cuffe Parade Police Station for offences under Sections 379, 409, and 420 of the Indian Penal Code, 1860, was quashed and set aside. The bonds executed by the complainant, respondent No. 2, respondent No. 4, and the petitioners were cancelled, and sureties were discharged.

### Case: *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr.*, Criminal Application No. 2689 of 2010 (Bombay High Court, 2010)

#### Key Legal Issues
- Whether a criminal complaint for offences under Sections 465, 468, 471, and 420 of the Indian Penal Code, 1860, can be quashed by exercising inherent jurisdiction under Section 482 of the Code of Criminal Procedure, 1973, when the parties have amicably settled a personal dispute.

#### Detailed Narrative
This application was filed for quashing a criminal complaint registered with the Gorai Police Station, Mumbai, as C.R. No. 6/2009, for offences punishable under Sections 465, 468, 471, and 420 of the Indian Penal Code, 1860, filed by respondent No. 2 against the applicants. The parties had amicably settled the dispute, and the dispute between them was personal in nature.

#### As per the Court
The Bombay High Court found no impediment to quash the criminal complaint by exercising its inherent jurisdiction under Section 482 of the Code of Criminal Procedure, 1973, given that the parties had amicably settled the dispute and it was a personal matter. The Court referred to the judgments of the Apex Court in *Madan Mohan Abbot v. State of Punjab, AIR 2008 SC 1969* and *Jagdish Chanana v. State of Haryana, AIR 2008 SC 1968*.

#### Judgment & Direction
The application was allowed in terms of prayer clause (a), and the criminal complaint was accordingly disposed of.

---

## Statutory Provisions Referenced

Section 482 of the Code of Criminal Procedure, 1973, confers inherent powers upon the High Court to make such orders as may be necessary to give effect to any order under the Code, or to prevent abuse of the process of any Court, or otherwise to secure the ends of justice. These powers are extraordinary and are to be exercised sparingly, only in exceptional circumstances.

It is important to note that the Code of Criminal Procedure, 1973, has been replaced by the Bharatiya Nagarik Suraksha Sanhita (BNSS), 2023. Under the Bharatiya Nagarik Suraksha Sanhita, 2023, Section 482 specifically governs anticipatory bail, replacing Section 438 of the Code of Criminal Procedure, 1973.

### Grounds for Quashing a Criminal Complaint

The High Court may exercise its inherent powers under Section 482 of the Code of Criminal Procedure, 1973, to quash a criminal complaint or First Information Report (FIR) on various grounds, including but not limited to:
- **No Offence Disclosed**: Where the allegations made in the FIR or complaint, even if taken at their face value and accepted in their entirety, do not prima facie constitute any offence or make out a case against the accused.
- **Absurd or Improbable Allegations**: Where the allegations in the FIR are so patently absurd and inherently improbable that no prudent person can ever reach a conclusion that there is sufficient ground for proceeding against the accused.
- **Abuse of Process of Court**: To prevent the abuse of the process of any court. This includes cases where the criminal proceeding is apparently prompted by mala fides and instituted with an ulterior motive, such as harassment or personal vendetta.
- **Legal Bar to Proceedings**: Where there is an express legal bar against the institution or continuance of the proceedings.
- **Predominantly Civil Nature**: Where the criminal complaint is essentially a civil dispute given a criminal colour to gain collateral advantage, and the allegations lack any criminal element.
- **Compromise between Parties**: In certain non-heinous offences, particularly those of a private nature or arising out of matrimonial or family disputes, where the parties involved have reached a genuine settlement and the continuation of legal proceedings would serve no purpose. However, this power is not exercised in serious offences involving mental depravity or offences against society at large, such as murder, rape, or dacoity.
- **Lack of Evidence**: When there is an absence of prima facie evidence to support the charges, or the evidence adduced clearly or manifestly fails to prove the charge.
- **Post-Conviction Matters**: The inherent powers can also be exercised in post-conviction matters when an appeal against the conviction is pending before a higher judicial forum.

### Step-by-Step Guide for Quashing a Criminal Complaint at the Bombay High Court

The procedure for filing a quashing petition before the Bombay High Court under the inherent powers (previously Section 482 of the Code of Criminal Procedure, 1973) generally involves the following steps:

1. **Drafting the Petition**: A detailed quashing petition, typically a Criminal Application, must be drafted. This petition should clearly set out the facts of the case, the FIR/complaint details, the grounds on which quashing is sought, and legal arguments supported by relevant case law.
2. **Affidavit in Support**: The petition must be supported by an affidavit sworn by the petitioner, affirming the truthfulness of the statements made in the petition.
3. **Annexures**: All relevant documents, such as copies of the FIR/complaint, charge sheet (if filed), evidence, and any other supporting material, must be annexed to the petition. In cases of compromise, the settlement agreement or consent terms should also be attached.
4. **Filing the Petition**: The petition, along with the affidavit and annexures, is filed with the Registry of the Bombay High Court.
5. **Court Fees**: Appropriate court fees must be paid as per the rules of the High Court.
6. **Service of Notice**: Notice of the petition is typically served on the respondent(s), which usually includes the State (through the Public Prosecutor) and the complainant.
7. **Hearing and Arguments**: The High Court will hear arguments from both sides. The petitioner's counsel will argue the grounds for quashing, while the respondent's counsel will present their opposition. The High Court examines the allegations on their face value without a deep inquiry into the evidence at this stage.
8. **Court's Decision**: After hearing the arguments and considering the material on record, the High Court will pass an order either quashing the criminal complaint/FIR/proceedings or dismissing the petition. The Court's decision is discretionary and aims to prevent abuse of the process of law and secure the ends of justice.

The High Court may refuse to exercise its discretion if a party has not approached it with clean hands.

---

## Related Supreme Court Authority

The Supreme Court has laid down several landmark rulings regarding the quashing of criminal complaints, primarily under Section 482 of the Code of Criminal Procedure, 1973 (Cr.P.C.). The power to quash criminal proceedings is to be exercised sparingly and with caution, only in exceptional cases to prevent abuse of the process of any court or otherwise to secure the ends of justice.

Here are some key principles and cases:

### Case: *CHANCHALPATI DAS v. THE STATE OF WEST BENGAL*, Crl.A. No.-001592-001592 - 2023 (Supreme Court, 2023)

#### Key Legal Issues
- Whether an inordinate and unexplained delay in filing a criminal complaint can be a ground for quashing the proceedings.
- Whether the allegations in the complaint and charge-sheet, even if taken at face value, disclose the commission of any offense.
- The scope of inherent powers of the High Court under Section 482 Cr.P.C. to quash criminal proceedings.

#### Detailed Narrative
The appellants, Madhu Pandit Das (President of ISKCON, Bengaluru) and Chanchalpati Das (Vice President of ISKCON,
Bengaluru), faced charges under Sections 468, 471, 406, and 120-B of the Indian Penal Code (IPC) based on an FIR
registered in 2009. The complaint alleged theft and criminal breach of trust concerning a bus belonging to ISKCON,
Kolkata, which was purportedly taken to Bengaluru in 2001-2002. The complainant claimed to have reported the matter to
the police in 2002 and 2006, but no action was taken, leading to a private complaint in 2009. The High Court dismissed
their petitions for quashing the charge-sheet.

#### As per the Court
The Supreme Court observed that there was an inordinate and unexplained delay of eight years in filing the complaint
regarding an alleged incident from 2001. The Court found it unbelievable that a powerful body like ISKCON Kolkata would
remain silent for such a long period if the bus was indeed precious to them. The Court held that such an inordinate
delay itself was a sufficient ground to quash the proceedings, as it amounted to a sheer misuse and abuse of the process
of the court. Furthermore, the Court noted that a bare perusal of the complaint and charge-sheet revealed only bald
allegations without any material or document to substantiate the offenses. Even after investigation, no evidence was
collected to prima facie satisfy the ingredients of the alleged offenses. The Court reiterated the guidelines from
*State of Haryana v. Bhajan Lal* (1992 Supp. (1) SCC 335) for exercising powers under Section 482 Cr.P.C., emphasizing
that proceedings can be quashed if the allegations do not prima facie constitute any offense, are absurd and improbable,
or are manifestly attended with mala fide intentions.

#### Judgment & Direction
The Supreme Court allowed the appeals, quashed the criminal proceedings pending against the appellants in the Court of Chief Judicial Magistrate, Alipore, arising out of FIR No. 33 of 2009, and imposed a cost of Rs. 1,00,000/- on the respondent-complainant.

**PDF Links:**
[Judgment PDF](https://api.sci.gov.in/supremecourt/2017/17824/17824_2017_4_1502_44572_Judgement_18-May-2023.pdf)

### Case: *KAHKASHAN KAUSAR @ SONAM v. THE STATE OF BIHAR*, Crl.A. No.-000195-000195 - 2022 (Supreme Court, 2022)

#### Key Legal Issues
- Whether general and omnibus allegations against in-laws in a matrimonial dispute under Section 498A IPC are liable to be quashed.
- The misuse of Section 498A IPC and the need for courts to be cautious in proceeding against distant relatives.
- Whether a subsequent FIR based on similar allegations constitutes a fresh cause of action or an abuse of process.

#### Detailed Narrative
The appellants, who were the in-laws of the complainant, challenged an FIR registered under Sections 341, 323, 379, 354,
498A read with Section 34 IPC. The complainant had previously filed a criminal complaint in 2017 with similar
allegations, where the Magistrate found no prima facie case against the in-laws due to the omnibus nature of the
allegations. Subsequently, in 2019, a fresh FIR was filed alleging continued dowry demand and harassment. The Patna High
Court dismissed the petition for quashing the FIR, stating that the averments disclosed a prima facie commission of an
offense.

#### As per the Court
The Supreme Court expressed concern over the misuse of Section 498A IPC and the increasing tendency to implicate
relatives of the husband in matrimonial disputes without specific allegations. Relying on precedents like *Arnesh Kumar
v. State of Bihar and Anr.* (2014) 8 SCC 273 and *Preeti Gupta & Anr. v. State of Jharkhand & Anr.* (2010) 7 SCC 667,
the Court emphasized that general and omnibus allegations, without attributing specific roles to each accused, do not
warrant prosecution. The Court found that the allegations against the in-laws were general and omnibus, failing to
establish specific instances of their involvement. While acknowledging that two FIRs could constitute independent
instances, the present complaint lacked clear allegations against the in-laws, leading to an abuse of the process of
law.

#### Judgment & Direction
The Supreme Court set aside the impugned order of the Patna High Court and quashed FIR No. 248 of 2019 against the appellants (in-laws) under Sections 341, 323, 379, 354, 498A read with Section 34 IPC.

**PDF Links:**
[Judgment PDF](https://api.sci.gov.in/supremecourt/2020/25575/25575_2020_37_1501_33195_Judgement_08-Feb-2022.pdf)

### Case: *PADAL VENKATA RAMA REDDY @ RAMU v. KOVVURI SATYANARAYANA REDDY*, Crl.A. No.-001499-001499 - 2011 (Supreme Court, 2011)

#### Key Legal Issues
- The scope and exercise of inherent powers of the High Court under Section 482 Cr.P.C. to quash criminal proceedings.
- Whether the High Court can analyze the probabilities of the prosecution version or the allegations of mala fides at the stage of quashing.
- The circumstances under which a criminal proceeding, even if it involves a civil wrong, can be quashed.

#### Detailed Narrative
The appellant, the defacto complainant, challenged an order of the Andhra Pradesh High Court which quashed criminal
proceedings against Respondent Nos. 1-3 (accused persons) under Section 482 Cr.P.C. The case involved allegations of
criminal conspiracy, assault, and attempt to murder due to political rivalry. The FIR did not initially name the
respondents as accused, but the charge-sheet and witness statements implicated them based on motive, circumstances, and
call records. The High Court quashed the proceedings, concluding that the materials were inadequate and the ingredients
of the alleged offenses were not made out.

#### As per the Court
The Supreme Court discussed the inherent powers of the High Court under Section 482 Cr.P.C., reiterating that these
powers are to be exercised sparingly, carefully, and with caution, primarily to prevent abuse of the process of any
court or to secure the ends of justice. The Court referred to the principles laid down in *R.P. Kapur v. State of
Punjab* AIR 1960 SC 866 and *State of Haryana v. Bhajan Lal* (1992 Supp. (1) SCC 335), which provide illustrative
categories for quashing criminal proceedings. The Court emphasized that at the stage of quashing, the High Court should
not embark upon an inquiry into the reliability of evidence or whether a conviction would be sustainable. It is not
proper to analyze the case in light of all probabilities. The Court found that the High Court had exceeded its power by
quashing the proceedings on the erroneous assumption that the ingredients of the offense were not made out and that the
prosecution could not end in conviction, despite the materials collected during the investigation.

#### Judgment & Direction
The Supreme Court set aside the impugned order of the High Court, which quashed the criminal proceedings against Respondent Nos. 1-3. The trial court was directed to proceed with the case against the respondents in accordance with law.

**PDF Links:**
[Judgment PDF](https://api.sci.gov.in/jonew/judis/38295.pdf)

### Step-by-Step Guide for Quashing a Criminal Complaint at the High Court:

1. **Identify Grounds for Quashing:**
 - **No prima facie case:** The allegations in the FIR/complaint, even if taken at face value, do not constitute any cognizable offense.
 - **Absurd and improbable allegations:** The allegations are so absurd and inherently improbable that no prudent person can conclude there are sufficient grounds to proceed.
 - **Express legal bar:** There is a specific legal bar to the institution or continuance of the proceedings (e.g., lack of sanction, limitation).
 - **Malice/Ulterior motive:** The proceeding is manifestly attended with mala fide intentions or is maliciously instituted to wreak vengeance.
 - **Abuse of process of court:** Allowing the proceedings to continue would be an abuse of the process of the court or would not secure the ends of justice.
 - **Inordinate and unexplained delay:** As seen in *CHANCHALPATI DAS*, an excessive and unexplained delay in filing the complaint can be a crucial factor.
 - **Omnibus allegations (especially in matrimonial cases):** As highlighted in *KAHKASHAN KAUSAR*, general and non-specific allegations against multiple family members without attributing specific roles can be a ground for quashing.

2. **Prepare the Petition under Section 482 Cr.P.C.:**
 - Draft a Criminal Petition (or Criminal Miscellaneous Application) under Section 482 of the Code of Criminal Procedure, 1973.
 - Clearly state the facts of the case, the FIR/complaint details, and the grounds on which quashing is sought.
 - Attach all relevant documents, such as the FIR, charge-sheet, and any other evidence supporting your claim.

3. **File the Petition:**
 - File the petition in the concerned High Court.
 - Ensure all necessary affidavits and court fees are submitted.

4. **Arguments before the High Court:**
 - The petitioner's counsel will argue that the case falls within one or more of the categories laid down by the Supreme Court (e.g., *Bhajan Lal* guidelines).
 - Emphasis will be placed on the lack of prima facie evidence, the absurdity of allegations, or the malicious intent behind the complaint.
 - The High Court will examine the allegations in the FIR/complaint and the evidence collected (if any) to determine if a case for quashing is made out. The Court will generally not conduct a detailed appreciation of evidence at this stage but will look for a clear abuse of process.

5. **High Court's Decision:**
 - If the High Court is satisfied that the grounds for quashing are met, it will allow the petition and quash the criminal proceedings.
 - If the High Court finds that a prima facie case is made out or that the matter requires investigation, it will dismiss the petition.

6. **Appeal to the Supreme Court (if necessary):**
 - If the High Court dismisses the petition, the aggrieved party may file a Special Leave Petition (Criminal) under Article 136 of the Constitution of India before the Supreme Court.
 - The Supreme Court will then review the High Court's decision based on the principles established in its landmark rulings.

