# Test Report
**Date:** 2026-02-21 21:15:02  
**Server:** `http://localhost:5000/pyapi/search`  
**Total Prompts:** 27  
**Total Time:** 736.7s  

## Summary

| Status | Count |
|--------|-------|
| **PASS** | **27** |
| **WEAK** | **0** |
| **FAIL** | **0** |
| **ERROR** | **0** |

## Timing Statistics

| Metric | Value |
|--------|-------|
| Average | 27.7s |
| Min | 8.3s |
| Max | 100.3s |
| Median | 21.1s |
| Total | 749.0s |

## Results Overview

| # | Status | Category | Prompt | Agent(s) | Time | Resp Len | Tokens |
|---|--------|----------|--------|----------|------|----------|--------|
| 1 | **PASS** | Newacts-Single | Section 302 of IPC | Newacts | 21.9s | 744 | 839 |
| 2 | **PASS** | Newacts-Single | Section 35 of BNS | Newacts | 22.6s | 580 | 706 |
| 3 | **PASS** | Newacts-Single | What is Section 438 of CrPC? | Newacts | 19.8s | 2,043 | 7,003 |
| 4 | **PASS** | Newacts-Multi | Sections 302 and 307 of IPC | Newacts | 11.8s | 4,316 | 2,528 |
| 5 | **PASS** | Newacts-Multi | Explain Sections 64, 65 and 66 of BSA | Newacts | 19.5s | 2,121 | 1,082 |
| 6 | **PASS** | Newacts-Multi | Compare Section 154 and Section 161 of CrPC | Newacts, Judgment | 29.5s | 5,083 | 16,728 |
| 7 | **PASS** | Newacts-Topic | punishment for theft in BNS | Newacts | 13.1s | 4,564 | 5,462 |
| 8 | **PASS** | Newacts-Topic | bail provisions under BNSS | Newacts | 38.8s | 20,700 | 11,475 |
| 9 | **PASS** | Newacts-Topic | electronic evidence rules in BSA | Newacts, Judgment | 26.5s | 7,862 | 16,595 |
| 10 | **PASS** | Newacts-Nearby | What does Section 100 of BNS say? | Newacts | 27.0s | 2,054 | 2,869 |
| 11 | **PASS** | Newacts-Nearby | Section 528 of BNSS | Newacts | 13.6s | 413 | 481 |
| 12 | **PASS** | Newacts-Subsection | Section 3(5) of Bharatiya Nyaya Sanhita | Newacts | 15.2s | 250 | 4,712 |
| 13 | **PASS** | Newacts-Subsection | IEA Section 65b | Newacts, Judgment | 21.1s | 10,641 | 6,765 |
| 14 | **PASS** | Newacts-Mapping | What is the equivalent of Section 498a of IPC in B... | Newacts, Legislation | 17.6s | 1,725 | 2,516 |
| 15 | **PASS** | Newacts-Mapping | Section 125 CrPC new law equivalent | Newacts, Legislation | 38.6s | 10,188 | 17,984 |
| 16 | **PASS** | Legislation-Single | Section 138 of Negotiable Instruments Act | Legislation | 8.3s | 890 | 1,760 |
| 17 | **PASS** | Legislation-Single | Section 9 of Arbitration Act | Legislation | 34.9s | 1,928 | 1,846 |
| 18 | **PASS** | Legislation-Multi | Sections 44 and 45 of Transfer of Property Act | Legislation | 18.6s | 1,657 | 1,424 |
| 19 | **PASS** | Legislation-Multi | Explain Sections 3, 4 and 5 of Consumer Protection... | Legislation | 37.5s | 1,110 | 1,645 |
| 20 | **PASS** | Legislation-Range | Sections 10 to 15 of Companies Act | Legislation | 68.6s | 12,911 | 7,517 |
| 21 | **PASS** | Legislation-Topic | director duties under companies act | Legislation, Judgment | 22.6s | 2,431 | 11,286 |
| 22 | **PASS** | Legislation-Topic | tenant rights in rent control legislation | Legislation, Judgment | 100.3s | 11,229 | 64,130 |
| 23 | **PASS** | Legislation-Subsection | Section 138(1) of Negotiable Instruments Act | Legislation | 62.5s | 483 | 1,672 |
| 24 | **PASS** | Edge-Nearby | What comes after section 35 of BNS? | Newacts | 14.7s | 162 | 620 |
| 25 | **PASS** | Edge-Vague | theft | Legal_Concepts | 15.7s | 2,952 | 731 |
| 26 | **PASS** | Edge-NonSection | Rule 3 of Maharashtra Rent Control Rules | Legislation | 9.8s | 176 | 727 |
| 27 | **PASS** | Edge-ManySection | Sections 302, 304, 304a, 307 and 376 of IPC | Newacts | 18.9s | 14,732 | 6,724 |

---

## Detailed Results

### #1 - PASS - Newacts-Single

**Query:** `Section 302 of IPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 21.9s  
**Response Length:** 744 chars  
**Tokens:** 839  

<details>
<summary>Response Preview</summary>

**Section 302 of The Indian Penal Code 1860 (IPC)**

**Section Name:** Punishment for murder

**Old Provision:** Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.

**New Provision (Bharatiya Nyaya Sanhita, 2023):**

-   **Section 103:**
    -   (1) Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine.
    -   (2) When a group of five or more persons acting in concert commits m...

</details>

---

### #2 - PASS - Newacts-Single

**Query:** `Section 35 of BNS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 22.6s  
**Response Length:** 580 chars  
**Tokens:** 706  

<details>
<summary>Response Preview</summary>

Section 35 of The Bharatiya Nyaya Sanhita, 2023, titled "Right of private defence of the body and of property," states that every person has a right, subject to the restrictions in section 37, to defend:
(a) his own body, and the body of any other person, against any offence affecting the human body;
(b) the property, whether movable or immovable, of himself or of any other person, against any act which is an offence falling under the definition of theft, robbery, mischief or criminal trespass, ...

</details>

---

### #3 - PASS - Newacts-Single

**Query:** `What is Section 438 of CrPC?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 19.8s  
**Response Length:** 2,043 chars  
**Tokens:** 7,003  

<details>
<summary>Response Preview</summary>

Section 438 of The Code of Criminal Procedure, 1973, titled "Direction for grant of bail to person apprehending arrest," states:

(1) When any person has reason to believe that he may be arrested on an accusation of having committed a non-bailable offence, he may apply to the High Court or the Court of Session for a direction under this section; and that Court may, if it thinks fit, direct that in the event of such arrest, he shall be released on bail.

(2) When the High Court or the Court of Se...

</details>

---

### #4 - PASS - Newacts-Multi

**Query:** `Sections 302 and 307 of IPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 11.8s  
**Response Length:** 4,316 chars  
**Tokens:** 2,528  

<details>
<summary>Response Preview</summary>

**Section 302 of The Indian Penal Code 1860 (Punishment for murder)**

-   **Old Provision:** Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.
-   **New Provision (Section 103 of Bharatiya Nyaya Sanhita, 2023):**
    -   (1) Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine.
    -   (2) When a group of five or more persons acting in concert commits murder on the ground of ...

</details>

---

### #5 - PASS - Newacts-Multi

**Query:** `Explain Sections 64, 65 and 66 of BSA`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 19.5s  
**Response Length:** 2,121 chars  
**Tokens:** 1,082  

<details>
<summary>Response Preview</summary>

Here are the explanations for Sections 64, 65, and 66 of The Bharatiya Sakshya Adhiniyam, 2023:

**Section 64: Rules as to notice to produce**

This section deals with the requirement of giving notice before admitting secondary evidence of a document. Secondary evidence of the contents of a document (as referred to in clause (a) of section 60) cannot be presented unless the party intending to use this secondary evidence has first given notice to the opposing party (or their advocate or represent...

</details>

---

### #6 - PASS - Newacts-Multi

**Query:** `Compare Section 154 and Section 161 of CrPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Judgment  
**Time:** 29.5s  
**Response Length:** 5,083 chars  
**Tokens:** 16,728  

<details>
<summary>Response Preview</summary>

This response synthesizes the provided information to compare Section 154 and Section 161 of the Code of Criminal Procedure, 1973 (CrPC).

## Comparison of Section 154 and Section 161 of the Code of Criminal Procedure, 1973

Both Section 154 and Section 161 of the CrPC are crucial provisions related to the initial stages of criminal investigation by the police. However, they serve distinct purposes and operate at different points in the investigative process.

### Statutory Basis

**Section 154:...

</details>

---

### #7 - PASS - Newacts-Topic

**Query:** `punishment for theft in BNS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 13.1s  
**Response Length:** 4,564 chars  
**Tokens:** 5,462  

<details>
<summary>Response Preview</summary>

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
    (2) Whoever commits any petty organised crime shall be punished with imprisonment for a term which shall not be less than one year but which may extend to seven years, and shall also be liable to fine.

-   **Section 307: Theft after preparation made for causing death, hurt or restraint in order to the committing of theft.**

-   **Section 134: Assault or criminal force in attempt to commit theft of property carried by a person.**

-   **Section 62: Punishment for attempting to commit offences punishable with imprisonment for life or other imprisonment.**

</details>

---

### #8 - PASS - Newacts-Topic

**Query:** `bail provisions under BNSS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 38.8s  
**Response Length:** 20,700 chars  
**Tokens:** 11,475  

<details>
<summary>Response Preview</summary>

Here are the bail provisions under The Bharatiya Nagarik Suraksha Sanhita, 2023:

**Definition of Bail:**
-   **Section 2(b):** "bail" means release of a person accused of or suspected of commission of an offence from the custody of law upon certain conditions imposed by an officer or Court on execution by such person of a bond or a bail bond.

**Bailable and Non-Bailable Offences:**
-   **Section 2(c):** "bailable offence" means an offence which is shown as bailable in the First Schedule, or wh...

</details>

---

### #9 - PASS - Newacts-Topic

**Query:** `electronic evidence rules in BSA`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Judgment  
**Time:** 26.5s  
**Response Length:** 7,862 chars  
**Tokens:** 16,595  

<details>
<summary>Response Preview</summary>

## Electronic Evidence Rules in The Bharatiya Sakshya Adhiniyam, 2023

The Bharatiya Sakshya Adhiniyam, 2023 (BSA 2023) significantly modernizes the rules surrounding electronic evidence, bringing them in line with contemporary technological advancements. The Act defines "document" to include electronic and digital records, thereby broadening the scope of what can be presented as evidence.

### Statutory Basis for Electronic Evidence

The BSA 2023 lays down specific provisions for the admissibility and effect of electronic records:

-   **Admissibility and Legal Effect:**
    -   **Section 61** explicitly states that the admissibility of an electronic or digital record in evidence will not be denied simply because it is in such a format.
    -   **Section 62** mandates that the contents of electronic records can be proven in accordance with the provisions of Section 63.

-   **Conditions for Admissibility of Computer Output (Section 63):**
    -   Information contained in an electronic record (computer output) printed on paper, stored, recorded, or copied in optical or magnetic media, or semiconductor memory, produced by a computer or communication device, is considered a document if certain conditions are met.
    -   Such computer output is admissible in any proceedings without further proof or production of the original.

-   **Presumptions Regarding Electronic Records and Signatures:**
    -   **Section 85** presumes that every electronic record purporting to be an agreement with an electronic or digital signature was concluded by the affixing of those signatures.
    -   **Section 86** establishes presumptions for secure electronic records and signatures.
    -   **Section 87** presumes the correctness of information listed in an Electronic Signature Certificate.
    -   **Section 90** allows the court to presume that an electronic message forwarded through an electronic mail server corresponds with the message fed into the computer for transmission.
    -   **Section 93** states that where an electronic record, purporting or proved to be five years old, is produced from proper custody, the court may presume any electronic signature was affixed by a particular person.

</details>

---

### #10 - PASS - Newacts-Nearby

**Query:** `What does Section 100 of BNS say?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 27.0s  
**Response Length:** 2,054 chars  
**Tokens:** 2,869  

<details>
<summary>Response Preview</summary>

Section 100 of The Bharatiya Nyaya Sanhita, 2023, defines "Culpable homicide" as follows:

"Whoever causes death by doing an act with the intention of causing death, or with the intention of causing such bodily injury as is likely to cause death, or with the knowledge that he is likely by such act to cause death, commits the offence of culpable homicide."

The section also provides three illustrations and three explanations:

**Illustrations:**
(a) A lays sticks and turf over a pit, with the int...

</details>

---

### #11 - PASS - Newacts-Nearby

**Query:** `Section 528 of BNSS`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 13.6s  
**Response Length:** 413 chars  
**Tokens:** 481  

<details>
<summary>Response Preview</summary>

Section 528 of The Bharatiya Nagarik Suraksha Sanhita 2023 is titled "Saving of inherent powers of High Court". The provision states: "Nothing in this Sanhita shall be deemed to limit or affect the inherent powers of the High Court to make such orders as may be necessary to give effect to any order under this Sanhita, or to prevent abuse of the process of any Court or otherwise to secure the ends of justice."


</details>

---

### #12 - PASS - Newacts-Subsection

**Query:** `Section 3(5) of Bharatiya Nyaya Sanhita`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 15.2s  
**Response Length:** 250 chars  
**Tokens:** 4,712  

<details>
<summary>Response Preview</summary>

Section 3(5) of The Bharatiya Nyaya Sanhita, 2023 states: "When a criminal act is done by several persons in furtherance of the common intention of all, each of such persons is liable for that act in the same manner as if it were done by him alone."


</details>

---

### #13 - PASS - Newacts-Subsection

**Query:** `IEA Section 65b`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Judgment  
**Time:** 21.1s  
**Response Length:** 10,641 chars  
**Tokens:** 6,765  

<details>
<summary>Response Preview</summary>

The provided court judgments discuss Section 65B of the Indian Evidence Act, 1872, primarily in the context of admitting electronic evidence, such as CDs containing voice recordings, into proceedings. The key issue revolves around the requirement of a certificate under Section 65B(4) for such evidence to be admissible.

In **Himani Malhotra v. I.P. College for Women**, the Delhi High Court set aside an Inquiry Officer's order that dismissed the petitioner's application to file a CD of voice reco...

</details>

---

### #14 - PASS - Newacts-Mapping

**Query:** `What is the equivalent of Section 498a of IPC in BNS?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Legislation  
**Time:** 17.6s  
**Response Length:** 1,725 chars  
**Tokens:** 2,516  

<details>
<summary>Response Preview</summary>

## Equivalent of Section 498A IPC in Bharatiya Nyaya Sanhita 2023

The Bharatiya Nyaya Sanhita, 2023, introduces Section 85 as the equivalent provision to Section 498A of the Indian Penal Code, 1860. Both sections address the offense of cruelty by a husband or his relatives towards a woman.

### Statutory Basis

**Section 85 of The Bharatiya Nyaya Sanhita, 2023:**

> "Whoever, being the husband or the relative of the husband of a woman, subjects such woman to cruelty shall be punished with impri...

</details>

---

### #15 - PASS - Newacts-Mapping

**Query:** `Section 125 CrPC new law equivalent`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts, Legislation  
**Time:** 38.6s  
**Response Length:** 10,188 chars  
**Tokens:** 17,984  

<details>
<summary>Response Preview</summary>

## Equivalent Provision for Section 125 of the Code of Criminal Procedure, 1973

The equivalent provision to Section 125 of the Code of Criminal Procedure, 1973 (CrPC), in the new legal framework is **Section 144 of The Bharatiya Nagarik Suraksha Sanhita, 2023**.

### Statutory Basis

Both provisions deal with the maintenance of wives, children, and parents.

**New Provision: Section 144 of The Bharatiya Nagarik Suraksha Sanhita, 2023**

(1) If any person having sufficient means neglects or refu...

</details>

---

### #16 - PASS - Legislation-Single

**Query:** `Section 138 of Negotiable Instruments Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 8.3s  
**Response Length:** 890 chars  
**Tokens:** 1,760  

<details>
<summary>Response Preview</summary>

Section 138 of The Negotiable Instruments Act, 1881, deals with the dishonour of a cheque due to insufficient funds or exceeding the arranged payment limit. If a cheque drawn on an account maintained with a bank is returned unpaid for these reasons, the drawer is deemed to have committed an offense. The punishment can be imprisonment for up to two years, a fine up to twice the amount of the cheque, or both.

However, this section is not applicable unless:
(a) The cheque is presented within six m...

</details>

---

### #17 - PASS - Legislation-Single

**Query:** `Section 9 of Arbitration Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 34.9s  
**Response Length:** 1,928 chars  
**Tokens:** 1,846  

<details>
<summary>Response Preview</summary>

Section 9 of The Arbitration and Conciliation Act, 1996, titled "Interim measures, etc., by Court," outlines the provisions for a party to apply to a Court for certain measures.

Sub-section (1) states that a party may apply to a Court before or during arbitral proceedings, or at any time after the making of an arbitral award but before its enforcement under section 36. The applications can be for:
(i) the appointment of a guardian for a minor or a person of unsound mind for the purposes of arbi...

</details>

---

### #18 - PASS - Legislation-Multi

**Query:** `Sections 44 and 45 of Transfer of Property Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 18.6s  
**Response Length:** 1,657 chars  
**Tokens:** 1,424  

<details>
<summary>Response Preview</summary>

Here are the provisions for Sections 44 and 45 of the Transfer of Property Act, 1977:

**Section 44: Transfer by one co-owner**

-   Where one of two or more co-owners of immovable property, legally competent to do so, transfers their share or any interest in that property, the transferee acquires, to the extent necessary to give effect to the transfer, the transferor's right to joint possession or other common or part enjoyment of the property, and the right to enforce a partition of the same.
...

</details>

---

### #19 - PASS - Legislation-Multi

**Query:** `Explain Sections 3, 4 and 5 of Consumer Protection Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 37.5s  
**Response Length:** 1,110 chars  
**Tokens:** 1,645  

<details>
<summary>Response Preview</summary>

**Section 3: Central Consumer Protection Council**

-   **Establishment:** The Central Government is mandated to establish the Central Consumer Protection Council, to be known as the Central Council, through a notification.
-   **Nature:** The Central Council functions as an advisory council.
-   **Composition:**
    -   The Chairperson is the Minister-in-charge of the Department of Consumer Affairs in the Central Government.
    -   It also includes other official or non-official members repres...

</details>

---

### #20 - PASS - Legislation-Range

**Query:** `Sections 10 to 15 of Companies Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 68.6s  
**Response Length:** 12,911 chars  
**Tokens:** 7,517  

<details>
<summary>Response Preview</summary>

Here are the provisions for Sections 10 to 15 of The Companies Act, 2013:

**Section 10: Effect of memorandum and articles**
(1) Subject to the provisions of this Act, the memorandum and articles shall, when registered, bind the company and the members thereof to the same extent as if they respectively had been signed by the company and by each member, and contained covenants on its and his part to observe all the provisions of the memorandum and of the articles.
(2) All monies payable by any me...

</details>

---

### #21 - PASS - Legislation-Topic

**Query:** `director duties under companies act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation, Judgment  
**Time:** 22.6s  
**Response Length:** 2,431 chars  
**Tokens:** 11,286  

<details>
<summary>Response Preview</summary>

## Director Duties Under the Companies Act, 2013

The Companies Act, 2013, outlines several provisions related to the identification and reporting of directors. While the provided judgment results do not offer specific case law on director duties, the statutory provisions highlight key obligations concerning Director Identification Numbers (DINs).

### Statutory Basis

The Act mandates the use and proper reporting of Director Identification Numbers (DINs) to ensure transparency and accountabilit...

</details>

---

### #22 - PASS - Legislation-Topic

**Query:** `tenant rights in rent control legislation`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation, Judgment  
**Time:** 100.3s  
**Response Length:** 11,229 chars  
**Tokens:** 64,130  

<details>
<summary>Response Preview</summary>

Tenant rights under rent control legislation are multifaceted, encompassing protection from eviction, rights related to rent payment and deposits, entitlement to amenities and repairs, and other specific protections. These rights are reinforced by various judicial pronouncements that interpret and apply the relevant statutes.

## Statutory Basis for Tenant Rights

Rent control legislation provides a framework for tenant protection, primarily through provisions that limit a landlord's ability to ...

</details>

---

### #23 - PASS - Legislation-Subsection

**Query:** `Section 138(1) of Negotiable Instruments Act`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 62.5s  
**Response Length:** 483 chars  
**Tokens:** 1,672  

<details>
<summary>Response Preview</summary>

Section 138(1) of The Negotiable Instruments Act, 1881 states that if a cheque drawn on an account maintained by a person with a bank for payment of money to another person, for the discharge of a debt or liability, is returned unpaid due to insufficient funds or exceeding the arranged payment limit, that person shall be deemed to have committed an offense. This offense is punishable with imprisonment for up to two years, or a fine up to twice the amount of the cheque, or both.


</details>

---

### #24 - PASS - Edge-Nearby

**Query:** `What comes after section 35 of BNS?`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 14.7s  
**Response Length:** 162 chars  
**Tokens:** 620  

<details>
<summary>Response Preview</summary>

Section 36 of The Bharatiya Nyaya Sanhita, 2023, titled "Right of private defence against the act of a person with mental illness, etc.", comes after Section 35.


</details>

---

### #25 - PASS - Edge-Vague

**Query:** `theft`  
**Expected Agent:** Any  
**Actual Agent(s):** Legal_Concepts  
**Time:** 15.7s  
**Response Length:** 2,952 chars  
**Tokens:** 731  

<details>
<summary>Response Preview</summary>

This query appears to be a request for a legal explanation regarding "theft."

## Legal Explanation: Theft in India

Theft, as defined under Indian law, is a criminal offense. The primary provision governing theft is Section 378 of the Indian Penal Code, 1860 (IPC).

### Definition of Theft (Section 378, IPC)

"Whoever, intending to take dishonestly any moveable property out of the possession of any person without that person's consent, moves that property in order to such taking, is said to com...

</details>

---

### #26 - PASS - Edge-NonSection

**Query:** `Rule 3 of Maharashtra Rent Control Rules`  
**Expected Agent:** Legislation  
**Actual Agent(s):** Legislation  
**Time:** 9.8s  
**Response Length:** 176 chars  
**Tokens:** 727  

<details>
<summary>Response Preview</summary>

Rule 3 of the Maharashtra Rent Control Rules, 2017, states that a receipt given under section 31 for the amount of rent received in respect of any premises shall be in Form I.


</details>

---

### #27 - PASS - Edge-ManySection

**Query:** `Sections 302, 304, 304a, 307 and 376 of IPC`  
**Expected Agent:** Newacts  
**Actual Agent(s):** Newacts  
**Time:** 18.9s  
**Response Length:** 14,732 chars  
**Tokens:** 6,724  

<details>
<summary>Response Preview</summary>

Here are the provisions for Sections 302, 304, and 307 of The Indian Penal Code, 1860, and their corresponding sections in the Bharatiya Nyaya Sanhita, 2023, along with the old provisions for comparison.

**Section 302 of The Indian Penal Code, 1860 (Punishment for murder)**

-   **Old Provision:** Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.
-   **New Provision (Section 103 of Bharatiya Nyaya Sanhita, 2023):**
    -   (1) Whoev...

</details>

---
