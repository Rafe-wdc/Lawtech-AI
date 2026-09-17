# bhc_quash_smoke_tool_lawttorney

**Endpoint:** `https://tool.lawttorney.com/pyapiv2/chat`  
**Thread ID:** `719761a6-f362-485c-ab59-9aa3070380b3`  
**Elapsed:** 28.66s  
**TTFB:** 1015 ms  
**Answer length:** 28,168 chars (4,502 words)  
**Raw SSE events:** 450  

## Prompt

```
Complete details on quashing of criminal complaint at Bombay high court and step by step guide
```

## Event-type counts

| Event type | Count |
|---|---:|
| `token` | 407 |
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
|    1 |    1.02s | `thread_id` | data='719761a6-f362-485c-ab59-9aa3070380b3' |
|    2 |    1.02s | `progress` | step='validate' message='Validating query...' agent='guardrail' |
|    3 |    1.59s | `status` | message='Validating query...' agent='guardrail_input' |
|    4 |    1.59s | `progress` | step='language' message='Detecting language...' agent='memory' |
|    5 |    1.59s | `progress` | step='language' message='Detected: en' agent='memory' |
|    6 |    1.59s | `progress` | step='history' message='Loading conversation history...' agent='memory' |
|    7 |    1.61s | `progress` | step='history' message='Found 0 previous turns' agent='memory' |
|    8 |    1.61s | `progress` | step='rewrite' message='Rewriting follow-up query...' agent='memory' |
|    9 |    1.61s | `status` | message='Loading context...' agent='memory' |
|   10 |    1.61s | `context` |  |
|   11 |    1.61s | `progress` | step='classify' message='Understanding your question...' agent='orchestrator' |
|   12 |    3.42s | `progress` | step='classify' message='Identified: Judgment, Legislation' agent='orchestrator' |
|   13 |    3.42s | `progress` | step='plan' message='Preparing search queries for 3 agents...' agent='orchestrator' |
|   14 |    4.27s | `status` | message='Planning search strategy...' agent='orchestrator_plan' |
|   15 |    4.27s | `agents_planned` | agents=["Judgment", "Legislation", "SCI_Judgment"] |
|   16 |    4.27s | `progress` | step='metadata' message='Extracting case details from query...' agent='judgment' |
|   17 |    4.28s | `progress` | step='search' message='Searching court judgments (2 strategies in parallel)...' agent='judgment' |
|   18 |    4.28s | `progress` | step='parse' message='Parsing query for section references...' agent='legislation' |
|   19 |    4.28s | `progress` | step='parse' message='Detected sections: 482' agent='legislation' |
|   20 |    4.28s | `progress` | step='search' message='Searching legislation database...' agent='legislation' |
|   21 |    4.28s | `progress` | step='prepare' message='Preparing Supreme Court search...' agent='sci_judgment' |
|   22 |    4.28s | `progress` | step='react' message='Running multi-step research (ReAct agent)...' agent='sci_judgment' |
|   23 |    5.72s | `progress` | step='search' message='Found 5 judgments via case_type:quashing' agent='judgment' |
|   24 |    5.72s | `progress` | step='search' message='Top match: Prakash Piniomal Haswani vs State of Maharashtra' agent='judgment' |
|   25 |    5.72s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='judgment' |
|   26 |    5.78s | `progress` | step='search' message='Found 5 matching provisions' agent='legislation' |
|   27 |    5.78s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='legislation' |
|   28 |    7.19s | `progress` | step='generate' message='Generating response with citations...' agent='judgment' |
|   29 |    7.35s | `progress` | step='fallback' message="Retrieved provisions don't match the query — searching the web..." agent='legislation' |
|   89 |   21.66s | `progress` | step='react' message='Completed 6 search steps' agent='sci_judgment' |
|   90 |   21.66s | `progress` | step='results' message='Found 5 Supreme Court judgments' agent='sci_judgment' |
|   91 |   21.66s | `progress` | step='generate' message='Generating response...' agent='sci_judgment' |
|   92 |   21.66s | `status` | message='Searching Supreme Court judgments...' agent='sci_judgment' |
|   99 |   22.87s | `status` | message='Searching court judgments...' agent='judgment' |
|  442 |   27.92s | `status` | message='Searching legislation...' agent='legislation' |
|  443 |   27.92s | `progress` | step='synthesize' message='Merging results from 3 agents...' agent='orchestrator' |
|  444 |   27.92s | `status` | message='Injecting citations into draft...' agent='orchestrator_synthesize' |
|  445 |   27.93s | `progress` | step='finalize' message='Finalizing response...' agent='guardrail' |
|  446 |   27.93s | `status` | message='Finalizing...' agent='guardrail_output' |
|  447 |   28.02s | `response` |  |
|  448 |   28.03s | `sources` | data=[{"source_type": "judgment", "title": "Prakash Piniomal Haswani vs State of Maharashtra", "content": ["16-wp-2676-2014\nIN THE HIGH COURT OF JUDICATURE AT BOMBAY\nCRIMINAL APPELLATE JURISDICTION\nCRIMINAL WRIT PETITIO... |
|  449 |   28.65s | `followup_suggestions` | data=["What are grounds for quashing?", "What is the cost involved?", "How long does it take?"] |
|  450 |   28.66s | `done` |  |

## Final response

The Bombay High Court has the power to quash a criminal complaint under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, 1973. This power is not limited by the provisions of Section 320 of the Code of Criminal Procedure, which deals with the compounding of offences. The High Court can exercise this inherent power even in cases of non-compoundable offences, especially when the parties have amicably settled their disputes.

However, the High Court exercises this power with caution. It must be satisfied that quashing the complaint is in the
best interest of the parties, particularly the complainant and/or victim. The Court will consider factors such as
whether the dispute is primarily personal, if the settlement was voluntary, and if the trial has progressed
significantly. For instance, in *Prakash Piniomal Haswani v. State of Maharashtra & Ors.*, the Bombay High Court
dismissed a petition for quashing a criminal complaint, noting that the petitioner had unconditionally withdrawn an
earlier application for dropping the proceedings and the trial had reached its final stage. The Court also emphasized
that an undertaking to consent to quashing does not automatically entitle the accused to have the complaint quashed, and
the Court must be satisfied that it is in the interest of the complainant.

I think this information will help you more to understand the case:

### Key Legal Issues
- Whether the High Court can quash a criminal complaint under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, 1973, even if the offence is non-compoundable under Section 320 of the Code.
- The circumstances under which the High Court should exercise its inherent powers to quash a criminal complaint, particularly when a settlement has been reached between the parties.
- The effect of an undertaking given by a complainant to consent to the quashing of a criminal complaint.
- Whether the stage of the trial (e.g., at the fag end) impacts the High Court's decision to quash a complaint.

### Detailed Narrative

In *Prakash Piniomal Haswani v. State of Maharashtra & Ors.*, the petitioner sought to quash a criminal complaint
registered with Trombay police station for offences under Sections 232, 420, 406, 497, 498(A), 506(ii) read with 34 of
the Indian Penal Code. The petitioner and respondent No.2 (complainant) were married but divorced by a decree dated
January 12, 2004. During an appeal related to the divorce, consent terms were filed, agreeing that Rs. 10,00,000/- would
be paid to the wife as full and final settlement, and she would give no objection for quashing the criminal complaint.
The petitioner alleged that the respondent-wife violated this undertaking by assisting the Public Prosecutor and giving
evidence against him. The petitioner also cited reasons for his non-appearance in the trial court, including advocate's
advice, spondylosis, and his brother's death. The respondent-wife, appearing in person, contended that the petitioner
had unconditionally withdrawn an application for dropping the proceedings based on the consent terms, causing her
harassment. She claimed she was compelled to accept the settlement due to her financial circumstances as a housewife.
She further submitted that the case had proceeded for six years and was at its final stage, arguing against interference
by the High Court.

In *Michelle M. Desai v. Shirin R. Majai & Anr.*, the petitioner, the original complainant, lodged an FIR for offences
under Sections 324, 341, 342, 506 (Part 2) and 114 of the Indian Penal Code against her paternal aunt. After the charge
was framed, the parties settled two civil suits out of court by filing consent terms. An application to the Metropolitan
Magistrate for compounding the offence was refused because Section 506 (Part 2) is not compoundable under Section 320 of
the Code of Criminal Procedure. The petitioner then approached the High Court under Section 482 of the Code for quashing
the criminal complaint.

In *Freddy Sunderdas Thomas v. The State of Maharashtra and others*, the petitioner, original accused no. 1, had
defaulted on loan instalments for two trucks financed by Respondent No. 2. An Arbitrator was appointed, and the dispute
was settled, with the amount due being paid by Respondent No. 4, who purchased the trucks. The complainant company filed
an application before the Additional Chief Metropolitan Magistrate to withdraw the complaint, stating the matter was
settled. However, the Magistrate dismissed the application and passed an order under Section 156(3) of the Code of
Criminal Procedure, leading to the registration of an FIR and the petitioner's arrest. The petitioner sought to quash
this complaint.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr.*, the applicants sought to quash a criminal complaint registered with Gorai Police Station for offences under Sections 465, 468, 471, 420 of the Indian Penal Code. The parties had amicably settled the dispute, which was personal in nature.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.*, the original complainant filed a petition for
quashing a complaint he had filed against Dattatray Ramchandra Jadhav for offences under Sections 387 and 326 of the
Indian Penal Code. The petitioner claimed the complaint was false and registered at the instance of the police, despite
him never having filed it. The learned APP contended that several complaints were pending against the accused, who
appeared to be a history-sheeter, and there was no reason for the police to lodge a false case.

### As per the Court

The Bombay High Court, in *Prakash Piniomal Haswani v. State of Maharashtra & Ors.*, dismissed the writ petition seeking
to quash the criminal complaint. The Court observed that the petitioner had unconditionally withdrawn an earlier
application for dropping the proceedings based on consent terms. Therefore, it was not open for him to approach the High
Court under Article 226 of the Constitution of India read with Section 482 of the Code of Criminal Procedure, especially
when the trial had commenced and reached its final stage. The Court reiterated the well-settled position that once a
trial commences, it should be concluded either in acquittal or conviction. The Court also held that a mere undertaking
by a party in a former proceeding to consent to quashing does not automatically entitle the accused to have the criminal
complaint quashed. Relying on *B.S. Joshi v. State of Haryana, (2003) 4 SCC 675*, the Court emphasized that while the
High Court has inherent powers to quash a complaint, it must be satisfied that quashing is in the best interest of the
wife, family, and children. The Court noted that an offence under Section 498(A) of the Indian Penal Code is not merely
a personal offence but is against society at large, and such complaints, especially those alleging harassment, cannot be
taken lightly and quashed. The Court also considered the delay of six years in filing the petition after the undertaking
was given, and the fact that a contempt petition for breach of undertaking would be time-barred under Section 20 of the
Contempt of Court Act, 1971.

In *Michelle M. Desai v. Shirin R. Majai & Anr.*, the Bombay High Court allowed the criminal writ petition and quashed
the criminal complaint and FIR. The Court relied on the Supreme Court's decision in *B.S. Joshi v. State of Haryana, JT
2003(3) SC 277*, which held that the High Court's inherent powers under Section 482 of the Code of Criminal Procedure
are not limited by Section 320 of the Code. Since the parties had amicably settled the matter and did not wish to
proceed with the criminal litigation, the Court found it a fit case for quashing the complaint.

In *Freddy Sunderdas Thomas v. The State of Maharashtra and others*, the Bombay High Court allowed the writ petition and
quashed the criminal complaint and FIR. The Court found that the Magistrate clearly erred in passing an order under
Section 156(3) of the Code of Criminal Procedure when the complainant had already applied for withdrawal, stating that
the matter was settled. The Court reiterated that while a Magistrate may not have the power to compound a
non-compoundable offence under Section 320 of the Code, the High Court's powers under Section 482 are not curtailed. The
Court, considering the peculiar facts and circumstances, deemed it appropriate to quash the complaint.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr.*, the Bombay High Court allowed the application for
quashing the criminal complaint. The Court noted that the parties had amicably settled the dispute, which was personal
in nature. Relying on the judgments of the Apex Court in *Madan Mohan Abbot v. State of Punjab, AIR 2008 SC 1969* and
*Jagdish Chanana v. State of Haryana, AIR 2008 SC 1968*, the Court found no impediment to quashing the criminal
complaint by exercising its inherent jurisdiction under Section 482 of the Code of Criminal Procedure.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.*, the Bombay High Court dismissed the writ
petition. The Court observed that the accused appeared to be a history-sheeter with numerous pending cases. The Court
was not inclined to quash the complaint, citing the possibility of the petitioner being pressurized by the accused to
file the petition. The Court found no reason for the police to file a false complaint through the petitioner or any
other person and thus declined to exercise its jurisdiction under Article 226 of the Constitution of India or Section
482 of the Code of Criminal Procedure.

### Additional Observations

The judgments highlight that while the Bombay High Court possesses broad inherent powers to quash criminal complaints,
the exercise of these powers is discretionary and depends heavily on the specific facts and circumstances of each case.
The Court considers the nature of the offence (personal vs. against society), the stage of the proceedings, the
voluntariness of any settlement, and the potential for harassment or abuse of process. The Court also emphasizes that
the power under Section 482 of the Code of Criminal Procedure is not limited by Section 320, allowing quashing even for
non-compoundable offences in appropriate cases, particularly where a genuine settlement has been reached between the
parties. However, this power is not to be exercised as a matter of right, and the Court must be satisfied that it serves
the ends of justice and is in the interest of the complainant/victim.

### Step-by-Step Guide for Quashing a Criminal Complaint at Bombay High Court:

1. **Identify the grounds for quashing:** Common grounds include:
 - Amicable settlement between parties (especially in matrimonial disputes or cases arising out of commercial transactions).
 - The allegations in the FIR/complaint, even if taken at face value, do not constitute an offence.
 - The complaint is a clear abuse of the process of law.
 - The complaint is filed with malicious intent.
 - Lack of evidence to support the charges.

2. **Gather necessary documents:**
 - Copy of the FIR/criminal complaint.
 - Charge-sheet (if filed).
 - All relevant court orders from the trial court.
 - Proof of settlement (e.g., consent terms, settlement agreement, divorce decree, payment receipts).
 - Affidavits from the parties confirming the settlement and their desire to quash the complaint.
 - Any other documents supporting the grounds for quashing.

3. **Draft the Criminal Writ Petition/Application under Section 482 Cr.P.C.:**
 - The petition should be filed under Article 226 of the Constitution of India (for writ petitions) or Section 482 of the Code of Criminal Procedure, 1973 (for inherent powers).
 - Clearly state the facts of the case, the registration of the FIR/complaint, and the current status of the proceedings.
 - Detail the grounds for quashing, supported by facts and legal arguments.
 - Specifically mention any settlement reached between the parties and attach the consent terms/settlement agreement.
 - Include a prayer for quashing the FIR/complaint and any consequential proceedings.

4. **File the Petition/Application:**
 - File the petition along with supporting affidavits and documents in the Criminal Appellate Jurisdiction of the Bombay High Court.
 - Ensure proper court fees are paid.

5. **Appearance and Arguments:**
 - The petitioner's counsel will present arguments before the High Court.
 - If a settlement has been reached, the complainant (Respondent No. 2) may be required to appear in person or through counsel to confirm the settlement and their no-objection to quashing.
 - The Public Prosecutor (for the State) will also be heard.

6. **Court's Consideration:**
 - The High Court will examine the facts, the nature of the offence, the genuineness of the settlement, and whether quashing would serve the ends of justice.
 - The Court will refer to precedents like *B.S. Joshi v. State of Haryana* to determine if the case is fit for exercising its inherent powers.
 - The Court will also consider the stage of the trial; if the trial is at an advanced stage, the Court may be less inclined to quash, as seen in *Prakash Piniomal Haswani*.

7. **Order of the Court:**
 - If the Court is satisfied, it will pass an order quashing the criminal complaint and any consequential proceedings.
 - If the Court is not satisfied, it will dismiss the petition, and the criminal proceedings will continue.

8. **Follow-up:**
 - Obtain a certified copy of the High Court's order.
 - Submit the order to the trial court and the police station concerned to ensure compliance and closure of the criminal case.

---

## Statutory Provisions Referenced

The inherent powers of the High Court to quash a criminal complaint are primarily exercised under Section 482 of the Code of Criminal Procedure, 1973. This provision allows the High Court to make such orders as may be necessary to give effect to any order under the said Code, or to prevent abuse of the process of any Court, or otherwise to secure the ends of justice. Additionally, High Courts can exercise this power under Articles 226 and 227 of the Constitution of India.

It is important to note that with the enactment of the Bharatiya Nagarik Suraksha Sanhita (BNSS), 2023, the Code of
Criminal Procedure, 1973, is replaced. Section 482 of the Bharatiya Nagarik Suraksha Sanhita, 2023, corresponds to
Section 438 of the Code of Criminal Procedure, 1973, and deals with anticipatory bail. While the specific numerical
provision for inherent powers to quash criminal complaints may differ in the new Sanhita, the High Courts'
constitutional powers under Articles 226 and 227 of the Constitution of India to quash such proceedings remain
unaffected.

### Scope of High Court's Power

The power under Section 482 of the Code of Criminal Procedure, 1973, is discretionary and must be exercised sparingly and with circumspection. The High Court does not embark on an inquiry to determine if the allegations in the complaint are likely to be established by evidence or conduct a mini-trial at this stage. The primary objective is to prevent abuse of the court's process and to secure the ends of justice.

### Grounds for Quashing a Criminal Complaint

The Supreme Court in *State of Haryana v. Bhajan Lal, AIR 1992 SC 604*, laid down illustrative categories where the High Court may quash a criminal complaint or First Information Report (FIR). These grounds include:

- Where the allegations in the FIR or complaint, even if taken at their face value and accepted in their entirety, do not prima facie constitute any offence or make out a case against the accused.
- Where the allegations in the FIR and other materials, if any, accompanying the FIR do not disclose a cognizable
offence, justifying an investigation by police officers under Section 156(1) of the Code of Criminal Procedure, 1973
(corresponding to Section 173 of the Bharatiya Nagarik Suraksha Sanhita, 2023), except under an order of a Magistrate
within the purview of Section 155(2) of the Code of Criminal Procedure, 1973 (corresponding to Section 172(2) of the
Bharatiya Nagarik Suraksha Sanhita, 2023).
- Where the allegations made in the FIR and the evidence collected in support of the same do not disclose the commission of any offence and make out a case against the accused.
- Where the allegations in the FIR are so absurd and inherently improbable on the basis of which no prudent person can ever reach a just conclusion that there is sufficient ground for proceeding against the accused.
- Where there is an express legal bar engrafted in any of the provisions of the Code of Criminal Procedure, 1973, or the concerned Act, under which the criminal proceeding is instituted, to the institution and continuance of the proceedings.
- Where a criminal proceeding is manifestly attended with mala fide and/or where the proceeding is maliciously instituted with an ulterior motive for wreaking vengeance on the accused and with a view to spite him due to private and personal grudge.
- Where there is a compromise between the parties, especially in cases arising out of matrimonial disputes, family disputes, or other offences that are predominantly civil in nature, and the court is satisfied that the settlement is genuine and continuing the proceedings would be an abuse of the process of law. However, this power is exercised cautiously in serious offences that have a larger impact on society.

### Quashing of Criminal Complaint at Bombay High Court: Step-by-Step Guide

The procedure for filing a petition for quashing a criminal complaint before the Bombay High Court under Section 482 of the Code of Criminal Procedure, 1973 (or under Articles 226/227 of the Constitution of India) generally involves the following steps:

1. **Consultation with a Lawyer:** It is essential to consult a qualified and experienced criminal lawyer who can assess the merits of the case, identify appropriate grounds for quashing, and provide legal advice.
2. **Gathering Documents:** Obtain all relevant documents, including a copy of the FIR or the criminal complaint, the charge sheet (if filed), and any other supporting evidence that demonstrates the grounds for quashing.
3. **Drafting the Petition:** The lawyer will draft a detailed quashing petition (often referred to as a Criminal Writ
Petition or Criminal Application) addressed to the Bombay High Court. The petition will clearly state the facts of the
case, cite the relevant legal provisions (Section 482 CrPC and/or Articles 226/227 of the Constitution), and articulate
the specific grounds on which the quashing is sought. The person named as an accused in the FIR is the petitioner, and
the State and the original complainant are typically made respondents.
4. **Preparation of Affidavit:** An affidavit supporting the quashing petition must be prepared and sworn by the petitioner. This affidavit typically contains facts related to the case and reiterates the legal grounds for seeking quashing.
5. **Annexures:** All relevant documents, such as the FIR, charge sheet, and any other evidence, are annexed to the petition.
6. **Filing the Petition:** The petition, along with the affidavit and annexures, is filed before the appropriate bench of the Bombay High Court within whose territorial jurisdiction the FIR was registered.
7. **Hearing and Arguments:** During the hearing, the High Court will hear arguments from the petitioner's counsel, the public prosecutor representing the State, and the counsel for the complainant (if they choose to appear). The court will consider the allegations in the FIR and the material on record to determine if a prima facie case is made out or if the proceedings constitute an abuse of process.
8. **Interim Relief (Optional):** In some cases, the High Court may grant interim relief, such as a stay on further proceedings or protection from arrest, pending the final disposal of the quashing petition.
9. **Court's Decision:** After hearing all parties and considering the material, the High Court will pass an order. If
satisfied that the grounds for quashing are met, it may quash the FIR, the criminal complaint, or the entire criminal
proceedings. The Bombay High Court has exercised this power, even post-conviction, when an appeal is pending and a
genuine settlement has been reached, particularly in matrimonial disputes. However, it has also refused to quash serious
offences with societal implications despite settlement.

---

## Related Supreme Court Authority

The Supreme Court has delivered several landmark rulings on the quashing of criminal complaints, primarily under Section 482 of the Code of Criminal Procedure, 1973 (Cr.P.C.). These judgments outline the circumstances under which High Courts can exercise their inherent powers to prevent abuse of the process of any court or otherwise to secure the ends of justice.

Here are two significant cases that discuss the principles for quashing criminal complaints:

#### *Chanchalpati Das v. The State of West Bengal*, Criminal Appeal No. 1592 of 2023, decided on 18-05-2023

This case involved an appeal against the High Court's dismissal of applications seeking to quash a charge-sheet for offences under Sections 468, 471, 406, and 120-B of the Indian Penal Code (IPC). The Supreme Court allowed the appeal, quashing the criminal proceedings.

### Key Legal Issues
- Whether an inordinate and unexplained delay in filing a criminal complaint can be a ground for quashing the proceedings.
- Whether the allegations in the complaint and charge-sheet, even if taken at face value, constitute a prima facie case.
- The scope of inherent powers of the High Court under Section 482 Cr.P.C. for quashing criminal proceedings.

### Facts
The case originated from a complaint filed in 2009 regarding the alleged theft of a bus in 2001. The complainant, ISKCON Kolkata, alleged that the bus was taken to Bengaluru by the appellants, who were the President and Vice-President of ISKCON Bengaluru. An FIR was registered in 2009, and a charge-sheet was filed in 2010. The appellants sought to quash these proceedings before the High Court, which was dismissed.

### Detailed Narrative
The appellants argued that the prosecution was an attempt to harass them and settle personal scores, especially given
the significant delay of eight years in filing the complaint for an incident that occurred in 2001. They also contended
that the allegations were absurd and improbable, and no reasonable possibility of conviction existed. The
respondent-complainant argued that a prima facie case was made out and that the documents produced by the appellants
regarding the bus transfer were new and should not be considered.

### Court Observations
The Supreme Court, relying on its landmark decision in *State of Haryana and Ors. v. Bhajan Lal and Ors.*, (1992) Supp. (1) SCC 335, reiterated the guidelines for exercising powers of quashing under Section 482 Cr.P.C. The Court emphasized that such powers should be exercised sparingly but must be used if a clear case of abuse of the process of law is made out.

The Court found that:
- There was an inordinate and unexplained delay of eight years in filing the complaint, which itself was a sufficient ground to quash the proceedings.
- The allegations in the complaint and charge-sheet, even if taken at face value, did not prima facie constitute any of the alleged offences.
- There was no cogent or substantive evidence collected by the investigating officer to prosecute the appellants.
- Allowing the prosecution to continue would be an abuse of the process of law and a gross wastage of the court's precious time.
- The Court also made observations on the proliferation of frivolous litigation and the need to deter litigants from misusing the judicial process.

### Judgment & Direction
The Supreme Court allowed the appeals, quashing the criminal proceedings pending against the appellants in the Court of Chief Judicial Magistrate, Alipore, arising out of FIR No. 33 of 2009. The Court also imposed a cost of Rs. 1,00,000/- on the respondent-complainant, to be deposited in the office of the Supreme Court Advocates-on-Record Association.

#### *Kahkashan Kausar @ Sonam v. The State of Bihar*, Criminal Appeal No. 195 of 2022, decided on 08-02-2022

This case dealt with the quashing of an FIR under Section 498A IPC and other sections, where general and omnibus allegations were made against the husband's relatives. The Supreme Court allowed the appeal, quashing the FIR against the appellants (in-laws).

### Key Legal Issues
- Whether general and omnibus allegations against in-laws in a matrimonial dispute warrant quashing of the criminal complaint.
- The misuse of Section 498A IPC and the need for courts to be cautious in proceeding against distant relatives.
- The application of principles laid down in *Lalita Kumari v. Government of U.P. & Ors.* regarding preliminary inquiry.

### Facts
The complainant wife filed an FIR against her husband and in-laws, alleging dowry demand and harassment. Previously, a similar complaint was filed where the Magistrate found no prima facie case against the in-laws due to omnibus allegations. The High Court dismissed the petition for quashing the subsequent FIR, leading to the appeal before the Supreme Court.

### Detailed Narrative
The appellants (in-laws) contended that the FIR was filed with a revengeful intent to harass them and that the allegations were omnibus in nature, similar to a previous complaint where no case was made out against them. The State and the complainant argued that the present FIR pertained to fresh offences and a fresh cause of action, and that investigation had found the case true against all accused.

### Court Observations
The Supreme Court expressed concern over the misuse of Section 498A IPC and the increasing tendency to implicate relatives of the husband in matrimonial disputes without specific allegations. The Court reiterated that false implication by way of general omnibus allegations, if unchecked, would result in misuse of the process of law.

The Court observed that:
- The allegations made in the FIR were general and omnibus, with no specific role attributed to the appellants (in-laws).
- Allowing prosecution in the absence of clear allegations against the in-laws would be an abuse of the process of law.
- A criminal trial leading to eventual acquittal inflicts severe scars upon the accused, and such an exercise must be discouraged when allegations are general and omnibus.
- The Court referred to its previous judgments in *Rajesh Sharma and Ors. v. State of U.P. & Anr.*, (2018) 10 SCC 472, and *Arnesh Kumar v. State of Bihar and Anr.*, (2014) 8 SCC 273, highlighting the need to prevent misuse of Section 498A IPC.

### Judgment & Direction
The Supreme Court set aside the impugned order of the High Court and quashed the FIR No. 248 of 2019 against the appellants (in-laws) under Sections 341, 323, 379, 354, 498A read with Section 34 IPC. The appeal was allowed.

### Broader Significance
These judgments underscore the Supreme Court's consistent stance on preventing the abuse of the criminal justice system.
They provide crucial guidance to High Courts on exercising their inherent powers under Section 482 Cr.P.C. to quash
criminal complaints in cases of inordinate and unexplained delay, general and omnibus allegations, or when the
allegations, even if taken at face value, do not constitute a prima facie offence. The rulings emphasize the need for
courts to scrutinize complaints carefully, especially in matrimonial disputes, to ensure that the process of law is not
used to settle personal scores or for harassment.

**PDF Links:**
- *CHANCHALPATI DAS VS THE STATE OF WEST BENGAL*: [Judgment PDF](https://api.sci.gov.in/supremecourt/2017/17824/17824_2017_4_1502_44572_Judgement_18-May-2023.pdf)
- *KAHKASHAN KAUSAR @ SONAM VS THE STATE OF BIHAR*: [Judgment PDF](https://api.sci.gov.in/supremecourt/2020/25575/25575_2020_37_1501_33195_Judgement_08-Feb-2022.pdf)

