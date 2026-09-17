# bhc_quash_smoke

**Endpoint:** `http://localhost:5000/pyapi/chat`  
**Thread ID:** `b99b6a5d-3cb0-44ac-ab58-54be81abd1ec`  
**Elapsed:** 39.14s  
**TTFB:** 879 ms  
**Answer length:** 31,203 chars (5,009 words)  
**Raw SSE events:** 137  

## Prompt

```
Complete details on quashing of criminal complaint at Bombay high court and step by step guide
```

## Event-type counts

| Event type | Count |
|---|---:|
| `token` | 93 |
| `progress` | 29 |
| `status` | 8 |
| `agents_planned` | 1 |
| `context` | 1 |
| `done` | 1 |
| `followup_suggestions` | 1 |
| `response` | 1 |
| `sources` | 1 |
| `thread_id` | 1 |

**Agents planned:** `['Judgment', 'Legislation', 'SCI_Judgment']`  
**Unique progress steps (16):** ['classify', 'finalize', 'generate', 'history', 'language', 'metadata', 'parse', 'plan', 'prepare', 'react', 'relevance_check', 'results', 'rewrite', 'search', 'synthesize', 'validate']  

## Internal steps (chronological, token events collapsed)

| # | Elapsed | Type | Summary |
|---:|---:|---|---|
|    1 |    0.88s | `thread_id` | data='b99b6a5d-3cb0-44ac-ab58-54be81abd1ec' |
|    2 |    0.88s | `progress` | step='validate' message='Validating query...' agent='guardrail' |
|    3 |    1.25s | `status` | message='Validating query...' agent='guardrail_input' |
|    4 |    1.25s | `progress` | step='language' message='Detecting language...' agent='memory' |
|    5 |    1.25s | `progress` | step='language' message='Detected: en' agent='memory' |
|    6 |    1.25s | `progress` | step='history' message='Loading conversation history...' agent='memory' |
|    7 |    1.26s | `progress` | step='history' message='Found 0 previous turns' agent='memory' |
|    8 |    1.27s | `progress` | step='rewrite' message='Rewriting follow-up query...' agent='memory' |
|    9 |    1.27s | `status` | message='Loading context...' agent='memory' |
|   10 |    1.27s | `context` |  |
|   11 |    1.27s | `progress` | step='classify' message='Understanding your question...' agent='orchestrator' |
|   12 |    4.85s | `progress` | step='classify' message='Identified: Judgment, Legislation' agent='orchestrator' |
|   13 |    4.86s | `progress` | step='plan' message='Preparing search queries for 3 agents...' agent='orchestrator' |
|   14 |    7.03s | `status` | message='Planning search strategy...' agent='orchestrator_plan' |
|   15 |    7.04s | `agents_planned` | agents=["Judgment", "Legislation", "SCI_Judgment"] |
|   16 |    7.05s | `progress` | step='metadata' message='Extracting case details from query...' agent='judgment' |
|   17 |    7.05s | `progress` | step='search' message='Searching court judgments (2 strategies in parallel)...' agent='judgment' |
|   18 |    7.06s | `progress` | step='parse' message='Parsing query for section references...' agent='legislation' |
|   19 |    7.06s | `progress` | step='parse' message='Detected sections: 482' agent='legislation' |
|   20 |    7.06s | `progress` | step='search' message='Searching legislation database...' agent='legislation' |
|   21 |    7.06s | `progress` | step='prepare' message='Preparing Supreme Court search...' agent='sci_judgment' |
|   22 |    7.06s | `progress` | step='react' message='Running multi-step research (ReAct agent)...' agent='sci_judgment' |
|   23 |    7.06s | `progress` | step='search' message='Found 5 matching provisions' agent='legislation' |
|   24 |    7.06s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='legislation' |
|   25 |   10.50s | `progress` | step='search' message='Found 5 judgments via case_type:quashing' agent='judgment' |
|   26 |   10.50s | `progress` | step='search' message='Top match: Prakash Piniomal Haswani vs State of Maharashtra' agent='judgment' |
|   27 |   10.50s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='judgment' |
|   28 |   11.72s | `progress` | step='search' message='Reading: High Court of Jharkhand Rules, 2001' agent='legislation' |
|   29 |   11.72s | `progress` | step='generate' message='Generating response...' agent='legislation' |
|   30 |   11.75s | `progress` | step='generate' message='Generating response with citations...' agent='judgment' |
|   76 |   17.57s | `status` | message='Searching legislation...' agent='legislation' |
|  125 |   27.63s | `status` | message='Searching court judgments...' agent='judgment' |
|  126 |   37.12s | `progress` | step='react' message='Completed 10 search steps' agent='sci_judgment' |
|  127 |   37.12s | `progress` | step='results' message='Found 5 Supreme Court judgments' agent='sci_judgment' |
|  128 |   37.12s | `progress` | step='generate' message='Generating response...' agent='sci_judgment' |
|  129 |   37.12s | `status` | message='Searching Supreme Court judgments...' agent='sci_judgment' |
|  130 |   37.15s | `progress` | step='synthesize' message='Merging results from 3 agents...' agent='orchestrator' |
|  131 |   37.15s | `status` | message='Injecting citations into draft...' agent='orchestrator_synthesize' |
|  132 |   37.15s | `progress` | step='finalize' message='Finalizing response...' agent='guardrail' |
|  133 |   37.15s | `status` | message='Finalizing...' agent='guardrail_output' |
|  134 |   38.30s | `response` |  |
|  135 |   38.30s | `sources` | data=[{"source_type": "judgment", "title": "Prakash Piniomal Haswani vs State of Maharashtra", "content": ["16-wp-2676-2014\nIN THE HIGH COURT OF JUDICATURE AT BOMBAY\nCRIMINAL APPELLATE JURISDICTION\nCRIMINAL WRIT PETITIO... |
|  136 |   39.10s | `followup_suggestions` | data=["What are the grounds for quashing?", "What evidence is needed?", "What are the costs involved?"] |
|  137 |   39.14s | `done` |  |

## Final response

The Bombay High Court has the power to quash criminal complaints under Article 226 of the Constitution of India and
Section 482 of the Code of Criminal Procedure, 1908. This power is not limited by the restrictions imposed on a
Magistrate under Section 320 of the Code of Criminal Procedure, 1908, which deals with the compounding of offences. The
High Court can exercise its inherent powers to quash proceedings even in non-compoundable cases, especially when parties
have amicably settled their disputes.

However, the High Court exercises this power with caution. For instance, in *Prakash Piniomal Haswani v. State of
Maharashtra*, the Bombay High Court dismissed a petition for quashing a criminal complaint, noting that the petitioner
had unconditionally withdrawn a previous application for dropping the proceedings based on consent terms, and the trial
had already reached its final stage. The Court emphasized that merely because an undertaking was given by a party in a
former proceeding to consent to quashing, it does not automatically entitle the accused to have the complaint quashed.
The Court must be satisfied that quashing is in the best interest of the complainant and/or victim, particularly in
cases involving offences like Section 498A of the Indian Penal Code, which are considered to be against society at large
and not merely personal.

I think this information will help you more to understand the case:

### Key Legal Issues
- Whether the Bombay High Court can quash a criminal complaint under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, 1908, even if the offence is non-compoundable under Section 320 of the Code of Criminal Procedure, 1908.
- The circumstances under which the High Court should exercise its inherent powers to quash criminal proceedings, particularly when a settlement has been reached between the parties.
- The effect of an undertaking given by a complainant to consent to the quashing of a criminal complaint, especially when the trial has commenced and is at an advanced stage.
- The relevance of the nature of the offence (e.g., Section 498A of the Indian Penal Code) in deciding whether to quash a criminal complaint.

### Detailed Narrative

In *Prakash Piniomal Haswani v. State of Maharashtra*, the petitioner sought to quash a criminal complaint registered
with Trombay police station for offences under Sections 232, 420, 406, 497, 498A, 506(ii) read with Section 34 of the
Indian Penal Code. The petitioner and respondent No. 2 (complainant/wife) were married but could not live together,
leading to a divorce petition in 1998 and a decree of divorce in 2004. During an appeal against the divorce order,
consent terms were filed, agreeing that the petitioner would pay Rs. 10,00,000/- to the wife as full and final
settlement, and the wife would give no objection for quashing the criminal complaint. The petitioner alleged that the
wife violated this undertaking by assisting the Public Prosecutor and giving evidence against him. The petitioner also
cited reasons for his non-appearance in the trial court, including advocate's advice, spondylosis, and his brother's
death.

The respondent No. 2, appearing in person, contended that the petitioner had unconditionally withdrawn an application
for dropping the proceedings based on the consent terms, which caused her harassment as she had to appear in court
repeatedly. She claimed she was compelled by circumstances (being a housewife with no income) to accept the Rs.
10,00,000/- settlement, as an earlier offer of a house was withdrawn. She further submitted that the case had proceeded
for six years and was at its final stage, arguing against interference by the High Court at such a late stage.

In *Michelle M. Desai v. Shirin R. Majai & Anr.*, the petitioner/original complainant lodged an FIR for offences under
Sections 324, 341, 342, 506 (Part 2), and 114 of the Indian Penal Code against her paternal aunt. After charge framing,
two civil suits between the parties were settled out of court by filing consent terms. An application to the
Metropolitan Magistrate for compounding the offence was refused because Section 506 (Part 2) is non-compoundable. The
petitioner then approached the High Court under Section 482 of the Code of Criminal Procedure, 1908, to quash the
criminal complaint.

In *Freddy Sunderdas Thomas v. The State of Maharashtra and others*, the petitioner, an accused, had taken finance for
two trucks from Respondent No. 2 (Ashok Leyland Finance Company). After defaulting on instalments, an Arbitrator was
appointed. The company had also filed a complaint with the Metropolitan Magistrate, seeking an order under Section
156(3) of the Code of Criminal Procedure, 1908. Subsequently, the dispute was settled, and the amount due was paid by
Respondent No. 4, who purchased the trucks. The complainant company then applied to withdraw the complaint, stating the
matter was settled. However, the Magistrate dismissed the withdrawal application and proceeded to pass an order under
Section 156(3), leading to the registration of an FIR and the petitioner's arrest. The petitioner sought to quash this
complaint.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr.*, the applicants sought to quash a criminal complaint registered with Gorai Police Station for offences under Sections 465, 468, 471, 420 of the Indian Penal Code. The parties had amicably settled the dispute, which was personal in nature.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.*, the original complainant filed a petition for
quashing a complaint he had filed against Dattatray Ramchandra Jadhav for offences under Sections 387 and 326 of the
Indian Penal Code. The petitioner claimed the complaint was false and registered at the instance of the police, despite
him never filing it. The learned APP, however, submitted that several complaints were pending against the accused,
Dattatray Jadhav, who appeared to be a history-sheeter, and there was no reason for the police to lodge a false case.

### As per the Court

In *Prakash Piniomal Haswani v. State of Maharashtra*, the Bombay High Court dismissed the writ petition. The Court
observed that since the petitioner had unconditionally withdrawn his application for dropping the proceedings based on
consent terms, it was not open for him to approach the Court under Article 226 of the Constitution of India read with
Section 482 of the Code of Criminal Procedure, 1908, especially when the trial had commenced and reached its final
stage. The Bombay High Court reiterated the principle that once a trial commences, it should be concluded either in
acquittal or conviction. The Court, relying on *B.S. Joshi v. State of Haryana, 2003(4) SCC 675*, acknowledged its
inherent powers to quash complaints but emphasized that such power is not to be exercised merely on consent. The Court
must be satisfied that quashing is in the best interest of the wife, family, and children. It noted that an offence
under Section 498A of the Indian Penal Code is not a "personal offence" but is against society at large, and allegations
of harassment cannot be taken lightly. The Court also considered the delay of six years in filing the petition after the
undertaking was given, and the fact that a contempt petition for breach of undertaking would be time-barred under
Section 20 of the Contempt of Court Act, 1971.

In *Michelle M. Desai v. Shirin R. Majai & Anr.*, the Bombay High Court allowed the petition and quashed the criminal
complaint. The Court referred to *B.S. Joshi v. State of Haryana, JT 2003(3) SC 277*, which held that the High Court, in
exercise of its inherent powers under Section 482 of the Code of Criminal Procedure, 1908, can quash criminal
proceedings or FIRs or complaints, and Section 320 of the Code does not limit or affect these powers. Since the parties
had amicably settled the matter and did not wish to proceed with the criminal litigation, the Bombay High Court found it
a fit case for quashing the complaint.

In *Freddy Sunderdas Thomas v. The State of Maharashtra and others*, the Bombay High Court allowed the writ petition and
quashed the criminal complaint and FIR. The Court found that the Magistrate clearly erred in passing an order under
Section 156(3) of the Code of Criminal Procedure, 1908, when the complainant had already applied for withdrawal, stating
that the matter was settled. The Bombay High Court reiterated that while a Magistrate may not have the power to compound
a non-compoundable offence under Section 320 of the Code of Criminal Procedure, 1908, the High Court's powers under
Section 482 are not curtailed. Relying on *B.S. Joshi and Ors. v. State of Haryana and Anr., 2003 Cri. L.J. 2028*, the
Court held that it could exercise its inherent powers to quash criminal proceedings or FIRs or complaints.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr.*, the Bombay High Court allowed the application for
quashing the criminal complaint. The Court noted that the parties had amicably settled the dispute, which was personal
in nature. Relying on the judgments of the Apex Court in *Madan Mohan Abbot v. State of Punjab, AIR 2008 SC 1969* and
*Jagdish Chanana v. State of Haryana, AIR 2008 SC 1968*, the Bombay High Court found no impediment to quash the criminal
complaint by exercising its inherent jurisdiction under Section 482 of the Code of Criminal Procedure, 1908.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.*, the Bombay High Court dismissed the writ
petition. The Court was not inclined to quash the complaint, noting that the accused, Dattatray Jadhav, appeared to be a
history-sheeter with numerous pending cases. The Bombay High Court observed that there was every possibility of the
petitioner being pressurized by the accused to file the petition for quashing. The Court found no reason for the police
to file a false complaint and thus declined to exercise its jurisdiction under Article 226 of the Constitution of India
or Section 482 of the Code of Criminal Procedure, 1908.

### Additional Observations

The judgments highlight that while the Bombay High Court possesses broad inherent powers to quash criminal complaints,
the exercise of these powers is discretionary and depends on the specific facts and circumstances of each case. Factors
such as the stage of the trial, the nature of the offence (personal vs. against society), the conduct of the parties
(e.g., unconditional withdrawal of applications), and the potential for coercion or harassment are crucial in the
Court's decision-making process. The Court emphasizes that quashing should not be a matter of right based solely on
consent, but rather a decision made after satisfying itself that it serves the interests of justice and the victim.

### Step-by-Step Guide for Quashing a Criminal Complaint at Bombay High Court:

1. **Identify the appropriate legal provision**: A petition for quashing a criminal complaint is typically filed under Article 226 of the Constitution of India (for a writ, order, or direction) and/or Section 482 of the Code of Criminal Procedure, 1908 (for inherent powers of the High Court).
2. **Grounds for Quashing**: Common grounds include:
 - **Settlement between parties**: If the dispute is primarily personal and the parties have amicably settled the matter, especially in cases where the offence is non-compoundable under Section 320 of the Code of Criminal Procedure, 1908.
 - **Abuse of process of law**: Where the complaint is frivolous, vexatious, or filed with malicious intent, or where continuation of proceedings would be an abuse of the process of any court.
 - **No prima facie case**: If the allegations in the FIR or complaint, even if taken at face value, do not disclose the commission of any cognizable offence.
 - **Legal bar**: Where there is an express legal bar to the institution or continuation of the proceedings.
3. **Prepare the Petition**:
 - Draft a Criminal Writ Petition or Criminal Application clearly stating the facts, the criminal complaint/FIR details, the grounds for quashing, and the relief sought.
 - Include all relevant documents, such as the FIR, charge sheet (if filed), consent terms (if any), divorce decree, and any other evidence supporting the grounds for quashing.
 - Ensure the petition is properly verified and supported by an affidavit.
4. **Involve the Complainant (if applicable)**:
 - If the quashing is sought on the basis of a settlement, the complainant's consent is crucial. The complainant may need to appear in person before the High Court to confirm the settlement and their no-objection to quashing.
 - Consent terms, if any, should be formally filed before the Court.
5. **Consider the Stage of Proceedings**:
 - The High Court is generally more inclined to quash proceedings at an early stage (e.g., before the commencement of trial or framing of charges).
 - If the trial has commenced and is at an advanced stage, the Court may be reluctant to interfere, as seen in *Prakash Piniomal Haswani*.
6. **Court's Discretion and Caution**:
 - The High Court exercises its inherent powers with caution and not as a matter of right for the petitioner.
 - The Court will assess whether quashing is in the interest of justice, the victim, and society at large, especially for serious offences or those considered to be against society (e.g., Section 498A of the Indian Penal Code).
 - The Court may also consider the conduct of the parties, such as previous withdrawals of applications or delays in seeking relief.
7. **Filing and Hearing**:
 - File the petition with the Criminal Appellate Jurisdiction of the Bombay High Court.
 - The petition will be heard by a Division Bench or a Single Judge, depending on the nature of the prayer and the Court's roster.
 - The Public Prosecutor (for the State) and the complainant (if a private party) will be given an opportunity to present their arguments.
8. **Order of the Court**:
 - If the Court is satisfied, it may pass an order quashing the criminal complaint/FIR and any consequential proceedings.
 - The Court may also cancel any bonds executed by the parties and discharge sureties, as seen in *Freddy Sunderdas Thomas*.

---

## Statutory Provisions Referenced

The quashing of a criminal complaint under Section 482 of the Code of Criminal Procedure, 1973 (CrPC) — corresponding to Section 530 of the Bharatiya Nagarik Suraksha Sanhita, 2023 — at the High Court of Jharkhand involves specific procedural steps as outlined in the High Court of Jharkhand Rules, 2001.

### Quashing of Criminal Complaint at Bombay High Court

While the user query specifically mentions the Bombay High Court, the provided legislation provisions are from the High Court of Jharkhand Rules, 2001. Therefore, the following details pertain to the procedure at the High Court of Jharkhand for quashing a criminal complaint under Section 482 CrPC (corresponding to Section 530 BNSS, 2023).

**1. Jurisdiction and Authority:**
- Applications for quashing, arising out of Section 482 CrPC (corresponding to Section 530 BNSS, 2023), may be heard and disposed of by a Single Judge of the High Court, as per Rule 35(1)(a) of the High Court of Jharkhand Rules, 2001.
- As far as practicable, such applications are to be listed before the Zonal Judge in-charge of the Zone where the court from which the matter arises is situated. To facilitate this, the cause title of the petition must mention the name of the district from where the matter has arisen (Rule 35(2) of the High Court of Jharkhand Rules, 2001).

**2. Contents of the Petition:**
- The applicant must state whether an application on the same facts and against the same order or judgment had been previously filed before the High Court on behalf of any of the petitioners, and if so, with what result (Rule 140(i) of the High Court of Jharkhand Rules, 2001).

**3. Listing for Admission or Orders:**
- Criminal Miscellaneous (Quashing) petitions, including motion slips, filed before 1:00 P.M. shall ordinarily be posted for admission or orders on the following day (Rule 80 of the High Court of Jharkhand Rules, 2001).

### Step-by-Step Guide for Quashing a Criminal Complaint (High Court of Jharkhand)

**Step 1: Preparation of the Petition**
- Draft a petition under Section 482 CrPC (corresponding to Section 530 BNSS, 2023) seeking to quash the criminal complaint.
- Ensure the petition clearly sets out the facts, grounds for quashing (e.g., abuse of process of court, no prima facie case, legal infirmities), and the specific relief sought.
- Include an affidavit affirming the truthfulness of the statements made in the petition.

**Step 2: Compliance with High Court Rules**
- **Previous Application Disclosure:** In the petition, explicitly state whether any prior application on the same facts and against the same order or judgment has been filed before the High Court by any of the petitioners, and disclose the outcome, if any (Rule 140(i) of the High Court of Jharkhand Rules, 2001).
- **District Mention:** Ensure the cause title of the petition clearly mentions the name of the district from where the criminal complaint or order sought to be quashed has arisen (Rule 35(2) of the High Court of Jharkhand Rules, 2001).

**Step 3: Filing the Petition**
- File the petition, along with all necessary annexures and supporting documents, with the Registry of the High Court of Jharkhand.
- Ensure the petition is filed before 1:00 P.M. for ordinary posting on the following day (Rule 80 of the High Court of Jharkhand Rules, 2001).

**Step 4: Admission and Hearing**
- The petition will ordinarily be posted for admission or orders before a Single Judge (Rule 35(1)(a) and Rule 80 of the High Court of Jharkhand Rules, 2001).
- During the admission hearing, the Single Judge will consider whether there are sufficient grounds to admit the petition and issue notice to the opposite parties.
- If admitted, notice will be issued to the respondent(s) (e.g., the complainant, the State).

**Step 5: Final Hearing and Order**
- After the respondents have filed their replies (if any) and arguments have been heard from both sides, the Single Judge will pass an order on the application for quashing.
- If the High Court finds that the criminal complaint constitutes an abuse of the process of the court or that no case is made out, it may quash the complaint.
- Any order passed by the Court under relevant sections of the CrPC (such as Sections 368, 415, or 416 CrPC — corresponding to Sections 417, 464, or 465 BNSS, 2023) shall be forwarded forthwith to the concerned court, and a copy sent to the Superintendent of Jail where the accused is lodged (Rule 147 of the High Court of Jharkhand Rules, 2001).

---

## Related Supreme Court Authority

The Supreme Court has delivered several landmark rulings on the quashing of criminal complaints, primarily under Section 482 of the Code of Criminal Procedure, 1973 (Cr.P.C.). These judgments outline the principles and circumstances under which High Courts can exercise their inherent powers to quash criminal proceedings.

I think this information will help you more to understand the case:

### Case: *CHANCHALPATI DAS v. THE STATE OF WEST BENGAL*, Crl.A. No. 1592 of 2023, decided on 18-05-2023

#### Key Legal Issues
- Whether an inordinate and unexplained delay in filing a criminal complaint can be a ground for quashing the proceedings.
- Whether the allegations in the complaint and charge-sheet, even if taken at face value, disclose the commission of any offense.
- The scope of inherent powers of the High Court under Section 482 Cr.P.C. to prevent abuse of the process of court.

#### Detailed Narrative
The case involved two appellants, Madhu Pandit Das (President of ISKCON, Bengaluru) and Chanchalpati Das (Vice President
of ISKCON, Bengaluru), against whom a charge-sheet was filed for offenses under Sections 468, 471, 406, and 120-B of the
Indian Penal Code (IPC). The complaint, filed in 2009, alleged the theft of a bus belonging to ISKCON, Kolkata, which
purportedly occurred in 2001. The High Court had dismissed their criminal revision applications seeking to quash the
charge-sheet. The appellants argued that the prosecution was an abuse of process, initiated to settle personal scores,
and that the allegations were absurd and improbable, with no evidence of forgery or fabrication. They also highlighted
the significant delay in filing the complaint. The respondent-complainant contended that a prima facie case was made out
and that the documents produced by the appellants regarding the bus transfer were not part of the charge-sheet.

#### Court Observations
The Supreme Court observed that there was an inordinate and unexplained delay of eight years in filing the complaint
regarding an alleged incident from 2001. The Court found it unbelievable that ISKCON Kolkata would remain silent for
such a long period if the bus was indeed precious to them. This delay, coupled with the lack of material or documents to
substantiate the bald allegations in the complaint and charge-sheet, led the Court to conclude that the criminal
machinery was set into motion as a sheer misuse and abuse of the process of the court. The Court reiterated the
guidelines from *State of Haryana v. Bhajan Lal, 1992 Supp. (1) SCC 335*, emphasizing that powers under Section 482
Cr.P.C. should be exercised to prevent abuse of process or to secure the ends of justice, especially when allegations
are absurd, improbable, or manifestly mala fide. The Court also cited *Hasmukhlal D. Vora & Anr. v. State of Tamil Nadu,
2022 SCC OnLine SC 1732*, which held that unexplained inordinate delay is a crucial factor for quashing a criminal
complaint.

#### Judgment & Direction
The Supreme Court allowed the appeals, quashing the criminal proceedings pending against the appellants in the Court of Chief Judicial Magistrate, Alipore, arising out of FIR No. 33 of 2009. The Court also imposed a cost of Rs. 1,00,000/- on the respondent-complainant, to be deposited in the office of the Supreme Court Advocates-on-Record Association, noting the proliferation of frivolous cases and the need to deter such litigation.

**PDF Links:**
- [Judgment PDF](https://api.sci.gov.in/supremecourt/2017/17824/17824_2017_4_1502_44572_Judgement_18-May-2023.pdf)

### Case: *KAHKASHAN KAUSAR @ SONAM v. THE STATE OF BIHAR*, Crl.A. No. 195 of 2022, decided on 08-02-2022

#### Key Legal Issues
- Whether general and omnibus allegations against in-laws in a matrimonial dispute under Section 498A IPC are liable to be quashed.
- The misuse of Section 498A IPC and the tendency to implicate relatives of the husband without specific allegations.
- The role of courts in preventing abuse of the process of law in matrimonial disputes.

#### Detailed Narrative
This appeal challenged a Patna High Court order that dismissed a criminal writ petition seeking to quash an FIR against
the appellants (in-laws of the complainant wife) for offenses under Sections 341, 323, 379, 354, 498A read with Section
34 IPC. The complainant had previously filed a similar complaint in 2017, where the Magistrate found no prima facie case
against the in-laws due to omnibus allegations. A subsequent FIR was filed in 2019, alleging continued dowry demand and
harassment. The High Court had held that the FIR disclosed a prima facie offense requiring investigation. The appellants
argued that the allegations were general and omnibus, made with a revengeful intent, and that a preliminary inquiry was
mandated. The State contended that the 2019 FIR constituted a fresh cause of action.

#### Court Observations
The Supreme Court expressed concern over the misuse of Section 498A IPC and the increasing tendency to implicate
relatives of the husband in matrimonial disputes without specific allegations. The Court referred to its previous
judgments in *Rajesh Sharma and Ors. v. State of U.P. & Anr., (2018) 10 SCC 472*, *Arnesh Kumar v. State of Bihar and
Anr., (2014) 8 SCC 273*, and *Preeti Gupta & Anr. v. State of Jharkhand & Anr., (2010) 7 SCC 667*, which highlighted
that many complaints under Section 498A are filed in the heat of the moment over trivial issues, often with oblique
motives, leading to uncalled-for harassment. The Court emphasized that courts must be careful in proceeding against
distant relatives based on omnibus allegations. Upon perusal of the FIR, the Court found that general allegations were
leveled against the appellants, with no specific and distinct roles attributed to them.

#### Judgment & Direction
The Supreme Court set aside the impugned order of the Patna High Court and quashed FIR No. 248 of 2019 against the appellants under Sections 341, 323, 379, 354, 498A read with Section 34 IPC. The Court held that allowing prosecution in the absence of clear allegations against the in-laws would result in an abuse of the process of law, and that general and omnibus allegations cannot force relatives of the complainant's husband to undergo trial.

**PDF Links:**
- [Judgment PDF](https://api.sci.gov.in/supremecourt/2020/25575/25575_2020_37_1501_33195_Judgement_08-Feb-2022.pdf)

### Case: *PADAL VENKATA RAMA REDDY @ RAMU v. KOVVURI SATYANARAYANA REDDY*, Crl.A. No. 1499 of 2011, decided on 29-07-2011

#### Key Legal Issues
- The scope and limitations of the High Court's inherent powers under Section 482 Cr.P.C. to quash criminal proceedings.
- The circumstances under which a criminal complaint can be quashed, particularly when the allegations do not prima facie constitute an offense or amount to an abuse of process.
- The distinction between civil wrongs and criminal offenses in the context of quashing criminal complaints.

#### Detailed Narrative
The appellant, the defacto complainant, challenged a High Court order that quashed criminal proceedings against
Respondent Nos. 1-3 (accused persons) for offenses including criminal conspiracy, rioting, mischief, attempt to murder,
and causing disappearance of evidence under the IPC. The complaint alleged that the respondents, due to political
rivalry, conspired to kill the appellant, leading to an assault where the appellant sustained injuries. The High Court
had quashed the proceedings, finding the prosecution materials inadequate and the ingredients of the alleged offenses
not made out.

#### Court Observations
The Supreme Court discussed in detail the inherent powers of the High Court under Section 482 Cr.P.C., reiterating that
these powers are to be exercised sparingly, carefully, and with caution, primarily to give effect to any order under the
Code, to prevent abuse of the process of any court, or to secure the ends of justice. The Court referred to the
principles laid down in *R.P. Kapur v. State of Punjab, AIR 1960 SC 866*, and *State of Haryana v. Bhajan Lal, 1992
Supp. (1) SCC 335*, which provide illustrative categories for quashing criminal proceedings. It was emphasized that the
High Court should not ordinarily embark upon an inquiry into the reliability of evidence or whether a conviction would
be sustainable at the stage of quashing. The Court also cited *Indian Oil Corporation v. NEPC India Ltd. and Others,
(2006) 6 SCC 736*, which stated that the power to quash criminal complaints should be used sparingly and with abundant
caution, and that a complaint is not required to verbatim reproduce the legal ingredients of the alleged offense if the
necessary factual foundation is laid. The Court clarified that while a given set of facts may constitute a civil wrong,
a criminal offense, or both, the inherent power should not be used to stifle a legitimate prosecution.

#### Judgment & Direction
The Supreme Court set aside the impugned order of the High Court, holding that the High Court had exceeded its power in quashing the criminal proceedings. The Court found that, based on a conjoint reading of the complaint, charge-sheet, and witness statements, it could not be presumed that there was no legal and acceptable evidence in support of the prosecution. The trial court was directed to proceed with the case against the respondents in accordance with law.

**PDF Links:**
- [Judgment PDF](https://api.sci.gov.in/jonew/judis/38295.pdf)

### Step-by-step guide for quashing a criminal complaint at the High Court:

1. **Identify Grounds for Quashing**: The primary grounds for quashing a criminal complaint or FIR under Section 482 Cr.P.C. (or Article 226 of the Constitution for writ petitions) include:
 - The allegations, even if taken at face value, do not prima facie constitute any offense.
 - The complaint is absurd, inherently improbable, or maliciously instituted with an ulterior motive.
 - There is an express legal bar to the institution or continuance of the proceedings (e.g., lack of sanction, limitation).
 - The proceedings amount to an abuse of the process of the court or are necessary to secure the ends of justice.
 - The dispute is predominantly civil in nature, and criminal proceedings are initiated to settle civil disputes.
 - Inordinate and unexplained delay in filing the complaint.

2. **Prepare the Petition**: Draft a Criminal Miscellaneous Petition (under Section 482 Cr.P.C.) or a Writ Petition (under Article 226) to the High Court. The petition should clearly state:
 - The facts of the case.
 - The specific FIR/complaint sought to be quashed.
 - The grounds for quashing, supported by legal arguments and relevant Supreme Court precedents.
 - A prayer for quashing the criminal proceedings.

3. **Annex Supporting Documents**: Attach all relevant documents, such as:
 - Copy of the FIR/complaint.
 - Copy of the charge-sheet (if filed).
 - Any other documents that support the grounds for quashing (e.g., evidence of civil dispute, documents showing delay, lack of evidence).

4. **File the Petition**: File the petition in the appropriate High Court. Ensure all procedural requirements, such as proper indexing, affidavits, and court fees, are met.

5. **Arguments before the High Court**: During the hearing, the petitioner's counsel will argue that the case falls within the categories identified by the Supreme Court for quashing criminal proceedings. The focus will be on demonstrating that the continuation of the proceedings would be an abuse of the process of law or that the allegations do not disclose any cognizable offense.

6. **High Court's Decision**: The High Court will consider the arguments and the material on record. If satisfied that the grounds for quashing are met, it may quash the FIR/complaint or the entire criminal proceedings. The High Court will generally not conduct a detailed appreciation of evidence at this stage but will examine whether a prima facie case is made out.

7. **Appeal to Supreme Court (if necessary)**: If the High Court dismisses the petition, the aggrieved party may file a Special Leave Petition (SLP) under Article 136 of the Constitution before the Supreme Court, challenging the High Court's order. The Supreme Court will then review the High Court's decision based on the principles it has laid down for the exercise of inherent powers.

