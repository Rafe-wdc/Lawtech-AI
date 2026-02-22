# Test Report — 2026-02-22 00:06:53

**Architecture:** Embedding microservice (port 5200) + API server (port 5050, 4 workers)  
**Total Prompts:** 27  
**Total Time:** 428.0s  

## Summary

| Status | Count |
|--------|-------|
| PASS | 27 |
| WEAK | 0 |
| FAIL | 0 |
| ERROR | 0 |

## Timing

| Metric | Value |
|--------|-------|
| Avg | 15.9s |
| Min | 7.4s |
| Max | 39.0s |
| Median | 14.6s |

## Results Overview

| # | Status | Category | Prompt | Agent(s) | Time | Resp Len |
|---|--------|----------|--------|----------|------|----------|
| 1 | PASS | Newacts-Single | Section 302 of IPC | Newacts | 39.0s | 745 |
| 2 | PASS | Newacts-Single | Section 35 of BNS | Newacts | 16.4s | 580 |
| 3 | PASS | Newacts-Single | What is Section 438 of CrPC? | Newacts | 16.9s | 2043 |
| 4 | PASS | Newacts-Multi | Sections 302 and 307 of IPC | Newacts | 14.6s | 4303 |
| 5 | PASS | Newacts-Multi | Explain Sections 64, 65 and 66 of BSA | Newacts | 10.3s | 2119 |
| 6 | PASS | Newacts-Multi | Compare Section 154 and Section 161 of CrPC | Newacts, Judgment | 20.8s | 5894 |
| 7 | PASS | Newacts-Topic | punishment for theft in BNS | Newacts | 13.5s | 5011 |
| 8 | PASS | Newacts-Topic | bail provisions under BNSS | Newacts | 25.6s | 20307 |
| 9 | PASS | Newacts-Topic | electronic evidence rules in BSA | Newacts, Judgment | 29.2s | 7780 |
| 10 | PASS | Newacts-Nearby | What does Section 100 of BNS say? | Newacts | 10.3s | 2054 |
| 11 | PASS | Newacts-Nearby | Section 528 of BNSS | Newacts | 8.3s | 413 |
| 12 | PASS | Newacts-Subsection | Section 3(5) of Bharatiya Nyaya Sanhita | Newacts | 7.4s | 250 |
| 13 | PASS | Newacts-Subsection | IEA Section 65b | Newacts, Judgment | 18.8s | 11088 |
| 14 | PASS | Newacts-Mapping | What is the equivalent of Section 498a of IPC in B... | Newacts, Legislation | 18.0s | 1392 |
| 15 | PASS | Newacts-Mapping | Section 125 CrPC new law equivalent | Newacts, Legislation | 22.4s | 4151 |
| 16 | PASS | Legislation-Single | Section 138 of Negotiable Instruments Act | Legislation | 8.9s | 1111 |
| 17 | PASS | Legislation-Single | Section 9 of Arbitration Act | Legislation | 11.3s | 2004 |
| 18 | PASS | Legislation-Multi | Sections 44 and 45 of Transfer of Property Act | Legislation | 7.6s | 1297 |
| 19 | PASS | Legislation-Multi | Explain Sections 3, 4 and 5 of Consumer Protection... | Legislation | 7.5s | 1128 |
| 20 | PASS | Legislation-Range | Sections 10 to 15 of Companies Act | Legislation | 17.9s | 12951 |
| 21 | PASS | Legislation-Topic | director duties under companies act | Legislation, Judgment | 16.6s | 2606 |
| 22 | PASS | Legislation-Topic | tenant rights in rent control legislation | Legislation, Judgment | 31.9s | 10003 |
| 23 | PASS | Legislation-Subsection | Section 138(1) of Negotiable Instruments Act | Legislation | 11.5s | 971 |
| 24 | PASS | Edge-Nearby | What comes after section 35 of BNS? | Newacts | 7.6s | 162 |
| 25 | PASS | Edge-Vague | theft | Legal_Concepts | 8.9s | 1940 |
| 26 | PASS | Edge-NonSection | Rule 3 of Maharashtra Rent Control Rules | Legislation | 10.2s | 176 |
| 27 | PASS | Edge-ManySection | Sections 302, 304, 304a, 307 and 376 of IPC | Newacts | 16.7s | 9742 |

---

## Detailed Results

### #1 — PASS — Newacts-Single

**Query:** `Section 302 of IPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 39.0s  
**Response Length:** 745 chars  
**Tokens:** 839  

<details>
<summary>Full Response</summary>

**Section 302 of The Indian Penal Code 1860 (IPC)**

**Section Name:** Punishment for murder

**Old Provisions:** Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.

**New Provision (Bharatiya Nyaya Sanhita, 2023):**

-   **Section 103:**
    -   (1) Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine.
    -   (2) When a group of five or more persons acting in concert commits murder on the ground of race, caste or community, sex, place of birth, language, personal belief or any other similar ground each member of such group shall be punished with death or with imprisonment for life, and shall also be liable to fine.


</details>

---

### #2 — PASS — Newacts-Single

**Query:** `Section 35 of BNS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 16.4s  
**Response Length:** 580 chars  
**Tokens:** 706  

<details>
<summary>Full Response</summary>

Section 35 of The Bharatiya Nyaya Sanhita, 2023, titled "Right of private defence of the body and of property," states that every person has a right, subject to the restrictions in section 37, to defend:
(a) his own body, and the body of any other person, against any offence affecting the human body;
(b) the property, whether movable or immovable, of himself or of any other person, against any act which is an offence falling under the definition of theft, robbery, mischief or criminal trespass, or which is an attempt to commit theft, robbery, mischief or criminal trespass.


</details>

---

### #3 — PASS — Newacts-Single

**Query:** `What is Section 438 of CrPC?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 16.9s  
**Response Length:** 2043 chars  
**Tokens:** 7003  

<details>
<summary>Full Response</summary>

Section 438 of The Code of Criminal Procedure, 1973, titled "Direction for grant of bail to person apprehending arrest," states:

(1) When any person has reason to believe that he may be arrested on an accusation of having committed a non-bailable offence, he may apply to the High Court or the Court of Session for a direction under this section; and that Court may, if it thinks fit, direct that in the event of such arrest, he shall be released on bail.

(2) When the High Court or the Court of Session makes a direction under sub-section (1), it may include such conditions in such directions in the light of the facts of the particular case, as it may think fit, including:
(i) a condition that the person shall make himself available for interrogation by a police officer as and when required;
(ii) a condition that the person shall not, directly or indirectly, make any inducement, threat or promise to any person acquainted with the facts of the case so as to dissuade him from disclosing such facts to the Court or to any police officer;
(iii) a condition that the person shall not leave India without the previous permission of the Court;
(iv) such other condition as may be imposed under sub-section (3) of section 437, as if the bail were granted under that section.

(3) If such person is thereafter arrested without warrant by an officer in charge of a police station on such accusation, and is prepared either at the time of arrest or at any time while in the custody of such officer to give bail, he shall be released on bail; and if a Magistrate taking cognizance of such offence decides that a warrant should be issued in the first instance against that person, he shall issue a bailable warrant in conformity with the direction of the Court under sub-section (1).

(4) Nothing in this section shall apply to any case involving the arrest of any person on accusation of having committed an offence under sub-section (3) of section 376 or section 376AB or section 376DA or section 376DB of the Indian Penal Code (45 of 1860).


</details>

---

### #4 — PASS — Newacts-Multi

**Query:** `Sections 302 and 307 of IPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 14.6s  
**Response Length:** 4303 chars  
**Tokens:** 2525  

<details>
<summary>Full Response</summary>

**Section 302 of The Indian Penal Code 1860 (Punishment for murder)**

-   **Old Provision:** Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.

**Section 307 of The Indian Penal Code 1860 (Attempt to murder)**

-   **Old Provision:** Whoever does any act with such intention or knowledge, and under such circumstances that, if he by that act caused death, he would be guilty of murder, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine; and if hurt is caused to any person by such act, the offender shall be liable either to imprisonment for life, or to such punishment as is hereinbefore mentioned.
-   **Attempts by life convicts:** Where any person offending under this section is under sentence of imprisonment for life, he may, if hurt is caused, be punished with death.
-   **Illustrations:**
    -   (a) A shoots at Z with intention to kill him, under such circumstances that, if death ensued, A would be guilty of murder. A is liable to punishment under this section.
    -   (b) A, with the intention of causing the death of a child of tender years, exposes it in a desert place. A has committed the offence defined by this section, though the death of the child does not ensue.
    -   (c) A, intending to murder Z, buys a gun and loads it. A has not yet committed the offence. A fires the gun at Z. He has committed the offence defined in this section, and, if by such firing he wounds Z, he is liable to the punishment provided by the latter part of the first paragraph of this section.
    -   (d) A, intending to murder Z by poison, purchases poison and mixes the same with food which remains in A's keeping; A has not yet committed the offence defined in this section. A places the food on Z's table or delivers it to Z's servant to place it on Z's table. A has committed the offence defined in this section.

---

**Section 103 of Bharatiya Nyaya Sanhita, 2023 (Punishment for murder)**

-   **(1)** Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine.
-   **(2)** When a group of five or more persons acting in concert commits murder on the ground of race, caste or community, sex, place of birth, language, personal belief or any other similar ground each member of such group shall be punished with death or with imprisonment for life, and shall also be liable to fine.

**Section 109 of Bharatiya Nyaya Sanhita, 2023 (Attempt to murder)**

-   **(1)** Whoever does any act with such intention or knowledge, and under such circumstances that, if he by that act caused death, he would be guilty of murder, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine; and if hurt is caused to any person by such act, the offender shall be liable either to imprisonment for life, or to such punishment as is hereinbefore mentioned.
-   **(2)** When any person offending under sub-section (1) is under sentence of imprisonment for life, he may, if hurt is caused, be punished with death or with imprisonment for life, which shall mean the remainder of that person’s natural life.
-   **Illustrations.**
    -   (a) A shoots at Z with intention to kill him, under such circumstances that, if death ensued, A would be guilty of murder. A is liable to punishment under this section.
    -   (b) A, with the intention of causing the death of a child of tender years, exposes it in a desert place. A has committed the offence defined by this section, though the death of the child does not ensue.
    -   (c) A, intending to murder Z, buys a gun and loads it. A has not yet committed the offence. A fires the gun at Z. He has committed the offence defined in this section, and, if by such firing he wounds Z, he is liable to the punishment provided by the latter part of sub-section (1).
    -   (d) A, intending to murder Z by poison, purchases poison and mixes the same with food which remains in A’s keeping; A has not yet committed the offence defined in this section. A places the food on Z’s table or delivers it to Z’s servants to place it on Z’s table. A has committed the offence defined in this section.


</details>

---

### #5 — PASS — Newacts-Multi

**Query:** `Explain Sections 64, 65 and 66 of BSA`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 10.3s  
**Response Length:** 2119 chars  
**Tokens:** 1080  

<details>
<summary>Full Response</summary>

Here are the explanations for Sections 64, 65, and 66 of The Bharatiya Sakshya Adhiniyam, 2023:

**Section 64: Rules as to notice to produce**

This section deals with the requirement of giving notice before admitting secondary evidence of a document. Secondary evidence of the contents of a document (as referred to in clause (a) of section 60) cannot be presented unless the party intending to use this secondary evidence has first given notice to the party who possesses the original document, or to their advocate or representative. This notice must be in accordance with the law, or if no specific notice is prescribed by law, then such notice as the Court deems reasonable given the circumstances.

However, this notice is not required in certain situations, or if the Court decides to dispense with it. These exceptions include:
-   When the document to be proven is itself a notice.
-   When, due to the nature of the case, the opposing party would reasonably expect to be required to produce the document.
-   When it is evident or proven that the opposing party obtained the original document through fraud or force.
-   When the opposing party or their agent has the original document in court.
-   When the opposing party or their agent has admitted that the document is lost.
-   When the person in possession of the document is beyond the reach of, or not subject to, the Court's process.

**Section 65: Proof of signature and handwriting of person alleged to have signed or written document produced**

If a document is claimed to have been signed or written, either entirely or partially, by a specific person, then the signature or the handwriting of the part of the document that is alleged to be in that person's handwriting must be proven to be indeed in their handwriting.

**Section 66: Proof as to electronic signature**

Unless the electronic signature is a secure electronic signature, if it is alleged that an electronic signature of a subscriber was affixed to an electronic record, then it must be proven that such electronic signature is indeed the electronic signature of that subscriber.


</details>

---

### #6 — PASS — Newacts-Multi

**Query:** `Compare Section 154 and Section 161 of CrPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Judgment  
**Time:** 20.8s  
**Response Length:** 5894 chars  
**Tokens:** 16930  

<details>
<summary>Full Response</summary>

This response synthesizes information regarding Section 154 and Section 161 of the Code of Criminal Procedure, 1973 (CrPC).

## Statutory Basis

### Section 154 of the CrPC: Information in Cognizable Cases

Section 154 of the CrPC deals with the recording of information related to the commission of a **cognizable offence**.

-   **Sub-section (1)** mandates that any information given orally to an officer in charge of a police station about a cognizable offence must be reduced to writing by the officer or under their direction. This written information must then be read over to the informant and signed by them. The substance of this information is to be entered into a prescribed book.
    -   **Provisos** to this sub-section introduce special procedures for recording information concerning offences under specific sections of the Indian Penal Code (IPC) related to sexual offences and offences against women. These include:
        -   Recording by a woman police officer or any woman officer if the informant is a woman against whom such an offence is alleged to have been committed or attempted.
        -   Special provisions for recording information from persons who are temporarily or permanently mentally or physically disabled, requiring the recording to be done at their residence or a convenient place, in the presence of an interpreter or special educator, and to be video-graphed. Such statements should also be recorded by a Judicial Magistrate as soon as possible.
-   **Sub-section (2)** ensures that the informant is given a copy of the recorded information free of cost, forthwith.
-   **Sub-section (3)** provides a remedy for aggrieved persons if a police officer refuses to record the information. Such persons can send the substance of the information in writing to the Superintendent of Police, who, if satisfied about the commission of a cognizable offence, shall initiate an investigation.

### Section 161 of the CrPC: Examination of Witnesses by Police

Section 161 of the CrPC outlines the powers of the police to examine witnesses during an investigation.

-   **Sub-section (1)** empowers any police officer making an investigation, or another officer acting on their requisition, to orally examine any person believed to be acquainted with the facts and circumstances of the case.
-   **Sub-section (2)** states that such examined persons are bound to answer truthfully all questions related to the case, except those that might expose them to a criminal charge, penalty, or forfeiture.
-   **Sub-section (3)** allows the police officer to reduce into writing any statement made during the examination. If written, a separate and true record of each person's statement must be made.
    -   **Provisos** to this sub-section allow for statements to be recorded by audio-video electronic means.
    -   A further **proviso** mandates that the statement of a woman against whom an offence under specific sections of the IPC (related to sexual offences and offences against women) is alleged to have been committed or attempted, shall be recorded by a woman police officer or any woman officer.

## Comparison and Analysis

While both Section 154 and Section 161 of the CrPC involve the police recording statements, they serve distinct purposes:

-   **Purpose:**
    -   **Section 154** is primarily concerned with the **initiation of investigation** by recording the First Information Report (FIR) in cognizable offences. It is the gateway to the criminal justice process.
    -   **Section 161** deals with the **investigation process itself**, allowing police to gather evidence and information by examining witnesses who are acquainted with the facts of the case.

-   **Nature of Information:**
    -   **Section 154** records **information about the commission of an offence**, typically from the victim or an eyewitness.
    -   **Section 161** records **statements of witnesses** who may have knowledge of the facts and circumstances surrounding the offence.

-   **Binding Nature of Statement:**
    -   Statements recorded under **Section 154** are the basis of the FIR and are crucial for initiating proceedings.
    -   Statements recorded under **Section 161** are not signed by the witness and are generally not admissible as substantive evidence in court, though they can be used for corroboration or contradiction of the witness's testimony during trial.

-   **Special Provisions for Women and Disabled Persons:**
    -   Both sections contain specific provisions to protect vulnerable individuals, particularly women who are victims of sexual offences, by mandating recording by female officers.
    -   Section 154 also includes detailed provisions for recording statements of mentally or physically disabled persons, emphasizing their comfort and understanding.

-   **Remedy for Refusal:**
    -   Section 154 provides a clear remedy for the refusal to record information by allowing recourse to the Superintendent of Police. Section 161 does not explicitly provide a similar direct remedy for refusal to examine, as it is part of the discretionary investigative powers of the police.

## Remedies

-   **Under Section 154:** If an officer in charge of a police station refuses to record information about a cognizable offence, the informant can send the substance of the information in writing to the Superintendent of Police. The Superintendent of Police can then order an investigation.
-   **Under Section 161:** While there isn't a direct remedy for refusal to examine a witness, a failure to investigate a cognizable offence or to properly record statements can be grounds for judicial intervention or departmental action.

It is important to note that statements recorded under Section 161 are distinct from statements recorded under Section 164 of the CrPC, which are made before a Magistrate and have higher evidentiary value.


</details>

---

### #7 — PASS — Newacts-Topic

**Query:** `punishment for theft in BNS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 13.5s  
**Response Length:** 5011 chars  
**Tokens:** 5461  

<details>
<summary>Full Response</summary>

Here are the provisions related to punishment for theft in The Bharatiya Nyaya Sanhita, 2023:

-   **Section 313: Punishment for belonging to gang of robbers, dacoits, etc.**
    Whoever belongs to any gang of persons associated in habitually committing theft or robbery, and not being a gang of dacoits, shall be punished with rigorous imprisonment for a term which may extend to seven years, and shall also be liable to fine.

-   **Section 305: Theft in a dwelling house, or means of transportation or place of worship, etc.**
    Whoever commits theft:
    (a) in any building, tent or vessel used as a human dwelling or used for the custody of property; or
    (b) of any means of transport used for the transport of goods or passengers; or
    (c) of any article or goods from any means of transport used for the transport of goods or passengers; or
    (d) of idol or icon in any place of worship; or
    (e) of any property of the Government or of a local authority, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

-   **Section 306: Theft by clerk or servant of property in possession of master.**
    Whoever, being a clerk or servant, or being employed in the capacity of a clerk or servant, commits theft in respect of any property in the possession of his master or employer, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

-   **Section 328: Punishment for intentionally running vessel aground or ashore with intent to commit theft, etc.**
    Whoever intentionally runs any vessel aground or ashore, intending to commit theft of any property contained therein or to dishonestly misappropriate any such property, or with intent that such theft or misappropriation of property may be committed, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine.

-   **Section 112: Petty organised crime**
    (1) Whoever, being a member of a group or gang, either singly or jointly, commits any act of theft, snatching, cheating, unauthorised selling of tickets, unauthorised betting or gambling, selling of public examination question papers or any other similar criminal act, is said to commit petty organised crime.
    Explanation: For the purposes of this sub-section "theft" includes trick theft, theft from vehicle, dwelling house or business premises, cargo theft, pick pocketing, theft through card skimming, shoplifting and theft of Automated Teller Machine.
    (2) Whoever commits any petty organised crime shall be punished with imprisonment for a term which shall not be less than one year but which may extend to seven years, and shall also be liable to fine.

-   **Section 307: Theft after preparation made for causing death, hurt or restraint in order to the committing of theft.**
    Whoever commits theft, having made preparation for causing death, or hurt, or restraint, or fear of death, or of hurt, or of restraint, to any person, in order to the committing of such theft, or in order to the effecting of his escape after the committing of such theft, or in order to the retaining of property taken by such theft, shall be punished with rigorous imprisonment for a term which may extend to ten years, and shall also be liable to fine.

-   **Section 134: Assault or criminal force in attempt to commit theft of property carried by a person.**
    Whoever assaults or uses criminal force to any person, in attempting to commit theft on any property which that person is then wearing or carrying, shall be punished with imprisonment of either description for a term which may extend to two years, or with fine, or with both.

-   **Section 62: Punishment for attempting to commit offences punishable with imprisonment for life or other imprisonment.**
    Whoever attempts to commit an offence punishable by this Sanhita with imprisonment for life or imprisonment, or to cause such an offence to be committed, and in such attempt does any act towards the commission of the offence, shall, where no express provision is made by this Sanhita for the punishment of such attempt, be punished with imprisonment of any description provided for the offence, for a term which may extend to one-half of the imprisonment for life or, as the case may be, one-half of the longest term of imprisonment provided for that offence, or with such fine as is provided for the offence, or with both.
    Illustrations:
    (a) A makes an attempt to steal some jewels by breaking open a box, and finds after so opening the box, that there is no jewel in it. He has done an act towards the commission of theft, and therefore is guilty under this section.
    (b) A makes an attempt to pick the pocket of Z by thrusting his hand into Z’s pocket. A fails in the attempt in consequence of Z’s having nothing in his pocket. A is guilty under this section.


</details>

---

### #8 — PASS — Newacts-Topic

**Query:** `bail provisions under BNSS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 25.6s  
**Response Length:** 20307 chars  
**Tokens:** 11495  

<details>
<summary>Full Response</summary>

Here are the bail provisions under The Bharatiya Nagarik Suraksha Sanhita, 2023:

**Definition of Bail:**
-   **Section 2(b):** "bail" means release of a person accused of or suspected of commission of an offence from the custody of law upon certain conditions imposed by an officer or Court on execution by such person of a bond or a bail bond.

**Types of Offences related to Bail:**
-   **Section 2(c):** "bailable offence" means an offence which is shown as bailable in the First Schedule, or which is made bailable by any other law for the time being in force; and "non-bailable offence" means any other offence.

**General Provisions for Bail:**
-   **Section 478(1):** When any person other than a person accused of a non-bailable offence is arrested or detained without warrant by an officer in charge of a police station, or appears or is brought before a Court, and is prepared at any time while in the custody of such officer or at any stage of the proceeding before such Court to give bail, such person shall be released on bail.
    -   **Proviso:** Such officer or Court, if he or it thinks fit, may, and shall, if such person is indigent and is unable to furnish surety, instead of taking bail bond from such person, discharge him on his executing a bond for his appearance.
    -   **Explanation:** Where a person is unable to give bail bond within a week of the date of his arrest, it shall be a sufficient ground for the officer or the Court to presume that he is an indigent person for the purposes of this proviso.
    -   **Further Proviso:** Nothing in this section shall be deemed to affect the provisions of sub-section (3) of section 135 or section 492.
-   **Section 478(2):** Notwithstanding anything in sub-section (1), where a person has failed to comply with the conditions of the bond or bail bond as regards the time and place of attendance, the Court may refuse to release him on bail, when on a subsequent occasion in the same case he appears before the Court or is brought in custody and any such refusal shall be without prejudice to the powers of the Court to call upon any person bound by such bond or bail bond to pay the penalty thereof under section 491.

**Bail in Case of Non-Bailable Offence:**
-   **Section 480(1):** When any person accused of, or suspected of, the commission of any non-bailable offence is arrested or detained without warrant by an officer in charge of a police station or appears or is brought before a Court other than the High Court or Court of Session, he may be released on bail, but:
    -   (i) such person shall not be so released if there appear reasonable grounds for believing that he has been guilty of an offence punishable with death or imprisonment for life;
    -   (ii) such person shall not be so released if such offence is a cognizable offence and he had been previously convicted of an offence punishable with death, imprisonment for life or imprisonment for seven years or more, or he had been previously convicted on two or more occasions of a cognizable offence punishable with imprisonment for three years or more but less than seven years.
    -   **Proviso 1:** The Court may direct that a person referred to in clause (i) or clause (ii) be released on bail if such person is a child or is a woman or is sick or infirm.
    -   **Proviso 2:** The Court may also direct that a person referred to in clause (ii) be released on bail if it is satisfied that it is just and proper so to do for any other special reason.
    -   **Proviso 3:** The mere fact that an accused person may be required for being identified by witnesses during investigation or for police custody beyond the first fifteen days shall not be sufficient ground for refusing to grant bail if he is otherwise entitled to be released on bail and gives an undertaking that he shall comply with such directions as may be given by the Court.
    -   **Proviso 4:** No person shall, if the offence alleged to have been committed by him is punishable with death, imprisonment for life, or imprisonment for seven years or more, be released on bail by the Court under this sub-section without giving an opportunity of hearing to the Public Prosecutor.
-   **Section 480(2):** If it appears to such officer or Court at any stage of the investigation, inquiry or trial, as the case may be, that there are not reasonable grounds for believing that the accused has committed a non-bailable offence, but that there are sufficient grounds for further inquiry into his guilt, the accused shall, subject to the provisions of section 492 and pending such inquiry, be released on bail, or, at the discretion of such officer or Court, on the execution by him of a bond for his appearance as hereinafter provided.
-   **Section 480(3):** When a person accused or suspected of the commission of an offence punishable with imprisonment which may extend to seven years or more or of an offence under Chapter VI, Chapter VII or Chapter XVII of the Bharatiya Nyaya Sanhita, 2023 or abetment of, or conspiracy or attempt to commit, any such offence, is released on bail under sub-section (1), the Court shall impose the conditions:
    -   (a) that such person shall attend in accordance with the conditions of the bond executed under this Chapter;
    -   (b) that such person shall not commit an offence similar to the offence of which he is accused, or suspected, of the commission of which he is suspected; and
    -   (c) that such person shall not directly or indirectly make any inducement, threat or promise to any person acquainted with the facts of the case so as to dissuade him from disclosing such facts to the Court or to any police officer or tamper with the evidence, and may also impose, in the interests of justice, such other conditions as it considers necessary.
-   **Section 480(4):** An officer or a Court releasing any person on bail under sub-section (1) or sub-section (2), shall record in writing his or its reasons or special reasons for so doing.
-   **Section 480(5):** Any Court which has released a person on bail under sub-section (1) or sub-section (2), may, if it considers it necessary so to do, direct that such person be arrested and commit him to custody.
-   **Section 480(6):** If, in any case triable by a Magistrate, the trial of a person accused of any non-bailable offence is not concluded within a period of sixty days from the first date fixed for taking evidence in the case, such person shall, if he is in custody during the whole of the said period, be released on bail to the satisfaction of the Magistrate, unless for reasons to be recorded in writing, the Magistrate otherwise directs.
-   **Section 480(7):** If, at any time, after the conclusion of the trial of a person accused of a non-bailable offence and before judgment is delivered, the Court is of opinion that there are reasonable grounds for believing that the accused is not guilty of any such offence, it shall release the accused, if he is in custody, on the execution by him of a bond for his appearance to hear judgment delivered.

**Anticipatory Bail:**
-   **Section 482(1):** When any person has reason to believe that he may be arrested on an accusation of having committed a non-bailable offence, he may apply to the High Court or the Court of Session for a direction under this section; and that Court may, if it thinks fit, direct that in the event of such arrest, he shall be released on bail.
-   **Section 482(2):** When the High Court or the Court of Session makes a direction under sub-section (1), it may include such conditions in such directions in the light of the facts of the particular case, as it may think fit, including:
    -   (i) a condition that the person shall make himself available for interrogation by a police officer as and when required;
    -   (ii) a condition that the person shall not, directly or indirectly, make any inducement, threat or promise to any person acquainted with the facts of the case so as to dissuade him from disclosing such facts to the Court or to any police officer;
    -   (iii) a condition that the person shall not leave India without the previous permission of the Court;
    -   (iv) such other condition as may be imposed under sub-section (3) of section 480, as if the bail were granted under that section.
-   **Section 482(3):** If such person is thereafter arrested without warrant by an officer in charge of a police station on such accusation, and is prepared either at the time of arrest or at any time while in the custody of such officer to give bail, he shall be released on bail; and if a Magistrate taking cognizance of such offence decides that a warrant should be issued in the first instance against that person, he shall issue a bailable warrant in conformity with the direction of the Court under sub-section (1).
-   **Section 482(4):** Nothing in this section shall apply to any case involving the arrest of any person on accusation of having committed an offence under section 65 and sub-section (2) of section 70 of the Bharatiya Nyaya Sanhita, 2023.

**Powers of High Court or Court of Session regarding Bail:**
-   **Section 483(1):** A High Court or Court of Session may direct:
    -   (a) that any person accused of an offence and in custody be released on bail, and if the offence is of the nature specified in sub-section (3) of section 480, may impose any condition which it considers necessary for the purposes mentioned in that sub-section;
    -   (b) that any condition imposed by a Magistrate when releasing any person on bail be set aside or modified.
    -   **Provided 1:** The High Court or the Court of Session shall, before granting bail to a person who is accused of an offence which is triable exclusively by the Court of Session or which, though not so triable, is punishable with imprisonment for life, give notice of the application for bail to the Public Prosecutor unless it is, for reasons to be recorded in writing, of opinion that it is not practicable to give such notice.
    -   **Provided 2:** The High Court or the Court of Session shall, before granting bail to a person who is accused of an offence triable under section 65 or sub-section (2) of section 70 of the Bharatiya Nyaya Sanhita, 2023, give notice of the application for bail to the Public Prosecutor within a period of fifteen days from the date of receipt of the notice of such application.
-   **Section 483(2):** The presence of the informant or any person authorised by him shall be obligatory at the time of hearing of the application for bail to the person under section 65 or sub-section (2) of section 70 of the Bharatiya Nyaya Sanhita, 2023.
-   **Section 483(3):** A High Court or Court of Session may direct that any person who has been released on bail under this Chapter be arrested and commit him to custody.

**Bail Bond and Sureties:**
-   **Section 486:** Every person standing surety to an accused person for his release on bail, shall make a declaration before the Court as to the number of persons to whom he has stood surety including the accused, giving therein all the relevant particulars.
-   **Section 488:** If, through mistake, fraud or otherwise, insufficient sureties have been accepted, or if they afterwards become insufficient, the Court may issue a warrant of arrest directing that the person released on bail be brought before it and may order him to find sufficient sureties, and, on his failing so to do, may commit him to jail.

**Conditions for Bail:**
-   **Section 480(3):** (As mentioned above, for offences punishable with imprisonment for seven years or more, or under specific Chapters of BNS, or abetment/conspiracy/attempt of such offences).
-   **Section 482(2):** (Conditions for anticipatory bail).

**Discharge of Person Apprehended:**
-   **Section 60:** No person who has been arrested by a police officer shall be discharged except on his bond, or bail bond, or under the special order of a Magistrate.

**Maximum Period for Undertrial Detention and Bail:**
-   **Section 479(1):** Where a person has, during the period of investigation, inquiry or trial under this Sanhita of an offence under any law (not being an offence for which the punishment of death or life imprisonment has been specified as one of the punishments under that law) undergone detention for a period extending up to one-half of the maximum period of imprisonment specified for that offence under that law, he shall be released by the Court on bail.
    -   **Proviso 1:** Where such person is a first-time offender (who has never been convicted of any offence in the past) he shall be released on bond by the Court, if he has undergone detention for the period extending up to one-third of the maximum period of imprisonment specified for such offence under that law.
    -   **Proviso 2:** The Court may, after hearing the Public Prosecutor and for reasons to be recorded by it in writing, order the continued detention of such person for a period longer than one-half of the said period or release him on bail bond instead of his bond.
    -   **Proviso 3:** No such person shall in any case be detained during the period of investigation, inquiry or trial for more than the maximum period of imprisonment provided for the said offence under that law.
    -   **Explanation:** In computing the period of detention under this section for granting bail, the period of detention passed due to delay in proceeding caused by the accused shall be excluded.
-   **Section 479(2):** Notwithstanding anything in sub-section (1), and subject to the third proviso thereof, where an investigation, inquiry or trial in more than one offence or in multiple cases are pending against a person, he shall not be released on bail by the Court.
-   **Section 479(3):** The Superintendent of jail, where the accused person is detained, on completion of one-half or one-third of the period mentioned in sub-section (1), as the case may be, shall forthwith make an application in writing to the Court to proceed under sub-section (1) for the release of such person on bail.

**Suspension of Sentence Pending Appeal and Release on Bail:**
-   **Section 430(1):** Pending any appeal by a convicted person, the Appellate Court may, for reasons to be recorded by it in writing, order that the execution of the sentence or order appealed against be suspended and, also, if he is in confinement, that he be released on bail, or on his own bond or bail bond.
    -   **Proviso 1:** The Appellate Court shall, before releasing on his own bond or bail bond a convicted person who is convicted of an offence punishable with death or imprisonment for life or imprisonment for a term of not less than ten years, shall give opportunity to the Public Prosecutor for showing cause in writing against such release.
    -   **Proviso 2:** In cases where a convicted person is released on bail it shall be open to the Public Prosecutor to file an application for the cancellation of the bail.
-   **Section 430(2):** The power conferred by this section on an Appellate Court may be exercised also by the High Court in the case of an appeal by a convicted person to a Court subordinate thereto.
-   **Section 430(3):** Where the convicted person satisfies the Court by which he is convicted that he intends to present an appeal, the Court shall:
    -   (i) where such person, being on bail, is sentenced to imprisonment for a term not exceeding three years; or
    -   (ii) where the offence of which such person has been convicted is a bailable one, and he is on bail, order that the convicted person be released on bail, unless there are special reasons for refusing bail, for such period as will afford sufficient time to present the appeal and obtain the orders of the Appellate Court under sub-section (1); and the sentence of imprisonment shall, so long as he is so released on bail, be deemed to be suspended.
-   **Section 430(4):** When the appellant is ultimately sentenced to imprisonment for a term or to imprisonment for life, the time during which he is so released shall be excluded in computing the term for which he is so sentenced.

**Arrest of Accused in Appeal from Acquittal:**
-   **Section 431:** When an appeal is presented under section 419, the High Court may issue a warrant directing that the accused be arrested and brought before it or any subordinate Court, and the Court before which he is brought may commit him to prison pending the disposal of the appeal or admit him to bail.

**Breach of Bond or Bail Bond:**
-   **Section 92:** When any person who is bound by any bond or bail bond taken under this Sanhita to appear before a Court, does not appear, the officer presiding in such Court may issue a warrant directing that such person be arrested and produced before him.
-   **Section 492:** Without prejudice to the provisions of section 491, where a bond or bail bond under this Sanhita is for appearance of a person in a case and it is forfeited for breach of a condition:
    -   (a) the bond executed by such person as well as the bond, if any, executed by one or more of his sureties in that case shall stand cancelled; and
    -   (b) thereafter no such person shall be released only on his own bond in that case, if the police officer or the Court, as the case may be, for appearance before whom the bond was executed, is satisfied that there was no sufficient cause for the failure of the person bound by the bond to comply with its condition.
    -   **Provided:** Subject to any other provisions of this Sanhita he may be released in that case upon the execution of a fresh personal bond for such sum of money and bond by one or more of such sureties as the police officer or the Court, as the case may be, thinks sufficient.

**Procedure in Case of Insolvency or Death of Surety or Forfeiture of Bond:**
-   **Section 493:** When any surety to a bail bond under this Sanhita becomes insolvent or dies, or when any bond is forfeited under the provisions of section 491, the Court by whose order such bond was taken, or a Magistrate of the first class may order the person from whom such security was demanded to furnish fresh security in accordance with the directions of the original order, and if such security is not furnished, such Court or Magistrate may proceed as if there had been a default in complying with such original order.

**Power in Plea Bargaining:**
-   **Section 296:** A Court shall have, for the purposes of discharging its functions under this Chapter, all the powers vested in respect of bail, trial of offences and other matters relating to the disposal of a case in such Court under this Sanhita.

**Security for Good Behaviour:**
-   **Section 127(1):** When an Executive Magistrate receives information that there is within his local jurisdiction any person who, within or without such jurisdiction, intentionally disseminates or attempts to disseminate or abets the dissemination of certain matters, or circulates obscene matter, and the Magistrate is of opinion that there is sufficient ground for proceeding, the Magistrate may require such person to show cause why he should not be ordered to execute a bond or bail bond, for his good behaviour for such period, not exceeding one year, as the Magistrate thinks fit.
-   **Section 139:** The bond or bail bond to be executed by any such person shall bind him to keep the peace or to be of good behaviour, as the case may be, and in the latter case the commission or attempt to commit, or the abetment of, any offence punishable with imprisonment, wherever it may be committed, is a breach of the bond or bail bond.

**Security for Keeping Peace on Conviction:**
-   **Section 125(1):** When a Court of Session or Court of a Magistrate of the first class convicts a person of certain offences or of abetting any such offence and is of opinion that it is necessary to take security from such person for keeping the peace, the Court may, at the time of passing sentence on such person, order him to execute a bond or bail bond, for keeping the peace for such period, not exceeding three years, as it thinks fit.
-   **Section 125(4):** An order under this section may also be made by an Appellate Court or by a Court when exercising its powers of revision.


</details>

---

### #9 — PASS — Newacts-Topic

**Query:** `electronic evidence rules in BSA`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Judgment  
**Time:** 29.2s  
**Response Length:** 7780 chars  
**Tokens:** 18490  

<details>
<summary>Full Response</summary>

The Bharatiya Sakshya Adhiniyam, 2023 (BSA 2023) significantly modernizes the admissibility and treatment of electronic evidence in India. It explicitly includes electronic and digital records within the definitions of "document" and "evidence," ensuring their seamless integration into legal proceedings.

## Statutory Basis for Electronic Evidence under BSA 2023

The BSA 2023 introduces several key provisions governing electronic evidence:

-   **Section 61: Electronic or Digital Record:** This section clarifies that the admissibility of an electronic or digital record will not be denied solely on the basis of its electronic nature. Such records, subject to Section 63, are to have the same legal effect, validity, and enforceability as other documents.

-   **Section 62: Special Provisions as to Evidence Relating to Electronic Record:** This section mandates that the contents of electronic records may be proved in accordance with the provisions of Section 63.

-   **Section 63: Admissibility of Electronic Records:** This is a pivotal section that defines the conditions for the admissibility of computer outputs, which are defined as information contained in an electronic record printed on paper, stored, recorded, or copied in optical or magnetic media, or semiconductor memory, produced by a computer or communication device.
    -   **Deemed Document:** Such computer outputs are deemed to be documents and admissible in proceedings without further proof or production of the original, provided certain conditions are met.
    -   **Conditions for Admissibility:**
        -   The computer output must have been produced by a computer or communication device regularly used for creating, storing, or processing information for an ongoing activity under lawful control.
        -   Information of the kind contained in the record, or from which it is derived, must have been regularly fed into the computer or communication device in the ordinary course of these activities.
        -   Throughout the material period, the computer or communication device must have been operating properly, or any malfunction must not have affected the accuracy of the record.
        -   The information in the electronic record must reproduce or be derived from information fed into the computer or communication device in the ordinary course of activities.
    -   **Multiple Devices:** If the process of creating, storing, or processing information involved multiple computers or communication devices (standalone, system, network, resource, or through an intermediary), they are to be treated as a single device for the purposes of this section.
    -   **Certificate Requirement:** In proceedings where a statement from a computer output is to be given in evidence, a certificate must be submitted along with the electronic record. This certificate should:
        -   Identify the electronic record and describe its production method.
        -   Provide particulars of any device involved to show it was produced by a computer or communication device.
        -   Address matters related to the conditions in sub-section (2).
        -   Be signed by the person in charge of the computer, communication device, or relevant activities.
        -   An expert's certificate is also admissible.
    -   **Definitions:** The section further defines "information supplied to a computer or communication device" and clarifies that a "computer output" is produced whether directly or through appropriate equipment or other electronic means.

-   **Section 32: Relevancy of Statements as to Any Law Contained in Law Books Including Electronic or Digital Form:** This section makes statements of law contained in books, including those in electronic or digital form, purporting to be printed or published under government authority, relevant.

-   **Section 33: What Evidence to be Given When Statement Forms Part of a Conversation, Document, Electronic Record, Book or Series of Letters or Papers:** When a statement is part of a larger document, conversation, or an electronic record, evidence shall be given of so much as the Court considers necessary for the full understanding of its nature and effect.

-   **Section 57: Primary Evidence:** This section expands the definition of primary evidence to include electronic or digital records.
    -   **Explanation 4:** Where an electronic or digital record is created or stored simultaneously or sequentially in multiple files, each such file is primary evidence.
    -   **Explanation 5:** An electronic or digital record produced from proper custody is primary evidence unless disputed.
    -   **Explanation 6:** Where a video recording is simultaneously stored in electronic form and transmitted or transferred, each stored recording is primary evidence.
    -   **Explanation 7:** Where an electronic or digital record is stored in multiple storage spaces in a computer resource, each such automated storage, including temporary files, is primary evidence.

-   **Section 81: Presumption as to Gazettes in Electronic or Digital Record:** The Court shall presume the genuineness of an electronic or digital record purporting to be the Official Gazette or directed by law to be kept by any person, if it is in the required form and produced from proper custody.

-   **Section 85: Presumption as to Electronic Agreements:** The Court shall presume that every electronic record purporting to be an agreement with an electronic or digital signature was concluded by the affixing of such signatures.

-   **Section 86: Presumption as to Electronic Records and Electronic Signatures:**
    -   **Secure Electronic Record:** The Court shall presume that a secure electronic record has not been altered since a specific point in time, unless proven otherwise.
    -   **Secure Electronic Signature:** The Court shall presume that a secure electronic signature was affixed by the subscriber with the intention of signing or approving the record, unless proven otherwise.
    -   **General Presumption:** Nothing in this section creates a presumption relating to the authenticity and integrity of electronic records or signatures, except in the case of secure ones.

-   **Section 87: Presumption as to Electronic Signature Certificates:** The Court shall presume the correctness of information listed in an Electronic Signature Certificate, except for unverified subscriber information, if the certificate was accepted by the subscriber.

-   **Section 90: Presumption as to Electronic Messages:** The Court may presume that an electronic message forwarded through an electronic mail server corresponds with the message fed into the computer for transmission, but not as to the sender.

-   **Section 93: Presumption as to Electronic Records Five Years Old:** Where a five-year-old electronic record is produced from proper custody, the Court may presume that any purported electronic signature was affixed by the person or their authorized representative.

## Definitions of Key Terms

-   **"Document" (Section 2(1)(d)):** This definition is expanded to explicitly include "electronic and digital records." Illustrations clarify that emails, server logs, documents on computers, messages, websites, locational evidence, and voice mail messages stored on digital devices are considered documents.

-   **"Evidence" (Section 2(1)(e)):** This definition includes "all documents including electronic or digital records produced for the inspection of the Court," which are then referred to as documentary evidence.

The Bharatiya Sakshya Adhiniyam, 2023, therefore, provides a comprehensive framework for the admissibility and evidentiary value of electronic records, aligning Indian law with the digital realities of the modern era.


</details>

---

### #10 — PASS — Newacts-Nearby

**Query:** `What does Section 100 of BNS say?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 10.3s  
**Response Length:** 2054 chars  
**Tokens:** 2869  

<details>
<summary>Full Response</summary>

Section 100 of The Bharatiya Nyaya Sanhita, 2023, defines "Culpable homicide" as follows:

"Whoever causes death by doing an act with the intention of causing death, or with the intention of causing such bodily injury as is likely to cause death, or with the knowledge that he is likely by such act to cause death, commits the offence of culpable homicide."

The section also provides three illustrations and three explanations:

**Illustrations:**
(a) A lays sticks and turf over a pit, with the intention of thereby causing death, or with the knowledge that death is likely to be thereby caused. Z, believing the ground to be firm, treads on it, falls in and is killed. A has committed the offence of culpable homicide.
(b) A knows Z to be behind a bush. B does not know it. A, intending to cause, or knowing it to be likely to cause Z’s death, induces B to fire at the bush. B fires and kills Z. Here B may be guilty of no offence; but A has committed the offence of culpable homicide.
(c) A, by shooting at a fowl with intent to kill and steal it, kills B, who is behind a bush; A not knowing that he was there. Here, although A was doing an unlawful act, he was not guilty of culpable homicide, as he did not intend to kill B, or to cause death by doing an act that he knew was likely to cause death.

**Explanation 1:** A person who causes bodily injury to another who is labouring under a disorder, disease or bodily infirmity, and thereby accelerates the death of that other, shall be deemed to have caused his death.

**Explanation 2:** Where death is caused by bodily injury, the person who causes such bodily injury shall be deemed to have caused the death, although by resorting to proper remedies and skilful treatment the death might have been prevented.

**Explanation 3:** The causing of the death of a child in the mother’s womb is not homicide. But it may amount to culpable homicide to cause the death of a living child, if any part of that child has been brought forth, though the child may not have breathed or been completely born.


</details>

---

### #11 — PASS — Newacts-Nearby

**Query:** `Section 528 of BNSS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 8.3s  
**Response Length:** 413 chars  
**Tokens:** 481  

<details>
<summary>Full Response</summary>

Section 528 of The Bharatiya Nagarik Suraksha Sanhita 2023 is titled "Saving of inherent powers of High Court". The provision states: "Nothing in this Sanhita shall be deemed to limit or affect the inherent powers of the High Court to make such orders as may be necessary to give effect to any order under this Sanhita, or to prevent abuse of the process of any Court or otherwise to secure the ends of justice."


</details>

---

### #12 — PASS — Newacts-Subsection

**Query:** `Section 3(5) of Bharatiya Nyaya Sanhita`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 7.4s  
**Response Length:** 250 chars  
**Tokens:** 4712  

<details>
<summary>Full Response</summary>

Section 3(5) of The Bharatiya Nyaya Sanhita, 2023 states: "When a criminal act is done by several persons in furtherance of the common intention of all, each of such persons is liable for that act in the same manner as if it were done by him alone."


</details>

---

### #13 — PASS — Newacts-Subsection

**Query:** `IEA Section 65b`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Judgment  
**Time:** 18.8s  
**Response Length:** 11088 chars  
**Tokens:** 6821  

<details>
<summary>Full Response</summary>

The provided text discusses Section 65B of the Indian Evidence Act, 1872, primarily in the context of admitting electronic records, such as CDs containing voice recordings, as evidence in legal proceedings. The section requires a certificate to be produced for the admissibility of such electronic records.

Several court cases highlight the importance and application of Section 65B:

-   **Himani Malhotra vs. I.P. College for Women (Delhi High Court):** In this case, the petitioner sought to introduce a CD of voice recordings. The Inquiry Officer initially dismissed the application because the petitioner failed to provide a certificate under Section 65B(4) of the Indian Evidence Act. The Delhi High Court set aside the order, directing the petitioner to furnish the certificate under Section 65B, emphasizing the principles of natural justice and the need for a fair opportunity to prove her case. In a subsequent related petition, the petitioner was directed to approach the Inquiry Officer to decide her pending application regarding the admissibility of the CDs and voice samples.

-   **Ignatius Tony Pereira vs. Travel Corporation (India) Pvt. Ltd. (Bombay High Court):** The petitioner challenged an order rejecting his application to admit certificates that he claimed complied with Section 65B. The Bombay High Court, while not interfering with the impugned order, granted the petitioner liberty to file fresh certificates compliant with Section 65B, stating that the admissibility of these new certificates would be decided by the Trial Court. The court clarified that this liberty did not permit the petitioner to make out an entirely new case.

-   **Shri Govind Sahay Goyal vs. Smt. Kusumlata Arora (Rajasthan High Court):** The petitioner-tenant challenged an order rejecting his application that argued a compact disc could not be considered evidence without a certificate under Section 65B. The respondent-landlord, on instructions, submitted that they would produce the certificate under Section 65B at an appropriate stage. The petition was disposed of with liberty to the petitioner to raise the issue of admissibility of evidence at the stage of final arguments.

-   **Kumar Yallappa Vijapure vs. Shrenik Industries (Bombay High Court):** The petitioner challenged a Labour Court order upholding an inquiry where a CD of alleged call records was produced without a Section 65B certificate. The Labour Court had held that strict rules of evidence are not applicable to departmental inquiries and that it could not go into the sufficiency or adequacy of evidence at that stage. The Bombay High Court found the Labour Court's findings justifiable and dismissed the petition, clarifying that the petitioner was at liberty to raise all available contentions in the Labour Court proceedings.

These cases collectively demonstrate that while Section 65B of the Indian Evidence Act, 1872, is crucial for the admissibility of electronic records, courts may allow parties to rectify deficiencies or raise objections at appropriate stages, balancing the need for proper evidence with principles of natural justice and the specific context of the proceedings (e.g., departmental inquiries vs. regular court trials).

---

### **Himani Malhotra vs. I.P. College for Women**

-   **Citation:** W.P.(C) 1101/2016 & C.M. APPL. No.4831/2016
-   **Court:** High Court of Delhi at New Delhi
-   **Bench/Judge(s):** Hon'ble Mr. Justice V.P. Vaish
-   **Date of Order:** 24.02.2016

**Facts:**
The petitioner, an Assistant Professor, was facing disciplinary proceedings initiated by the respondent college. She filed an application before the Inquiry Officer to place on record a CD containing voice recordings of conversations between herself, another individual (Dr. Seema Singh), the Principal, and students. She also sought directions for the individuals whose voices were recorded to provide samples for forensic verification. The Inquiry Officer dismissed this application, citing the petitioner's failure to provide a certificate under Section 65B(4) and to satisfy the conditions laid down in Section 65B(2) and 65B(4) of the Indian Evidence Act, 1872.

**Issues:**
1.  Whether the Inquiry Officer erred in dismissing the petitioner's application to file a CD of voice recordings and seek voice samples for verification.
2.  Whether the petitioner was denied a fair opportunity to prove her case due to the rejection of her application.

**Final Decision/Order:**
The High Court set aside the impugned order dated 18.01.2016 passed by the Inquiry Officer. The petitioner was directed to furnish a certificate under Section 65B of the Indian Evidence Act, 1872, within one week, and the Inquiry Officer was directed to consider the same in accordance with law. The court held that the principles of natural justice demand that an opportunity be given to the petitioner to prove her case.

---

### **Ignatius Tony Pereira vs. Travel Corporation (India) Pvt. Ltd.**

-   **Citation:** WRIT PETITION NO. 443 OF 2015
-   **Court:** High Court of Bombay at Goa
-   **Bench/Judge(s):** S. B. Shukre, J.
-   **Date of Order:** 10.02.2016

**Facts:**
The petitioner's application for permission to take certain certificates on record was rejected by the Magistrate. The petitioner contended that these certificates complied with the essential requirements of Section 65B of the Indian Evidence Act, 1872, a claim disputed by the respondents.

**Issues:**
1.  Whether the certificates produced by the petitioner complied with Section 65B of the Indian Evidence Act, 1872.
2.  Whether the rejection of the petitioner's application to admit the certificates prejudiced his case.

**Final Decision/Order:**
The High Court decided not to interfere with the impugned order but granted liberty to the petitioner to file fresh certificates that, in his opinion, were compliant with Section 65B. The admissibility of these fresh certificates would be decided by the Trial Court in accordance with law, with a hearing granted to both sides. The court clarified that this order did not permit the petitioner to make out an entirely new case regarding the production of electronic records as secondary evidence. The Rule was made absolute in these terms, and the writ petition was disposed of.

---

### **Shri Govind Sahay Goyal vs. Smt. Kusumlata Arora**

-   **Citation:** S.B. Civil Writ Petition No.16331/2023
-   **Court:** High Court of Judicature for Rajasthan, Bench at Jaipur
-   **Bench/Judge(s):** Hon'ble Mr. Justice Avneesh Jhingan
-   **Date of Order:** 13.05.2024

**Facts:**
The petition was filed aggrieved by an order dated 18.08.2023, which rejected the petitioner-tenant's application stating that a compact disc (CD) could not be considered in evidence in the absence of a certificate under Section 65B of the Indian Evidence Act, 1872.

**Issues:**
1.  Whether the rejection of the petitioner's application, which argued for the necessity of a Section 65B certificate for admitting a CD in evidence, was justified.

**Final Decision/Order:**
The petition was disposed of with liberty to the petitioner to raise the issue of admissibility of evidence at the stage of final argument. The learned counsel for the respondent submitted that if need be, the respondent would produce the certificate under Section 65B of the Act at an appropriate stage.

---

### **Himani Malhotra vs. IP College For Women**

-   **Citation:** W.P.(C) 4679/2016
-   **Court:** High Court of Delhi at New Delhi
-   **Bench/Judge(s):** Hon'ble Ms. Justice Hima Kohli
-   **Date of Order:** 24.05.2016

**Facts:**
This was a second round of litigation for the petitioner during the pendency of inquiry proceedings against her. She sought directions for three CDs containing voice recordings, along with her Section 65B certificate, to be sent to the Forensic Science Laboratory (FSL) for analysis, and for two employees of the respondent college to provide voice samples. This followed a previous order (in W.P.(C) 1101/2016) that had set aside an earlier order dismissing her application to file CDs and directed her to furnish a Section 65B certificate. Subsequent proceedings before the Inquiry Officer involved agreements and retractions regarding voice sample recording and the production of a master CD. The petitioner was unable to trace the original mobile phone but had produced a laptop and master CD.

**Issues:**
1.  Whether the Inquiry Officer should be directed to consider the petitioner's pending application dated 06.05.2015 in light of the changed circumstances, particularly the Management's refusal to get voice samples recorded.
2.  Whether the petitioner should be permitted to place on record secondary evidence for FSL opinion regarding recorded voices.

**Final Decision/Order:**
The High Court disposed of the petition with liberty granted to the petitioner to approach the learned Inquiry Officer directly with a request to take up and decide her pending application dated 6.5.2015 before proceeding further. The Inquiry Officer was directed to consider and decide such a request as per law. The court noted that if the petitioner had a request, she should make it directly to the Inquiry Officer instead of approaching the court.

---

### **Kumar Yallappa Vijapure vs. Shrenik Industries**

-   **Citation:** WRIT PETITION NO.15040 OF 2022
-   **Court:** High Court of Judicature at Bombay, Appellate Civil Jurisdiction
-   **Bench/Judge(s):** N. J. Jamadar, J.
-   **Date of Order:** 14.03.2023

**Facts:**
The petitioner challenged an order dated 29th July, 2022, passed by the Labour Court, Kolhapur, which held that the inquiry conducted against the petitioner was legal and proper, and the findings were not perverse. During the inquiry, the respondent produced a CD containing alleged call records of the petitioner admitting to submitting bogus medical bills. The petitioner objected to the admissibility of the CD due to the absence of a certificate under Section 65B of the Indian Evidence Act, 1872. The Labour Court held that strict rules of evidence are not applicable to departmental inquiries and that it could not go into the sufficiency or adequacy of the evidence at that stage.

**Issues:**
1.  Whether the Labour Court erred in holding that strict rules of evidence are not applicable to a departmental inquiry and in admitting the CD without a Section 65B certificate.
2.  Whether the Labour Court's findings regarding the legality and propriety of the inquiry and the non-perversity of its findings were justifiable.

**Final Decision/Order:**
The High Court found the Labour Court's findings justifiable, considering the material on record and the principles governing the consideration of whether an inquiry is vitiated or its findings are perverse. The court held that no case for interference was made out in its extraordinary writ jurisdiction. However, it clarified that in the proceedings before the Labour Court, the petitioner was at liberty to raise all contentions available in law. The petition was dismissed with this clarification.


</details>

---

### #14 — PASS — Newacts-Mapping

**Query:** `What is the equivalent of Section 498a of IPC in BNS?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Legislation  
**Time:** 18.0s  
**Response Length:** 1392 chars  
**Tokens:** 2502  

<details>
<summary>Full Response</summary>

## Equivalent of Section 498A IPC in Bharatiya Nyaya Sanhita 2023

The Bharatiya Nyaya Sanhita, 2023, has introduced **Section 85** as the equivalent of Section 498A of the Indian Penal Code, 1860. Both provisions address the offense of cruelty by a husband or his relatives towards a woman.

### Statutory Basis

**Section 498A of the Indian Penal Code, 1860:**
"Whoever, being the husband or the relative of the husband of a woman, subjects such woman to cruelty shall be punished with imprisonment for a term which may extend to three years and shall also be liable to fine."

**Section 85 of The Bharatiya Nyaya Sanhita, 2023:**
"Whoever, being the husband or the relative of the husband of a woman, subjects such woman to cruelty shall be punished with imprisonment for a term which may extend to three years and shall also be liable to fine."

### Analysis

The text of Section 85 of the Bharatiya Nyaya Sanhita, 2023, is identical to that of Section 498A of the Indian Penal Code, 1860. This indicates a direct continuation of the legal framework concerning cruelty by a husband or his relatives. The offense remains defined as subjecting a woman to cruelty by her husband or his relatives.

### Remedies

The punishment prescribed under both Section 498A of the IPC and Section 85 of the BNS is imprisonment for a term which may extend to three years, along with a liability to fine.


</details>

---

### #15 — PASS — Newacts-Mapping

**Query:** `Section 125 CrPC new law equivalent`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Legislation  
**Time:** 22.4s  
**Response Length:** 4151 chars  
**Tokens:** 16722  

<details>
<summary>Full Response</summary>

## Equivalent Provision for Section 125 of the Code of Criminal Procedure, 1973

The equivalent provision to Section 125 of the Code of Criminal Procedure, 1973 (CrPC) in the new legislation is **Section 144 of The Bharatiya Nagarik Suraksha Sanhita, 2023**.

### Statutory Basis

Both Section 125 of the CrPC, 1973, and Section 144 of The Bharatiya Nagarik Suraksha Sanhita, 2023, deal with the maintenance of wives, children, and parents. The core provisions and their intent remain largely the same, focusing on ensuring that individuals with sufficient means do not neglect their legal obligations to maintain their dependents.

**Key Similarities in Provisions:**

-   **Scope of Maintenance:** Both sections empower a Magistrate of the first class to order a person with sufficient means to provide a monthly allowance for the maintenance of:
    -   His wife, unable to maintain herself.
    -   His legitimate or illegitimate child, whether married or not, unable to maintain itself.
    -   His legitimate or illegitimate child (not being a married daughter) who has attained majority, if such child is unable to maintain itself due to physical or mental abnormality or injury.
    -   His father or mother, unable to maintain himself or herself.
-   **Interim Maintenance:** Both provisions allow for the Magistrate to order interim maintenance and expenses of proceedings during the pendency of the main application.
-   **Payment and Enforcement:** Both sections stipulate that the allowance is payable from the date of the order or the date of the application. Failure to comply with the order can lead to the issuance of a warrant for levying the amount due, similar to levying fines, and imprisonment for a term not exceeding one month or until payment.
-   **Conditions for Refusal of Maintenance:** Both sections outline conditions under which a wife may not be entitled to maintenance, such as living in adultery, refusing to live with her husband without sufficient reason, or living separately by mutual consent. The Magistrate has the power to cancel an order if these conditions are proven.
-   **Definition of "Wife":** Both sections include a woman who has been divorced by or has obtained a divorce from her husband and has not remarried within the definition of "wife" for the purposes of maintenance.

**Notable Differences/Clarifications in the New Provision (Section 144, BNSS, 2023):**

While the substance is largely the same, the wording in Section 144 of The Bharatiya Nagarik Suraksha Sanhita, 2023, is a direct replacement and continuation of the principles laid out in Section 125 of the CrPC, 1973. The new provision aims to consolidate and update the criminal procedure code.

### Analysis

The transition from Section 125 of the CrPC, 1973, to Section 144 of The Bharatiya Nagarik Suraksha Sanhita, 2023, signifies a legislative intent to maintain the existing framework for maintenance claims. This ensures continuity in the legal recourse available to individuals seeking maintenance from those legally obligated to provide it. The substantive rights and obligations under this provision remain largely unchanged, reflecting the enduring importance of ensuring basic sustenance for vulnerable family members.

### Remedies

The remedies available under Section 144 of The Bharatiya Nagarik Suraksha Sanhita, 2023, are identical to those previously available under Section 125 of the CrPC, 1973. These include:

-   **Order for Monthly Allowance:** A Magistrate can order the payment of a monthly allowance for maintenance.
-   **Interim Maintenance:** An order for interim maintenance and expenses can be made during the pendency of proceedings.
-   **Enforcement of Order:** If the order is not complied with, a warrant can be issued for the recovery of the due amount.
-   **Imprisonment:** In case of non-payment, imprisonment for a term not exceeding one month or until payment is made can be ordered.
-   **Cancellation of Order:** The order for maintenance can be cancelled under specific circumstances, such as the wife living in adultery or refusing to live with her husband without sufficient reason.


</details>

---

### #16 — PASS — Legislation-Single

**Query:** `Section 138 of Negotiable Instruments Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 8.9s  
**Response Length:** 1111 chars  
**Tokens:** 1798  

<details>
<summary>Full Response</summary>

Section 138 of The Negotiable Instruments Act, 1881, titled "Dishonour of cheque for insufficiency, etc., of funds in the account," states that if a cheque drawn on an account maintained by a person with a banker for payment of money to another person, for the discharge of a debt or liability, is returned unpaid due to insufficient funds or exceeding the arranged payment limit, that person shall be deemed to have committed an offense. This offense is punishable with imprisonment up to two years, or a fine up to twice the amount of the cheque, or both.

However, this section does not apply unless:
(a) The cheque is presented to the bank within six months of its date or its validity period, whichever is earlier.
(b) The payee or holder in due course gives a written notice to the drawer within thirty days of receiving information from the bank about the cheque being returned unpaid, demanding payment.
(c) The drawer fails to make the payment within fifteen days of receiving this notice.

An "Explanation" clarifies that "debt or other liability" means a legally enforceable debt or other liability.


</details>

---

### #17 — PASS — Legislation-Single

**Query:** `Section 9 of Arbitration Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 11.3s  
**Response Length:** 2004 chars  
**Tokens:** 1846  

<details>
<summary>Full Response</summary>

Section 9 of The Arbitration and Conciliation Act, 1996, titled "Interim measures, etc., by Court," outlines the provisions for a party to apply to a Court for specific measures. This application can be made before or during arbitral proceedings, or at any time after the making of an arbitral award but before its enforcement under section 36.

The applications that can be made to the Court under this section are:
1.  For the appointment of a guardian for a minor or a person of unsound mind for the purposes of arbitral proceedings.
2.  For an interim measure of protection in respect of the following matters:
    -   The preservation, interim custody, or sale of any goods that are the subject-matter of the arbitration agreement.
    -   Securing the amount in dispute in the arbitration.
    -   The detention, preservation, or inspection of any property or thing that is the subject-matter of the dispute in arbitration, or as to which any question may arise therein. This also includes authorizing any person to enter upon any land or building in the possession of any party for these purposes, or authorizing samples to be taken, observations to be made, or experiments to be tried, if necessary for obtaining full information or evidence.
    -   Interim injunction or the appointment of a receiver.
    -   Any other interim measure of protection that the Court deems just and convenient.

The Court has the same powers for making orders as it does for proceedings before it.

Furthermore, if a Court passes an order for an interim measure of protection under sub-section (1) before the commencement of arbitral proceedings, those proceedings must commence within ninety days from the date of the order, or within any extended time determined by the Court.

Once an arbitral tribunal has been constituted, the Court will generally not entertain an application under sub-section (1) unless it finds that circumstances exist that would render the remedy provided under section 17 ineffective.


</details>

---

### #18 — PASS — Legislation-Multi

**Query:** `Sections 44 and 45 of Transfer of Property Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 7.6s  
**Response Length:** 1297 chars  
**Tokens:** 1330  

<details>
<summary>Full Response</summary>

Section 44 of the Transfer of Property Act, 1977, titled "Transfer by one co-owner," states that if a co-owner of immovable property legally transfers their share or interest, the transferee acquires the transferor's right to joint possession or common enjoyment of the property, and the right to enforce partition. This is subject to any conditions and liabilities affecting the transferred share at the time of transfer. However, if the transferee of a share in a dwelling house belonging to an undivided family is not a member of that family, they are not entitled to joint possession or common enjoyment of the house.

Section 45 of the Transfer of Property Act, 1977, titled "Joint transfer for consideration," deals with situations where immovable property is transferred for consideration to two or more persons. If the consideration is paid from a common fund, the transferees are entitled to interests in the property identical to their interests in the fund, unless otherwise agreed. If the consideration is paid from separate funds, they are entitled to interests in proportion to their contributions, unless otherwise agreed. In the absence of evidence regarding their respective interests in the fund or their contributions, they are presumed to have equal interests in the property.


</details>

---

### #19 — PASS — Legislation-Multi

**Query:** `Explain Sections 3, 4 and 5 of Consumer Protection Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 7.5s  
**Response Length:** 1128 chars  
**Tokens:** 1650  

<details>
<summary>Full Response</summary>

**Section 3: Central Consumer Protection Council**

-   **Establishment:** The Central Government is mandated to establish the Central Consumer Protection Council, to be known as the Central Council, through a notification.
-   **Nature:** The Central Council functions as an advisory council.
-   **Composition:**
    -   The Chairperson is the Minister-in-charge of the Department of Consumer Affairs in the Central Government.
    -   It also includes other official or non-official members representing various interests, as prescribed.

**Section 4: Procedure for meetings of Central Council**

-   **Meeting Frequency:** The Central Council must meet when necessary, with at least one meeting held every year.
-   **Meeting Logistics:** Meetings are to be held at a time and place determined by the Chairperson.
-   **Business Procedure:** The Council must follow the prescribed procedure for conducting its business.

**Section 5: Objects of Central Council**

-   **Purpose:** The primary objective of the Central Council is to provide advice on the promotion and protection of consumers' rights as outlined in the Act.


</details>

---

### #20 — PASS — Legislation-Range

**Query:** `Sections 10 to 15 of Companies Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 17.9s  
**Response Length:** 12951 chars  
**Tokens:** 7521  

<details>
<summary>Full Response</summary>

Here are the provisions for Sections 10 to 15 of The Companies Act, 2013:

**Section 10: Effect of memorandum and articles**
(1) Subject to the provisions of this Act, the memorandum and articles shall, when registered, bind the company and the members thereof to the same extent as if they respectively had been signed by the company and by each member, and contained covenants on its and his part to observe all the provisions of the memorandum and of the articles.
(2) All monies payable by any member to the company under the memorandum or articles shall be a debt due from him to the company.

**Section 10A: Commencement of business, etc.**
(1) A company incorporated after the commencement of the Companies (Amendment) Act, 2019 and having a share capital shall not commence any business or exercise any borrowing powers unless-
(a) a declaration is filed by a director within a period of one hundred and eighty days of the date of incorporation of the company in such form and verified in such manner as may be prescribed, with the Registrar that every subscriber to the memorandum has paid the value of the shares agreed to be taken by him on the date of making of such declaration, and
(b) the company has filed with the Registrar a verification of its registered office as provided in sub-section (2) of section 12.
(2) If any default is made in complying with the requirements of this section, the company shall be liable to a penalty of fifty thousand rupees and every officer who is in default shall be liable to a penalty of one thousand rupees for each day during which such default continues but not exceeding an amount of one lakh rupees.
(3) Where no declaration has been filed with the Registrar under clause (a) of sub-section (1) within a period of one hundred and eighty days of the date of incorporation of the company and the Registrar has reasonable cause to believe that the company is not carrying on any business or operations, he may, without prejudice to the provisions of sub-section (2), initiate action for the removal of the name of the company from the register of companies under Chapter XVIII.

**Section 11: Omitted.**
omitted

**Section 12: Registered office of company**
(1) A company shall, within thirty days of its incorporation and at all times thereafter, have a registered office capable of receiving and acknowledging all communications and notices as may be addressed to it.
(2) The company shall furnish to the Registrar verification of its registered office within a period of thirty days of its incorporation in such manner as may be prescribed.
(3) Every company shall:
(a) paint or affix its name, and the address of its registered office, and keep the same painted or affixed, on the outside of every office or place in which its business is carried on, in a conspicuous position, in legible letters, and if the characters employed therefor are not those of the language or of one of the languages in general use in that locality, also in the characters of that language or of one of those languages,
(b) have its name engraved in legible characters on its seal, if any,
(c) get its name, address of its registered office and the Corporate Identity Number along with telephone number, fax number, if any, e-mail and website addresses, if any, printed in all its business letters, bill heads, letter papers and in all its notices and other official publications, and
(d) have its name printed on hundies, promissory notes, bills of exchange and such other documents as may be prescribed:
Provided that where a company has changed its name or names during the last two years, it shall paint or affix or print, as the case may be, along with its name, the former name or names so changed during the last two years as required under clauses (a) and (c):
Provided further that the words "One Person Company" shall be mentioned in brackets below the name of such company, wherever its name is printed, affixed or engraved.
(4) Notice of every change of the situation of the registered office, verified in the manner prescribed, after the date of incorporation of the company, shall be given to the Registrar within thirty days of the change, who shall record the same.
(5) Except on the authority of a special resolution passed by a company, the registered office of the company shall not be changed,
(a) in the case of an existing company, outside the local limits of any city, town or village where such office is situated at the commencement of this Act or where it may be situated later by virtue of a special resolution passed by the company, and
(b) in the case of any other company, outside the local limits of any city, town or village where such office is first situated or where it may be situated later by virtue of a special resolution passed by the company:
Provided that no company shall change the place of its registered office from the jurisdiction of one Registrar to the jurisdiction of another Registrar within the same State unless such change is confirmed by the Regional Director on an application made in this behalf by the company in the prescribed manner.
(6) The confirmation referred to in sub-section (5) shall be communicated within a period of thirty days from the date of receipt of application by the Regional Director to the company and the company shall file the confirmation with the Registrar within a period of sixty days of the date of confirmation who shall register the same and certify the registration within a period of thirty days from the date of filing of such confirmation.
(7) The certificate referred to in sub-section (6) shall be conclusive evidence that all the requirements of this Act with respect to change of registered office in pursuance of sub-section (5) have been complied with and the change shall take effect from the date of the certificate.
(8) If any default is made in complying with the requirements of this section, the company and every officer who is in default shall be liable to a penalty of one thousand rupees for every day during which the default continues but not exceeding one lakh rupees.
(9) If the Registrar has reasonable cause to believe that the company is not carrying on any business or operations, he may cause a physical verification of the registered office of the company in such manner as may be prescribed and if any default is found to be made in complying with the requirements of sub-section (1), he may without prejudice to the provisions of sub-section (8), initiate action for the removal of the name of the company from the register of companies under Chapter XVIII.

**Section 13: Alteration of memorandum**
(1) Save as provided in section 61, a company may, by a special resolution and after complying with the procedure specified in this section, alter the provisions of its memorandum.
(2) Any change in the name of a company shall be subject to the provisions of sub-sections (2) and (3) of section 4 and shall not have effect except with the approval of the Central Government in writing:
Provided that no such approval shall be necessary where the only change in the name of the company is the deletion therefrom, or addition thereto, of the word "Private", consequent on the conversion of any one class of companies to another class in accordance with the provisions of this Act.
(3) When any change in the name of a company is made under sub-section (2), the Registrar shall enter the new name in the register of companies in place of the old name and issue a fresh certificate of incorporation with the new name and the change in the name shall be complete and effective only on the issue of such a certificate.
(4) The alteration of the memorandum relating to the place of the registered office from one State to another shall not have any effect unless it is approved by the Central Government on an application in such form and manner as may be prescribed.
(5) The Central Government shall dispose of the application under sub-section (4) within a period of sixty days and before passing its order may satisfy itself that the alteration has the consent of the creditors, debenture-holders and other persons concerned with the company or that the sufficient provision has been made by the company either for the due discharge of all its debts and obligations or that adequate security has been provided for such discharge.
(6) Save as provided in section 64, a company shall, in relation to any alteration of its memorandum, file with the Registrar:
(a) the special resolution passed by the company under sub-section (1),
(b) the approval of the Central Government under sub-section (2), if the alteration involves any change in the name of the company.
(7) Where an alteration of the memorandum results in the transfer of the registered office of a company from one State to another, a certified copy of the order of the Central Government approving the alteration shall be filed by the company with the Registrar of each of the States within such time and in such manner as may be prescribed, who shall register the same, and the Registrar of the State where the registered office is being shifted to, shall issue a fresh certificate of incorporation indicating the alteration.
(8) A company, which has raised money from public through prospectus and still has any unutilised amount out of the money so raised, shall not change its objects for which it raised the money through prospectus unless a special resolution is passed by the company and:
(i) the details, as may be prescribed, in respect of such resolution shall also be published in the newspapers (one in English and one in vernacular language) which is in circulation at the place where the registered office of the company is situated and shall also be placed on the website of the company, if any, indicating therein the justification for such change,
(ii) the dissenting shareholders shall be given an opportunity to exit by the promoters and shareholders having control in accordance with regulations to be specified by the Securities and Exchange Board.
(9) The Registrar shall register any alteration of the memorandum with respect to the objects of the company and certify the registration within a period of thirty days from the date of filing of the special resolution in accordance with clause (a) of sub-section (6) of this section.
(10) No alteration made under this section shall have any effect until it has been registered in accordance with the provisions of this section.
(11) Any alteration of the memorandum, in the case of a company limited by guarantee and not having a share capital, purporting to give any person a right to participate in the divisible profits of the company otherwise than as a member, shall be void.

**Section 14: Alteration of articles**
(1) Subject to the provisions of this Act and the conditions contained in its memorandum, if any, a company may, by a special resolution, alter its articles including alterations having the effect of conversion of-
(a) a private company into a public company, or
(b) a public company into a private company:
Provided that where a company being a private company alters its articles in such a manner that they no longer include the restrictions and limitations which are required to be included in the articles of a private company under this Act, the company shall, as from the date of such alteration, cease to be a private company:
Provided further that any alteration having the effect of conversion of a public company into a private company shall not be valid unless it is approved by an order of the Central Government on an application made in such form and manner as may be prescribed:
Provided also that any application pending before the Tribunal, as on the date of commencement of the Companies (Amendment) Act, 2019, shall be disposed of by the Tribunal in accordance with the provisions applicable to it before such commencement.
(2) Every alteration of the articles under this section and a copy of the order of the Central Government approving the alteration as per sub-section (1) shall be filed with the Registrar, together with a printed copy of the altered articles, within a period of fifteen days in such manner as may be prescribed, who shall register the same.
(3) Any alteration of the articles registered under sub-section (2) shall, subject to the provisions of this Act, be valid as if it were originally in the articles.

**Section 15: Alteration of memorandum or articles to be noted in every copy**
(1) Every alteration made in the memorandum or articles of a company shall be noted in every copy of the memorandum or articles, as the case may be.
(2) If a company makes any default in complying with the provisions of sub-section (1), the company and every officer who is in default shall be liable to a penalty of one thousand rupees for every copy of the memorandum or articles issued without such alteration.


</details>

---

### #21 — PASS — Legislation-Topic

**Query:** `director duties under companies act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation, Judgment  
**Time:** 16.6s  
**Response Length:** 2606 chars  
**Tokens:** 11296  

<details>
<summary>Full Response</summary>

## Director Duties Under the Companies Act, 2013

This response synthesizes the available information regarding director duties under the Companies Act, 2013, focusing on the statutory requirements related to Director Identification Numbers (DIN).

### Statutory Basis

The Companies Act, 2013, outlines specific provisions concerning the Director Identification Number (DIN), which is a prerequisite for individuals acting as directors.

-   **Section 156 of The Companies Act, 2013** mandates that: "Every existing director shall, within one month of receiving their Director Identification Number (DIN) from the Central Government, intimate this DIN to the company or all companies where they are a director."

-   **Section 155 of The Companies Act, 2013** establishes a prohibition against multiple DINs: "any individual who has already been allotted a DIN under section 154 from applying for, obtaining, or possessing another DIN."

-   **Section 158 of The Companies Act, 2013** requires the disclosure of DIN in official filings: "every person or company, when furnishing any return, information, or particulars required under the Act, must mention the Director Identification Number if such information relates to or references a director."

### Case Laws

The provided information does not contain any judgments or case law specifically addressing director duties under the Companies Act, 2013. The available judgment results pertain to company amalgamation schemes under the Companies Act, 1956, and are therefore not relevant to this query.

### Analysis

The statutory provisions highlighted above underscore the importance of a unique and registered Director Identification Number for all individuals acting as directors. These sections aim to ensure transparency and accountability by establishing a clear identification system for directors. The prohibition against possessing multiple DINs prevents potential circumvention of regulations and ensures that a director's responsibilities are tied to a single, verifiable identity. Furthermore, the requirement to disclose the DIN in all official filings reinforces this identification framework, making it easier for regulatory bodies and stakeholders to track and verify director information.

### Remedies

The provided information does not detail specific remedies or penalties for the contravention of these director duties related to DIN. However, non-compliance with statutory provisions under the Companies Act, 2013, generally carries consequences, which can include fines, penalties, or other legal actions as prescribed by the Act.


</details>

---

### #22 — PASS — Legislation-Topic

**Query:** `tenant rights in rent control legislation`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation, Judgment  
**Time:** 31.9s  
**Response Length:** 10003 chars  
**Tokens:** 61428  

<details>
<summary>Full Response</summary>

Tenant rights under rent control legislation are multifaceted, encompassing protection against arbitrary eviction, specific conditions for rent payment and deposit, rights concerning building amenities and repairs, and provisions for restoration to possession and compensation. These rights are established through statutory provisions and further interpreted and reinforced by judicial pronouncements.

## Statutory Basis for Tenant Rights

The **Tripura Buildings (Lease and Rent Control) Act, 1975**, outlines several key tenant rights:

### Protection Against Eviction

-   A tenant cannot be evicted except in accordance with the provisions of the Act (Section 12(1)).

### Conditions for Eviction

Landlords can seek eviction under specific circumstances, but tenants have corresponding protections:

-   **Non-payment of Rent:** Eviction is permissible if rent is not paid or tendered within fifteen days of the due date (or by the last day of the following month if no agreement exists) and this default has continued for three months within a twelve-month period. However, the landlord must first issue a registered notice of default, and the tenant has fifteen days from receipt to pay the rent, interest (6% per annum), and postal charges (Section 12(2)(b) & proviso).
-   **Denial of Landlord's Title or Claim of Permanent Tenancy:** If a tenant denies the landlord's title or claims permanent tenancy, the Rent Control Court will determine the bonafides of such a claim. If found not bonafide, the landlord may seek eviction in a civil court (Section 12(1), second proviso).
-   **Sub-letting or Unauthorized Transfer:** Eviction can occur if the tenant, without written consent, transfers lease rights or sub-lets the entire building or any part thereof, unless the lease permits it (Section 12(4)(i)).
-   **Damage to the Building:** Eviction is possible if the tenant uses the building in a manner that materially and permanently destroys or reduces its value or utility (Section 12(4)(ii)).
-   **Sufficient Alternative Accommodation:** If the tenant possesses or acquires reasonably sufficient alternative accommodation in the same town or village (Section 12(4)(iii)).
-   **Cessation of Occupation:** If the tenant ceases to occupy the building continuously for six months without reasonable cause (Section 12(4)(v)).
-   **Reconstruction:** If the building requires reconstruction and the landlord has a bonafide intention to reconstruct, provided they possess the necessary plans, licenses, and ability to rebuild, and the proposal is not a pretext for eviction (Section 12(4)(iv)).
-   **Landlord's Bonafide Need:** A landlord can seek eviction for their own occupation or that of a dependent family member, subject to conditions such as not owning another suitable building in the same locality and not evicting a tenant reliant on the building for livelihood if no other suitable premises are available (Section 12(3) & provisos).
-   **Specific Period Tenancy:** A landlord cannot seek eviction before the expiry of a tenancy agreed for a specified period (Section 12(9)).
-   **Armed Forces Personnel:** Special provisions allow members of the armed forces or their widows to recover possession for bonafide occupation under specific conditions and certifications (Section 12(11A)).

### Rights Related to Rent and Deposits

-   **Payment/Deposit of Rent During Eviction Proceedings:** Tenants contesting eviction must pay or deposit all admitted arrears of rent and continue to pay or deposit future rent until proceedings conclude. Failure to comply can lead to eviction (Section 13(1) & (3)).
-   **Time for Deposit:** The Rent Control Court can set deadlines for depositing arrears (not less than 45 days from the order) and future rent (not less than two weeks from when it becomes due) (Section 13(2) & proviso).
-   **Withdrawal of Deposited Rent:** Landlords can withdraw deposited rent upon application (Section 13(4)).

### Rights Regarding Amenities and Repairs

-   **Protection from Withholding Amenities:** Landlords cannot, without just or sufficient cause, cut off or withhold any amenities enjoyed by the tenant (Section 14(1)).
-   **Remedy for Withheld Amenities:** Tenants can apply to the Rent Control Court if amenities are cut off. The court can issue interim orders for restoration and, after inquiry, order permanent restoration (Section 14(2), (3), & (4)).
-   **Compensation for Frivolous Withholding of Amenities:** Tenants may be awarded compensation if amenities are withheld frivolously or vexatiously by the landlord (Section 14(5)(b)).
-   **Landlord's Duty for Repairs:** Landlords are obligated to perform periodical maintenance and necessary repairs. If they fail to do so after notice, tenants can apply to the Rent Control Court to have repairs done and deduct the cost (up to a specified amount) from the rent (Section 18(1)).

### Other Rights

-   **Restoration to Possession:** If a landlord obtains possession for bonafide need but fails to occupy it within one month or vacates it within six months without reasonable cause, the evicted tenant can apply to be restored to possession (Section 12(12)).
-   **Compensation for Frivolous Eviction Applications:** If an eviction application is found to be frivolous or vexatious, the Rent Control Court may order the landlord to pay compensation to the tenant (Section 12(14)).
-   **Continuation of Tenancy:** If an eviction application is rejected, the tenancy continues on the same terms and conditions and cannot be terminated by the landlord except on grounds specified in the Act (Section 12(15)).
-   **Rent Increase Limitations:** Increases in taxes and cesses by the landlord are recoverable from the tenant, but not exceeding 5% of the original fair rent (Section 7(1) & proviso).

### Exemptions and Special Cases

-   Eviction provisions do not apply to tenants whose landlord is the State Government, Central Government, or a notified public authority (Section 12(1), first proviso).
-   Buildings let to recognized educational institutions are exempt from eviction as long as recognition continues (Section 12(11)(ii)).
-   Tenants engaged in notified essential services are protected from eviction unless the landlord is also in a notified essential service and requires the building for their own occupation (Section 12(11)(i)).

## Judicial Interpretation of Tenant Rights

Judgments from various High Courts and the Supreme Court have further clarified and expanded upon tenant rights under rent control legislation.

### Right to Essential Services and Utilities

-   In **Mahendra Kumar Rawat vs. Radheshyam Gupta (Rajasthan High Court)**, it was held that a tenant is entitled to the restoration or installation of an electricity connection, recognizing it as a basic fundamental requirement and right.

### Overriding Effect of Rent Control Laws

-   The **Kerala High Court** in **N.P. Pushpangadan & Ors. vs. The Federal Bank Ltd. & Ors.** ruled that the Securitisation and Reconstruction of Financial Assets and Enforcement of Security Interest Act, 2002 (SARFAESI Act) does **not** have an overriding effect over the Kerala Buildings (Lease and Rent Control) Act, 1965. Tenants inducted before the creation of a mortgage are protected from summary eviction under the SARFAESI Act and can approach the Debts Recovery Tribunal if their rights are affected.

### Tenancy Inheritance and Legal Heirs

-   The **Delhi High Court** in **Neelima Singh vs. Ashit Kumar Bhattacharya**, referencing a Full Bench judgment, held that legal representatives must be brought on record to defend eviction proceedings on behalf of a deceased tenant. If the legal heir has inherited tenancy rights, they are protected by the relevant Rent Control Act.
-   Similarly, the **Kerala High Court** in **Rexine House & Anr. vs. Paul Varghese & Ors.** clarified that the legal heir of a deceased tenant does not require the landlord's consent for the renewal of a trade license from the Municipality, as the Municipality cannot adjudicate on issues of subletting, which fall under the Rent Control Authority's jurisdiction.

### Payment of Rent and Default

-   In **Anilkumar vs. Dheevara Education Trust (Kerala High Court)**, it was held that payment of rent to an unauthorized entity does not absolve the tenant of their liability to pay rent to the rightful landlord, and such tenants may be liable for eviction for default. However, tenants were given liberty to recover erroneously paid amounts.

### Material Alterations and Eviction Grounds

-   The **Supreme Court** in **Babu Manmohan Das Shah & Ors. vs. Bishun Das** interpreted "material alterations" under the U.P. (Temporary) Control of Rent and Eviction Act, 1947, as grounds for eviction even if they do not substantially diminish the property's value. Alterations that change the form and structure of the accommodation without the landlord's permission can lead to eviction.

### Continuation of Tenancy and Trade Licenses

-   The **Kerala High Court** in **Ashokan P.K. & Anr. vs. Parakkadavu Grama Panchayath & Ors.** ruled that a tenant holding over under the Kerala Buildings Lease and Rent Control Act, 1965, is entitled to continue in possession, and a Panchayat cannot insist on the landlord's consent for renewing a trade license. Eviction of a statutory tenant can only occur through the procedures laid down in the Rent Control Act.

### Fair Rent Fixation

-   In **Kunnath Balakrishnan & Ors. vs. Sheenath A. & Ors. (Kerala High Court)**, the Court reiterated principles for fixing fair rent, considering factors like inflation, cost of living, demand, construction costs, and prevailing rents. The Act aims to balance landlord and tenant interests, and landlords have a right to fair rent.

These statutory provisions and judicial interpretations collectively establish a robust framework for protecting tenant rights within rent-controlled environments, ensuring security of tenure and fair treatment.


</details>

---

### #23 — PASS — Legislation-Subsection

**Query:** `Section 138(1) of Negotiable Instruments Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 11.5s  
**Response Length:** 971 chars  
**Tokens:** 1771  

<details>
<summary>Full Response</summary>

Section 138 of The Negotiable Instruments Act, 1881, states that if a cheque drawn on an account maintained by a person with a banker for payment of money to another person, for the discharge of a debt or liability, is returned unpaid due to insufficient funds or exceeding the arranged limit, that person shall be deemed to have committed an offense. This offense is punishable with imprisonment up to two years, a fine up to twice the amount of the cheque, or both.

However, this section does not apply unless:
(a) The cheque is presented within six months of its date or its validity period, whichever is earlier.
(b) The payee or holder in due course gives a written notice to the drawer within thirty days of receiving information from the bank about the cheque being unpaid.
(c) The drawer fails to pay the amount within fifteen days of receiving the notice.

The explanation clarifies that "debt or other liability" means a legally enforceable debt or liability.


</details>

---

### #24 — PASS — Edge-Nearby

**Query:** `What comes after section 35 of BNS?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 7.6s  
**Response Length:** 162 chars  
**Tokens:** 620  

<details>
<summary>Full Response</summary>

Section 36 of The Bharatiya Nyaya Sanhita, 2023, titled "Right of private defence against the act of a person with mental illness, etc.", comes after Section 35.


</details>

---

### #25 — PASS — Edge-Vague

**Query:** `theft`  
**Expected Agent:** Any  
**Actual Agent(s):** Legal_Concepts  
**Time:** 8.9s  
**Response Length:** 1940 chars  
**Tokens:** 551  

<details>
<summary>Full Response</summary>

This query is about a legal explanation.

### Theft in India

Theft, as defined under Indian law, is a criminal offense.

**Definition:**
Section 378 of the Indian Penal Code, 1860 (IPC) defines theft as:
"Whoever, intending to take dishonestly any moveable property out of the possession of any person without that person's consent, moves that property in order to such taking, is said to commit theft."

**Key Elements:**
1.  **Dishonest Intention:** The act must be done with the intention of causing wrongful gain to one person or wrongful loss to another.
2.  **Moveable Property:** The subject of theft must be something that can be moved from one place to another. Immovable property cannot be stolen.
3.  **Out of Possession:** The property must be taken from the possession of another person.
4.  **Without Consent:** The taking must be without the owner's or possessor's consent.
5.  **Moving the Property:** Even the slightest movement of the property with the dishonest intention constitutes the act of theft.

**Punishment:**
Section 379 of the IPC prescribes the punishment for theft:
"Whoever commits theft shall be punished with imprisonment of either description for a term which may extend to three years, or with fine, or with both."

**Aggravated Forms of Theft:**
The IPC also defines more serious forms of theft with enhanced punishments, such as:
-   **Theft in a dwelling-house, etc. (Section 380):** Punishable with imprisonment up to seven years and fine.
-   **Theft after preparation made for causing death, hurt or restraint (Section 382):** Punishable with rigorous imprisonment up to ten years and fine.

**References:**
-   Indian Penal Code, 1860 (Sections 378, 379, 380, 382).


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

---

### #26 — PASS — Edge-NonSection

**Query:** `Rule 3 of Maharashtra Rent Control Rules`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 10.2s  
**Response Length:** 176 chars  
**Tokens:** 727  

<details>
<summary>Full Response</summary>

Rule 3 of the Maharashtra Rent Control Rules, 2017, states that a receipt given under section 31 for the amount of rent received in respect of any premises shall be in Form I.


</details>

---

### #27 — PASS — Edge-ManySection

**Query:** `Sections 302, 304, 304a, 307 and 376 of IPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 16.7s  
**Response Length:** 9742 chars  
**Tokens:** 5548  

<details>
<summary>Full Response</summary>

**Section 302 of The Indian Penal Code 1860 (Punishment for murder)**

-   **Old Provision:** Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.
-   **New Provision (Section 103 of Bharatiya Nyaya Sanhita, 2023):**
    -   (1) Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine.
    -   (2) When a group of five or more persons acting in concert commits murder on the ground of race, caste or community, sex, place of birth, language, personal belief or any other similar ground each member of such group shall be punished with death or with imprisonment for life, and shall also be liable to fine.

**Section 304 of The Indian Penal Code 1860 (Punishment for culpable homicide not amounting to murder)**

-   **Old Provision:** Whoever commits culpable homicide not amounting to murder, shall be punished with imprisonment for life, or imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine, if the act by which the death is caused is done with the intention of causing death, or of causing such bodily injury as is likely to cause death; or with imprisonment of either description for a term which may extend to ten years, or with fine, or with both, if the act is done with the knowledge that it is likely to cause death, but without any intention to cause death, or to cause such bodily injury as is likely to cause death.
-   **New Provision (Section 105 of Bharatiya Nyaya Sanhita, 2023):** Whoever commits culpable homicide not amounting to murder, shall be punished with imprisonment for life, or imprisonment of either description for a term which shall not be less than five years but which may extend to ten years, and shall also be liable to fine, if the act by which the death is caused is done with the intention of causing death, or of causing such bodily injury as is likely to cause death; or with imprisonment of either description for a term which may extend to ten years and with fine, if the act is done with the knowledge that it is likely to cause death, but without any intention to cause death, or to cause such bodily injury as is likely to cause death.

**Section 304A of The Indian Penal Code 1860 (Causing death by negligence)**

-   There is no direct corresponding section provided in the context for Section 304A of the Indian Penal Code 1860.

**Section 307 of The Indian Penal Code 1860 (Attempt to murder)**

-   **Old Provision:** Whoever does any act with such intention or knowledge, and under such circumstances that, if he by that act caused death, he would be guilty of murder, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine; and if hurt is caused to any person by such act, the offender shall be liable either to imprisonment for life, or to such punishment as is hereinbefore mentioned. Attempts by life convicts. Where any person offending under this section is under sentence of imprisonment for life, he may, if hurt is caused, be punished with death.
-   **New Provision (Section 109 of Bharatiya Nyaya Sanhita, 2023):**
    -   (1) Whoever does any act with such intention or knowledge, and under such circumstances that, if he by that act caused death, he would be guilty of murder, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine; and if hurt is caused to any person by such act, the offender shall be liable either to imprisonment for life, or to such punishment as is hereinbefore mentioned.
    -   (2) When any person offending under sub-section (1) is under sentence of imprisonment for life, he may, if hurt is caused, be punished with death or with imprisonment for life, which shall mean the remainder of that person’s natural life.

**Section 376 of The Indian Penal Code 1860 (Punishment for rape)**

-   **Old Provision (Section 376(1) and (2) of the Indian Penal Code, 1860):**
    -   (1) Whoever, except in the cases provided for in sub-section (2), commits rape, shall be punished with rigorous imprisonment of either description for a term which shall not be less than ten years, but which may extend to imprisonment for life, and shall also be liable to fine.
    -   (2) Whoever, (a) being a police officer, commits rape (i) within the limits of the police station to which such police officer is appointed; or (ii) in the premises of any station house; or (iii) on a woman in such police officer's custody or in the custody of a police officer subordinate to such police officer; or (b) being a public servant, commits rape on a woman in such public servant's custody or in the custody of a public servant subordinate to such public servant; or (c) being a member of the armed forces deployed in an area by the Central or a State Government commits rape in such area; or (d) being on the management or on the staff of a jail, remand home or other place of custody established by or under any law for the time being in force or of a women's or children's institution, commits rape on any inmate of such jail, remand home, place or institution; or (e) being on the management or on the staff of a hospital, commits rape on a woman in that hospital; or (f) being a relative, guardian or teacher of, or a person in a position of trust or authority towards the woman, commits rape on such woman; or (g) commits rape during communal or sectarian violence; or (h) commits rape on a woman knowing her to be pregnant; or (j) commits rape, on a woman incapable of giving consent; or (k) being in a position of control or dominance over a woman, commits rape on such woman; or (l) commits rape on a woman suffering from mental or physical disability; or (m) while committing rape causes grievous bodily harm or maims or disfigures or endangers the life of a woman; or (n) commits rape repeatedly on the same woman, shall be punished with rigorous imprisonment for a term which shall not be less than ten years, but which may extend to imprisonment for life, which shall mean imprisonment for the remainder of that person's natural life, and shall also be liable to fine.
-   **New Provision (Section 64 of Bharatiya Nyaya Sanhita, 2023):**
    -   (1) Whoever, except in the cases provided for in sub-section (2), commits rape, shall be punished with rigorous imprisonment of either description for a term which shall not be less than ten years, but which may extend to imprisonment for life, and shall also be liable to fine.
    -   (2) Whoever, (a) being a police officer, commits rape (i) within the limits of the police station to which such police officer is appointed; or (ii) in the premises of any station house; or (iii) on a woman in such police officer’s custody or in the custody of a police officer subordinate to such police officer; or (b) being a public servant, commits rape on a woman in such public servant’s custody or in the custody of a public servant subordinate to such public servant; or (c) being a member of the armed forces deployed in an area by the Central Government or a State Government commits rape in such area; or (d) being on the management or on the staff of a jail, remand home or other place of custody established by or under any law for the time being in force or of a women’s or children’s institution, commits rape on any inmate of such jail, remand home, place or institution; or (e) being on the management or on the staff of a hospital, commits rape on a woman in that hospital; or (f) being a relative, guardian or teacher of, or a person in a position of trust or authority towards the woman, commits rape on such woman; or (g) commits rape during communal or sectarian violence; or (h) commits rape on a woman knowing her to be pregnant; or (i) commits rape, on a woman incapable of giving consent; or (j) being in a position of control or dominance over a woman, commits rape on such woman; or (k) commits rape on a woman suffering from mental or physical disability; or (l) while committing rape causes grievous bodily harm or maims or disfigures or endangers the life of a woman; or (m) commits rape repeatedly on the same woman, shall be punished with rigorous imprisonment for a term which shall not be less than ten years, but which may extend to imprisonment for life, which shall mean imprisonment for the remainder of that person’s natural life, and shall also be liable to fine.

-   **Old Provision (Section 376(3) of the Indian Penal Code, 1860):** Whoever, commits rape on a woman under sixteen years of age shall be punished with rigorous imprisonment for a term which shall not be less than twenty years, but which may extend to imprisonment for life, which shall mean imprisonment for the remainder of that person's natural life, and shall also be liable to fine: Provided that such fine shall be just and reasonable to meet the medical expenses and rehabilitation of the victim: Provided further that any fine imposed under this sub-section shall be paid to the victim.
-   **New Provision (Section 65(1) of Bharatiya Nyaya Sanhita, 2023):** Whoever, commits rape on a woman under sixteen years of age shall be punished with rigorous imprisonment for a term which shall not be less than twenty years, but which may extend to imprisonment for life, which shall mean imprisonment for the remainder of that person’s natural life, and shall also be liable to fine: Provided that such fine shall be just and reasonable to meet the medical expenses and rehabilitation of the victim: Provided further that any fine imposed under this sub-section shall be paid to the victim.


</details>

---
