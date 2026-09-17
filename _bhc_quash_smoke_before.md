# bhc_quash_smoke

**Endpoint:** `http://localhost:5000/pyapi/chat`  
**Thread ID:** `5b988a5c-f813-41da-9400-601cfe0e7288`  
**Elapsed:** 58.22s  
**TTFB:** 1210 ms  
**Answer length:** 26,886 chars (4,255 words)  
**Raw SSE events:** 115  

## Prompt

```
Complete details on quashing of criminal complaint at Bombay high court and step by step guide
```

## Event-type counts

| Event type | Count |
|---|---:|
| `token` | 71 |
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
|    1 |    1.21s | `thread_id` | data='5b988a5c-f813-41da-9400-601cfe0e7288' |
|    2 |    1.21s | `progress` | step='validate' message='Validating query...' agent='guardrail' |
|    3 |    3.08s | `status` | message='Validating query...' agent='guardrail_input' |
|    4 |    3.09s | `progress` | step='language' message='Detecting language...' agent='memory' |
|    5 |    3.09s | `progress` | step='language' message='Detected: en' agent='memory' |
|    6 |    3.09s | `progress` | step='history' message='Loading conversation history...' agent='memory' |
|    7 |    3.12s | `progress` | step='history' message='Found 0 previous turns' agent='memory' |
|    8 |    3.13s | `progress` | step='rewrite' message='Rewriting follow-up query...' agent='memory' |
|    9 |    3.13s | `status` | message='Loading context...' agent='memory' |
|   10 |    3.13s | `context` |  |
|   11 |    3.14s | `progress` | step='classify' message='Understanding your question...' agent='orchestrator' |
|   12 |    8.56s | `progress` | step='classify' message='Identified: Judgment, Legislation' agent='orchestrator' |
|   13 |    8.56s | `progress` | step='plan' message='Preparing search queries for 3 agents...' agent='orchestrator' |
|   14 |   11.29s | `status` | message='Planning search strategy...' agent='orchestrator_plan' |
|   15 |   11.30s | `agents_planned` | agents=["Judgment", "Legislation", "SCI_Judgment"] |
|   16 |   11.31s | `progress` | step='metadata' message='Extracting case details from query...' agent='judgment' |
|   17 |   11.32s | `progress` | step='search' message='Searching court judgments (2 strategies in parallel)...' agent='judgment' |
|   18 |   11.32s | `progress` | step='parse' message='Parsing query for section references...' agent='legislation' |
|   19 |   11.32s | `progress` | step='parse' message='Detected sections: 482' agent='legislation' |
|   20 |   11.32s | `progress` | step='search' message='Searching legislation database...' agent='legislation' |
|   21 |   11.32s | `progress` | step='prepare' message='Preparing Supreme Court search...' agent='sci_judgment' |
|   22 |   11.32s | `progress` | step='react' message='Running multi-step research (ReAct agent)...' agent='sci_judgment' |
|   23 |   20.98s | `progress` | step='search' message='Found 5 judgments via case_type:quashing' agent='judgment' |
|   24 |   20.98s | `progress` | step='search' message='Top match: Prakash Piniomal Haswani vs State of Maharashtra' agent='judgment' |
|   25 |   20.99s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='judgment' |
|   26 |   20.99s | `progress` | step='search' message='Found 5 matching provisions' agent='legislation' |
|   27 |   20.99s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='legislation' |
|   28 |   37.81s | `progress` | step='search' message='Reading: High Court of Jharkhand Rules, 2001' agent='legislation' |
|   29 |   37.82s | `progress` | step='generate' message='Generating response...' agent='legislation' |
|   30 |   39.44s | `progress` | step='generate' message='Generating response with citations...' agent='judgment' |
|   79 |   45.59s | `status` | message='Searching legislation...' agent='legislation' |
|  103 |   50.50s | `status` | message='Searching court judgments...' agent='judgment' |
|  104 |   55.63s | `progress` | step='react' message='Completed 10 search steps' agent='sci_judgment' |
|  105 |   55.63s | `progress` | step='results' message='Found 5 Supreme Court judgments' agent='sci_judgment' |
|  106 |   55.63s | `progress` | step='generate' message='Generating response...' agent='sci_judgment' |
|  107 |   55.65s | `status` | message='Searching Supreme Court judgments...' agent='sci_judgment' |
|  108 |   55.69s | `progress` | step='synthesize' message='Merging results from 3 agents...' agent='orchestrator' |
|  109 |   55.69s | `status` | message='Injecting citations into draft...' agent='orchestrator_synthesize' |
|  110 |   55.69s | `progress` | step='finalize' message='Finalizing response...' agent='guardrail' |
|  111 |   55.69s | `status` | message='Finalizing...' agent='guardrail_output' |
|  112 |   56.92s | `response` |  |
|  113 |   56.92s | `sources` | data=[{"source_type": "judgment", "title": "Prakash Piniomal Haswani vs State of Maharashtra", "content": ["16-wp-2676-2014\nIN THE HIGH COURT OF JUDICATURE AT BOMBAY\nCRIMINAL APPELLATE JURISDICTION\nCRIMINAL WRIT PETITIO... |
|  114 |   58.15s | `followup_suggestions` | data=["What are the grounds for quashing?", "What evidence is needed?", "How long does it take?"] |
|  115 |   58.22s | `done` |  |

## Final response

The Bombay High Court has the power to quash a criminal complaint under Article 226 of the Constitution of India and
Section 482 of the Code of Criminal Procedure, 1973. This power is not limited by the provisions of Section 320 of the
Code of Criminal Procedure, which deals with the compounding of offences. The High Court can exercise its inherent
powers to quash criminal proceedings, FIRs, or complaints, especially when the parties have amicably settled the
dispute, and the dispute is primarily personal in nature.

However, the Bombay High Court exercises this power with caution. It must be satisfied that quashing the complaint is in
the best interest of the parties, particularly the complainant and/or victim. The Court will consider factors such as
whether the trial has commenced and reached an advanced stage, whether the offence is personal or against society at
large, and whether the consent for quashing was given under duress. The Court will not quash a complaint merely on the
consent of parties if it is not satisfied that it is in the interest of the complainant and/or victim.

I think this information will help you more to understand the case:

### Key Legal Issues
- Whether the Bombay High Court can quash a criminal complaint under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, even if the offence is non-compoundable under Section 320 of the Code.
- The circumstances under which the High Court should exercise its inherent powers to quash a criminal complaint, particularly in cases involving matrimonial disputes or personal offences.
- The effect of an undertaking given by a complainant to consent to the quashing of a criminal complaint, and its enforceability.
- The relevance of the stage of the trial (e.g., if it has reached the fag end) in deciding whether to quash a criminal complaint.

### Detailed Narrative

In *Prakash Piniomal Haswani v. State of Maharashtra & Ors.*, the petitioner sought to quash a criminal complaint
registered at Trombay police station for offences under Sections 232, 420, 406, 497, 498(A), 506(ii) read with 34 of the
Indian Penal Code. The petitioner and respondent No.2 (wife) were married but could not live together, leading to a
divorce decree in 2004. During an appeal related to the divorce, consent terms were filed, agreeing that Rs. 10,00,000/-
would be paid to the wife as full and final settlement, and she would give no objection for quashing the criminal
complaint. The petitioner alleged that the wife violated this undertaking by assisting the Public Prosecutor and giving
evidence against him. The wife, appearing in person, contended that the petitioner had unconditionally withdrawn an
application for dropping the proceedings based on the consent terms, causing her harassment. She also claimed she was
compelled to accept the settlement due to her financial circumstances and that the case was at its final stage after six
years. The Bombay High Court dismissed the petition, noting that the petitioner had withdrawn his application for
dropping the proceedings and that the trial was at its fag end. The Court emphasized that quashing a complaint requires
satisfaction that it is in the best interest of the complainant and/or victim, and an offence under Section 498(A) of
the Indian Penal Code is not merely a personal offence.

In *Michelle M. Desai v. Shirin R. Majai & Anr.*, the petitioner/original complainant sought to quash a criminal
complaint for offences under Sections 324, 341, 342, 506 (Part 2) and 114 of the Indian Penal Code. The parties, who
were paternal aunt and niece, had settled their dispute amicably, including two civil suits. An application to compound
the offence before the Metropolitan Magistrate was refused because Section 506 (Part 2) is non-compoundable. The Bombay
High Court, relying on *B.S. Joshi v. State of Haryana, JT 2003(3) SC 277*, allowed the petition, quashing the criminal
complaint and FIR, as the parties had settled the matter amicably and did not wish to proceed with the criminal
litigation.

In *Freddy Sunderdas Thomas v. The State of Maharashtra and others*, the petitioner, an accused, sought to quash a
complaint filed by M/s. Ashok Leyland Finance Company for offences under Sections 379, 409, and 420 of the Indian Penal
Code. The dispute arose from defaults in loan repayments for two trucks. An arbitrator was appointed, and the matter was
settled, with the outstanding amount paid by a third party who purchased the trucks. The complainant company sought to
withdraw the complaint, but the Magistrate dismissed the application and ordered an investigation under Section 156(3 of
the Code of Criminal Procedure. The Bombay High Court found that the Magistrate erred in ordering investigation after
the settlement and allowed the petition, quashing the complaint and FIR, citing *B.S. Joshi and Ors. v. State of Haryana
and Anr., 2003 Cri. L.J. 2028*.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr.*, the applicants sought to quash a criminal
complaint for offences under Sections 465, 468, 471, and 420 of the Indian Penal Code. The parties had amicably settled
the dispute, which was personal in nature. The Bombay High Court, exercising its inherent jurisdiction under Section 482
of the Code of Criminal Procedure and relying on *Madan Mohan Abbot v. State of Punjab, AIR 2008 S.C. 1969* and *Jagdish
Chanana v. State of Haryana, AIR 2008 S.C. 1968*, allowed the application and quashed the criminal complaint.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.*, the original complainant filed a petition to
quash a complaint he had filed against Dattatray Ramchandra Jadhav for offences under Sections 387 and 326 of the Indian
Penal Code. The petitioner claimed the complaint was false and registered at the instance of the police. The learned APP
submitted that several complaints were pending against the accused, who appeared to be a history-sheeter. The Bombay
High Court was not inclined to quash the complaint, citing the possibility of the petitioner being pressurized by the
accused and the fact that the police had no reason to file a false complaint. The petition was dismissed.

### As per the Court

The Bombay High Court, in these judgments, consistently affirmed its inherent powers under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure to quash criminal complaints, even for non-compoundable offences, as established by the Supreme Court in *B.S. Joshi v. State of Haryana, (2003) 4 SCC 675*. The Court clarified that these powers are not limited by the restrictions imposed on a Magistrate under Section 320 of the Code of Criminal Procedure.

However, the exercise of this power is not a matter of right for the accused. The Bombay High Court emphasized that it
must be satisfied that quashing the complaint is in the best interest of the complainant and/or victim. In *Prakash
Piniomal Haswani*, the Court refused to quash the complaint despite an undertaking by the wife, noting that the
petitioner had unconditionally withdrawn his application for dropping the proceedings, the trial was at its final stage,
and an offence under Section 498(A) of the Indian Penal Code is not merely a personal offence but affects society at
large. The Court also considered the possibility of the wife being compelled to give consent due to financial hardship.

Conversely, in *Michelle M. Desai*, *Freddy Sunderdas Thomas*, and *Mrs. Dale Herman Nazareth*, the Bombay High Court quashed the complaints because the parties had amicably settled their disputes, which were primarily personal in nature. In *Freddy Sunderdas Thomas*, the Court specifically noted the Magistrate's error in ordering an investigation under Section 156(3) of the Code of Criminal Procedure after the parties had settled the matter and the complainant sought to withdraw the complaint.

The Bombay High Court also demonstrated caution in *Sagar Dnyandeo Patekar*, where it refused to quash a complaint against a "history-sheeter" due to the possibility of the complainant being pressurized, highlighting that the police had no reason to file a false complaint. This indicates that the Court scrutinizes the circumstances to ensure that the request for quashing is genuine and not a result of coercion or an attempt to subvert justice, especially when the accused has a criminal history.

### Additional Observations

In *Prakash Piniomal Haswani*, the Bombay High Court also addressed the issue of the undertaking given by the
respondent-wife. It observed that even if there was a breach of the undertaking, a contempt petition could not be
initiated against the wife since the period of limitation under Section 20 of the Contempt of Courts Act, 1971, is one
year, and six years had elapsed since the undertaking was given. This highlights the importance of timely action in
seeking remedies for breach of undertakings. The Court also noted that while false complaints under Section 498(A) of
the Indian Penal Code are often filed, due care and caution must be exercised before quashing such complaints, as they
are not merely personal offences.

---

## Statutory Provisions Referenced

The quashing of a criminal complaint under Section 482 of the Code of Criminal Procedure, 1973 (CrPC) — corresponding to Section 530 of the Bharatiya Nagarik Suraksha Sanhita, 2023 — at the High Court of Jharkhand involves specific procedural steps as outlined in the High Court of Jharkhand Rules, 2001.

### Quashing of Criminal Complaint at Bombay High Court

While the user query specifically mentions the Bombay High Court, the provided legislation provisions are from the High Court of Jharkhand Rules, 2001. Therefore, the following details pertain to the procedure at the Jharkhand High Court based on the provided rules.

**1. Jurisdiction:**
Rule 35(1)(a) of the High Court of Jharkhand Rules, 2001, explicitly states that applications for quashing, which arise out of Section 482 CrPC (corresponding to Section 530 of the Bharatiya Nagarik Suraksha Sanhita, 2023), may be heard and disposed of by a Single Judge.

**2. Contents of the Application:**
- **Previous Applications:** As per Rule 140(i), the applicant must state whether an application on the same facts and against the same order or judgment had been previously filed before the High Court on behalf of any of the petitioners, and if so, with what result.
- **District Mention:** Rule 35(2) requires that in the cause title of every such petition, the name of the district from where the matter has arisen shall be mentioned. This is to facilitate listing before the Zonal Judge in-charge of that Zone.

**3. Filing and Listing:**
- **Timely Posting:** Rule 80 provides that Criminal Miscellaneous applications for quashing, including motion slips filed before 1:00 P.M., shall ordinarily be posted for admission or orders on the following day.
- **Zonal Judge:** As far as practicable, applications under Section 482 CrPC (corresponding to Section 530 of the Bharatiya Nagarik Suraksha Sanhita, 2023) shall be listed before the Zonal Judge in-charge of the Zone where the Court from which the matter arises is situated (Rule 35(2)).

### Step-by-Step Guide for Quashing a Criminal Complaint (Jharkhand High Court)

**Step 1: Preparation of the Petition**
- **Drafting:** Prepare a Criminal Miscellaneous Petition under Section 482 CrPC (corresponding to Section 530 of the Bharatiya Nagarik Suraksha Sanhita, 2023).
- **Grounds for Quashing:** Clearly articulate the grounds for quashing the criminal complaint, such as:
 - The allegations in the FIR/complaint, even if taken at their face value, do not constitute any offence.
 - The allegations do not disclose a cognizable offence, justifying an investigation by police officers under Section 156(1) CrPC (corresponding to Section 173(1) of the Bharatiya Nagarik Suraksha Sanhita, 2023).
 - The allegations constitute a cognizable offence but there is no sufficient ground to proceed against the accused.
 - The proceeding is maliciously instituted with an ulterior motive.
 - There is a legal bar to the institution and continuance of the proceedings.
- **Cause Title:** Ensure the cause title includes the name of the district from where the matter has arisen, as required by Rule 35(2).
- **Declaration of Previous Applications:** Include a clear statement regarding any previous applications filed on the same facts and against the same order/judgment, along with their outcome, in compliance with Rule 140(i).

**Step 2: Filing the Petition**
- **Court Fees and Affidavits:** Pay the requisite court fees and attach necessary affidavits.
- **Submission:** File the petition with the Registry of the High Court of Jharkhand. Ensure all necessary documents, such as the FIR/complaint, charge sheet (if any), and other relevant annexures, are properly paginated and indexed.
- **Motion Slip:** File a motion slip along with the petition. If filed before 1:00 P.M., it will ordinarily be posted for admission or orders on the following day (Rule 80).

**Step 3: Listing and Hearing**
- **Admission/Orders:** The petition will be listed before a Single Judge for admission or orders (Rule 35(1)(a)).
- **Zonal Judge:** The Registry will endeavor to list the matter before the Zonal Judge in-charge of the relevant Zone (Rule 35(2)).
- **Arguments:** The counsel for the petitioner will present arguments before the Single Judge, explaining why the criminal complaint should be quashed. The respondent (State or complainant) will have an opportunity to present their arguments.

**Step 4: Interim Orders (if applicable)**
- During the pendency of the quashing petition, the High Court may grant interim relief, such as a stay on further proceedings in the criminal case, depending on the facts and circumstances.

**Step 5: Final Order**
- After hearing both sides, the Single Judge will pass a final order either quashing the criminal complaint or dismissing the petition.
- **Communication of Order:** Any order passed by the Court under relevant sections of the CrPC shall be forwarded forthwith to the Court concerned, and a copy shall also be sent to the Superintendent of Jail where the accused is lodged, if applicable (Rule 147).

**Note:** The provided rules do not contain specific provisions for the Bombay High Court. The procedure outlined above is strictly based on the High Court of Jharkhand Rules, 2001.

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

