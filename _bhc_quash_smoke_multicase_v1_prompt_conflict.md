# bhc_quash_smoke

**Endpoint:** `http://localhost:5000/pyapi/chat`  
**Thread ID:** `c24a22ca-5e0e-4a2c-b121-71ee3590a704`  
**Elapsed:** 85.75s  
**TTFB:** 897 ms  
**Answer length:** 30,914 chars (4,774 words)  
**Raw SSE events:** 366  

## Prompt

```
Complete details on quashing of criminal complaint at Bombay high court and step by step guide
```

## Event-type counts

| Event type | Count |
|---|---:|
| `token` | 323 |
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
|    1 |    0.90s | `thread_id` | data='c24a22ca-5e0e-4a2c-b121-71ee3590a704' |
|    2 |    0.90s | `progress` | step='validate' message='Validating query...' agent='guardrail' |
|    3 |    2.89s | `status` | message='Validating query...' agent='guardrail_input' |
|    4 |    2.89s | `progress` | step='language' message='Detecting language...' agent='memory' |
|    5 |    2.89s | `progress` | step='language' message='Detected: en' agent='memory' |
|    6 |    2.89s | `progress` | step='history' message='Loading conversation history...' agent='memory' |
|    7 |    2.92s | `progress` | step='history' message='Found 0 previous turns' agent='memory' |
|    8 |    2.92s | `progress` | step='rewrite' message='Rewriting follow-up query...' agent='memory' |
|    9 |    2.93s | `status` | message='Loading context...' agent='memory' |
|   10 |    2.93s | `context` |  |
|   11 |    2.93s | `progress` | step='classify' message='Understanding your question...' agent='orchestrator' |
|   12 |    6.63s | `progress` | step='classify' message='Identified: Judgment, Legislation' agent='orchestrator' |
|   13 |    6.63s | `progress` | step='plan' message='Preparing search queries for 3 agents...' agent='orchestrator' |
|   14 |    8.85s | `status` | message='Planning search strategy...' agent='orchestrator_plan' |
|   15 |    8.86s | `agents_planned` | agents=["Judgment", "Legislation", "SCI_Judgment"] |
|   16 |    8.87s | `progress` | step='metadata' message='Extracting case details from query...' agent='judgment' |
|   17 |    8.87s | `progress` | step='search' message='Searching court judgments (2 strategies in parallel)...' agent='judgment' |
|   18 |    8.87s | `progress` | step='parse' message='Parsing query for section references...' agent='legislation' |
|   19 |    8.87s | `progress` | step='parse' message='Detected sections: 482' agent='legislation' |
|   20 |    8.88s | `progress` | step='search' message='Searching legislation database...' agent='legislation' |
|   21 |    8.88s | `progress` | step='prepare' message='Preparing Supreme Court search...' agent='sci_judgment' |
|   22 |    8.88s | `progress` | step='react' message='Running multi-step research (ReAct agent)...' agent='sci_judgment' |
|   23 |   12.46s | `progress` | step='search' message='Found 5 matching provisions' agent='legislation' |
|   24 |   12.46s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='legislation' |
|   25 |   16.39s | `progress` | step='fallback' message="Retrieved provisions don't match the query — searching the web..." agent='legislation' |
|   26 |   29.42s | `progress` | step='search' message='Refining search with extracted metadata...' agent='judgment' |
|   27 |   61.12s | `progress` | step='search' message='Top match: Prakash Piniomal Haswani vs State of Maharashtra' agent='judgment' |
|   28 |   61.12s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='judgment' |
|   29 |   61.13s | `progress` | step='react' message='Completed 5 search steps' agent='sci_judgment' |
|   30 |   61.13s | `progress` | step='results' message='Found 5 Supreme Court judgments' agent='sci_judgment' |
|   31 |   61.13s | `progress` | step='generate' message='Generating response...' agent='sci_judgment' |
|   32 |   61.14s | `status` | message='Searching Supreme Court judgments...' agent='sci_judgment' |
|   33 |   63.89s | `progress` | step='generate' message='Generating response with citations...' agent='judgment' |
|  273 |   65.28s | `status` | message='Searching legislation...' agent='legislation' |
|  358 |   83.75s | `status` | message='Searching court judgments...' agent='judgment' |
|  359 |   83.77s | `progress` | step='synthesize' message='Merging results from 3 agents...' agent='orchestrator' |
|  360 |   83.77s | `status` | message='Injecting citations into draft...' agent='orchestrator_synthesize' |
|  361 |   83.77s | `progress` | step='finalize' message='Finalizing response...' agent='guardrail' |
|  362 |   83.77s | `status` | message='Finalizing...' agent='guardrail_output' |
|  363 |   84.92s | `response` |  |
|  364 |   84.92s | `sources` | data=[{"source_type": "judgment", "title": "Prakash Piniomal Haswani vs State of Maharashtra", "content": ["16-wp-2676-2014\nIN THE HIGH COURT OF JUDICATURE AT BOMBAY\nCRIMINAL APPELLATE JURISDICTION\nCRIMINAL WRIT PETITIO... |
|  365 |   85.70s | `followup_suggestions` | data=["What are grounds for quashing?", "What evidence is needed?", "How long does it take?"] |
|  366 |   85.75s | `done` |  |

## Final response

The Bombay High Court has the power to quash criminal complaints under Article 226 of the Constitution of India and
Section 482 of the Code of Criminal Procedure, 1908. This power is not limited by Section 320 of the Code of Criminal
Procedure, 1908, which deals with the compounding of offences. The High Court can quash proceedings even in
non-compoundable cases, especially when the dispute is of a personal nature and the parties have amicably settled the
matter. However, the Court exercises this power with caution, particularly when the trial has commenced and reached an
advanced stage, or when the offence is not purely personal but affects society at large, such as offences under Section
498A of the Indian Penal Code, 1860. The Court must be satisfied that quashing the complaint is in the best interest of
the complainant and/or victim.

I think this information will help you more to understand the case:

### Key Legal Issues
- Whether the High Court can quash a criminal complaint under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, 1908, even if the offence is non-compoundable under Section 320 of the Code of Criminal Procedure, 1908.
- The circumstances under which the High Court should exercise its inherent powers to quash a criminal complaint, especially when the trial has commenced or when there is an undertaking by the complainant to consent to quashing.
- The relevance of the nature of the offence (personal vs. against society) in deciding whether to quash a criminal complaint.

### Detailed Narrative
The provided judgments from the Bombay High Court illustrate various scenarios where the quashing of criminal complaints was sought.

In *Prakash Piniomal Haswani v. State of Maharashtra & Ors., Criminal Writ Petition No.2676 of 2014*, the petitioner
sought to quash a criminal complaint for offences including Sections 232, 420, 406, 497, 498A, 506(ii) read with 34 of
the Indian Penal Code, 1860, based on an undertaking by his ex-wife (respondent No.2) in divorce proceedings to consent
to quashing. Despite the undertaking and a settlement amount, the wife continued to assist the prosecution. The
petitioner had also unconditionally withdrawn an earlier application for dropping the proceedings. The trial had
commenced and was at its final stage.

In *Michelle M. Desai v. Shirin R. Majai & Anr., Criminal Writ Petition No. 1279 of 2005*, the petitioner sought to quash a complaint for offences under Sections 324, 341, 342, 506 (Part 2) and 114 of the Indian Penal Code, 1860, after the parties settled the matter amicably. The Magistrate had refused to compound the offence under Section 506 (Part 2) as it was non-compoundable.

In *Freddy Sunderdas Thomas v. The State of Maharashtra and others, Criminal Writ Petition No. 1080 of 2005*, the
petitioner sought to quash a complaint for offences under Sections 379, 409, and 420 of the Indian Penal Code, 1860. The
dispute arose from a finance agreement for trucks, and the matter was settled between the parties, with the complainant
receiving the due amount. The Magistrate had initially dismissed the complainant's application to withdraw the complaint
and proceeded to order investigation under Section 156(3) of the Code of Criminal Procedure, 1908.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr., Criminal Application No. 2689 of 2010*, the applicants sought to quash a complaint for offences under Sections 465, 468, 471, 420 of the Indian Penal Code, 1860, as the parties had amicably settled the dispute, which was personal in nature.

In *Gurmit Singh Ghai v. Nasrudin K. Jagmagia & Anr., Criminal Writ Petition No.2022 of 2010*, the petitioner sought to quash a complaint for offences under Sections 465, 471, 420 read with Section 34 of the Indian Penal Code, 1860, after the parties settled the dispute and the complainant filed an affidavit of no objection.

In *Raju Shankar Shinde v. The State of Maharashtra and Another, Writ Petition No.3598 of 2014*, the petitioner sought to quash a complaint for offences under Sections 420, 437, 438, and 471 read with Section 34 of the Indian Penal Code, 1860. The charge-sheet was filed, charges framed, and trial had commenced with one witness's statement recorded. The original complainant had given consent for quashing.

In *Atish Vasant Pawar & Ors. v. The State of Maharashtra & Anr., Writ Petition No. 4250 of 2014*, the petitioners sought to quash a complaint filed by the wife for offences under Sections 498A, 323, 504, 406 read with 34 of the Indian Penal Code, 1860. The wife was present in court and stated she had no objection, also filing an affidavit to that effect.

In *Farhan Jilani Kokani & Anr. v. Mohd. Arif Babubhai Shaikh & Ors., Criminal Writ Petition No. 321 of 2015*, the petitioners sought to quash an FIR for offences under Sections 406, 420 read with 34 of the Indian Penal Code, 1860, as the parties had arrived at an amicable settlement during the pendency of the investigation. The respondent No.1 filed an affidavit of no objection and was personally present in court.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors., Writ Petition No. 4386 of 2014*, the original complainant filed a petition for quashing his own complaint against an accused for offences under Sections 387, 326 of the Indian Penal Code, 1860. The learned APP submitted that several complaints were pending against the accused, who appeared to be a history-sheeter.

In *Ramesh Rama Shetty v. The State of Maharashtra & Anr., Writ Petition No. 46 of 2013*, the petitioner sought to quash a criminal complaint for offences under Sections 504, 506(II) of the Indian Penal Code, 1860. The petition was dismissed for want of prosecution, and the Court found that the complaint disclosed ingredients of the offences.

### As per the Court

The Bombay High Court, in these judgments, consistently referred to the Supreme Court's decision in *B.S. Joshi v. State
of Haryana, (2003) 4 SCC 675* (also cited as AIR 2003 SC 1386 or JT 2003(3) SC 277 = 2003 CRI.L.J.2028), which
established that the High Court's inherent powers under Section 482 of the Code of Criminal Procedure, 1908, are not
curtailed by Section 320 of the Code of Criminal Procedure, 1908. This allows the High Court to quash criminal
proceedings even for non-compoundable offences, especially in cases arising out of matrimonial disputes or other
personal disputes where the parties have settled.

However, the Court also emphasized the need for caution and discretion. In *Prakash Piniomal Haswani*, the Bombay High
Court dismissed the petition, noting that the petitioner had unconditionally withdrawn an earlier application for
dropping proceedings, and the trial had reached its final stage. The Court also stated that an undertaking to consent to
quashing does not give the accused a "passport" to demand quashing. For offences like Section 498A of the Indian Penal
Code, 1860, which are not purely personal but affect society, the Court must exercise due care and caution, ensuring
that quashing is in the best interest of the wife, family, and children, and not merely a result of compulsion due to
the wife's lack of income or harassment. The Court also observed that the delay of six years in filing the petition
after the undertaking was a factor.

In contrast, in *Michelle M. Desai*, *Freddy Sunderdas Thomas*, *Mrs. Dale Herman Nazareth & Ors.*, *Gurmit Singh Ghai*,
and *Farhan Jilani Kokani & Anr.*, the Bombay High Court allowed the petitions and quashed the complaints. In these
cases, the disputes were primarily personal, the parties had amicably settled, and the Court was satisfied that quashing
was appropriate. In *Freddy Sunderdas Thomas*, the Court specifically noted that the Magistrate erred in ordering
investigation under Section 156(3) of the Code of Criminal Procedure, 1908, when the complainant had already sought to
withdraw the complaint due to settlement.

In *Raju Shankar Shinde*, the Bombay High Court dismissed the petition because the charge-sheet was filed, charges framed, and the trial had commenced with a witness's statement recorded. Despite the complainant's consent, the Court found it impossible to quash the complaint at that advanced stage, especially since the complaint contained specific averments implicating the petitioner.

In *Sagar Dnyandeo Patekar*, the Bombay High Court refused to quash the complaint, noting that the accused was a "history sheeter" with multiple pending cases, and there was a possibility of the complainant being pressurized to file the petition for quashing. The Court found no reason for the police to file a false complaint.

In *Ramesh Rama Shetty*, the Bombay High Court dismissed the petition for want of prosecution and because the complaint disclosed the ingredients of the alleged offences.

The Bombay High Court, in *Atish Vasant Pawar & Ors.*, allowed the petition for quashing a Section 498A complaint, finding that the ratio of *B.S. Joshi* applied and that quashing was in the best interest of the wife, who had given her no objection through an affidavit and was present in court. This indicates that while Section 498A cases require caution, they can be quashed if the Court is satisfied about the genuine settlement and the victim's best interest.

**Step-by-step guide for quashing a criminal complaint at the Bombay High Court:**

1. **Identify the appropriate legal provision:** A petition for quashing a criminal complaint is typically filed under Article 226 of the Constitution of India (for writ jurisdiction) read with Section 482 of the Code of Criminal Procedure, 1908 (for inherent powers of the High Court).
2. **Grounds for Quashing:**
 - **Settlement between parties:** This is a common ground, especially in cases arising from matrimonial disputes or other personal disputes, even if the offences are non-compoundable under Section 320 of the Code of Criminal Procedure, 1908. The Court will examine if the settlement is genuine and voluntary.
 - **Lack of evidence/prima facie case:** If the complaint or FIR does not disclose any cognizable offence or makes out a case against the accused.
 - **Abuse of process of law:** If the criminal proceedings are initiated with malicious intent or for ulterior motives, or if their continuation would be an abuse of the process of any court.
 - **Delay:** Unexplained and inordinate delay in filing the complaint or in the prosecution of the case, which prejudices the accused.
3. **Preparation of Petition:**
 - Draft a Criminal Writ Petition (or Criminal Application) clearly stating the facts of the case, the FIR/complaint details, the offences alleged, and the grounds for seeking quashing.
 - Include an affidavit from the petitioner.
 - If there is a settlement, obtain an affidavit from the complainant/victim stating their no-objection to the quashing, detailing the terms of settlement (if any), and confirming that the settlement is voluntary and in their best interest. The complainant's presence in court during the hearing is often required.
 - Attach all relevant documents, such as the FIR, charge-sheet (if filed), divorce decree, consent terms, settlement agreements, etc.
4. **Filing the Petition:** File the petition with the Registry of the Bombay High Court.
5. **Hearing and Arguments:**
 - The petitioner's counsel will argue the case, highlighting the grounds for quashing, especially the settlement (if applicable) and the principles laid down in *B.S. Joshi v. State of Haryana* and other relevant precedents.
 - The Public Prosecutor (for the State) will present the State's stand, often opposing quashing, especially for serious or non-personal offences, or if the trial is at an advanced stage.
 - If the complainant is present in person or represented by counsel, they will confirm their stand regarding the settlement and quashing.
6. **Court's Consideration:** The High Court will consider:
 - The nature and gravity of the offences.
 - Whether the dispute is purely personal or affects society at large.
 - The stage of the criminal proceedings (e.g., investigation, charge-sheet filed, charges framed, trial commenced, evidence recorded). Quashing is less likely if the trial is at an advanced stage.
 - The genuineness and voluntariness of the settlement.
 - Whether quashing would be in the best interest of the victim and justice.
 - Any delay in approaching the High Court for quashing.
 - The conduct of the parties (e.g., unconditional withdrawal of previous applications, as seen in *Prakash Piniomal Haswani*).
7. **Order:** The High Court will pass an order either allowing the petition (quashing the complaint/FIR and consequential proceedings) or dismissing it. The Court may impose conditions, such as payment of costs to a charitable institution, as seen in *Farhan Jilani Kokani & Anr.*

### Additional Observations
The judgments highlight the discretionary nature of the High Court's power under Section 482 of the Code of Criminal
Procedure, 1908. While the power is wide, it is not to be exercised as a matter of right. The Court considers the
specific facts and circumstances of each case, balancing the interests of justice, the victim, and society. The stage of
the trial is a crucial factor, with courts generally reluctant to interfere once the trial has significantly progressed.
The Court also takes into account the possibility of coercion or pressure on the complainant, especially in cases
involving vulnerable parties like wives in matrimonial disputes.

### PDF Links
- *Prakash Piniomal Haswani v. State of Maharashtra & Ors., Criminal Writ Petition No.2676 of 2014*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2014/July/16-wp-2676-2014.pdf))
- *Michelle M. Desai v. Shirin R. Majai & Anr., Criminal Writ Petition No. 1279 of 2005*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2005/May/16-wp-1279-2005.pdf))
- *Freddy Sunderdas Thomas v. The State of Maharashtra and others, Criminal Writ Petition No. 1080 of 2005*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2005/April/16-wp-1080-2005.pdf))
- *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra & Anr., Criminal Application No. 2689 of 2010*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2010/August/16-ca-2689-2010.pdf))
- *Gurmit Singh Ghai v. Nasrudin K. Jagmagia & Anr., Criminal Writ Petition No.2022 of 2010*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2010/August/16-wp-2022-2010.pdf))
- *Ramesh Rama Shetty v. The State of Maharashtra & Anr., Writ Petition No. 46 of 2013*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2014/September/16-wp-46-2013.pdf))
- *Farhan Jilani Kokani & Anr. v. Mohd. Arif Babubhai Shaikh & Ors., Criminal Writ Petition No. 321 of 2015*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2015/February/16-wp-321-2015.pdf))
- *Raju Shankar Shinde v. The State of Maharashtra and Another, Writ Petition No.3598 of 2014*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2014/October/16-wp-3598-2014.pdf))
- *Atish Vasant Pawar & Ors. v. The State of Maharashtra & Anr., Writ Petition No. 4250 of 2014*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2014/November/16-wp-4250-2014.pdf))
- *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors., Writ Petition No. 4386 of 2014*
 ([Judgment PDF](https://lawttorney.s3.ap-south-1.amazonaws.com/bombay/2014/November/16-wp-4386-2014.pdf))

---

## Statutory Provisions Referenced

The High Court's inherent powers to quash a criminal complaint are enshrined in Section 482 of the Code of Criminal Procedure, 1973 (CrPC), corresponding to Section 528 of the Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS), which will come into effect from July 1, 2024. This provision safeguards against the abuse of legal processes and ensures that justice is served.

### Inherent Powers of the High Court

Section 482 of the Code of Criminal Procedure, 1973, preserves the inherent powers of the High Court to:
- Give effect to any order under the Code.
- Prevent abuse of the process of any court.
- Otherwise secure the ends of justice.

These powers are not granted by any statute but exist due to the High Court's position as a constitutional court. They are extraordinary in nature and are to be exercised sparingly and with caution, typically not interfering with a genuine investigation at an early stage. The High Court can intervene when a person's right to life or liberty is threatened by an illegal prosecution.

### Grounds for Quashing a Criminal Complaint

The Supreme Court has laid down guiding principles for the exercise of these inherent powers, notably in *State of Haryana v. Bhajan Lal, 1992*. A criminal complaint or First Information Report (FIR) may be quashed on the following grounds:
- Where the allegations made in the FIR or complaint, even if taken at their face value and accepted in their entirety, do not prima facie constitute any offence or make out a case against the accused.
- Where the allegations in the FIR do not disclose a cognizable offence, warranting an investigation under Section 156(1) of the Code of Criminal Procedure, 1973.
- Where the allegations are improbable and so absurd that no prudent person would believe them.
- Where the criminal proceedings are initiated with malicious intent, an ulterior motive, or are an abuse of the legal process, such as for harassment or personal vendetta.
- Where there is a legal bar against the proceedings, such as a want of sanction.
- Where there is no legal evidence adduced in support of the charge, or the evidence clearly fails to prove the charge.
- Where the parties involved reach a settlement, particularly in non-heinous offences of a private nature, and the continuance of proceedings would serve no purpose. However, offences against society at large, such as rape, offences under the Narcotics Drugs and Psychotropic Substances Act, 1985, or terrorism, generally cannot be quashed on the basis of compromise.
- When the case involves a purely civil dispute given a criminal colour to gain collateral advantage.

### Step-by-Step Guide for Quashing a Criminal Complaint at the Bombay High Court

The procedure for filing a quashing petition under Section 482 of the Code of Criminal Procedure, 1973, before the Bombay High Court typically involves the following steps:

1. **Consult a Lawyer**: Engage a lawyer practicing in the High Court who can assess the merits of the case and advise on the appropriate legal strategy.
2. **Drafting the Quashing Petition**: The lawyer will draft a detailed petition, often referred to as a Criminal Application, clearly stating the facts of the case, the specific criminal complaint or FIR sought to be quashed, and the legal grounds for quashing.
3. **Prepare an Affidavit**: A supporting affidavit must be prepared, outlining the facts and legal grounds for seeking the quashing of the complaint.
4. **Attach Relevant Documents**: The petition should be accompanied by all relevant documents, such as a copy of the FIR, the charge sheet (if filed), the complaint, and any other evidence that supports the grounds for quashing.
5. **File the Petition**: The petition, along with the affidavit and supporting documents, is filed with the Registry of the Bombay High Court. The requisite court fees and procedural formalities must be satisfied.
6. **Court Proceedings**:
 - **Initial Hearing**: At the first stage, the High Court may grant a stay on the ongoing proceedings in the lower court and issue notice to the opposing party (complainant/State).
 - **Exchange of Pleadings**: The opposing party will file their reply (affidavit-in-reply), and the petitioner may file a rejoinder.
 - **Arguments**: Both parties will present their arguments before the High Court.
7. **Court's Decision**: After hearing both sides and considering the evidence and legal arguments, the High Court will pass an order. If satisfied that the grounds for quashing are met, the court will quash the criminal complaint or proceedings. The High Court exercises its discretion based on the facts and circumstances of each case.

---

## Related Supreme Court Authority

The Supreme Court has provided significant rulings on the quashing of criminal complaints, primarily under Section 482 of the Code of Criminal Procedure, 1973 (Cr.P.C.). The power to quash a criminal complaint is an inherent power of the High Court, to be exercised sparingly and with caution, to prevent abuse of the process of any court or otherwise to secure the ends of justice.

Here are some landmark rulings from the Supreme Court regarding the quashing of criminal complaints:

### Case: *CHANCHALPATI DAS v. THE STATE OF WEST BENGAL*, Crl.A. No.-001592-001592 - 2023 (Supreme Court, 2023)

#### Key Legal Issues
- Whether the High Court was justified in dismissing the criminal revision applications seeking quashing of the charge-sheet.
- Whether the prosecution initiated against the appellants was an abuse of the process of law due to delay and lack of evidence.
- The applicability of principles laid down in *State of Haryana v. Bhajan Lal* for quashing criminal proceedings.

#### Detailed Narrative
The appellants, Madhu Pandit Das (President of ISKCON, Bengaluru) and Chanchalpati Das (Vice President of ISKCON,
Bengaluru), faced charges under Sections 468, 471, 406, and 120-B of the Indian Penal Code (IPC) based on an FIR
registered in 2009. The complaint alleged theft and criminal breach of trust regarding a bus belonging to ISKCON,
Kolkata, which was allegedly taken to Bengaluru in 2001-2002. The appellants contended that the FIR was filed with an
oblique motive to harass them and that there was an inordinate delay in filing the complaint. They also produced
documents regarding the transfer of registration of the bus to Bengaluru in 2002. The High Court dismissed their
criminal revision applications, leading to the present appeal before the Supreme Court.

#### As per the Court
The Supreme Court observed that the alleged incident of bus theft took place in 2001, but the substantial complaint was
made in the Court of Chief Judicial Magistrate, Alipore, only in 2009. The Court found it unbelievable that the ISKCON
Kolkata, a powerful body, would not have taken any action on reports made in 2002 and 2006. The Court emphasized that
the respondent-complainant did not take concrete action for a long period of eight years. The Court referred to the
landmark decision in *State of Haryana v. Bhajan Lal*, which outlined guidelines for quashing criminal proceedings. The
Court noted that the High Court had not properly considered the documents produced by the appellants regarding the
transfer of the bus. The Court concluded that the continuation of the criminal proceedings against the appellants would
be an abuse of the process of the court.

#### Judgment & Direction
The Supreme Court allowed the appeals, setting aside the impugned judgment of the High Court and quashing the criminal proceedings against the appellants. The Court directed that the appellants' offer to purchase a new bus for ISKCON Kolkata be accepted, and the criminal proceedings be closed.

**PDF Links:**
 - [Judgment PDF](https://api.sci.gov.in/supremecourt/2017/17824/17824_2017_4_1502_44572_Judgement_18-May-2023.pdf)

### Case: *KAHKASHAN KAUSAR @ SONAM v. THE STATE OF BIHAR*, Crl.A. No.-000195-000195 - 2022 (Supreme Court, 2022)

#### Key Legal Issues
- Whether the allegations made against the in-laws (appellants) were general omnibus allegations and therefore liable to be quashed.
- The misuse of Section 498A of the IPC in matrimonial disputes.
- The scope of the High Court's power under Section 482 Cr.P.C. to quash an FIR.

#### Detailed Narrative
The complainant (Respondent No. 5) was married to Md. Ikram. She filed a criminal complaint against her husband and
in-laws (appellants) alleging demand for dowry and harassment under Sections 341, 323, 379, 354, 498A read with Section
34 of the IPC. Previously, a similar complaint was filed in 2017, where the Magistrate found no prima facie case against
the in-laws. The present FIR was filed in 2019, alleging continued dowry demand and threats. The appellants filed a
criminal writ petition before the Patna High Court for quashing the FIR, which was dismissed. The appellants then
approached the Supreme Court.

#### As per the Court
The Supreme Court acknowledged the laudable object of Section 498A IPC but also noted the increasing tendency to use it
as a weapon to settle personal scores in matrimonial disputes. The Court referred to its previous judgments in *Rajesh
Sharma v. State of U.P.* and *Arnesh Kumar v. State of Bihar*, which highlighted the misuse of Section 498A and the need
for caution. The Court observed that the allegations against the in-laws were general and omnibus in nature, without
specific instances of their involvement. The Court reiterated that the High Court, in exercising its power under Section
482 Cr.P.C., should not hesitate to quash criminal proceedings where the allegations are vague and general, especially
against distant relatives.

#### Judgment & Direction
The Supreme Court allowed the appeal, quashing the FIR and the criminal proceedings against the appellants (in-laws). The Court held that allowing the proceedings to continue would be an abuse of the process of law.

**PDF Links:**
 - [Judgment PDF](https://api.sci.gov.in/supremecourt/2020/25575/25575_2020_37_1501_33195_Judgement_08-Feb-2022.pdf)

### Case: *PADAL VENKATA RAMA REDDY @ RAMU v. KOVVURI SATYANARAYANA REDDY*, Crl.A. No.-001499-001499 - 2011 (Supreme Court, 2011)

#### Key Legal Issues
- The scope and exercise of inherent powers of the High Court under Section 482 Cr.P.C. for quashing criminal proceedings.
- The circumstances under which a High Court can quash an FIR or criminal complaint.

#### Detailed Narrative
The appellant, the defacto complainant, filed a charge sheet against the respondents (accused persons) for various
offenses including criminal conspiracy, rioting, mischief, attempt to murder, and causing disappearance of evidence. The
allegations stemmed from an incident where the appellant was attacked. The respondents filed a criminal petition before
the High Court of Andhra Pradesh under Section 482 Cr.P.C. to quash the criminal proceedings against them. The High
Court allowed the petition and quashed the proceedings. Aggrieved by this order, the appellant filed an appeal before
the Supreme Court.

#### As per the Court
The Supreme Court discussed in detail the inherent powers of the High Court under Section 482 Cr.P.C. and reiterated the
principles laid down in *R.P. Kapur v. State of Punjab* and *State of Karnataka v. L. Muniswamy*. The Court emphasized
that the power to quash criminal complaints should be used sparingly and with abundant caution, only in exceptional
cases where allowing the proceedings to continue would be an abuse of the process of the court or to secure the ends of
justice. The Court also referred to *State of Haryana v. Bhajan Lal*, which provided a comprehensive list of guidelines
for the High Courts to exercise their inherent powers. The Court noted that the High Court should not enter into any
finding of facts, particularly when there are concurrent findings of facts by lower courts.

#### Judgment & Direction
The Supreme Court, after considering the facts and the legal principles, examined whether the High Court was justified in quashing the criminal proceedings. The judgment text provided up to 12,000 characters does not contain the final operative direction of the Supreme Court in this specific case. To understand the final outcome, further reading of the judgment would be required.

**PDF Links:**
 - [Judgment PDF](https://api.sci.gov.in/jonew/judis/38295.pdf)

### Step-by-step guide for quashing a criminal complaint at the High Court:

The process of quashing a criminal complaint or FIR in the High Court typically involves the following steps:

1. **Grounds for Quashing**: Identify strong grounds for quashing the complaint. These are generally based on the principles laid down by the Supreme Court in cases like *State of Haryana v. Bhajan Lal*. Common grounds include:
 - The allegations in the FIR/complaint, even if taken at face value, do not constitute a cognizable offence.
 - The allegations are absurd and inherently improbable.
 - The criminal proceedings are maliciously instituted with an ulterior motive for wreaking vengeance.
 - There is an express legal bar to the institution or continuance of the proceedings.
 - The dispute is purely civil in nature, and a criminal color has been given to it.
 - Inordinate and unexplained delay in filing the FIR/complaint.
 - The case involves a matrimonial dispute where allegations are omnibus and general, especially against distant relatives.

2. **Drafting the Petition**: Prepare a Criminal Writ Petition or an application under Section 482 of the Cr.P.C. to the concerned High Court. The petition should clearly state:
 - The facts of the case.
 - The FIR/complaint details (number, date, police station, sections invoked).
 - The specific grounds for quashing, supported by legal arguments and relevant case laws.
 - A prayer for quashing the FIR/complaint and any consequential proceedings.

3. **Filing the Petition**: File the petition along with supporting documents (copy of FIR/complaint, charge sheet if filed, any relevant evidence) in the High Court.

4. **Interim Relief (Stay)**: In many cases, the petitioner may seek an interim stay on the investigation or further proceedings in the criminal case. If the High Court is convinced that a prima facie case for quashing exists, it may grant an interim stay.

5. **Arguments and Hearing**: The High Court will hear arguments from both sides (petitioner and respondent/State). The petitioner's counsel will argue why the complaint should be quashed based on the legal grounds and facts. The respondent/State will present their arguments against quashing.

6. **Decision by the High Court**: After hearing both sides, the High Court will deliver its judgment. If the High Court finds merit in the petition, it will quash the criminal complaint/FIR. If not, it will dismiss the petition, and the criminal proceedings will continue.

7. **Appeal to Supreme Court**: If the High Court dismisses the petition, the aggrieved party may file a Special Leave Petition (SLP) before the Supreme Court under Article 136 of the Constitution of India.

It is crucial to consult with a legal professional for specific advice and assistance in drafting and filing such petitions, as the facts and circumstances of each case are unique.

