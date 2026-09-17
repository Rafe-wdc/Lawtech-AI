# bhc_quash_smoke

**Endpoint:** `http://localhost:5000/pyapi/chat`  
**Thread ID:** `bea4bb6d-1e8c-498d-ac6e-a2546dd01de3`  
**Elapsed:** 56.86s  
**TTFB:** 894 ms  
**Answer length:** 32,107 chars (5,158 words)  
**Raw SSE events:** 144  

## Prompt

```
Complete details on quashing of criminal complaint at Bombay high court and step by step guide
```

## Event-type counts

| Event type | Count |
|---|---:|
| `token` | 100 |
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
|    1 |    0.89s | `thread_id` | data='bea4bb6d-1e8c-498d-ac6e-a2546dd01de3' |
|    2 |    0.90s | `progress` | step='validate' message='Validating query...' agent='guardrail' |
|    3 |    2.89s | `status` | message='Validating query...' agent='guardrail_input' |
|    4 |    2.89s | `progress` | step='language' message='Detecting language...' agent='memory' |
|    5 |    2.89s | `progress` | step='language' message='Detected: en' agent='memory' |
|    6 |    2.89s | `progress` | step='history' message='Loading conversation history...' agent='memory' |
|    7 |    2.92s | `progress` | step='history' message='Found 0 previous turns' agent='memory' |
|    8 |    2.93s | `progress` | step='rewrite' message='Rewriting follow-up query...' agent='memory' |
|    9 |    2.93s | `status` | message='Loading context...' agent='memory' |
|   10 |    2.94s | `context` |  |
|   11 |    2.94s | `progress` | step='classify' message='Understanding your question...' agent='orchestrator' |
|   12 |    6.71s | `progress` | step='classify' message='Identified: Judgment, Legislation' agent='orchestrator' |
|   13 |    6.71s | `progress` | step='plan' message='Preparing search queries for 3 agents...' agent='orchestrator' |
|   14 |    9.08s | `status` | message='Planning search strategy...' agent='orchestrator_plan' |
|   15 |    9.08s | `agents_planned` | agents=["Judgment", "Legislation", "SCI_Judgment"] |
|   16 |    9.10s | `progress` | step='metadata' message='Extracting case details from query...' agent='judgment' |
|   17 |    9.10s | `progress` | step='search' message='Searching court judgments (2 strategies in parallel)...' agent='judgment' |
|   18 |    9.10s | `progress` | step='parse' message='Parsing query for section references...' agent='legislation' |
|   19 |    9.10s | `progress` | step='parse' message='Detected sections: 482' agent='legislation' |
|   20 |    9.10s | `progress` | step='search' message='Searching legislation database...' agent='legislation' |
|   21 |    9.10s | `progress` | step='prepare' message='Preparing Supreme Court search...' agent='sci_judgment' |
|   22 |    9.10s | `progress` | step='react' message='Running multi-step research (ReAct agent)...' agent='sci_judgment' |
|   23 |   20.62s | `progress` | step='search' message='Found 5 judgments via case_type:quashing' agent='judgment' |
|   24 |   20.62s | `progress` | step='search' message='Top match: Prakash Piniomal Haswani vs State of Maharashtra' agent='judgment' |
|   25 |   20.62s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='judgment' |
|   26 |   20.62s | `progress` | step='search' message='Found 5 matching provisions' agent='legislation' |
|   27 |   20.62s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='legislation' |
|   28 |   23.23s | `progress` | step='search' message='Reading: High Court of Jharkhand Rules, 2001' agent='legislation' |
|   29 |   23.23s | `progress` | step='generate' message='Generating response...' agent='legislation' |
|   30 |   23.25s | `progress` | step='generate' message='Generating response with citations...' agent='judgment' |
|   79 |   29.95s | `status` | message='Searching legislation...' agent='legislation' |
|  132 |   40.62s | `status` | message='Searching court judgments...' agent='judgment' |
|  133 |   54.71s | `progress` | step='react' message='Completed 8 search steps' agent='sci_judgment' |
|  134 |   54.71s | `progress` | step='results' message='Found 5 Supreme Court judgments' agent='sci_judgment' |
|  135 |   54.71s | `progress` | step='generate' message='Generating response...' agent='sci_judgment' |
|  136 |   54.72s | `status` | message='Searching Supreme Court judgments...' agent='sci_judgment' |
|  137 |   54.74s | `progress` | step='synthesize' message='Merging results from 3 agents...' agent='orchestrator' |
|  138 |   54.74s | `status` | message='Injecting citations into draft...' agent='orchestrator_synthesize' |
|  139 |   54.74s | `progress` | step='finalize' message='Finalizing response...' agent='guardrail' |
|  140 |   54.74s | `status` | message='Finalizing...' agent='guardrail_output' |
|  141 |   55.97s | `response` |  |
|  142 |   55.97s | `sources` | data=[{"source_type": "judgment", "title": "Prakash Piniomal Haswani vs State of Maharashtra", "content": ["16-wp-2676-2014\nIN THE HIGH COURT OF JUDICATURE AT BOMBAY\nCRIMINAL APPELLATE JURISDICTION\nCRIMINAL WRIT PETITIO... |
|  143 |   56.83s | `followup_suggestions` | data=["What are the grounds for quashing?", "What documents are needed?", "How long does it take?"] |
|  144 |   56.86s | `done` |  |

## Final response

The Bombay High Court has the power to quash a criminal complaint under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, 1973. This power is not limited by the provisions of Section 320 of the Code of Criminal Procedure, which deals with the compounding of offences. The High Court can exercise this inherent power even in cases of non-compoundable offences, especially when the parties have amicably settled their disputes.

However, the Bombay High Court exercises this power with caution. For instance, in *Prakash Piniomal Haswani v. State of
Maharashtra*, the Court refused to quash a complaint under Section 498A of the Indian Penal Code, 1860, when the trial
had commenced and was at its final stage, and the petitioner had previously withdrawn an application for dropping the
proceedings. The Court emphasized that quashing should be in the best interest of the wife, family, and children, and
not merely based on consent, especially for offences against society at large. Conversely, in *Michelle M. Desai v.
Shirin R. Majai*, the Court quashed a complaint under Sections 324, 341, 342, 506 (Part 2), and 114 of the Indian Penal
Code, 1860, where the parties had settled the matter amicably, even though Section 506 (Part 2) is non-compoundable.
Similarly, in *Freddy Sunderdas Thomas v. State of Maharashtra*, the Court quashed a complaint under Sections 379, 409,
and 420 of the Indian Penal Code, 1860, where the dispute was settled and the complainant sought to withdraw the
complaint. In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra*, the Court quashed a complaint under Sections
465, 468, 471, and 420 of the Indian Penal Code, 1860, as the parties had amicably settled the personal dispute.

I think this information will help you more to understand the case:

### Key Legal Issues
- Whether the High Court can quash a criminal complaint under Article 226 of the Constitution of India and Section 482 of the Code of Criminal Procedure, 1973, even for non-compoundable offences, when parties have settled their disputes.
- The circumstances under which the High Court should exercise its inherent powers to quash a criminal complaint, particularly when a trial has commenced or is at an advanced stage.
- The relevance of an undertaking given by a complainant to consent to the quashing of a criminal complaint.
- The distinction between personal disputes and offences against society at large in the context of quashing criminal complaints.

### Detailed Narrative

**1. *Prakash Piniomal Haswani v. State of Maharashtra***
The petitioner sought to quash a criminal complaint registered with Trombay police station for offences under Sections
232, 420, 406, 497, 498A, 506(ii) read with 34 of the Indian Penal Code, 1860. The petitioner and respondent No. 2
(wife) were married and had filed for divorce. A decree of divorce was granted in 2004. During an appeal against the
divorce order, consent terms were filed, agreeing that Rs. 10,00,000/- would be paid to the wife as full and final
settlement, and she would give no objection for quashing the criminal complaint. The petitioner alleged that the wife
violated this undertaking by assisting the Public Prosecutor and giving evidence against him. The wife, appearing in
person, submitted that the petitioner had unconditionally withdrawn an application for dropping the proceedings based on
the consent terms, which caused her harassment. She claimed she was compelled by circumstances to accept the settlement.
She also stated that the case had proceeded for six years and was at its fag end.

**2. *Michelle M. Desai v. Shirin R. Majai***
The petitioner, the original complainant, lodged an FIR for offences under Sections 324, 341, 342, 506 (Part 2), and 114
of the Indian Penal Code, 1860, against her paternal aunt (respondent No. 1). After the charge was framed, two civil
suits between the parties were settled out of court by filing consent terms. An application to the Metropolitan
Magistrate for compounding the offence was refused because Section 506 (Part 2) is not compoundable under Section 320 of
the Code of Criminal Procedure, 1973. The petitioner then approached the High Court under Section 482 of the Code of
Criminal Procedure, 1973, to quash the criminal complaint.

**3. *Freddy Sunderdas Thomas v. State of Maharashtra***
The petitioner, original accused No. 1, had taken finance from respondent No. 2 (M/s. Ashok Leyland Finance Company) for
two trucks and defaulted on instalments. The company filed a complaint under Section 156(3) of the Code of Criminal
Procedure, 1973. Subsequently, the dispute was settled, and the amount due was paid by respondent No. 4, who purchased
the trucks. The complainant company then applied to the Magistrate to withdraw the complaint, stating the matter was
settled. However, the Magistrate dismissed the application and proceeded to order investigation under Section 156(3),
leading to the registration of an FIR and the petitioner's arrest. The petitioner sought quashing of the complaint.

**4. *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra***
The applicants filed an application for quashing a criminal complaint registered with Gorai Police Station for offences under Sections 465, 468, 471, and 420 of the Indian Penal Code, 1860. The parties had amicably settled the dispute, which was personal in nature.

**5. *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.***
The original complainant filed a petition for quashing a complaint he had filed against Dattatray Ramchandra Jadhav for
offences under Sections 387 and 326 of the Indian Penal Code, 1860. The petitioner claimed that a false complaint was
registered at his instance due to an interview given by the Superintendent of Police, and that he had never filed such a
complaint. The learned APP contended that several complaints were pending against the accused Dattatray Jadhav, who
appeared to be a history-sheeter, and there was no reason for the police to lodge a false case.

### As per the Court

**1. *Prakash Piniomal Haswani v. State of Maharashtra***
The Bombay High Court, Coram: V.M. Kanade & P.D. Kode JJ., dismissed the writ petition. The Court held that since the
petitioner had unconditionally withdrawn the application for dropping the proceedings based on consent terms, it was not
open for him to approach the Court under Article 226 of the Constitution of India read with Section 482 of the Code of
Criminal Procedure, 1973, for quashing the complaint, especially when the trial had commenced and reached its final
stage. The Court reiterated that when a trial commences, it must be concluded either in acquittal or conviction. Merely
because an undertaking was given by a party in a former proceeding to consent to quashing does not give the accused a
right to demand quashing. Relying on *B.S. Joshi v. State of Haryana, (2003) 4 SCC 675*, the Court noted that while the
High Court has inherent powers to quash, it must be satisfied that quashing is in the best interest of the wife, family,
and children. The Court emphasized that an offence under Section 498A of the Indian Penal Code, 1860, is not merely a
personal offence but is against society at large, and such complaints cannot be taken lightly and quashed, especially
when allegations of harassment are made. The Court also observed that the petitioner filed the petition six years after
the undertaking was given, and even a contempt petition would be time-barred under Section 20 of the Contempt of Court
Act, 1971.

**2. *Michelle M. Desai v. Shirin R. Majai***
The Bombay High Court, Coram: V.M. Kanade, J., allowed the criminal writ petition. The Court noted that the inherent
power of the High Court under Section 482 of the Code of Criminal Procedure, 1973, is not curtailed or limited by
Section 320 of the Code. Relying on *B.S. Joshi v. State of Haryana, JT 2003(3) SC 277*, the Court reiterated that the
High Court can quash criminal proceedings or FIR or complaint, and Section 320 does not limit or affect these powers.
Since the parties had settled the matter amicably and did not wish to proceed with the criminal litigation, the Court
found it a fit case for quashing the criminal complaint. The criminal case No. 551/P/1996 and FIR No. 271/1996 were
quashed and set aside.

**3. *Freddy Sunderdas Thomas v. State of Maharashtra***
The Bombay High Court, Coram: V.M. Kanade, J., allowed the writ petition. The Court held that the Magistrate clearly
erred in passing an order under Section 156(3) of the Code of Criminal Procedure, 1973, when the complainant had already
applied for withdrawal of the complaint, stating that the matter was settled. While a Magistrate may not have the power
to compound a non-compoundable offence under Section 320 of the Code of Criminal Procedure, 1973, the High Court's
inherent powers under Section 482 are not curtailed. Citing *B.S. Joshi and Ors. v. State of Haryana and Anr., 2003 Cri.
L.J. 2028*, the Court reaffirmed its power to quash criminal proceedings, FIR, or complaint. Considering the peculiar
facts and circumstances, the Court quashed the criminal complaint and FIR registered pursuant to the Magistrate's order.
The bonds executed by the parties were also cancelled.

**4. *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra***
The Bombay High Court, Coram: V.M. Kanade, J., allowed the application. The Court observed that since the parties had
amicably settled the dispute, and the dispute between them was a personal one, there was no impediment to quashing the
criminal complaint by exercising the inherent jurisdiction under Section 482 of the Code of Criminal Procedure, 1973.
The Court referred to the judgments of the Apex Court in *Madan Mohan Abbot v. State of Punjab, AIR 2008 SC 1969* and
*Jagdish Chanana v. State of Haryana, AIR 2008 SC 1968*.

**5. *Sagar Dnyandeo Patekar v. The Inspector General of Police and Ors.***
The Bombay High Court, Coram: V.M. Kanade & Smt. Anuja Prabhudessai, JJ., dismissed the writ petition. The Court noted
that several complaints were pending against the accused Dattatray Jadhav, who appeared to be a history-sheeter. The
Court was not inclined to quash the complaint, as there was a possibility of the petitioner being pressurized by the
accused to file the petition for quashing. The Court found no reason for the police to file a false complaint through
the petitioner or any other person and therefore declined to exercise its jurisdiction under Article 226 of the
Constitution of India or Section 482 of the Code of Criminal Procedure, 1973.

### Additional Observations

- The Bombay High Court consistently refers to the Supreme Court's decision in *B.S. Joshi v. State of Haryana* as the foundational precedent for exercising its inherent powers under Section 482 of the Code of Criminal Procedure, 1973, to quash criminal proceedings, even for non-compoundable offences, when there is an amicable settlement between the parties.
- The Court emphasizes that while it has broad powers, these powers are not to be exercised mechanically. Factors such as the stage of the trial, the nature of the offence (personal vs. against society), and the voluntariness of the settlement are crucial considerations.
- The Court also considers the conduct of the parties, such as the unconditional withdrawal of an application for dropping proceedings, as a factor against quashing.
- The Court is cautious when there is a possibility of coercion or pressure on the complainant, especially when the accused has a history of criminal activity.

### Step-by-Step Guide for Quashing a Criminal Complaint at Bombay High Court:

1. **Identify the Grounds for Quashing:**
 - **Amicable Settlement:** The most common ground is an amicable settlement between the parties, especially in cases arising out of matrimonial disputes or other personal disputes.
 - **Abuse of Process of Law:** If the complaint is frivolous, vexatious, or filed with an ulterior motive to harass the accused.
 - **No Prima Facie Case:** If the allegations in the FIR/complaint, even if taken at their face value, do not constitute a cognizable offence or disclose the commission of any offence.
 - **Legal Bar:** If there is an express legal bar to the institution and continuance of the proceedings.
 - **Lack of Jurisdiction:** If the court where the complaint is filed lacks jurisdiction.

2. **Prepare Consent Terms (if applicable):**
 - If the quashing is sought on the basis of an amicable settlement, the parties must draw up detailed consent terms outlining the terms of the settlement, including any financial arrangements, withdrawal of other cases, and the complainant's consent to quash the criminal proceedings.
 - These consent terms should be signed by both parties and their respective advocates.

3. **File a Criminal Writ Petition/Application:**
 - A Criminal Writ Petition under Article 226 of the Constitution of India read with Section 482 of the Code of Criminal Procedure, 1973, is typically filed before the Bombay High Court.
 - The petition should clearly state the facts of the case, the FIR/complaint details, the grounds for quashing, and the relief sought.
 - The consent terms (if any) should be annexed to the petition.
 - The complainant (respondent No. 2) should be made a party to the petition.

4. **Affidavits:**
 - The petitioner (accused) will file an affidavit in support of the petition.
 - In cases of settlement, the complainant (respondent No. 2) must also file an affidavit confirming the settlement and their consent to the quashing of the criminal complaint. This affidavit should explicitly state that the settlement is voluntary and without coercion.

5. **Appearance Before the High Court:**
 - Both the petitioner and the complainant (respondent No. 2) are usually required to appear before the High Court.
 - The Court will interact with the parties to ensure that the settlement is genuine and voluntary, especially from the complainant's side.
 - The Public Prosecutor representing the State will also be heard.

6. **Court's Consideration:**
 - The High Court will examine the nature of the offence. While it can quash non-compoundable offences, it exercises greater caution in serious offences or those affecting society at large (e.g., Section 498A IPC, as seen in *Prakash Piniomal Haswani*).
 - The Court will assess whether the quashing is in the interest of justice and the welfare of the parties involved, particularly the victim.
 - The stage of the trial is a significant factor. Quashing is less likely if the trial has progressed significantly or is at its final stage.
 - The Court will also consider the conduct of the parties, such as previous withdrawals of applications or any indication of coercion.

7. **Order of Quashing:**
 - If the High Court is satisfied, it will pass an order quashing the criminal complaint and the FIR.
 - The order will typically direct the cancellation of any bonds executed by the parties and the discharge of sureties.
 - A certified copy of the order should be submitted to the concerned trial court and police station for compliance.

---

## Statutory Provisions Referenced

The quashing of a criminal complaint under Section 482 of the Code of Criminal Procedure, 1973 (CrPC) — corresponding to Section 530 of the Bharatiya Nagarik Suraksha Sanhita, 2023 — at the High Court of Jharkhand involves specific procedural steps as outlined in the High Court of Jharkhand Rules, 2001.

### Quashing of Criminal Complaint at Bombay High Court

While the user query specifically mentions the Bombay High Court, the provided rules are from the High Court of Jharkhand. Therefore, the following information pertains to the procedure at the High Court of Jharkhand based on the provided rules. The general principles for quashing under Section 482 CrPC (corresponding to Section 530 BNSS, 2023) are similar across High Courts, but specific procedural rules may vary.

### Step-by-Step Guide for Quashing a Criminal Complaint (High Court of Jharkhand)

1. **Jurisdiction and Competent Bench:**
 - Applications for quashing, arising out of Section 482 CrPC (corresponding to Section 530 BNSS, 2023), are heard and disposed of by a Single Judge of the High Court. (Rule 35(1)(a) of the High Court of Jharkhand Rules, 2001).
 - As far as practicable, such applications shall be listed before the Zonal Judge in-charge of the Zone where the court from which the matter arises is situated. To facilitate this, the name of the district from where the matter has arisen must be mentioned in the cause title of the petition. (Rule 35(2) of the High Court of Jharkhand Rules, 2001).

2. **Contents of the Petition:**
 - The applicant must state whether an application on the same facts and against the same order or judgment had been previously filed before the High Court on behalf of any of the petitioners, and if so, with what result. (Rule 140(i) of the High Court of Jharkhand Rules, 2001).
 - While Rule 140(ii) specifically refers to Revision Petitions under Section 397 CrPC (corresponding to Section 427 BNSS, 2023), it is a good practice to disclose any similar applications made to lower courts, if applicable, to ensure full disclosure.

3. **Filing and Listing:**
 - Criminal Miscellaneous (Quashing) petitions, including motion slips, filed before 1:00 P.M. shall ordinarily be posted for admission or orders on the following day. (Rule 80 of the High Court of Jharkhand Rules, 2001).

4. **Grounds for Quashing:**
 - Section 482 CrPC (corresponding to Section 530 BNSS, 2023) preserves the inherent power of the High Court to make such orders as may be necessary to give effect to any order under the Code, or to prevent abuse of the process of any Court or otherwise to secure the ends of justice. Common grounds for quashing include:
 - The allegations in the FIR/complaint, even if taken at their face value, do not constitute any offence.
 - The allegations are absurd and inherently improbable.
 - There is an express legal bar to the institution and continuance of the proceedings.
 - The criminal proceeding is manifestly attended with mala fide and/or maliciously instituted with an ulterior motive.
 - The dispute is essentially civil in nature, given a criminal colour.

5. **Procedure during Hearing:**
 - The Single Judge will hear arguments from the petitioner's counsel.
 - Notice may be issued to the respondent (State and/or the complainant) to present their case.
 - The Court may examine the FIR, charge sheet, and other relevant documents to determine if a prima facie case is made out or if the proceedings constitute an abuse of process.

6. **Order of the Court:**
 - If the High Court is satisfied that the criminal complaint or proceedings are liable to be quashed, it will pass an order to that effect.
 - Any order passed by the Court under relevant sections of the CrPC (such as Sections 368, 415 or 416 CrPC, corresponding to Sections 398, 445, or 446 BNSS, 2023) shall be forwarded forthwith to the concerned court, and a copy sent to the Superintendent of Jail where the accused is lodged, if applicable. (Rule 147 of the High Court of Jharkhand Rules, 2001).

It is important to note that the power under Section 482 CrPC (corresponding to Section 530 BNSS, 2023) is to be exercised sparingly and with caution.

---

## Related Supreme Court Authority

The Supreme Court has laid down principles for quashing criminal complaints, primarily under Section 482 of the Code of Criminal Procedure, 1973. The power to quash should be exercised sparingly and only in exceptional cases where the allegations, even if taken at face value, do not constitute an offense or where the proceedings are a clear abuse of the process of the court.

Here are some landmark rulings from the Supreme Court regarding the quashing of criminal complaints:

#### *PROF. R.K. VIJAYASARATHY v. SUDHA SEETHARAM*, (2019) 2 SCC 240

This case, decided on 15-02-2019, by a bench of Hon'ble the Chief Justice and Hon'ble Mr. Justice Hemant Gupta, dealt with the quashing of a criminal complaint alleging offenses under Sections 405, 406, 415, and 420 read with Section 34 of the Indian Penal Code.

### Key Legal Issues
- Whether the High Court erred in rejecting the plea for quashing criminal proceedings.
- Whether the averments in the complaint disclose the ingredients necessary to constitute an offense under the Penal Code.
- The scope of inherent powers of the High Court under Section 482 of the Code of Criminal Procedure, 1973.

### Detailed Narrative
The case involved a dispute between the appellants (parents of the husband) and the first respondent (mother of the
wife). The first respondent filed a private complaint alleging criminal intimidation and dowry demand, and later another
complaint alleging offenses under Sections 405, 406, 415, and 420 read with Section 34 of the Penal Code. This second
complaint stemmed from a transfer of Rs. 20 lakhs by the appellants' son to the first respondent's bank account, which
the first respondent claimed was returned in cash to the appellants. The appellants' son had also filed a civil suit for
recovery of this money. The High Court had rejected the appellants' prayer to quash the criminal proceedings but stayed
them until the disposal of the civil suit.

### Court Observations
The Supreme Court, referring to its earlier decision in *Indian Oil Corpn. v. NEPC India Ltd.*, (2006) 6 SCC 736, reiterated the guiding principles for exercising jurisdiction under Section 482 CrPC:
- A complaint can be quashed if the allegations, taken at face value, do not prima facie constitute any offense.
- A complaint may be quashed if it is a clear abuse of the process of the court, initiated with mala fides or malice.
- The power to quash should be used sparingly and with caution.
- The complaint is not required to reproduce verbatim the legal ingredients, but must lay the necessary factual foundation.

The Court analyzed the ingredients of Sections 405 (criminal breach of trust), 415 (cheating), and 420 (cheating and
dishonestly inducing delivery of property) of the Penal Code. It found that the complaint did not disclose entrustment
of property to the appellants, which is essential for criminal breach of trust. Similarly, there was no dishonest
inducement by the appellants to the first respondent to deliver any property, which is crucial for cheating. The Court
noted that the money belonged to the son of the appellants and was transferred to the first respondent by his own
volition, and allegedly returned on his instructions.

The Court also referred to *Binod Kumar v. State of Bihar*, (2014) 10 SCC 663, where it was held that if the basic essential ingredients of dishonest misappropriation and cheating are missing, criminal proceedings are liable to be quashed. It emphasized that criminal proceedings are not a shortcut for other remedies.

### Judgment & Direction
The Supreme Court allowed the appeal, setting aside the High Court's judgment and quashing the criminal proceedings arising from PCR 2116 of 2016 against the appellants. The Court clarified that no opinion was expressed on the merits of the pending civil suit for recovery of money, which should be disposed of in accordance with law. The Court concluded that the complaint was an attempt to cloak a civil dispute with a criminal nature, constituting an abuse of the process of the court.

### Broader Significance
This judgment reaffirms the principle that the High Court's inherent powers under Section 482 CrPC should be exercised to prevent the abuse of the process of the court, especially when a civil dispute is given a criminal color without the necessary ingredients of a criminal offense being made out.

#### *SUMIT BANSAL v. M/S MGI DEVELOPERS AND PROMOTERS*, 2026 INSC 40

This case, decided on 08-01-2026, by a bench of Hon'ble Mr. Justice Prashant Kumar Mishra and Hon'ble Mr. Justice N.V. Anjaria, dealt with the quashing of criminal complaints under Section 138 of the Negotiable Instruments Act, 1881.

### Key Legal Issues
- Whether the High Court was correct in quashing a complaint under Section 138 of the NI Act on the ground of multiplicity of complaints for the same underlying liability.
- Whether the High Court erred in not quashing other criminal proceedings against the respondent.
- The scope of inherent jurisdiction of the High Court under Section 482 of the Code of Criminal Procedure, 1973, in cases involving Section 138 of the NI Act.

### Detailed Narrative
The case involved multiple complaints filed under Section 138 of the Negotiable Instruments Act, 1881, arising from an
Agreement to Sell for commercial units. The complainant had paid the full sale consideration, but the vendor failed to
execute the sale deeds. Consequently, the vendor (Respondent No. 1) and its proprietor (Respondent No. 2) issued several
cheques, both from the firm's account and personally, to refund the amount and appreciation. When these cheques were
dishonoured, the complainant filed multiple complaints. The High Court quashed one complaint (Complaint Case No. 3298 of
2019) on the ground that it related to the same underlying liability for which another complaint (Complaint Case No.
2823 of 2019) had already been instituted, deeming it a parallel prosecution. However, the High Court refused to quash
other complaints, noting they arose from distinct causes of action.

### Court Observations
The Supreme Court referred to *State of Haryana and Others v. Bhajan Lal and Others*, 1992 Supp (1) SCC 335, which
outlined categories of cases where the power under Section 482 CrPC could be exercised. It also cited *Neeharika
Infrastructure Private Limited v. State of Maharashtra and Others*, (2021) 19 SCC 401, emphasizing that the power to
quash criminal proceedings must be exercised sparingly and only where the complaint discloses no offense or continuation
would amount to abuse of the process of law. The Court reiterated that the High Court should not usurp the function of a
Trial Court or conduct a mini-trial when disputed factual questions are involved.

Regarding the quashing of Complaint Case No. 3298 of 2019, the Supreme Court disagreed with the High Court's reasoning.
It held that under Section 138 of the NI Act, a separate cause of action arises upon each dishonour of a cheque,
provided the statutory sequence of presentation, dishonour, notice, and failure to pay is complete. The fact that
multiple cheques arise from one transaction does not merge them into a single cause of action. The Court stated that
whether the cheques were alternative or supplementary instruments, or represented fresh undertakings, are disputed
questions of fact requiring evidence at trial and cannot be resolved under Section 482 CrPC.

The Court also referred to *Kusum Ingots & Alloys Ltd. v. Pennar Peterson Securities Ltd. and Others*, (2000) 2 SCC 745, which highlighted the ingredients for making out a case under Section 138 of the NI Act. It further cited *M.M.T.C. Ltd. and Another v. Medchl Chemicals and Pharma (P) Ltd. and Another*, (2002) 1 SCC 234, to emphasize that the burden of proving that there was no existing debt or liability is on the accused, and this must be discharged in trial.

### Judgment & Direction
The Supreme Court allowed the appeal filed by the complainant, setting aside the High Court's judgment that quashed
Complaint Case No. 3298 of 2019. This complaint was restored for trial. The appeals filed by Respondent No. 2
challenging the High Court's refusal to quash other complaints (Complaint Case No. 2823 of 2019, Complaint Case No.
13508 of 2019, and Complaint Case No. 743 of 2020) were dismissed. The Court held that these complaints prima facie
disclosed the ingredients of an offense under Section 138 of the NI Act and must proceed to trial. All contentions of
the parties were left open to be decided by the Trial Court on its own merits.

### Broader Significance
This judgment clarifies that in cases under Section 138 of the Negotiable Instruments Act, 1881, multiple complaints arising from distinct dishonours of cheques, even if related to the same underlying transaction, can be maintained. It reinforces the principle that the High Court should not conduct a mini-trial or delve into disputed questions of fact while exercising its inherent powers under Section 482 CrPC for quashing criminal complaints.

I think this information will help you more to understand the case:

### Step-by-step guide for quashing a criminal complaint at the High Court (under Section 482 CrPC):

1. **Identify Grounds for Quashing**:
 - **No prima facie case**: The allegations in the complaint, even if taken at face value, do not disclose the commission of any cognizable offense.
 - **Abuse of process of court**: The criminal proceeding is initiated with mala fides, malice, or for wreaking vengeance, or to cause harm, or where the allegations are absurd and inherently improbable.
 - **Legal bar**: There is an express legal bar to the institution and continuance of the proceedings (e.g., limitation, lack of sanction).
 - **Civil nature**: The dispute is essentially of a civil nature, and a criminal color has been given to it without the necessary ingredients of a criminal offense.
 - **Multiplicity of proceedings**: Where multiple complaints are filed for the same cause of action, leading to parallel prosecution.

2. **Prepare the Petition**:
 - Draft a Criminal Miscellaneous Petition (Crl.M.C.) under Section 482 of the Code of Criminal Procedure, 1973, to the concerned High Court.
 - Clearly state the facts of the case, including the FIR/complaint details, the offenses alleged, and the stage of the proceedings.
 - Articulate the specific grounds for quashing, supported by legal arguments and relevant Supreme Court judgments.
 - Attach all necessary documents, such as the FIR/complaint, charge sheet (if filed), and any other relevant evidence.

3. **File the Petition**:
 - File the petition in the High Court registry.
 - Ensure proper court fees and stamp duty are paid.
 - Serve a copy of the petition on the respondent(s) (complainant and/or State).

4. **Arguments and Hearing**:
 - During the hearing, the petitioner's counsel will argue that the continuation of the criminal proceedings would be an abuse of the process of law or that no prima facie case is made out.
 - The respondent's counsel will present arguments against quashing.
 - The High Court will examine the allegations in the complaint/FIR and the accompanying material to determine if the ingredients of the alleged offenses are disclosed. The Court will generally not embark on a detailed inquiry into the reliability or genuineness of the allegations at this stage.

5. **High Court's Decision**:
 - If the High Court is satisfied that the grounds for quashing are met, it may quash the FIR, complaint, or the entire criminal proceedings.
 - The High Court may also pass interim orders, such as staying the investigation or directing no coercive steps to be taken, during the pendency of the quashing petition.
 - If the High Court finds that a prima facie case is made out or that the matter requires a trial, it will dismiss the petition, allowing the criminal proceedings to continue.

6. **Appeal to Supreme Court (if necessary)**:
 - If the High Court dismisses the quashing petition, the aggrieved party may file a Special Leave Petition (Criminal) under Article 136 of the Constitution of India before the Supreme Court.

**PDF Links:**
- *PROF. R.K. VIJAYASARATHY v. SUDHA SEETHARAM*, 2019 INSC 216: [https://api.sci.gov.in/supremecourt/2018/39319/39319_2018_Judgement_15-Feb-2019.pdf](https://api.sci.gov.in/supremecourt/2018/39319/39319_2018_Judgement_15-Feb-2019.pdf)
- *SUMIT BANSAL v. M/S MGI DEVELOPERS AND PROMOTERS*, 2026 INSC 40: [https://api.sci.gov.in/supremecourt/2025/36370/36370_2025_17_1502_67236_Judgement_08-Jan-2026.pdf](https://api.sci.gov.in/supremecourt/2025/36370/36370_2025_17_1502_67236_Judgement_08-Jan-2026.pdf)

