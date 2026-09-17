# bhc_quash_smoke_tool_lawttorney

**Endpoint:** `https://tool.lawttorney.com/pyapiv2/chat`  
**Thread ID:** `dc2feeb1-14bc-4a45-98c4-4226dd25589f`  
**Elapsed:** 30.95s  
**TTFB:** 766 ms  
**Answer length:** 29,601 chars (4,658 words)  
**Raw SSE events:** 419  

## Prompt

```
Complete details on quashing of criminal complaint at Bombay high court and step by step guide
```

## Event-type counts

| Event type | Count |
|---|---:|
| `token` | 376 |
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
|    1 |    0.77s | `thread_id` | data='dc2feeb1-14bc-4a45-98c4-4226dd25589f' |
|    2 |    0.77s | `progress` | step='validate' message='Validating query...' agent='guardrail' |
|    3 |    0.79s | `status` | message='Validating query...' agent='guardrail_input' |
|    4 |    0.79s | `progress` | step='language' message='Detecting language...' agent='memory' |
|    5 |    0.79s | `progress` | step='language' message='Detected: en' agent='memory' |
|    6 |    0.79s | `progress` | step='history' message='Loading conversation history...' agent='memory' |
|    7 |    0.79s | `progress` | step='history' message='Found 0 previous turns' agent='memory' |
|    8 |    0.79s | `progress` | step='rewrite' message='Rewriting follow-up query...' agent='memory' |
|    9 |    0.79s | `status` | message='Loading context...' agent='memory' |
|   10 |    0.81s | `context` |  |
|   11 |    0.81s | `progress` | step='classify' message='Understanding your question...' agent='orchestrator' |
|   12 |    2.41s | `progress` | step='classify' message='Identified: Judgment, Legislation' agent='orchestrator' |
|   13 |    2.41s | `progress` | step='plan' message='Preparing search queries for 3 agents...' agent='orchestrator' |
|   14 |    3.23s | `status` | message='Planning search strategy...' agent='orchestrator_plan' |
|   15 |    3.24s | `agents_planned` | agents=["Judgment", "Legislation", "SCI_Judgment"] |
|   16 |    3.24s | `progress` | step='metadata' message='Extracting case details from query...' agent='judgment' |
|   17 |    3.24s | `progress` | step='search' message='Searching court judgments (2 strategies in parallel)...' agent='judgment' |
|   18 |    3.24s | `progress` | step='parse' message='Parsing query for section references...' agent='legislation' |
|   19 |    3.24s | `progress` | step='parse' message='Detected sections: 482' agent='legislation' |
|   20 |    3.24s | `progress` | step='search' message='Searching legislation database...' agent='legislation' |
|   21 |    3.24s | `progress` | step='prepare' message='Preparing Supreme Court search...' agent='sci_judgment' |
|   22 |    3.24s | `progress` | step='react' message='Running multi-step research (ReAct agent)...' agent='sci_judgment' |
|   23 |    4.14s | `progress` | step='search' message='Found 5 judgments via case_type:quashing' agent='judgment' |
|   24 |    4.14s | `progress` | step='search' message='Top match: Prakash Piniomal Haswani vs State of Maharashtra' agent='judgment' |
|   25 |    4.14s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='judgment' |
|   26 |    4.57s | `progress` | step='search' message='Found 5 matching provisions' agent='legislation' |
|   27 |    4.57s | `progress` | step='relevance_check' message='Verifying retrieval relevance...' agent='legislation' |
|   28 |    5.65s | `progress` | step='fallback' message="Retrieved provisions don't match the query — searching the web..." agent='legislation' |
|   29 |    5.66s | `progress` | step='generate' message='Generating response with citations...' agent='judgment' |
|   80 |   17.07s | `status` | message='Searching court judgments...' agent='judgment' |
|  407 |   22.26s | `status` | message='Searching legislation...' agent='legislation' |
|  408 |   30.26s | `progress` | step='react' message='Completed 9 search steps' agent='sci_judgment' |
|  409 |   30.26s | `progress` | step='results' message='Found 5 Supreme Court judgments' agent='sci_judgment' |
|  410 |   30.26s | `progress` | step='generate' message='Generating response...' agent='sci_judgment' |
|  411 |   30.26s | `status` | message='Searching Supreme Court judgments...' agent='sci_judgment' |
|  412 |   30.26s | `progress` | step='synthesize' message='Merging results from 3 agents...' agent='orchestrator' |
|  413 |   30.26s | `status` | message='Injecting citations into draft...' agent='orchestrator_synthesize' |
|  414 |   30.26s | `progress` | step='finalize' message='Finalizing response...' agent='guardrail' |
|  415 |   30.26s | `status` | message='Finalizing...' agent='guardrail_output' |
|  416 |   30.27s | `response` |  |
|  417 |   30.27s | `sources` | data=[{"source_type": "judgment", "title": "Prakash Piniomal Haswani vs State of Maharashtra", "content": ["16-wp-2676-2014\nIN THE HIGH COURT OF JUDICATURE AT BOMBAY\nCRIMINAL APPELLATE JURISDICTION\nCRIMINAL WRIT PETITIO... |
|  418 |   30.95s | `followup_suggestions` | data=["What are grounds for quashing?", "What documents are needed?", "What is the typical timeline?"] |
|  419 |   30.95s | `done` |  |

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
- The circumstances under which the High Court should exercise its inherent powers to quash a criminal complaint, particularly when the trial has commenced or is at an advanced stage.
- The relevance of an undertaking given by a complainant to consent to the quashing of a criminal complaint.
- The distinction between personal disputes and offences against society at large in the context of quashing criminal complaints.

### Detailed Narrative

In *Prakash Piniomal Haswani v. State of Maharashtra*, the petitioner sought to quash a criminal complaint registered
under Sections 232, 420, 406, 497, 498A, 506(ii) read with Section 34 of the Indian Penal Code, 1860, following a
divorce decree and consent terms in an appeal where the wife agreed to a full and final settlement of Rs. 10,00,000/-
and undertook to give no objection for quashing the criminal complaint. The petitioner alleged that the wife violated
this undertaking by assisting the prosecution. The wife, appearing in person, contended that the petitioner had
unconditionally withdrawn an application for dropping the proceedings based on the consent terms, causing her
harassment. She also claimed she was compelled by circumstances to accept the settlement. The trial had proceeded for
six years and was at its fag end.

In *Michelle M. Desai v. Shirin R. Majai*, the petitioner, the original complainant, sought to quash an FIR for offences under Sections 324, 341, 342, 506 (Part 2), and 114 of the Indian Penal Code, 1860, against her paternal aunt. The parties had settled two civil suits out of court by filing consent terms. The Metropolitan Magistrate refused to compound the offence under Section 506 (Part 2) as it was non-compoundable under Section 320 of the Code of Criminal Procedure, 1973.

In *Freddy Sunderdas Thomas v. State of Maharashtra*, the petitioner, an accused, sought to quash a criminal complaint
under Sections 379, 409, and 420 of the Indian Penal Code, 1860. The complaint was filed by M/s. Ashok Leyland Finance
Company after the petitioner defaulted on loan instalments for two trucks. An arbitrator was appointed, and the dispute
was settled, with the outstanding amount paid by a third party (Respondent No. 4) who purchased the trucks. The
complainant company then applied to the Magistrate to withdraw the complaint, but the Magistrate dismissed the
application and instead passed an order under Section 156(3) of the Code of Criminal Procedure, 1973, leading to the
registration of an FIR and the petitioner's arrest.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police*, the original complainant filed a petition to quash a complaint he had filed against Dattatray Ramchandra Jadhav for offences under Sections 387 and 326 of the Indian Penal Code, 1860. The petitioner claimed the complaint was false and registered at the instance of the police. The learned APP submitted that several complaints were pending against the accused, who appeared to be a history-sheeter.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra*, the applicants sought to quash a criminal complaint registered under Sections 465, 468, 471, and 420 of the Indian Penal Code, 1860. The parties had amicably settled the dispute, which was personal in nature.

### As per the Court

In *Prakash Piniomal Haswani v. State of Maharashtra*, the Bombay High Court dismissed the petition, holding that the
petitioner, having unconditionally withdrawn the application for dropping the proceedings, could not approach the Court
under Article 226 of the Constitution of India read with Section 482 of the Code of Criminal Procedure, 1973, especially
when the trial had commenced and reached its final stage. The Court reiterated that once a trial commences, it must
conclude in either acquittal or conviction. The Bombay High Court emphasized that an undertaking to consent to quashing
in a former proceeding does not automatically entitle the accused to have the complaint quashed. Relying on *B.S. Joshi
v. State of Haryana, 2003(4) SCC 675*, the Court noted that while it has inherent powers to quash complaints, it must be
satisfied that quashing is in the best interest of the wife, family, and children. The Bombay High Court observed that
an offence under Section 498A of the Indian Penal Code, 1860, is not merely a personal offence but is against society at
large, and therefore, such complaints cannot be taken lightly and quashed solely on consent. The Court also considered
the harassment faced by wives with no source of income who are compelled to appear in court and sometimes give consent
under duress. The delay of six years in filing the petition after the undertaking was also a factor, noting that a
contempt petition would be time-barred under Section 20 of the Contempt of Courts Act, 1971.

In *Michelle M. Desai v. Shirin R. Majai*, the Bombay High Court allowed the petition, quashing the criminal complaint
and FIR. The Court relied on *B.S. Joshi v. State of Haryana, JT 2003(3) SC 277*, which held that the High Court's
inherent powers under Section 482 of the Code of Criminal Procedure, 1973, are not limited by Section 320 of the Code.
Since the parties had amicably settled the matter and did not wish to proceed with the criminal litigation, the Bombay
High Court found it a fit case for quashing the complaint.

In *Freddy Sunderdas Thomas v. State of Maharashtra*, the Bombay High Court allowed the writ petition, quashing the
criminal complaint and FIR. The Court found that the Magistrate clearly erred in passing an order under Section 156(3)
of the Code of Criminal Procedure, 1973, when the complainant had already sought to withdraw the complaint due to a
settlement. The Bombay High Court reiterated that while a Magistrate may not have the power to compound a
non-compoundable offence under Section 320 of the Code of Criminal Procedure, 1973, the High Court's inherent powers
under Section 482 of the Code are not curtailed. The Bombay High Court also relied on *B.S. Joshi and Ors. v. State of
Haryana and Anr., 2003 Cri. L.J. 2028*. The bonds executed by the parties were also cancelled.

In *Sagar Dnyandeo Patekar v. The Inspector General of Police*, the Bombay High Court dismissed the writ petition,
refusing to quash the complaint. The Court noted that the accused, Dattatray Jadhav, appeared to be a history-sheeter
with numerous pending cases. The Bombay High Court expressed concern about the possibility of the petitioner being
pressurized by the accused to file the petition for quashing the complaint. The Court found no reason for the police to
file a false complaint through the petitioner.

In *Mrs. Dale Herman Nazareth & Ors. v. State of Maharashtra*, the Bombay High Court allowed the application, quashing
the criminal complaint. The Court noted that the parties had amicably settled the dispute, which was personal in nature.
The Bombay High Court referred to the judgments of the Apex Court in *Madan Mohan Abbot v. State of Punjab, AIR 2008 SC
1969* and *Jagdish Chanana v. State of Haryana, AIR 2008 SC 1968*, which support the exercise of inherent jurisdiction
under Section 482 of the Code of Criminal Procedure, 1973, in such cases.

### Additional Observations

The judgments highlight the discretionary nature of the High Court's power to quash criminal complaints. While the power
under Section 482 of the Code of Criminal Procedure, 1973, is broad and not limited by Section 320, its exercise depends
on the specific facts and circumstances of each case. Factors such as the stage of the trial, the nature of the offence
(personal vs. societal), the conduct of the parties (e.g., withdrawal of applications, delay), and whether the
settlement is truly voluntary and in the best interest of all parties, are crucial considerations for the Bombay High
Court. The Court is particularly cautious when quashing complaints involving serious offences or where there is a
possibility of coercion or harassment.

---

## Statutory Provisions Referenced

The quashing of a criminal complaint in India is primarily governed by Section 482 of the Code of Criminal Procedure, 1973 (CrPC). This provision grants inherent powers to the High Courts to make orders necessary to give effect to any order under the Code, or to prevent abuse of the process of any Court, or otherwise to secure the ends of justice.

With the enactment of the Bharatiya Nagarik Suraksha Sanhita (BNSS), 2023, the corresponding provision for the inherent powers of the High Court to quash criminal proceedings is Section 528 of the BNSS, 2023. It is important to note that Section 482 of the Bharatiya Nagarik Suraksha Sanhita, 2023, now pertains to anticipatory bail, replacing Section 438 of the Code of Criminal Procedure, 1973.

### Grounds for Quashing a Criminal Complaint

The High Court, including the Bombay High Court, exercises its inherent powers under Section 482 of the Code of Criminal Procedure, 1973 (corresponding to Section 528 of the Bharatiya Nagarik Suraksha Sanhita, 2023) sparingly and with circumspection. The Supreme Court has laid down various guidelines for the exercise of this power. Some of the key grounds on which a criminal complaint or First Information Report (FIR) may be quashed include:

- **No Cognizable Offence Disclosed**: Where the allegations made in the FIR or complaint, even if taken at face value and accepted in their entirety, do not prima facie constitute any cognizable offence, justifying an investigation by police officers.
- **Abuse of Process of Court**: To prevent abuse of the process of any Court. This includes situations where criminal proceedings are maliciously instituted with an ulterior motive for wreaking vengeance or on account of personal grudge.
- **Securing Ends of Justice**: To otherwise secure the ends of justice.
- **Legal Bar**: Where there is an express legal bar against the institution or continuance of the criminal proceedings.
- **Absence of Sanction**: Where the allegations against the accused constitute an offence but no legal evidence is presented in support of the case, or where there is an absence of requisite sanction.
- **Absurd or Improbable Allegations**: Where the allegations made in the FIR or complaint are so absurd and inherently improbable that no prudent person can reach a just conclusion that there exist sufficient grounds for proceeding against the accused.
- **Civil Dispute with Criminal Colour**: Where a criminal proceeding is manifestly attended with mala fide intention and/or where the proceeding is maliciously instituted with an ulterior motive for wreaking vengeance on the accused and with a view to spite him due to private and personal grudge. This often occurs when a civil dispute is given a criminal colour.
- **Compromise in Certain Offences**: In cases of non-compoundable offences, particularly those arising out of
matrimonial disputes or other disputes of a predominantly civil nature where the parties have genuinely settled their
differences, the High Court may quash the proceedings. The Bombay High Court has also exercised this power
post-conviction when an appeal is pending, especially in matrimonial disputes where parties have settled. However, this
power is not to be exercised in heinous and serious offences involving mental depravity or offences such as murder,
rape, and dacoity, even if the victim or their family has settled the dispute.
- **Multiple FIRs for Same Offence**: If multiple FIRs of the same offence have been lodged against the accused.
- **Inordinate Delay**: If there is an inordinate and unexplained delay in the institution of criminal proceedings, which infringes the accused's right to a speedy trial.

### Step-by-Step Guide for Quashing a Criminal Complaint at the Bombay High Court

The process for filing a quash petition under Section 482 of the Code of Criminal Procedure, 1973 (or Section 528 of the Bharatiya Nagarik Suraksha Sanhita, 2023) before the Bombay High Court typically involves the following steps:

1. **Obtain FIR/Complaint Copy**: Obtain a certified copy of the FIR or the criminal complaint, along with any other relevant documents such as the charge sheet (if filed).
2. **Consult a Lawyer**: Engage a lawyer experienced in criminal law, particularly with experience in High Court practice. The lawyer will assess the merits of the case and determine if there are sufficient grounds for quashing.
3. **Draft the Quash Petition**: The lawyer will draft a quash petition (also known as a criminal application or criminal writ petition) detailing the facts of the case, the legal grounds for quashing, and citing relevant judgments of the Supreme Court and the High Courts. The petition will clearly state why the continuation of the criminal proceedings would be an abuse of the process of law or would not secure the ends of justice.
4. **Prepare Supporting Affidavit**: A supporting affidavit outlining the facts and affirming the statements made in the petition must be prepared and sworn by the petitioner.
5. **Assemble Documents**: Attach all supporting documents, including the FIR/complaint, charge sheet, and any other evidence that substantiates the grounds for quashing.
6. **File the Petition**: The petition, along with the affidavit and annexures, is then filed in the Registry of the Bombay High Court. The requisite court fees and procedural formalities must be satisfied.
7. **Mentioning and Listing**: After filing, the matter is usually mentioned before a bench for admission and interim relief, if any. The court may issue notice to the State and the complainant.
8. **Arguments and Hearing**: During the hearing, both parties (the petitioner and the State/complainant) will present their arguments. The High Court will scrutinize the allegations and evidence to determine if a prima facie case is made out or if the proceedings are an abuse of process.
9. **Court Order**: After hearing both sides, the High Court will pass an order. If the court is satisfied that the grounds for quashing are met, it may quash the FIR, charge sheet, or the entire criminal proceedings. If not satisfied, the petition may be dismissed.

The High Court, in exercising its powers, does not function as a court of appeal or revision, and it generally does not embark upon an inquiry to ascertain the reliability of the evidence. The focus is on whether the uncontroverted allegations disclose a cognizable offence or whether the proceedings constitute an abuse of process.

---

## Related Supreme Court Authority

The Supreme Court has delivered several landmark rulings on the quashing of criminal complaints, primarily under Section 482 of the Code of Criminal Procedure, 1973 (Cr.P.C.). These judgments outline the principles and circumstances under which High Courts can exercise their inherent powers to prevent abuse of the process of any court or otherwise to secure the ends of justice.

I think this information will help you more to understand the case:

### Case: *Chanchalpati Das v. The State of West Bengal*, Criminal Appeal No. 1592 of 2023 (DB ID: 42738)

#### Key Legal Issues
- Whether an inordinate and unexplained delay in filing a criminal complaint can be a ground for quashing the proceedings.
- Whether the allegations in the complaint and charge-sheet, even if taken at face value, disclose a prima facie case for the alleged offenses.
- The scope of inherent powers of the High Court under Section 482 Cr.P.C. to quash criminal proceedings.

#### Detailed Narrative
The case involved two appellants, Madhu Pandit Das (President of ISKCON, Bengaluru) and Chanchalpati Das (Vice President
of ISKCON, Bengaluru), who were accused of offenses under Sections 468, 471, 406, and 120-B of the Indian Penal Code
(IPC). The complaint, filed in 2009, alleged the theft of a bus belonging to ISKCON, Kolkata, which purportedly occurred
in 2001. The complainant claimed that a report was made to the police in 2002 and a letter was sent in 2006, but no
action was taken. Subsequently, a private complaint was filed in 2009, leading to the registration of an FIR and a
charge-sheet. The appellants sought to quash these proceedings before the High Court, which dismissed their
applications.

#### Court Observations
The Supreme Court, relying on its landmark decision in *State of Haryana v. Bhajan Lal*, (1992) Supp. (1) SCC 335, reiterated the guidelines for exercising powers of quashing under Section 482 Cr.P.C. The Court emphasized that these powers should be exercised sparingly and with caution, but must be invoked if a clear case of abuse of the process of law is made out.

The Court found that there was an inordinate and unexplained delay of eight years in filing the complaint regarding an alleged incident that occurred in 2001. It observed that if the bus was so precious, the complainant would not have remained silent for such a long period. The Court held that such an inordinate delay itself would be a sufficient ground to quash the proceedings, as it amounted to a sheer misuse and abuse of the process of the court.

Furthermore, the Court noted that a bare perusal of the complaint and charge-sheet revealed bald allegations without any material or document to substantiate the claims against the appellants. Even after investigation, no evidence was collected to prima facie satisfy the ingredients of the alleged offenses. The Court concluded that the allegations were so absurd and improbable that no prudent person could reach a conclusion that there was sufficient ground for proceeding against the appellants.

The Court also highlighted the issue of frivolous litigation, stating that "bad cases drive out good cases from being heard on time" and that parties initiating such litigation should be saddled with exemplary costs.

#### Judgment & Direction
The Supreme Court allowed the appeals, quashed the criminal proceedings pending against the appellants in the Court of Chief Judicial Magistrate, Alipore, arising out of FIR No. 33 of 2009, and imposed a cost of Rs. 1,00,000/- on the respondent-complainant, to be deposited in the office of the Supreme Court Advocates-on-Record Association.

### Case: *Kahkashan Kausar @ Sonam v. The State of Bihar*, Criminal Appeal No. 195 of 2022 (DB ID: 41136)

#### Key Legal Issues
- Whether general and omnibus allegations against in-laws in a matrimonial dispute under Section 498A IPC are liable to be quashed.
- The misuse of Section 498A IPC and the increased tendency of implicating relatives of the husband without specific allegations.
- The role of courts in preventing abuse of the process of law in matrimonial disputes.

#### Detailed Narrative
This case involved an appeal against a Patna High Court order that dismissed a criminal writ petition seeking to quash
an FIR filed under Sections 341, 323, 379, 354, 498A read with Section 34 IPC against the husband and his in-laws. The
complainant wife alleged dowry demand and harassment. Previously, a similar complaint was filed in 2017, where the
Magistrate found no prima facie case against the in-laws due to omnibus allegations. The present FIR, filed in 2019,
alleged fresh instances of harassment and dowry demand.

#### Court Observations
The Supreme Court expressed concern over the misuse of Section 498A IPC and the tendency to implicate relatives of the husband in matrimonial disputes without specific allegations. The Court referred to its previous landmark judgments in *Arnesh Kumar v. State of Bihar*, (2014) 8 SCC 273, and *Preeti Gupta v. State of Jharkhand*, (2010) 7 SCC 667, which highlighted the phenomenal increase in matrimonial disputes and the use of Section 498A as a weapon rather than a shield.

The Court observed that general and omnibus allegations were leveled against the appellants (in-laws), without
attributing any specific role to each of them in furtherance of the alleged offenses. It held that allowing prosecution
in the absence of clear allegations against the in-laws would amount to an abuse of the process of law. The Court
reiterated that a criminal trial, even if it leads to an eventual acquittal, inflicts severe scars upon the accused, and
such an exercise must be discouraged when based on general allegations.

#### Judgment & Direction
The Supreme Court set aside the impugned order of the Patna High Court and quashed the FIR against the appellants (in-laws) under Sections 341, 323, 379, 354, 498A read with Section 34 IPC. The appeal was allowed.

### Case: *Dinesh Kumar Mathur v. The State of Madhya Pradesh*, Criminal Appeal No. 4915 of 2024 (DB ID: 44332)

#### Key Legal Issues
- Whether a criminal complaint should be quashed if the dispute is essentially civil in nature.
- The applicability of protection under Section 83 of the Madhya Pradesh Housing Board Act, 1972, akin to Section 197 Cr.P.C., for actions taken by a public servant in discharge of official duty.
- The requirement of *mens rea* for offenses like cheating and criminal conspiracy.

#### Detailed Narrative
The appellant, an official of the Housing Board, was accused of conniving with others to forge a Power of Attorney and
register a property in the name of a co-accused. An FIR was lodged, and a charge-sheet was filed under Sections 419,
420, 467, 468, 471, and 120B read with Section 34 IPC. The appellant sought to quash the FIR and subsequent proceedings,
arguing that the dispute was civil in nature, his actions were in connection with official duty, and there was no *mens
rea*. A civil suit related to the property had also been dismissed.

#### Court Observations
The Supreme Court considered the application of Section 197 Cr.P.C. and Section 83 of the Madhya Pradesh Housing Board
Act, 1972, which provides protection for actions taken in good faith in pursuance of the Act. The Court noted that the
protection under Section 197(1) Cr.P.C. is available to public servants for offenses committed while acting or
purporting to act in the discharge of their official duty, provided there is a direct and reasonable connection between
the act constituting the offense and the official duty.

The Court found that there was no prima facie evidence to suggest that the appellant had willfully, with the intent to
defraud, acted upon the allegedly forged Power of Attorney. The charge-sheet lacked substance to establish the
ingredients of cheating (Section 420 IPC) or criminal conspiracy (Section 120B IPC). The Court reiterated the principles
from *State of Haryana v. Bhajan Lal* (supra), stating that if the allegations in the FIR do not prima facie constitute
any offense or make out a case against the accused, the proceedings should be quashed.

The Court concluded that no intention could be attributed to the appellant, and in its absence, no criminal offense could be made out. The FIR and other materials were unable to disclose any cognizable offense.

#### Judgment & Direction
The Supreme Court allowed the appeal, quashed and set aside the impugned judgment of the High Court, and closed all proceedings arising from the subject FIR and subsequent proceedings in Crime No. 241 of 2016.

### Step-by-Step Guide for Quashing a Criminal Complaint at the High Court (under Section 482 Cr.P.C.):

1. **Identify Grounds for Quashing**:
 - **No Prima Facie Case**: The allegations in the FIR/complaint, even if taken at face value, do not constitute a cognizable offense or make out a case against the accused.
 - **Absurd/Improbable Allegations**: The allegations are so absurd and inherently improbable that no prudent person can conclude there are sufficient grounds for proceeding.
 - **Express Legal Bar**: There is a specific legal bar to the institution or continuance of the proceedings (e.g., lack of sanction for prosecution of a public servant, limitation).
 - **Malice/Ulterior Motive**: The criminal proceeding is manifestly attended with mala fide intentions or is maliciously instituted with an ulterior motive to wreak vengeance.
 - **Civil Nature of Dispute**: The dispute is essentially civil in nature, and criminal proceedings are initiated to settle civil scores.
 - **Inordinate Delay**: There is an inordinate and unexplained delay in filing the complaint, which prejudices the accused.
 - **Omnibus Allegations**: General and vague allegations are made against multiple accused without specific roles attributed to each.
 - **Settlement/Compromise**: In certain compoundable offenses, if the parties have genuinely settled the dispute, the High Court may quash the proceedings.

2. **Prepare the Petition under Section 482 Cr.P.C.**:
 - Draft a detailed petition (Criminal Miscellaneous Application/Petition) clearly stating the facts of the case, the FIR/complaint details, and the specific grounds for quashing.
 - Attach all relevant documents, including the FIR, charge-sheet (if filed), and any other evidence supporting the grounds for quashing.
 - Include an affidavit affirming the truthfulness of the statements made in the petition.

3. **File the Petition in the High Court**:
 - File the petition in the appropriate High Court (usually the High Court having territorial jurisdiction over the police station where the FIR was registered or the court where the complaint is pending).
 - Pay the requisite court fees.

4. **Arguments before the High Court**:
 - The petitioner's counsel will argue the case, highlighting the grounds for quashing based on the facts and legal precedents.
 - The prosecution/complainant will present their arguments against quashing.
 - The High Court will examine the FIR, charge-sheet, and other materials on record to determine if a prima facie case is made out or if the proceedings constitute an abuse of process.

5. **High Court's Decision**:
 - The High Court, after hearing both sides, will pass an order either quashing the criminal complaint/FIR/proceedings or dismissing the petition.
 - If the petition is allowed, the criminal proceedings against the accused will be terminated.
 - If the petition is dismissed, the criminal proceedings will continue in the lower court.

6. **Appeal to the Supreme Court (if necessary)**:
 - If the High Court dismisses the petition for quashing, the aggrieved party may file a Special Leave Petition (Criminal) under Article 136 of the Constitution of India before the Supreme Court.

**Important Considerations**:
- The power under Section 482 Cr.P.C. is extraordinary and should be exercised with great caution.
- The High Court generally does not embark upon an appreciation of evidence or delve into disputed questions of fact at the stage of quashing.
- The focus is on whether the allegations, even if taken at their face value, disclose a cognizable offense.

**PDF Links:**
- *CHANCHALPATI DAS VS THE STATE OF WEST BENGAL*, 18-05-2023: [https://api.sci.gov.in/supremecourt/2017/17824/17824_2017_4_1502_44572_Judgement_18-May-2023.pdf](https://api.sci.gov.in/supremecourt/2017/17824/17824_2017_4_1502_44572_Judgement_18-May-2023.pdf)
- *KAHKASHAN KAUSAR @ SONAM VS THE STATE OF BIHAR*, 08-02-2022: [https://api.sci.gov.in/supremecourt/2020/25575/25575_2020_37_1501_33195_Judgement_08-Feb-2022.pdf](https://api.sci.gov.in/supremecourt/2020/25575/25575_2020_37_1501_33195_Judgement_08-Feb-2022.pdf)
- *DINESH KUMAR MATHUR VS THE STATE OF MADHYA PRADESH*, 02-01-2025: [https://api.sci.gov.in/supremecourt/2017/19719/19719_2017_17_1502_58238_Judgement_02-Jan-2025.pdf](https://api.sci.gov.in/supremecourt/2017/19719/19719_2017_17_1502_58238_Judgement_02-Jan-2025.pdf)

