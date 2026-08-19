# Lawttorney API — single-thread prompt suite (prod)

Every prompt in `docs/test prompt.txt`, sent **in order, in one thread**, so
each turn sees the previous turns' history (memory agent, query rewriting,
per-turn typed state). Prompts are verbatim from the source file, typos included.

| | |
|---|---|
| Environment | `https://api.lawttorney.com` |
| Endpoint | `POST /pyapi/chat` (multipart/form-data → SSE) |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| Started (UTC) | 2026-08-17T14:39:36.180173+00:00 |
| Finished (UTC) | 2026-08-17T14:48:21.085963+00:00 |
| Turns | 10 sent, 10 completed without error |

## Turn overview

| # | Section | Prompt (short) | Agents used | Latency | Response chars | Tokens | Cost |
|---|---------|----------------|-------------|---------|----------------|--------|------|
| 1 | Scenario / Drafting | Partition suit + temporary injunction | Drafting | 137.9s | 10,273 | 119,850 | $0.3334 |
| 2 | Scenario / Drafting | Medical negligence damages suit | Drafting | 118.5s | 13,242 | 119,145 | $0.2758 |
| 3 | Citations | Case laws for the Rupa partition scenario | Scenario, Judgment, SCI_Judgment | 31.2s | 35,842 | 98,039 | $0.0341 |
| 4 | Citations | Section 65B electronic evidence case laws | Newacts, Judgment, SCI_Judgment | 28.7s | 54,886 | 61,341 | $0.0210 |
| 5 | Citations | Anticipatory bail case laws | Judgment, SCI_Judgment | 22.8s | 48,155 | 65,142 | $0.0218 |
| 6 | Argument Generation | Medical negligence — plaintiff + defendant | Scenario, Judgment, SCI_Judgment | 55.5s | 45,051 | 486,569 | $0.1478 |
| 7 | Argument Generation | Same medical-negligence argument prompt in | Scenario | 25.4s | 45,460 | 9,107 | $0.0010 |
| 8 | Argument Generation | S.420 IPC — civil-not-criminal defence arg | Newacts, Scenario, Judgment, SCI_Judgment | 58.0s | 60,486 | 71,771 | $0.0218 |
| 9 | Consumer Laws | Defective product liability under CPA 2019 | Newacts, Scenario | 26.4s | 68,893 | 13,785 | $0.0016 |
| 10 | Pdf | Review the attached draft (PDF built from  | Document | 20.1s | 68,893 | 12,418 | $0.0055 |

**Run totals:** 1,057,167 tokens, $0.8639.

---

## Per-turn detail

### Turn 1 — Partition suit + temporary injunction

*Source section in `docs/test prompt.txt`: **Scenario / Drafting***

#### 1. User prompt (verbatim)

```text
Draft a civil suit for partition along with a prayer for temporary injunction. Facts: Rupa is an adopted daughter of the deceased owner. She has no siblings. Her father died 5 years ago, leaving behind two flats in Pune. The only legal heirs are Rupa and her mother. Her name is not yet on the property documents. Her mother is attempting to dispossess her and has expressed intent to transfer the properties to her cousin. The suit should include legal provisions for partition of the properties, declaration of her share, and a temporary injunction restraining the mother or any third party from selling, transferring, or creating third-party rights in the properties until disposal of the suit.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 697 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 1 |
| History turns loaded | 1 |
| Query rewritten by memory agent | False |
| Effective query after rewrite | *(unchanged)* |
| Agents planned | Drafting |
| Agents used | Drafting |
| Sources returned | 1 |
| Wall clock | 137.9s |
| First streamed token | —s |

<details>
<summary>Sources cited back to the client (1)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | A Suit for Partition and Permanent Injunction | Drafting | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (10,273 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, _____ AT _____**

**Civil Suit No: ____**

Rupa,

daughter of Late [Father's Name],

Age: [Plaintiff's Age],

Occupation: [Plaintiff's Occupation],

Residing at: [Plaintiff's Address], Pune.

.....Plaintiff

**vs**

[Mother's Name],

wife of Late [Father's Name],

Age: [Defendant's Age],

Occupation: [Defendant's Occupation],

Residing at: [Defendant's Address], Pune.

.....Defendant

A SUIT FOR PARTITION, DECLARATION, AND PERMANENT AND TEMPORARY INJUNCTION

The Plaintiff abovenamed submits this Plaint, praying to state as follows:

1. That the Plaintiff is the adopted daughter of Late [Father's Name] and the Defendant, [Mother's Name]. The said [Father's Name] passed away intestate approximately five years ago, leaving behind the Plaintiff and the Defendant as his sole Class I legal heirs under the Hindu Succession Act, 1956. The Plaintiff has no siblings.

2. That at the time of his demise, the deceased [Father's Name] was seized and possessed of certain self-acquired properties, including two residential flats situated in Pune. These properties, hereinafter referred to as the "Suit Properties", are more particularly described in the Schedule of Properties annexed hereto and marked as **Schedule 'A'**.

3. That upon the intestate death of the said [Father's Name], his entire estate, including the Suit Properties, devolved upon his Class I legal heirs. As per the provisions of Section 8 and Section 10 of the Hindu Succession Act, 1956, the Plaintiff (daughter) and the Defendant (widow) are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties.

4. That since the demise of her father, the Plaintiff's name has not been mutated in the relevant property records concerning the Suit Properties. The Defendant, being the Plaintiff's mother, has been managing the affairs related to the said properties.

5. That recently, the Defendant's conduct towards the Plaintiff has become hostile and adverse to the Plaintiff's legal rights. The Defendant is attempting to dispossess the Plaintiff and has explicitly communicated her intention to unlawfully sell, transfer, or otherwise alienate the entirety of the Suit Properties to her cousin, with the malafide intent of depriving the Plaintiff of her rightful and legitimate share.

6. That upon learning of the Defendant's illegal intentions, the Plaintiff approached the Defendant on or about [Date] and requested an amicable partition of the Suit Properties and the demarcation of her one-half share. However, the Defendant categorically refused to partition the properties and threatened to proceed with the alienation to third parties.

7. That the cause of action for the present suit first arose on [Date], when the Defendant refused the Plaintiff's legitimate demand for partition, and the same is continuing from day to day. The suit is filed within the period of limitation as prescribed under the Limitation Act, 1963.

8. That the Suit Properties are situated in Pune, which is within the territorial jurisdiction of this Hon'ble Court. Therefore, this Hon'ble Court has the jurisdiction to try, entertain, and decide the present suit.

9. That for the purposes of court fees and jurisdiction, the suit for partition and declaration is valued at Rs. [Valuation of Plaintiff's 1/2 Share], and the requisite court fee as per the Maharashtra Court Fees Act, 1959, is paid herewith. The Plaintiff undertakes to pay any deficit court fee if and when directed by this Hon'ble Court.

10. That the Plaintiff has a strong prima facie case, and the balance of convenience is overwhelmingly in her favour. If
the Defendant is not restrained by an order of temporary injunction from selling, transferring, alienating, or creating
any third-party rights in the Suit Properties during the pendency of this suit, it will lead to a multiplicity of
proceedings and cause irreparable loss and injury to the Plaintiff, which cannot be compensated in monetary terms. It is
therefore just and necessary to grant a temporary injunction under Order XXXIX Rules 1 and 2 of the Code of Civil
Procedure, 1908.

11. The Plaintiff reiterates that she is the legally adopted daughter of the late [Father's Name] and the Defendant, [Mother's Name]. The said [Father's Name] passed away intestate about five years prior to the filing of this suit, leaving behind the Plaintiff and the Defendant as his only surviving legal heirs.

12. The deceased was the absolute owner of two self-acquired residential flats located in Pune, which are the subject matter of the present suit and are more particularly described in **Schedule 'A'** hereto (the "Suit Properties"). Upon his death, the said properties devolved upon his legal heirs by way of intestate succession.

13. As the adopted daughter, the Plaintiff holds the same legal status as a biological daughter and is a Class I heir under the Hindu Succession Act, 1956. Consequently, the Plaintiff and the Defendant each inherited an equal, undivided one-half (1/2) share in the entire estate of the deceased, including the Suit Properties.

14. The Plaintiff submits that despite her clear and indefeasible right, her name has not been entered into the official
property records. Taking undue advantage of this situation and her position as the mother, the Defendant has recently
adopted a hostile stance. The Defendant is actively attempting to dispossess the Plaintiff and has unequivocally
declared her intention to sell or transfer the entirety of the Suit Properties to her cousin, thereby extinguishing the
Plaintiff's legitimate share and rights.

15. The Plaintiff made sincere efforts to resolve the matter amicably and demanded a peaceful partition of the Suit Properties. However, on or about [Date], the Defendant flatly refused to effect any partition and reiterated her threat to alienate the properties, leaving the Plaintiff with no other remedy than to approach this Hon'ble Court.

16. This suit is filed seeking partition and separate possession of the Plaintiff's share in the Suit Properties, a declaration of her title to the extent of her share, and consequential reliefs of permanent and temporary injunctions. The suit is maintainable under the provisions of the Hindu Succession Act, 1956, and the Code of Civil Procedure, 1908.

17. It is submitted that under Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves upon the heirs specified in Class I of the Schedule. The Plaintiff, being the daughter, and the Defendant, being the widow, are both Class I heirs. As per the rules of distribution laid down in Section 10 of the said Act, the widow and the daughter take one share each. Therefore, the Plaintiff is legally entitled to a one-half (1/2) share in the Suit Properties.

18. The Plaintiff seeks a declaration from this Hon'ble Court to the effect that she is the lawful owner of an undivided one-half (1/2) share in the Suit Properties as described in Schedule 'A'. Following such declaration, the Plaintiff is entitled to have her share partitioned by metes and bounds and to be put in separate possession thereof.

19. The Plaintiff has an urgent and immediate need for a temporary injunction under Order XXXIX Rules 1 and 2 of the
Code of Civil Procedure, 1908. The Defendant's explicit threat to alienate the Suit Properties demonstrates that the
properties are in danger of being wrongfully sold and wasted, which would defeat the very purpose of this suit and lead
to a multiplicity of legal proceedings involving third parties. The Plaintiff has a strong prima facie case, the balance
of convenience lies in her favour, and she will suffer irreparable harm if the injunction is not granted.

20. The Plaintiff is also entitled to a decree of permanent injunction restraining the Defendant, her agents, servants, or any person claiming through her from selling, transferring, alienating, mortgaging, or creating any third-party rights, title, or interest in the Suit Properties in any manner whatsoever, to protect the Plaintiff's rights permanently.

## Prayer

The Plaintiff, therefore, prays that this Hon'ble Court be pleased to:

(a) Pass a decree declaring that the Plaintiff is the lawful owner of and is entitled to an equal, undivided one-half (1/2) share in the Suit Properties, more particularly described in **Schedule 'A'** annexed hereto;

(b) Pass a preliminary decree for partition of the Suit Properties by metes and bounds and separate the Plaintiff's one-half (1/2) share;

(c) Pass a final decree for partition and put the Plaintiff in separate, peaceful, and vacant possession of her one-half (1/2) share in the Suit Properties;

(d) Pass an order of temporary injunction under Order XXXIX Rules 1 and 2 of the Code of Civil Procedure, 1908, restraining the Defendant, her agents, servants, assigns, or any person claiming through her, from selling, transferring, alienating, mortgaging, encumbering, or creating any third-party rights, title, or interest in the Suit Properties during the pendency and final disposal of this suit;

(e) Pass a decree of permanent injunction restraining the Defendant, her agents, servants, assigns, or any person claiming through her, from selling, transferring, alienating, mortgaging, encumbering, or creating any third-party rights, title, or interest in the Suit Properties in any manner whatsoever;

(f) Award the costs of this suit to the Plaintiff from the Defendant;

(g) Grant any other and further reliefs as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

Place: Pune
Date:

Sd/-
(ADVOCATE FOR PLAINTIFF)

Sd/-
(PLAINTIFF)

## Verification

I, Rupa, daughter of Late [Father's Name], the Plaintiff abovenamed, do hereby state on solemn affirmation that the contents of the foregoing Plaint in paragraphs 1 to 20 are true and correct to the best of my knowledge, information, and belief, and I have signed the same at Pune on this ___ day of __________, 2024.

Sd/-
(PLAINTIFF)


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

Follow-up suggestions returned: `What evidence is needed for the injunction?`, `Can I claim mesne profits?`, `What are the court fees involved?`

---

### Turn 2 — Medical negligence damages suit

*Source section in `docs/test prompt.txt`: **Scenario / Drafting***

#### 1. User prompt (verbatim)

```text
Draft a civil suit for damages arising from medical negligence, incorporating detailed legal reasoning and relevant case laws. Scenario: My client, Mrs. Anjali Deshmukh, aged 42, and a resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the procedure, the attending surgeon, Dr. Rakesh Nair, negligently caused damage to her bile duct. Following the surgery, she experienced severe abdominal pain and had to be admitted to another hospital for corrective surgery. As a result, she incurred additional medical expenses amounting to ₹3.5 lakhs and endured significant physical pain and mental agony. Despite repeated follow-ups, both the hospital and the doctor have refused to acknowledge their negligence or offer any compensation. Mrs. Deshmukh now seeks damages of ₹25 lakhs, covering medical expenses, mental harassment, pain and suffering, and anticipated costs of future treatment.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 940 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 2 |
| History turns loaded | 1 |
| Query rewritten by memory agent | False |
| Effective query after rewrite | *(unchanged)* |
| Agents planned | Drafting |
| Agents used | Drafting |
| Sources returned | 1 |
| Wall clock | 118.5s |
| First streamed token | —s |

<details>
<summary>Sources cited back to the client (1)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (13,242 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

Follow-up suggestions returned: `What specific documents are needed?`, `What's the timeline for this suit?`, `Are there punitive damages possible?`

---

### Turn 3 — Case laws for the Rupa partition scenario

*Source section in `docs/test prompt.txt`: **Citations***

#### 1. User prompt (verbatim)

```text
Rupa is a adopted child. She doesnt have any siblings. her father passed away 5 years ago. She and her mothers are only legal heirs. Her father had 2 flats in pune. Her mother told her to leave the house and her mother says that she'll transfer the property to her cousins name. now rupa is scared that she won't get any share in the properties (her name is still not on the legal papers of the properties). Give me 5 plus Case law or Citations According to the Scenario.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 471 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 3 |
| History turns loaded | 2 |
| Query rewritten by memory agent | True |
| Effective query after rewrite | `Provide 5+ case laws or citations relevant to a scenario where an adopted daughter, Rupa, who has no siblings, is an heir to her deceased father's two flats in Pune. Her father passed away 5 years ago` |
| Agents planned | Scenario, Judgment, SCI_Judgment |
| Agents used | Scenario, Judgment, SCI_Judgment |
| Sources returned | 75 |
| Wall clock | 31.2s |
| First streamed token | 27.713s |

<details>
<summary>Sources cited back to the client (75)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | legaleye.co.in | Scenario | None |
| 3 | scenario | pahujalawacademy.com | Scenario | None |
| 4 | scenario | lawbhoomi.com | Scenario | None |
| 5 | scenario | medium.com | Scenario | None |
| 6 | scenario | ipleaders.in | Scenario | None |
| 7 | scenario | lawgratis.com | Scenario | None |
| 8 | scenario | emkaywealth.com | Scenario | None |
| 9 | scenario | khannaandassociates.com | Scenario | None |
| 10 | scenario | legalhelpnri.com | Scenario | None |
| 11 | scenario | urbanrightslegal.com | Scenario | None |
| 12 | scenario | ipleaders.in | Scenario | None |
| 13 | scenario | ladderuptrustee.com | Scenario | None |
| 14 | scenario | lclawfirm.in | Scenario | None |
| 15 | scenario | indiafilings.com | Scenario | None |
| 16 | scenario | viveklegal.in | Scenario | None |
| 17 | scenario | 99acres.com | Scenario | None |
| 18 | scenario | supremetoday.ai | Scenario | None |
| 19 | scenario | indiatimes.com | Scenario | None |
| 20 | scenario | supremetoday.ai | Scenario | None |
| 21 | scenario | khaadvocates.com | Scenario | None |
| 22 | scenario | scconline.com | Scenario | None |
| 23 | scenario | supremetoday.ai | Scenario | None |
| 24 | scenario | supremetoday.ai | Scenario | None |
| 25 | scenario | supremetoday.ai | Scenario | None |
| 26 | scenario | youtube.com | Scenario | None |
| 27 | scenario | supremetoday.ai | Scenario | None |
| 28 | scenario | indiankanoon.org | Scenario | None |
| 29 | scenario | mahendrabhavsar.com | Scenario | None |
| 30 | scenario | legalhelpnri.com | Scenario | None |
| 31 | scenario | scribd.com | Scenario | None |
| 32 | scenario | basichomeloan.com | Scenario | None |
| 33 | judgment | legalhelpnri.com | Judgment | None |
| 34 | judgment | scconline.com | Judgment | None |
| 35 | judgment | legaleye.co.in | Judgment | None |
| 36 | judgment | delhilawacademy.com | Judgment | None |
| 37 | judgment | azbpartners.com | Judgment | None |
| 38 | judgment | sheokandlegal.com | Judgment | None |
| 39 | judgment | lawrato.com | Judgment | None |
| 40 | judgment | economictimes.com | Judgment | None |
| 41 | judgment | lawbeat.in | Judgment | None |
| 42 | judgment | supremetoday.ai | Judgment | None |
| 43 | judgment | chowdhrylawoffices.com | Judgment | None |
| 44 | judgment | supremetoday.ai | Judgment | None |
| 45 | judgment | indiankanoon.org | Judgment | None |
| 46 | judgment | sslawcodes.com | Judgment | None |
| 47 | judgment | supremetoday.ai | Judgment | None |
| 48 | judgment | 99acres.com | Judgment | None |
| 49 | judgment | khannaandassociates.com | Judgment | None |
| 50 | judgment | unwomen.org | Judgment | None |
| 51 | sci_judgment | SMT. ASS KAUR (DECEASED) BY LRS. VS KARTAR SINGH (DEAD) BY LRS. . | SCI_Judgment | None |
| 52 | sci_judgment | RADHA BAI VS RAM NARAYAN | SCI_Judgment | None |
| 53 | sci_judgment | SHEELA DEVI VS LAL CHAND | SCI_Judgment | None |
| 54 | sci_judgment | RANBIR SINGH VS KARTAR SINGH | SCI_Judgment | None |
| 55 | sci_judgment | GIANI RAM & ORS. VS RAMJI LAL & ORS. | SCI_Judgment | None |
| 56 | sci_judgment | SITABAI & ANR. VS RAM CHANDRA | SCI_Judgment | None |
| 57 | sci_judgment | SAWAN RAM & OTHERS VS KALA WANTI & OTHERS | SCI_Judgment | None |
| 58 | sci_judgment | DHARMA SHAMRAO AGALAWE VS PANDURANG MIRAGU AGALAWE & ORS. | SCI_Judgment | None |
| 59 | sci_judgment | NAMDEV VYANKAT GHADGE VS CHANDRAKANT GANPAT CHADGE . | SCI_Judgment | None |
| 60 | sci_judgment | DINAJI &ORS VS DADDI | SCI_Judgment | None |
| 61 | sci_judgment | NAGABHUSHANAMMAL (D) THRU LRS VS C.CHANDIKESWARALINGAM | SCI_Judgment | None |
| 62 | sci_judgment | GURBINDER SINGH AND ANOTHER VS LAL SINGH AND ANOTHER | SCI_Judgment | None |
| 63 | sci_judgment | WUNTAKAL YALPI CHENABASAVANA GOWD VS RAO BAHADUR Y. MAHABALESHWARAPPA  | SCI_Judgment | None |
| 64 | sci_judgment | SRILEKHA GHOSH (ROY) VS PARTHA SARATHI GHOSH | SCI_Judgment | None |
| 65 | sci_judgment | RAJ RANI & ANR. VS KAILASH CHAND & ANR. | SCI_Judgment | None |
| 66 | sci_judgment | MAHILA BAJRANGI(DEAD) THR.LRS. VS BADRIBAI | SCI_Judgment | None |
| 67 | sci_judgment | RAJINDER SINGH VS STATE OF JAMMU & KASHMIR . | SCI_Judgment | None |
| 68 | sci_judgment | ARJAN SINGH AND ORS. VS NARAIN SINGH & ORS. | SCI_Judgment | None |
| 69 | sci_judgment | N. KHOSLA VS RAJLAKSHMI(DEAD) . | SCI_Judgment | None |
| 70 | sci_judgment | BAPUSAHEB CHIMASAHEB NAIK-NIMBALKR(DEAD THROUGH LRS.) VS MAHESH VIJAYS | SCI_Judgment | None |
| 71 | sci_judgment | KALINDI DAMODAR GARDE(D) BY LRS. VS MANOHAR LAXMAN KULKARNI AND ORS. | SCI_Judgment | None |
| 72 | sci_judgment | KASABAI TUKARAM KARVAR VS NIVRUTI (DEAD) THR. LRS. | SCI_Judgment | None |
| 73 | sci_judgment | LACHMAN SINGH VS KIRPA SINGH & OTHERS | SCI_Judgment | None |
| 74 | sci_judgment | ANNAGOUDA NATHGOUDA PATIL VS COURT OF WARDS AND ANOTHER | SCI_Judgment | None |
| 75 | sci_judgment | S.   S. MUNNA LAL VS S.   S. RAJKUMAR AND OTHERS | SCI_Judgment | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (35,842 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
The following case laws and legal principles are relevant to the scenario presented:

1.  ***Rights of an Adopted Daughter***: Under the Hindu Adoptions and Maintenance Act, 1956 (HAMA), an adopted child is
deemed to be the child of their adoptive parents for all purposes from the date of adoption, acquiring the same status,
rights, and obligations as a biological child. This includes the right to inherit both ancestral and self-acquired
property of the adoptive parents. Section 12 of HAMA explicitly states that an adopted child shall be deemed to be the
child of his or her adoptive father or mother for all purposes with effect from the date of the adoption, and from such
date all the ties of the child in the family of his or her birth shall be deemed to be severed and replaced by those
created by the adoption in the adoptive family.

2.  ***Intestate Succession under Hindu Succession Act, 1956***: When a Hindu male dies intestate (without a will), his
property devolves upon his Class I legal heirs simultaneously. Class I heirs include the mother, widow, sons, and
daughters. As per Section 10 of the Hindu Succession Act, 1956, the widow and the daughter each take one equal share.
Therefore, Rupa, as the adopted daughter, and her mother, as the widow, are each entitled to an equal one-half (1/2)
share in the self-acquired properties of the deceased father.

3.  ***Right to Partition for Co-owners***: Any co-owner of a jointly owned property has an inherent legal right to seek partition of the property to define their share. This right can be exercised at any time as long as co-ownership subsists. A partition suit enables the court to determine the shares of each co-owner and order the division of the property accordingly.

4.  ***Mutation Entries Not Conclusive Proof of Title***: The Supreme Court has consistently reiterated that mutation of
property in revenue records neither creates nor extinguishes the title to a property. An entry in revenue records does
not confer title on a person whose name appears in the record-of-rights; such entries have only "fiscal purpose," i.e.,
for the payment of land revenue. The title of a property can only be decided by a competent civil court. Therefore, the
fact that Rupa's name is not yet on the property documents does not negate her ownership rights by inheritance.

5.  ***Injunction Against Alienation by Co-owner***: While generally, a co-owner cannot claim an injunction against
another co-owner with regard to the property owned jointly, an injunction can be granted to preserve the property's
nature and character and prevent wrongful acts like waste, damage, or unauthorized alienation. Courts will grant
injunctions to protect the proprietary rights of co-owners against unauthorized alienation of the entire undivided
property by another co-owner. A co-owner cannot sell the entire property or a specific portion of it so as to bind other
co-owners, unless their share has been formally determined and demarcated through a partition. Temporary injunctions are
granted to prevent irreparable injury and maintain the balance of convenience, especially when one co-owner's actions
could harm shared interests, such as the alienation of joint property.

Based on these principles, the following case laws and citations are relevant:

-   *Smt. Sitabai v. Ramchandra, AIR 1970 SC 343* (Supreme Court): This case affirmed that adoption results in the complete severance of all ties with the birth family, and the adopted child is fully integrated into the adoptive family, thereby creating new legal relationships upon adoption. This supports Rupa's status as a full legal heir in her adoptive family.
-   *Balwant Singh v. Daulat Singh, (1997) 7 SCC 137* (Supreme Court): This judgment clarified that mutation entries do not confer any title nor do they have any presumptive value on ownership. They are primarily for revenue collection and cannot be used as conclusive proof of ownership. This is crucial for Rupa's claim, as her name not being on the documents does not affect her title.
-   *Smt. Sawarni v. Smt. Inder Kaur, 2016 4 Supreme 580* (Supreme Court): This case reiterated that mutation in favour of an individual does not convey title and that mutation entries are only for fiscal purposes.
-   *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1* (Supreme Court): While this landmark ruling primarily dealt with coparcenary rights of daughters in ancestral property, it reinforces the principle of gender equality in inheritance and the equal status of daughters (including adopted daughters by extension of HAMA principles) in property rights. The judgment affirmed that daughters are equal coparceners by birth with equal rights to sons.
-   *Khatri Hotels P.Ltd.& Anr vs Union Of India & Anr, (2011) 9 SCC 126* (Supreme Court): This case, among others, highlights the principle that an injunction can be sought by a co-owner to protect their rights in jointly held property, especially against attempts to dispose of the property. While a blanket injunction against a co-sharer dealing with their own share may not be sustainable, an injunction to preserve the property's nature and character is permissible.
-   *Pazhanathal (D) Thr. Lrs vs Alamathal (D) Thr.Lrs, 2026 SCC OnLine SC 1000* (Supreme Court): This case, while dealing with a counterblast to a partition suit, generally confirms that injunctions could not be granted against co-owners unless specific adverse acts are proven. However, the context of preventing alienation of the *entire* property by one co-owner without partition would warrant an injunction.
-   *Tanusree Basu & Ors vs Ishani Prasad Basu & Ors, AIR 2008 SC 999* (Supreme Court): This case discusses that while a co-owner cannot claim an order of injunction against another co-owner with regard to the property owned jointly, liability to pay compensation arises against a co-owner who deliberately excludes the other co-owners from the enjoyment of the property. This supports the need for partition and protection of Rupa's share.

### JUDGMENT CITATIONS:
Rupa, as an adopted daughter, has the same legal rights to her deceased father's property as a biological daughter. The
Hindu Succession (Amendment) Act, 2005, recognized daughters as coparceners by birth, placing them on par with sons in
ancestral property, irrespective of whether the father was alive on the date of the amendment. This means Rupa has an
equal share in the ancestral property by birth. Furthermore, the fact that her name is not yet on the property documents
does not diminish her ownership rights, as mutation entries in revenue records or property documents do not confer
title; they are for fiscal and administrative purposes only. Title is established through legal succession, a valid
will, or a court order. Rupa's mother cannot unilaterally dispossess her or transfer the properties to Rupa's cousin, as
co-owners are deemed to be in joint possession, and one co-owner cannot forcibly dispossess another without due process
of law or consent.

I think this information will help you more to understand the case:

### Key Legal Issues
-   **Inheritance Rights of an Adopted Daughter**: Whether an adopted daughter has equal rights to ancestral property as a biological daughter.
-   **Coparcenary Rights of Daughters**: The applicability and scope of the Hindu Succession (Amendment) Act, 2005, in granting coparcenary rights to daughters.
-   **Evidentiary Value of Mutation Entries**: Whether the absence of Rupa's name on property documents affects her ownership rights.
-   **Protection Against Dispossession by Co-owner**: The legal remedies available to a co-owner against dispossession by another co-owner.
-   **Father's Demise Before 2005 Amendment**: Whether the father's death prior to the 2005 amendment impacts the daughter's coparcenary rights.

### Detailed Narrative
Rupa is an adopted daughter and the only child of her deceased father, who passed away five years ago. The father owned
two flats in Pune. The legal heirs to the property are Rupa and her mother. Rupa's name has not yet been mutated onto
the property documents. Her mother is now threatening to dispossess Rupa and transfer the properties to Rupa's cousin.
The core of the dispute revolves around Rupa's right to her share in the ancestral property despite her name not being
on the legal papers and her mother's attempts to deny her inheritance.

### As per the Court
The legal position regarding a daughter's rights in ancestral property has been significantly clarified by the Hindu Succession (Amendment) Act, 2005, and subsequent Supreme Court judgments.

1.  **Adopted Daughter's Rights**: An adopted daughter has the same legal rights as a biological daughter in her adoptive family's property. Under the Hindu Adoptions and Maintenance Act, 1956, an adopted child is treated as a natural-born child, acquiring the same rights and status. This means Rupa is entitled to an equal share in both ancestral and self-acquired property of her adoptive father.

2.  **Coparcenary Rights**: The Hindu Succession (Amendment) Act, 2005, brought about a fundamental change by
recognizing daughters as coparceners by birth, similar to sons. This amendment ensures that daughters acquire an
interest in ancestral property from the moment of their birth, with the same rights and liabilities as sons. The Supreme
Court, in *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1*, definitively held that a daughter's coparcenary right
accrues by birth and is not dependent on whether her father was alive on the date of the 2005 amendment. Therefore, even
though Rupa's father passed away five years ago (which would be after the 2005 amendment), her rights as a coparcener
are secured. She can demand a partition of the ancestral property and claim her share.

3.  **Mutation Entries and Title**: It is a well-established legal principle that mutation entries in revenue records or
property documents do not confer or extinguish ownership rights; they serve purely fiscal and administrative purposes,
mainly for the collection of revenue. Title to property is established through legal succession, a valid will, or a
decree passed by a competent court. Therefore, the absence of Rupa's name on the property documents does not negate her
inherent ownership rights derived from her status as a legal heir and coparcener.

4.  **Protection Against Dispossession**: As a co-owner, Rupa is deemed to be in joint possession of the property along
with her mother. One co-owner cannot forcibly dispossess another co-owner without due process of law or consent. If
Rupa's mother attempts to dispossess her or transfer the entire property without Rupa's consent, Rupa can seek legal
remedies such as filing a partition suit, a suit for injunction to prevent unauthorized sale or transfer, or a suit for
ejectment and recovery of possession if she is wrongfully ousted.

### Additional Observations
The legal framework in India, particularly after the 2005 amendment to the Hindu Succession Act, 1956, strongly supports
the equal rights of daughters in ancestral property, including adopted daughters. The Supreme Court's pronouncements
have further solidified these rights, ensuring that gender discrimination in inheritance is eliminated. The case
highlights the importance of understanding that actual ownership vests by law upon succession, irrespective of
administrative entries, and that co-owners have strong legal protections against unilateral actions by other co-owners.

### Case Law or Citations
1.  *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2010/37409/37409_2010_3_1501_23469_Judgement_11-Aug-2020.pdf)): This landmark Supreme Court judgment clarified that a daughter's coparcenary right under the Hindu Succession Act, 1956 (as amended in 2005) is by birth and is not dependent on whether her father was alive on the date of the 2005 amendment. This ruling ensures that daughters have equal rights to ancestral property as sons.
2.  *Prakash v. Phulavati, (2016) 2 SCC 36*: While this case initially held that the father must be alive on the date of the 2005 amendment for the daughter to claim coparcenary rights, it was subsequently clarified and effectively overturned on this specific point by *Vineeta Sharma v. Rakesh Sharma*. It is relevant for understanding the evolution of legal interpretation.
3.  *Danamma v. Amar, (2018) 3 SCC 343*: This Supreme Court judgment reinforced that married daughters cannot be denied property rights and are entitled to an equal share in ancestral property.
4.  **General Principle on Mutation Entries**: The Supreme Court has consistently held that mutation entries in revenue records do not confer title or ownership rights; they are merely for fiscal purposes. This principle has been reiterated in numerous judgments, emphasizing that title can only be established through lawful succession, a valid will, or a court decree.
5.  **General Principle on Dispossession by Co-owner**: The Indian judiciary has consistently held that co-owners are deemed to be in joint possession of the property, and one co-owner cannot forcibly dispossess another without due process of law or consent. An ousted co-owner may maintain a suit for injunction or possession against the dispossessing co-owner.

### PDF Links
-   *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2010/37409/37409_2010_3_1501_23469_Judgement_11-Aug-2020.pdf))

### SCI_JUDGMENT CITATIONS:
Rupa, as an adopted daughter, has significant rights to her deceased father's property under the Hindu Succession Act, 1956. Her adoption severs her ties with her natural family and establishes her as a member of her adoptive family for all purposes, including inheritance. The fact that her name is not yet on the property documents does not extinguish her inherent right as a legal heir. Her mother's attempt to dispossess her and transfer the properties to a cousin would be legally challenged.

Here are some relevant case laws and citations that address Rupa's rights:

### Case Laws and Citations

1.  ***Radha Bai v. Ram Narayan, C.A. No. 5889 of 2009, decided on 22-11-2019*** ([Judgment PDF](https://api.sci.gov.in/supremecourt/2007/19564/19564_2007_7_1501_18424_Judgement_22-Nov-2019.pdf))
    -   **Court**: Supreme Court of India
    -   **Bench**: Hon'ble Mr. Justice A.M. Khanwilkar, Hon'ble Mr. Justice Dinesh Maheshwari
-   **Key Legal Principle**: This case, while dealing with the general rules of succession in the case of males under
Section 8 of the Hindu Succession Act, 1956, reinforces the principle that the Act lays down the general rules of
succession. Although the specific facts relate to a daughter's right in ancestral property after a partition, it
highlights the importance of the Hindu Succession Act in determining inheritance rights. In Rupa's scenario, her rights
would be governed by the provisions of the Hindu Succession Act, which treats an adopted daughter on par with a
natural-born daughter.

2.  ***Sitabai & Anr. v. Ram Chandra, AIR 1970 SC 343, decided on 20-08-1969*** ([Judgment PDF](https://api.sci.gov.in/jonew/judis/1674.pdf))
    -   **Court**: Supreme Court of India
    -   **Bench**: Hon'ble Mr. Justice V. Ramaswami, Hon'ble Mr. Justice J.C. Shah, Hon'ble Mr. Justice A.N. Grover
-   **Key Legal Principle**: This judgment clarifies that under the Hindu Adoptions and Maintenance Act, 1956, an
adopted child becomes absorbed into the adoptive family. In the case of adoption by a widow, the adopted child becomes
the adopted son of her deceased husband. By implication, an adopted daughter would also be considered the daughter of
the deceased father, thereby acquiring coparcenary interest in the joint family properties. This case strongly supports
Rupa's position as a legal heir with full rights in her adoptive father's property.

3.  ***Nagabhushanammal (D) Thru Lrs. v. C. Chandikeswaralingam, C.A. Nos. 1858-1859 of 2016, decided on 26-02-2016*** ([Judgment PDF](https://api.sci.gov.in/jonew/judis/43448.pdf))
    -   **Court**: Supreme Court of India
    -   **Bench**: Hon'ble Mr. Justice Kurian Joseph, Hon'ble Mr. Justice Rohinton Fali Nariman
-   **Key Legal Principle**: This case deals with the principles of ouster and adverse possession among co-owners. It
reiterates that the possession of one co-heir is considered, in law, as possession of all co-heirs. There can be no
adverse possession between co-owners unless there has been a denial of title and an ouster to the knowledge of the other
co-owner. In Rupa's situation, her mother, as a co-owner, cannot simply dispossess her or claim adverse possession
without a clear and hostile act of ouster known to Rupa. This case would be crucial in challenging her mother's attempts
to dispossess her.

4.  ***Kalindi Damodar Garde (D) By Lrs. v. Manohar Laxman Kulkarni And Ors., C.A. Nos. 6642-6643 of 2010, decided on 07-02-2020*** ([Judgment PDF](https://api.sci.gov.in/supremecourt/2007/14460/14460_2007_11_1502_20374_Judgement_07-Feb-2020.pdf))
    -   **Court**: Supreme Court of India
    -   **Bench**: Hon'ble Mr. Justice L. Nageswara Rao, Hon'ble Mr. Justice Hemant Gupta
    -   **Key Legal Principle**: This judgment discusses the inheritance rights of children born before adoption in the adoptive family. While the specific facts are different, the underlying principle is that the Hindu Succession Act governs intestate succession. The case highlights that an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes. This reinforces Rupa's position as a full legal heir in her adoptive family.

5.  ***Rajinder Singh v. State of Jammu & Kashmir & Ors., C.A. No. 5269 of 2003, decided on 11-07-2008*** ([Judgment PDF](https://api.sci.gov.in/jonew/judis/32009.pdf))
    -   **Court**: Supreme Court of India
    -   **Bench**: Hon'ble Mr. Justice C.K. Thakker, Hon'ble Mr. Justice Lokeshwar Singh Panta
    -   **Key Legal Principle**: This case emphasizes that revenue records (like mutation entries) do not confer title to property. Such entries are primarily for fiscal purposes, and substantive rights of title and ownership can only be decided by a competent civil court. This is highly relevant to Rupa's situation, as the absence of her name on property documents does not negate her legal ownership rights as an heir. She can assert her rights in a civil court.

### Key Legal Issues

-   **Inheritance Rights of an Adopted Daughter**: Under the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes, including inheritance. This means Rupa has the same rights as a natural-born daughter.
-   **Ancestral Property**: If the flats were ancestral property in the hands of her father, Rupa would have a birthright in them. Even if they were self-acquired, she would inherit as a Class I heir under the Hindu Succession Act, 1956.
-   **Co-ownership and Dispossession**: Rupa and her mother are co-owners of the property. One co-owner cannot unilaterally dispossess another co-owner or transfer the entire property without the consent of all co-owners.
-   **Evidentiary Value of Property Documents**: The absence of Rupa's name on property documents (like mutation records) does not extinguish her ownership rights. These records are for fiscal purposes, and actual title is determined by succession laws.

### Detailed Narrative

Rupa's father passed away five years ago, leaving behind two flats in Pune. The only legal heirs are Rupa, his adopted daughter, and her mother. Despite being a legal heir, Rupa's name has not been mutated on the property documents. Her mother is now threatening to dispossess Rupa and transfer the properties to her cousin. This action by the mother is a direct challenge to Rupa's inheritance rights.

### As per the Court

The Supreme Court has consistently upheld the rights of adopted children, treating them on par with natural-born children for inheritance purposes. The Hindu Adoptions and Maintenance Act, 1956, explicitly states that an adopted child is deemed to be the child of their adoptive parents for all purposes. Therefore, Rupa, as an adopted daughter, has a clear legal right to inherit her deceased father's property.

Furthermore, the courts have clarified that mutation entries in revenue records do not confer title. They are primarily for administrative and fiscal purposes. The actual ownership and inheritance are governed by personal laws, in this case, the Hindu Succession Act, 1956. Thus, the absence of Rupa's name on the property documents does not diminish her legal claim.

Regarding dispossession by a co-owner, the Supreme Court has held that the possession of one co-owner is considered the possession of all co-owners. For one co-owner to claim adverse possession against another, there must be a clear and hostile ouster known to the dispossessed co-owner. The mother's threat to dispossess Rupa and transfer the property to a third party would be a violation of Rupa's co-ownership rights and can be challenged in a civil court.

### Judgment & Direction

Based on the principles established in the cited judgments, Rupa can seek legal recourse to protect her inheritance rights. She can file a suit for declaration of her share in the property, partition, and injunction to prevent her mother from alienating the property. The courts would likely uphold her rights as an adopted daughter and a co-owner, ensuring she receives her rightful share in the property.

**PDF Links:**
-   *Radha Bai v. Ram Narayan, C.A. No. 5889 of 2009, decided on 22-11-2019*: https://api.sci.gov.in/supremecourt/2007/19564/19564_2007_7_1501_18424_Judgement_22-Nov-2019.pdf
-   *Sitabai & Anr. v. Ram Chandra, AIR 1970 SC 343, decided on 20-08-1969*: https://api.sci.gov.in/jonew/judis/1674.pdf
-   *Nagabhushanammal (D) Thru Lrs. v. C. Chandikeswaralingam, C.A. Nos. 1858-1859 of 2016, decided on 26-02-2016*: https://api.sci.gov.in/jonew/judis/43448.pdf
-   *Kalindi Damodar Garde (D) By Lrs. v. Manohar Laxman Kulkarni And Ors., C.A. Nos. 6642-6643 of 2010, decided on 07-02-2020*: https://api.sci.gov.in/supremecourt/2007/14460/14460_2007_11_1502_20374_Judgement_07-Feb-2020.pdf
-   *Rajinder Singh v. State of Jammu & Kashmir & Ors., C.A. No. 5269 of 2003, decided on 11-07-2008*: https://api.sci.gov.in/jonew/judis/32009.pdf


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

Follow-up suggestions returned: `What if mother transfers property first?`, `Can Rupa claim inheritance now?`, `What if father's will exists?`

---

### Turn 4 — Section 65B electronic evidence case laws

*Source section in `docs/test prompt.txt`: **Citations***

#### 1. User prompt (verbatim)

```text
Provide case laws on the admissibility of electronic evidence under Section 65B of the Indian Evidence Act.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 107 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 4 |
| History turns loaded | 3 |
| Query rewritten by memory agent | True |
| Effective query after rewrite | `Case laws on admissibility of electronic evidence under Section 65B of the Indian Evidence Act, 1872.` |
| Agents planned | Newacts, Judgment, SCI_Judgment |
| Agents used | Newacts, Judgment, SCI_Judgment |
| Sources returned | 46 |
| Wall clock | 28.7s |
| First streamed token | 6.969s |

<details>
<summary>Sources cited back to the client (46)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | legaleye.co.in | Scenario | None |
| 3 | scenario | pahujalawacademy.com | Scenario | None |
| 4 | scenario | lawbhoomi.com | Scenario | None |
| 5 | scenario | medium.com | Scenario | None |
| 6 | scenario | ipleaders.in | Scenario | None |
| 7 | scenario | lawgratis.com | Scenario | None |
| 8 | scenario | emkaywealth.com | Scenario | None |
| 9 | scenario | khannaandassociates.com | Scenario | None |
| 10 | scenario | legalhelpnri.com | Scenario | None |
| 11 | scenario | urbanrightslegal.com | Scenario | None |
| 12 | scenario | ipleaders.in | Scenario | None |
| 13 | scenario | ladderuptrustee.com | Scenario | None |
| 14 | scenario | lclawfirm.in | Scenario | None |
| 15 | scenario | indiafilings.com | Scenario | None |
| 16 | scenario | viveklegal.in | Scenario | None |
| 17 | scenario | 99acres.com | Scenario | None |
| 18 | scenario | supremetoday.ai | Scenario | None |
| 19 | scenario | indiatimes.com | Scenario | None |
| 20 | scenario | supremetoday.ai | Scenario | None |
| 21 | scenario | khaadvocates.com | Scenario | None |
| 22 | scenario | scconline.com | Scenario | None |
| 23 | scenario | supremetoday.ai | Scenario | None |
| 24 | scenario | supremetoday.ai | Scenario | None |
| 25 | scenario | supremetoday.ai | Scenario | None |
| 26 | scenario | youtube.com | Scenario | None |
| 27 | scenario | supremetoday.ai | Scenario | None |
| 28 | scenario | indiankanoon.org | Scenario | None |
| 29 | scenario | mahendrabhavsar.com | Scenario | None |
| 30 | scenario | legalhelpnri.com | Scenario | None |
| 31 | scenario | scribd.com | Scenario | None |
| 32 | scenario | basichomeloan.com | Scenario | None |
| 33 | judgment | Nyati Builders Pvt Ltd vs Mr. Rajat Dinesh Chauhan | Judgment | 188.3097 |
| 34 | judgment | AJAY JAGDISHCHANDRA SHARMA vs LARSEN AND TOURBO LTD. | Judgment | 181.51266 |
| 35 | judgment | PURNESH ISHVARBHAI MODI vs STATE OF GUJARAT | Judgment | 174.14023 |
| 36 | judgment | SURESH SHANKAR BOLADE vs MALLAPPA SANGAPPA VHATTE | Judgment | 173.77931 |
| 37 | judgment | M/s J.J.Extrusion Private Limited vs Commissioner of Central Excise &  | Judgment | 173.46251 |
| 38 | judgment | Ignatius Tony Pereira vs Travel Corporation (India) Pvt. Ltd. | Judgment | 173.02277 |
| 39 | judgment | DHRUBEN GURALDAS BALANI vs STATE OF GUJARAT | Judgment | 172.70555 |
| 40 | judgment | HEMCHANDBHAI LALJIBHAI BHABHOR vs STATE OF GUJARAT | Judgment | 172.23886 |
| 41 | judgment | Deepali Santosh Lokhande vs Mr.Santosh Vasantrao Lokhande | Judgment | 171.5667 |
| 42 | judgment | M/S ICICI BANK LIMITED vs KAPIL DEV SHARMA | Judgment | 171.03592 |
| 43 | sci_judgment | ARJUN PANDITRAO KHOTKAR VS KAILASH KUSHANRAO GORANTYAL | SCI_Judgment | None |
| 44 | sci_judgment | ANVAR P.V. VS P.K.BASHEER . | SCI_Judgment | None |
| 45 | sci_judgment | THE STATE OF KARNATAKA VS T NASEER @ NASIR @ THANDIANTAVIDA NASEER @ U | SCI_Judgment | None |
| 46 | newacts | Indian Evidence Act 1872 - Section 65B | Newacts | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (54,886 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
The following case laws and legal principles are relevant to the scenario presented:

1.  ***Rights of an Adopted Daughter***: Under the Hindu Adoptions and Maintenance Act, 1956 (HAMA), an adopted child is
deemed to be the child of their adoptive parents for all purposes from the date of adoption, acquiring the same status,
rights, and obligations as a biological child. This includes the right to inherit both ancestral and self-acquired
property of the adoptive parents. Section 12 of HAMA explicitly states that an adopted child shall be deemed to be the
child of his or her adoptive father or mother for all purposes with effect from the date of the adoption, and from such
date all the ties of the child in the family of his or her birth shall be deemed to be severed and replaced by those
created by the adoption in the adoptive family.

2.  ***Intestate Succession under Hindu Succession Act, 1956***: When a Hindu male dies intestate (without a will), his
property devolves upon his Class I legal heirs simultaneously. Class I heirs include the mother, widow, sons, and
daughters. As per Section 10 of the Hindu Succession Act, 1956, the widow and the daughter each take one equal share.
Therefore, Rupa, as the adopted daughter, and her mother, as the widow, are each entitled to an equal one-half (1/2)
share in the self-acquired properties of the deceased father.

3.  ***Right to Partition for Co-owners***: Any co-owner of a jointly owned property has an inherent legal right to seek partition of the property to define their share. This right can be exercised at any time as long as co-ownership subsists. A partition suit enables the court to determine the shares of each co-owner and order the division of the property accordingly.

4.  ***Mutation Entries Not Conclusive Proof of Title***: The Supreme Court has consistently reiterated that mutation of
property in revenue records neither creates nor extinguishes the title to a property. An entry in revenue records does
not confer title on a person whose name appears in the record-of-rights; such entries have only "fiscal purpose," i.e.,
for the payment of land revenue. The title of a property can only be decided by a competent civil court. Therefore, the
fact that Rupa's name is not yet on the property documents does not negate her ownership rights by inheritance.

5.  ***Injunction Against Alienation by Co-owner***: While generally, a co-owner cannot claim an injunction against
another co-owner with regard to the property owned jointly, an injunction can be granted to preserve the property's
nature and character and prevent wrongful acts like waste, damage, or unauthorized alienation. Courts will grant
injunctions to protect the proprietary rights of co-owners against unauthorized alienation of the entire undivided
property by another co-owner. A co-owner cannot sell the entire property or a specific portion of it so as to bind other
co-owners, unless their share has been formally determined and demarcated through a partition. Temporary injunctions are
granted to prevent irreparable injury and maintain the balance of convenience, especially when one co-owner's actions
could harm shared interests, such as the alienation of joint property.

Based on these principles, the following case laws and citations are relevant:

-   *Smt. Sitabai v. Ramchandra, AIR 1970 SC 343* (Supreme Court): This case affirmed that adoption results in the complete severance of all ties with the birth family, and the adopted child is fully integrated into the adoptive family, thereby creating new legal relationships upon adoption. This supports Rupa's status as a full legal heir in her adoptive family.
-   *Balwant Singh v. Daulat Singh, (1997) 7 SCC 137* (Supreme Court): This judgment clarified that mutation entries do not confer any title nor do they have any presumptive value on ownership. They are primarily for revenue collection and cannot be used as conclusive proof of ownership. This is crucial for Rupa's claim, as her name not being on the documents does not affect her title.
-   *Smt. Sawarni v. Smt. Inder Kaur, 2016 4 Supreme 580* (Supreme Court): This case reiterated that mutation in favour of an individual does not convey title and that mutation entries are only for fiscal purposes.
-   *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1* (Supreme Court): While this landmark ruling primarily dealt with coparcenary rights of daughters in ancestral property, it reinforces the principle of gender equality in inheritance and the equal status of daughters (including adopted daughters by extension of HAMA principles) in property rights. The judgment affirmed that daughters are equal coparceners by birth with equal rights to sons.
-   *Khatri Hotels P.Ltd.& Anr vs Union Of India & Anr, (2011) 9 SCC 126* (Supreme Court): This case, among others, highlights the principle that an injunction can be sought by a co-owner to protect their rights in jointly held property, especially against attempts to dispose of the property. While a blanket injunction against a co-sharer dealing with their own share may not be sustainable, an injunction to preserve the property's nature and character is permissible.
-   *Pazhanathal (D) Thr. Lrs vs Alamathal (D) Thr.Lrs, 2026 SCC OnLine SC 1000* (Supreme Court): This case, while dealing with a counterblast to a partition suit, generally confirms that injunctions could not be granted against co-owners unless specific adverse acts are proven. However, the context of preventing alienation of the *entire* property by one co-owner without partition would warrant an injunction.
-   *Tanusree Basu & Ors vs Ishani Prasad Basu & Ors, AIR 2008 SC 999* (Supreme Court): This case discusses that while a co-owner cannot claim an order of injunction against another co-owner with regard to the property owned jointly, liability to pay compensation arises against a co-owner who deliberately excludes the other co-owners from the enjoyment of the property. This supports the need for partition and protection of Rupa's share.

### JUDGMENT CITATIONS:
The admissibility of electronic evidence under Section 65B of the Indian Evidence Act, 1872, is a critical aspect of modern litigation. The Supreme Court of India has provided extensive guidance on this matter, emphasizing that Section 65B is a special provision that governs the admissibility of electronic records, overriding the general provisions for secondary evidence under Sections 63 and 65 of the Act.

The core principle established by the Supreme Court is that an electronic record, when presented as secondary evidence, is admissible only if the conditions stipulated in Section 65B are strictly satisfied. This typically requires a certificate under Section 65B(4) to accompany the electronic record, unless the original electronic device itself is produced and proved by its owner/operator.

I think this information will help you more to understand the case:

### Key Legal Issues
-   **Admissibility of Electronic Records**: The primary issue revolves around the conditions and procedure for admitting electronic records as evidence in court.
-   **Interpretation of Section 65B of the Indian Evidence Act, 1872**: The courts have extensively interpreted the requirements of Section 65B, particularly subsections (1), (2), and (4).
-   **Distinction between Primary and Secondary Electronic Evidence**: Clarifying when an electronic record can be considered primary evidence (not requiring a Section 65B certificate) versus secondary evidence (mandatorily requiring a certificate).
-   **Role of Certificate under Section 65B(4)**: The necessity, content, and timing of the certificate for the admissibility of electronic evidence.
-   **Conflict with General Law of Evidence**: How Section 65B, as a special law, prevails over general provisions like Sections 63 and 65 for electronic records.
-   **Discretion of the Court**: The extent to which a court can allow the production of a Section 65B certificate at a later stage of the trial.
-   **Vicarious Liability of Hospital**: The liability of a hospital for the negligent acts of its doctors.
-   **Standard of Care in Medical Negligence**: The degree of skill and care expected from medical professionals.

### Detailed Narrative
The provided judgments discuss various scenarios where the admissibility of electronic evidence was challenged.

In *Nyati Builders Pvt Ltd v. Mr. Rajat Dinesh Chauhan and Ors.*, the petitioners (defendant no.1) challenged a trial
court order that permitted the plaintiffs to furnish a fresh certificate under Section 65B(2) and (4) of the Indian
Evidence Act, 1872, for e-mails. The trial court had initially admitted the e-mails subject to objection regarding
admissibility, to be decided at the final argument stage. The High Court, relying on *Anvar P.V. v. P.K. Basheer*,
affirmed that electronic records as secondary evidence require a Section 65B certificate. It held that the trial court
was justified in allowing the plaintiffs to file a fresh certificate, as the issue of admissibility was kept open and
not finally decided. However, the High Court clarified that the e-mails should be treated as secondary evidence, not
primary.

In *AJAY JAGDISHCHANDRA SHARMA v. LARSEN AND TOURBO LTD.*, the petitioner (plaintiff) sought to produce an electronic device (MP3 player) containing voice recordings. The trial court dismissed the application because the device did not satisfy the conditions of Section 65B of the Indian Evidence Act. The High Court upheld this decision, citing *Anvar P.V. v. P.K. Basheer*, which mandates compliance with Section 65B(2) and (4) for the admissibility of electronic records.

In *PURNESH ISHVARBHAI MODI v. STATE OF GUJARAT*, a defamation case, the applicant (complainant) sought to examine
additional witnesses to prove the authenticity and source of electronic records (Pen-drive and CDs) containing alleged
defamatory remarks. The trial court had tentatively exhibited these records subject to proof under the Indian Evidence
Act. The trial court rejected the application, stating that adequate witnesses had already been examined. The High
Court, referring to *Arjun Pandit Rao Khotkar v. Kailash Kushan Rao Gorantyal* and *Anvar P.V. v. P.K. Basheer*,
emphasized that a Section 65B(4) certificate is a condition precedent for admissibility of electronic records as
secondary evidence. It noted that the certificate is a curable defect and can be produced at a later stage, provided it
does not prejudice the accused. The High Court quashed the trial court's order and remitted the matter for a fresh
decision, stressing the importance of a fair trial and the need to prove the electronic evidence correctly.

In *SURESH SHANKAR BOLADE v. MALLAPPA SANGAPPA VHATTE AND OTHERS*, an election petition, the petitioner sought a
direction for the original petitioner to produce a certificate of authenticity for videography recordings before
admitting them. The trial court rejected this, noting that the original petitioner was not in possession of the device
or the CD/pen drive, as they were seized by the police. The High Court dismissed the petition, stating that since the
electronic evidence was not yet led and the original petitioner was not in possession of the device, the application was
premature. It reiterated that electronic evidence is governed by Section 65B.

In *J.J.Extrusion Private Limited v. Commissioner of Central Excise & Service Tax, Jamshedpur*, the demand for duty was
based on printouts from pen drives and computers seized from the appellant's premises. The Tribunal found that the
procedure under Section 36B of the Central Excise Act, 1944 (pari materia to Section 65B of the Indian Evidence Act),
was not followed. Citing *Anvar P.V. v. P.K. Basheer* and *Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal*, the
Tribunal held that printouts from electronic devices are not admissible as evidence without the mandatory certificate
under Section 36B(4). The Tribunal set aside the demand, emphasizing that the entire case hinged on inadmissible
electronic evidence.

In *Ignatius Tony Pereira v. Travel Corporation (India) Pvt. Ltd. and Anr.*, the petitioner sought to admit certificates under Section 65B. The Magistrate rejected the application without detailed reasons. The High Court, while not interfering with the rejection, granted liberty to the petitioner to file fresh certificates compliant with Section 65B, clarifying that the admissibility would be decided by the trial court.

In *DHRUBEN GURALDAS BALANI v. STATE OF GUJARAT*, the petitioner (accused) sought to exhibit electronic evidence (DVDs,
CDs, photographs, and transcripts) in a sessions case, arguing that the victim's partial admission in cross-examination
dispensed with the need for a Section 65B certificate. The High Court rejected this, emphasizing that Section 65B is a
complete code for electronic evidence and its requirements are mandatory. It reiterated that oral admission alone cannot
prove the contents of electronic records and that a certificate is essential for secondary electronic evidence,
especially when the original device is not produced by its owner/operator.

In *HEMCHANDBHAI LALJIBHAI BHABHOR & 2 other(s) v. STATE OF GUJARAT*, the petitioners (accused) challenged the
exhibition of a CD containing CCTV footage without providing a copy to them and without following Section 65B. The High
Court, relying on *P. Gopalkrishnan @ Dileep v. State of Kerala* and *Arjun Panditrao Khotkar v. Kailash Kushanrao
Gorantyal*, held that the accused has a right to a copy of all documents relied upon by the prosecution. It directed
that the CD be de-exhibited, a copy provided to the accused, and its admissibility be determined strictly in accordance
with Section 65B.

In *Deepali Santosh Lokhande v. Mr. Santosh Vasantrao Lokhande*, a Family Court case, the petitioner-wife sought to
exhibit medical bills, receipts, and other documents, some of which were electronic records. The Family Court rejected
this, applying Section 65B of the Indian Evidence Act. The High Court, however, held that Section 14 of the Family
Courts Act, 1984, which has an overriding effect, allows the Family Court to receive any document as evidence if it
assists in dealing with the dispute, irrespective of its relevancy or admissibility under the Indian Evidence Act. Thus,
the strict rigor of Section 65B would not apply to proceedings before the Family Court.

In *M/S ICICI BANK LIMITED v. KAPIL DEV SHARMA*, the bank's suit for recovery was dismissed by the trial court for
failure to file the original loan recall notice. The High Court found this reasoning untenable, stating that the
original notice would be with the defendant. It emphasized that in commercial transactions, especially with electronic
records, strict insistence on primary evidence (like server hard drives) is impractical. The court held that certified
statements of account under the Bankers' Books Evidence Act, 1891, along with a Section 65B certificate, are admissible.
The High Court set aside the trial court's judgment, highlighting the need for a pragmatic approach to electronic
evidence.

### As per the Court
The consistent position of the Supreme Court, as reiterated in *Anvar P.V. v. P.K. Basheer, (2014) 10 SCC 433* and *Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal, (2020) 7 SCC 1*, is that Section 65B of the Indian Evidence Act, 1872, is a special provision governing the admissibility of electronic records. This special law prevails over the general provisions of secondary evidence under Sections 63 and 65 of the Act.

**Key Principles from the Judgments:**

1.  **Mandatory Nature of Section 65B**: An electronic record, when presented as secondary evidence (e.g., printouts, CDs, VCDs, pen drives), shall not be admitted in evidence unless the requirements under Section 65B are satisfied. This includes the mandatory certificate under Section 65B(4).
2.  **Conditions for Admissibility (Section 65B(2))**: The electronic record must have been produced by a computer regularly used to store or process information, the information must have been regularly fed into the computer, the computer must have been operating properly, and the information in the record must be a reproduction or derivation of the information fed into the computer.
3.  **Certificate Requirements (Section 65B(4))**: The certificate must identify the electronic record, describe its manner of production, furnish particulars of the device involved, deal with the conditions mentioned in Section 65B(2), and be signed by a person in a responsible official position related to the device's operation or activity management. This person need only state the matters to the best of their knowledge and belief.
4.  **Primary vs. Secondary Electronic Evidence**:
    -   If the original electronic device (e.g., laptop, mobile phone) on which the information was first stored is produced, and its owner/operator steps into the witness box to prove it, then a Section 65B(4) certificate is unnecessary. This constitutes primary evidence under Section 62.
    -   If the original device cannot be physically brought to court (e.g., a computer system or network), then the information contained in the electronic record can only be proved in accordance with Section 65B(1) along with the requisite certificate under Section 65B(4).
5.  **Timing of Certificate Production**: The certificate can be produced at any stage of the trial, as long as the trial is not over. It is a curable defect, and courts can direct its production, provided it does not cause serious or irreversible prejudice to the opposing party, especially in criminal trials where the accused's right to a fair trial is paramount.
6.  **Mutation Entries and Title**: Mutation entries in revenue records do not confer title; they are for fiscal purposes. Title is established through legal succession, a valid will, or a court order.
7.  **Overriding Effect of Special Laws**: Section 65B, being a special provision for electronic evidence, overrides the general law of secondary evidence under Sections 63 and 65. Similarly, Section 14 of the Family Courts Act, 1984, being a special law, allows Family Courts to admit documents irrespective of their strict compliance with the Indian Evidence Act, if they assist in resolving the dispute.
8.  **Right to Fair Trial**: The right to a fair trial, enshrined in Article 21 of the Constitution, mandates that the accused be furnished with all documents the prosecution intends to rely upon. This includes copies of electronic evidence, which must be provided to the accused to enable them to prepare their defense and raise objections regarding admissibility.
9.  **Pragmatic Approach**: Courts are encouraged to adopt a pragmatic approach, especially in commercial transactions, where insisting on the physical production of original electronic media (like server hard drives) might be impossible. Duly certified printouts of electronic documents, accompanied by a Section 65B certificate, should generally be accepted unless there is a serious challenge to their authenticity (e.g., tampering, forgery).

In essence, the courts have established a clear and stringent framework for the admissibility of electronic evidence, balancing the need for authenticity and reliability with the practicalities of modern digital information.

### Additional Observations
The judgments highlight the evolving nature of evidence law in the digital age. The Supreme Court has repeatedly
emphasized the need for strict compliance with Section 65B to prevent the "travesty of justice" that could result from
tampered or unauthenticated electronic records. The distinction between primary and secondary electronic evidence is
crucial, as is the flexibility allowed for producing the Section 65B certificate at a later stage, provided it does not
prejudice the opposing party. The special dispensation for Family Courts under Section 14 of the Family Courts Act
demonstrates a legislative intent to simplify procedures in specific contexts, allowing for a more flexible approach to
evidence. The ongoing need for appropriate rules and directions under the Information Technology Act, 2000, for data
preservation and retention, is also a recurring theme, underscoring the challenges in managing electronic evidence
effectively.

### PDF Links
-   *Radha Bai v. Ram Narayan, C.A. No. 5889 of 2009, decided on 22-11-2019*: https://api.sci.gov.in/supremecourt/2007/19564/19564_2007_7_1501_18424_Judgement_22-Nov-2019.pdf
-   *Sitabai & Anr. v. Ram Chandra, AIR 1970 SC 343, decided on 20-08-1969*: https://api.sci.gov.in/jonew/judis/1674.pdf
-   *Nagabhushanammal (D) Thru Lrs. v. C. Chandikeswaralingam, C.A. Nos. 1858-1859 of 2016, decided on 26-02-2016*: https://api.sci.gov.in/jonew/judis/43448.pdf
-   *Kalindi Damodar Garde (D) By Lrs. v. Manohar Laxman Kulkarni And Ors., C.A. Nos. 6642-6643 of 2010, decided on 07-02-2020*: https://api.sci.gov.in/supremecourt/2007/14460/14460_2007_11_1502_20374_Judgement_07-Feb-2020.pdf
-   *Rajinder Singh v. State of Jammu & Kashmir & Ors., C.A. No. 5269 of 2003, decided on 11-07-2008*: https://api.sci.gov.in/jonew/judis/32009.pdf
-   *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1*: https://api.sci.gov.in/supremecourt/2010/37409/37409_2010_3_1501_23469_Judgement_11-Aug-2020.pdf

### SCI_JUDGMENT CITATIONS:
The Supreme Court has delivered several landmark rulings on the admissibility of electronic evidence under Section 65B of the Indian Evidence Act, 1872. The most significant cases are *Anvar P.V. v. P.K. Basheer* (2014) and *Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal* (2020), which clarified the requirements for such evidence.

I think this information will help you more to understand the case:

### Key Legal Issues
-   Interpretation of Section 65B of the Indian Evidence Act, 1872, regarding the admissibility of electronic records.
-   Distinction between primary and secondary electronic evidence and the necessity of a certificate under Section 65B(4).
-   The stage at which a Section 65B certificate can be produced and whether its non-production is a curable defect.
-   Reconciliation of conflicting judgments on the issue of electronic evidence admissibility.

### Detailed Narrative

#### *Anvar P.V. v. P.K. Basheer, C.A. No.-004226-004226 - 2012, Judgment Date: 18-09-2014*
This case arose from an election petition challenging the election of P.K. Basheer to the Kerala Legislative Assembly.
The appellant, Anvar P.V., sought to set aside the election on grounds of corrupt practices, relying significantly on
electronic records (CDs containing speeches, songs, and announcements). The High Court dismissed the election petition,
finding that the corrupt practices were not proven. The Supreme Court was tasked with determining the nature and manner
of admission of these electronic records. The Court noted that electronic records are documentary evidence under Section
3 of the Evidence Act and that the Act underwent significant amendments with the Information Technology Act, 2000,
introducing Sections 65A and 65B.

#### *Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal, C.A. No.-020825-020826 - 2017, Judgment Date: 14-07-2020*
This case involved two election petitions challenging the election of Arjun Panditrao Khotkar to the Maharashtra State
Legislative Assembly. The core of the dispute revolved around the timely filing of nomination papers, with the
respondents relying on video recordings (CDs/VCDs) from the Election Commission's office to prove that the papers were
filed after the stipulated time. The High Court admitted these electronic records based on "substantial compliance" with
Section 65B, even though a formal written certificate was not initially furnished. The High Court relied on the oral
evidence of the Returning Officer to satisfy the conditions of Section 65B. The matter was referred to a three-judge
bench of the Supreme Court to reconcile conflicting judgments, particularly *Anvar P.V.* and *Shafhi Mohammad v. State
of Himachal Pradesh* (2018).

#### *The State of Karnataka v. T Naseer @ Nasir @ Thandiantavida Naseer @ Umarhazi @ Hazi, Crl.A. No.-003456-003456 - 2023, Judgment Date: 06-11-2023*
This case stemmed from serial bomb blasts in Bangalore, where electronic devices were seized and a CFSL report was
prepared. The trial court deemed the CFSL report inadmissible without a Section 65B certificate. The prosecution later
obtained a certificate and sought to recall a witness to produce it, but the application was rejected by the trial court
and upheld by the High Court on grounds of delay. The State of Karnataka appealed to the Supreme Court, arguing that the
certificate could be furnished at any stage and that the original electronic devices were primary evidence.

### Court Observations

#### *Anvar P.V. v. P.K. Basheer*
The Supreme Court in *Anvar P.V.* held that electronic records, being secondary evidence, require strict compliance with
the conditions laid down in Section 65B of the Evidence Act for their admissibility. The Court emphasized that the
purpose of Section 65B is to authenticate electronic records, which are susceptible to tampering. It clarified that if
an electronic record itself is used as primary evidence under Section 62 of the Evidence Act, then the conditions of
Section 65B are not required. However, for secondary electronic evidence (like printouts, copies in optical or magnetic
media), a certificate under Section 65B(4) is mandatory. This certificate must identify the electronic record, describe
the manner of its production, give particulars of the device, and deal with the conditions mentioned in Section 65B(2),
and must be signed by a person occupying a responsible official position.

#### *Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal*
The three-judge bench in *Arjun Panditrao Khotkar* largely affirmed the principles laid down in *Anvar P.V.*,
reiterating the mandatory nature of the Section 65B certificate for secondary electronic evidence. The Court clarified
that the "non obstante" clause in Section 65B(1) means that the admissibility and proof of information contained in an
electronic record must follow the procedure of Section 65B, rendering Sections 62 to 65 irrelevant for this purpose. It
distinguished between the original electronic record (primary evidence) and computer output (secondary evidence),
stating that the certificate is unnecessary when the original electronic record itself is produced. The Court also held
that the certificate under Section 65B(4) can be produced at any stage of the trial, provided that the party seeking to
produce it has been diligent and has not acted in bad faith. The Court overruled *Shafhi Mohammad*, which had suggested
that a certificate could be dispensed with in the interest of justice.

#### *The State of Karnataka v. T Naseer @ Nasir @ Thandiantavida Naseer @ Umarhazi @ Hazi*
In this recent judgment, the Supreme Court, relying on *Anvar P.V.* and *Arjun Panditrao Khotkar*, reiterated that a
certificate under Section 65B is not required if the electronic record is used as primary evidence. The Court also
emphasized that the non-production of a Section 65B certificate is a curable defect, and an opportunity should be given
to the prosecution to rectify the deficiency. The Court found that the trial court and High Court erred in rejecting the
application to produce the certificate on the ground of delay, especially in a serious case involving bomb blasts.

### Judgment & Direction

#### *Anvar P.V. v. P.K. Basheer*
The Supreme Court dismissed the appeal, upholding the High Court's decision to reject the electronic evidence due to non-compliance with Section 65B. The Court clarified the legal position on electronic evidence, establishing the mandatory nature of the Section 65B certificate for secondary electronic records.

#### *Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal*
The Supreme Court set aside the High Court's judgment, which had relied on electronic evidence without a proper Section 65B certificate. The Court clarified that the certificate is mandatory for secondary electronic evidence and can be produced at any stage. The case was remanded for further proceedings in accordance with the clarified legal position.

#### *The State of Karnataka v. T Naseer @ Nasir @ Thandiantavida Naseer @ Umarhazi @ Hazi*
The Supreme Court allowed the appeal, setting aside the orders of the High Court and the trial court. The Court permitted the prosecution to recall the witness and produce the certificate under Section 65B of the Evidence Act, emphasizing that the non-production of the certificate was a curable defect and that the original electronic devices were already on record as primary evidence.

### Broader Significance
These judgments collectively establish a clear and consistent legal framework for the admissibility of electronic
evidence in India. They underscore the importance of Section 65B as a special provision governing electronic records,
ensuring their authenticity and reliability. The rulings clarify the distinction between primary and secondary
electronic evidence and the mandatory requirement of a certificate for the latter, while also providing flexibility for
its production at various stages of a trial.

**PDF Links:**
-   *ARJUN PANDITRAO KHOTKAR VS KAILASH KUSHANRAO GORANTYAL*: [https://api.sci.gov.in/supremecourt/2017/39058/39058_2017_34_1501_22897_Judgement_14-Jul-2020.pdf](https://api.sci.gov.in/supremecourt/2017/39058/39058_2017_34_1501_22897_Judgement_14-Jul-2020.pdf)
-   *ANVAR P.V. VS P.K.BASHEER .*: [https://api.sci.gov.in/jonew/judis/41931.pdf](https://api.sci.gov.in/jonew/judis/41931.pdf)
-   *THE STATE OF KARNATAKA VS T NASEER @ NASIR @ THANDIANTAVIDA NASEER @ UMARHAZI @ HAZI*: [https://api.sci.gov.in/supremecourt/2022/19800/19800_2022_11_1504_48074_Judgement_06-Nov-2023.pdf](https://api.sci.gov.in/supremecourt/2022/19800/19800_2022_11_1504_48074_Judgement_06-Nov-2023.pdf)

### NEWACTS CITATIONS:
Section 65B of the Indian Evidence Act, 1872, deals with the admissibility of electronic records, and its corresponding new provision is Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

### Old Provision: Section 65B of Indian Evidence Act, 1872

(1) Notwithstanding anything contained in this Act, any information contained in an electronic record which is printed
on a paper, stored, recorded or copied in optical or magnetic media produced by a computer (hereinafter referred to as
the computer output) shall be deemed to be also a document, if the conditions mentioned in this section are satisfied in
relation to the information and computer in question and shall be admissible in any proceedings, without further proof
or production of the original, as evidence of any contents of the original or of any fact stated therein of which direct
evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer during the period over which the computer was used regularly to store or process information for the purposes of any activities regularly carried on over that period by the person having lawful control over the use of the computer;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer in the ordinary course of the said activities.

(3) Where over any period, the function of storing or processing information for the purposes of any activities regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by computers, whether-
(a) by a combination of computers operating over that period; or
(b) by different computers operating in succession over that period; or
(c) by different combinations of computers operating in succession over that period; or
(d) in any other manner involving the successive operation over that period, in whatever order, of one or more computers and one or more combinations of computers.
all the computers used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer; and references in this section to a computer shall be construed accordingly.

(4) In any proceedings where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things, that is to say,
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer;
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate,
and purporting to be signed by a person occupying a responsible official position in relation to the operation of the relevant device or the management of the relevant activities (whichever is appropriate) shall be evidence of any matter stated in the certificate; and for the purposes of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the person stating it.

(5) For the purposes of this section,
(a) information shall be taken to be supplied to a computer if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) whether in the course of activities carried on by any official, information is supplied with a view to its being stored or processed for the purposes of those activities by a computer operated otherwise than in the course of those activities, that information, if duly supplied to that computer, shall be taken to be supplied to it in the course of those activities;
(c) a computer output shall be taken to have been produced by a computer whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment.

Explanation: For the purposes of this section any reference to information being derived from other information shall be a reference to its being derived therefrom by calculation, comparison or any other process.

### New Provision: Section 63 of Bharatiya Sakshya Adhiniyam, 2023

(1) Notwithstanding anything contained in this Adhiniyam, any information contained in an electronic record which is
printed on paper, stored, recorded or copied in optical or magnetic media or semiconductor memory which is produced by a
computer or any communication device or otherwise stored, recorded or copied in any electronic form (hereinafter
referred to as the computer output) shall be deemed to be also a document, if the conditions mentioned in this section
are satisfied in relation to the information and computer in question and shall be admissible in any proceedings,
without further proof or production of the original, as evidence or any contents of the original or of any fact stated
therein of which direct evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer or communication device during the period over which the computer or communication device was used regularly to create, store or process information for the purposes of any activity regularly carried on over that period by the person having lawful control over the use of the computer or communication device;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer or communication device in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer or communication device was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer or communication device in the ordinary course of the said activities.

(3) Where over any period, the function of creating, storing or processing information for the purposes of any activity regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by means of one or more computers or communication device, whether:
(a) in standalone mode; or
(b) on a computer system; or
(c) on a computer network; or
(d) on a computer resource enabling information creation or providing information processing and storage; or
(e) through an intermediary,
all the computers or communication devices used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer or communication device; and references in this section to a computer or communication device shall be construed accordingly.

(4) In any proceeding where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things shall be submitted along with the electronic record at each instance where it is being submitted for admission, namely:
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer or a communication device referred to in clauses (a) to (e) of sub-section (3);
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate, and purporting to be
signed by a person in charge of the computer or communication device or the management of the relevant activities
(whichever is appropriate) and an expert shall be evidence of any matter stated in the certificate; and for the purposes
of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the
person stating it in the certificate specified in the Schedule.

(5) For the purposes of this section,:
(a) information shall be taken to be supplied to a computer or communication device if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) a computer output shall be taken to have been produced by a computer or communication device whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment or by other electronic means as referred to in clauses (a) to (e) of sub-section (3).

### Key Differences

-   **Scope of Devices**: The new provision explicitly includes "communication device" alongside "computer" throughout the section, broadening the scope of electronic records covered. It also adds "semiconductor memory" as a storage medium.
-   **Computer Output Definition**: The new provision expands the definition of "computer output" to include information "otherwise stored, recorded or copied in any electronic form."
-   **Activities in Sub-section (2)(a)**: The new provision adds "create" to the activities for which the computer or communication device was used regularly, i.e., "to create, store or process information."
-   **Complex Systems in Sub-section (3)**: The new provision significantly updates the description of how functions are performed by multiple devices, replacing the older clauses (a) to (d) with more modern terms like "standalone mode," "computer system," "computer network," "computer resource enabling information creation or providing information processing and storage," and "through an intermediary."
-   **Certificate Requirements in Sub-section (4)**: The new provision specifies that the certificate must be submitted "at each instance where it is being submitted for admission" and adds that it should be signed by "an expert" in addition to the person in charge. It also refers to a "certificate specified in the Schedule."
-   **Sub-section (5) Changes**: The new provision removes clause (b) of the old sub-section (5) and modifies clause (c) to clause (b), expanding the means by which a computer output is produced to include "other electronic means as referred to in clauses (a) to (e) of sub-section (3)."
-   **Explanation**: The Explanation from the old provision is omitted in the new one.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

Follow-up suggestions returned: `What are the certification requirements?`, `What if original data is lost?`, `How is authenticity proven?`

---

### Turn 5 — Anticipatory bail case laws

*Source section in `docs/test prompt.txt`: **Citations***

#### 1. User prompt (verbatim)

```text
Provide citations or case laws related to anticipatory bail
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 59 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 5 |
| History turns loaded | 4 |
| Query rewritten by memory agent | True |
| Effective query after rewrite | `Case laws on anticipatory bail` |
| Agents planned | Judgment, SCI_Judgment |
| Agents used | Judgment, SCI_Judgment |
| Sources returned | 43 |
| Wall clock | 22.8s |
| First streamed token | 11.824s |

<details>
<summary>Sources cited back to the client (43)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | legaleye.co.in | Scenario | None |
| 3 | scenario | pahujalawacademy.com | Scenario | None |
| 4 | scenario | lawbhoomi.com | Scenario | None |
| 5 | scenario | medium.com | Scenario | None |
| 6 | scenario | ipleaders.in | Scenario | None |
| 7 | scenario | lawgratis.com | Scenario | None |
| 8 | scenario | emkaywealth.com | Scenario | None |
| 9 | scenario | khannaandassociates.com | Scenario | None |
| 10 | scenario | legalhelpnri.com | Scenario | None |
| 11 | scenario | urbanrightslegal.com | Scenario | None |
| 12 | scenario | ipleaders.in | Scenario | None |
| 13 | scenario | ladderuptrustee.com | Scenario | None |
| 14 | scenario | lclawfirm.in | Scenario | None |
| 15 | scenario | indiafilings.com | Scenario | None |
| 16 | scenario | viveklegal.in | Scenario | None |
| 17 | scenario | 99acres.com | Scenario | None |
| 18 | scenario | supremetoday.ai | Scenario | None |
| 19 | scenario | indiatimes.com | Scenario | None |
| 20 | scenario | supremetoday.ai | Scenario | None |
| 21 | scenario | khaadvocates.com | Scenario | None |
| 22 | scenario | scconline.com | Scenario | None |
| 23 | scenario | supremetoday.ai | Scenario | None |
| 24 | scenario | supremetoday.ai | Scenario | None |
| 25 | scenario | supremetoday.ai | Scenario | None |
| 26 | scenario | youtube.com | Scenario | None |
| 27 | scenario | supremetoday.ai | Scenario | None |
| 28 | scenario | indiankanoon.org | Scenario | None |
| 29 | scenario | mahendrabhavsar.com | Scenario | None |
| 30 | scenario | legalhelpnri.com | Scenario | None |
| 31 | scenario | scribd.com | Scenario | None |
| 32 | scenario | basichomeloan.com | Scenario | None |
| 33 | judgment | Gorabai vs State | Judgment | 155.22325 |
| 34 | judgment | NARENDRAKUMAR KESHAVLAL PATEL vs STATE OF GUJARAT | Judgment | 152.82469 |
| 35 | judgment | Faju Sk alias Sekh Faju vs State | Judgment | 152.05803 |
| 36 | judgment | IMTHIYAS vs STATE OF KERALA | Judgment | 146.81003 |
| 37 | judgment | GUDDU @ BIHARI @ NARESH SINGH vs THE STATE OF MADHYA PRADESH | Judgment | 143.81436 |
| 38 | sci_judgment | BALMUKUND SINGH GAUTAM VS STATE OF MADHYA PRADESH | SCI_Judgment | None |
| 39 | sci_judgment | SATYAJIT BALLUBHAI DESAI VS STATE OF GUJARAT | SCI_Judgment | None |
| 40 | sci_judgment | POKAR RAM VS STATE OF RAJASTHAN AND ANR | SCI_Judgment | None |
| 41 | sci_judgment | PRIYA INDORIA VS THE STATE OF KARNATAKA | SCI_Judgment | None |
| 42 | sci_judgment | SUSHILA AGGARWAL VS STATE (NCT OF DELHI) | SCI_Judgment | None |
| 43 | newacts | Indian Evidence Act 1872 - Section 65B | Newacts | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (48,155 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
The following case laws and legal principles are relevant to the scenario presented:

1.  ***Rights of an Adopted Daughter***: Under the Hindu Adoptions and Maintenance Act, 1956 (HAMA), an adopted child is
deemed to be the child of their adoptive parents for all purposes from the date of adoption, acquiring the same status,
rights, and obligations as a biological child. This includes the right to inherit both ancestral and self-acquired
property of the adoptive parents. Section 12 of HAMA explicitly states that an adopted child shall be deemed to be the
child of his or her adoptive father or mother for all purposes with effect from the date of the adoption, and from such
date all the ties of the child in the family of his or her birth shall be deemed to be severed and replaced by those
created by the adoption in the adoptive family.

2.  ***Intestate Succession under Hindu Succession Act, 1956***: When a Hindu male dies intestate (without a will), his
property devolves upon his Class I legal heirs simultaneously. Class I heirs include the mother, widow, sons, and
daughters. As per Section 10 of the Hindu Succession Act, 1956, the widow and the daughter each take one equal share.
Therefore, Rupa, as the adopted daughter, and her mother, as the widow, are each entitled to an equal one-half (1/2)
share in the self-acquired properties of the deceased father.

3.  ***Right to Partition for Co-owners***: Any co-owner of a jointly owned property has an inherent legal right to seek partition of the property to define their share. This right can be exercised at any time as long as co-ownership subsists. A partition suit enables the court to determine the shares of each co-owner and order the division of the property accordingly.

4.  ***Mutation Entries Not Conclusive Proof of Title***: The Supreme Court has consistently reiterated that mutation of
property in revenue records neither creates nor extinguishes the title to a property. An entry in revenue records does
not confer title on a person whose name appears in the record-of-rights; such entries have only "fiscal purpose," i.e.,
for the payment of land revenue. The title of a property can only be decided by a competent civil court. Therefore, the
fact that Rupa's name is not yet on the property documents does not negate her ownership rights by inheritance.

5.  ***Injunction Against Alienation by Co-owner***: While generally, a co-owner cannot claim an injunction against
another co-owner with regard to the property owned jointly, an injunction can be granted to preserve the property's
nature and character and prevent wrongful acts like waste, damage, or unauthorized alienation. Courts will grant
injunctions to protect the proprietary rights of co-owners against unauthorized alienation of the entire undivided
property by another co-owner. A co-owner cannot sell the entire property or a specific portion of it so as to bind other
co-owners, unless their share has been formally determined and demarcated through a partition. Temporary injunctions are
granted to prevent irreparable injury and maintain the balance of convenience, especially when one co-owner's actions
could harm shared interests, such as the alienation of joint property.

Based on these principles, the following case laws and citations are relevant:

-   *Smt. Sitabai v. Ramchandra, AIR 1970 SC 343* (Supreme Court): This case affirmed that adoption results in the complete severance of all ties with the birth family, and the adopted child is fully integrated into the adoptive family, thereby creating new legal relationships upon adoption. This supports Rupa's status as a full legal heir in her adoptive family.
-   *Balwant Singh v. Daulat Singh, (1997) 7 SCC 137* (Supreme Court): This judgment clarified that mutation entries do not confer any title nor do they have any presumptive value on ownership. They are primarily for revenue collection and cannot be used as conclusive proof of ownership. This is crucial for Rupa's claim, as her name not being on the documents does not affect her title.
-   *Smt. Sawarni v. Smt. Inder Kaur, 2016 4 Supreme 580* (Supreme Court): This case reiterated that mutation in favour of an individual does not convey title and that mutation entries are only for fiscal purposes.
-   *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1* (Supreme Court): While this landmark ruling primarily dealt with coparcenary rights of daughters in ancestral property, it reinforces the principle of gender equality in inheritance and the equal status of daughters (including adopted daughters by extension of HAMA principles) in property rights. The judgment affirmed that daughters are equal coparceners by birth with equal rights to sons.
-   *Khatri Hotels P.Ltd.& Anr vs Union Of India & Anr, (2011) 9 SCC 126* (Supreme Court): This case, among others, highlights the principle that an injunction can be sought by a co-owner to protect their rights in jointly held property, especially against attempts to dispose of the property. While a blanket injunction against a co-sharer dealing with their own share may not be sustainable, an injunction to preserve the property's nature and character is permissible.
-   *Pazhanathal (D) Thr. Lrs vs Alamathal (D) Thr.Lrs, 2026 SCC OnLine SC 1000* (Supreme Court): This case, while dealing with a counterblast to a partition suit, generally confirms that injunctions could not be granted against co-owners unless specific adverse acts are proven. However, the context of preventing alienation of the *entire* property by one co-owner without partition would warrant an injunction.
-   *Tanusree Basu & Ors vs Ishani Prasad Basu & Ors, AIR 2008 SC 999* (Supreme Court): This case discusses that while a co-owner cannot claim an order of injunction against another co-owner with regard to the property owned jointly, liability to pay compensation arises against a co-owner who deliberately excludes the other co-owners from the enjoyment of the property. This supports the need for partition and protection of Rupa's share.

### JUDGMENT CITATIONS:
Here are some case laws and citations relevant to anticipatory bail under Section 438 of the Code of Criminal Procedure, 1973, based on the provided judgments:

### Case Laws on Anticipatory Bail

1.  ***Narendrakumar Keshavlal Patel v. State of Gujarat, Special Criminal Application No. 535 of 2001, decided on 16/07/2001***
    -   **Court**: High Court of Gujarat at Ahmedabad
    -   **Bench**: Hon'ble Mr. Justice H.K. Rathod
-   **Key Legal Principle**: This case clarifies the effect of an anticipatory bail order once a case is committed to
the Sessions Court. The High Court held that when anticipatory bail has been granted by the High Court with conditions,
and subsequently, the Magistrate also grants regular bail while committing the case, there is no necessity for the
petitioner to obtain fresh regular bail from the Sessions Court. The bail granted by the Magistrate should be treated as
regular bail. This judgment emphasizes the continuity of bail protection, especially when the conditions of anticipatory
bail have been complied with.

2.  ***M.Cr.C.No.5456/2012, decided on 17.07.2012***
    -   **Court**: [Not explicitly stated, but likely a High Court given the M.Cr.C. format]
    -   **Bench**: Hon'ble Mrs. Justice S.R. Waghmare
-   **Key Legal Principle**: This case illustrates a scenario where anticipatory bail was not granted due to the nature
of the allegations (dowry death under Section 304-B, 498-A/34 of the IPC, with death occurring within one year of
marriage and dowry demand established). However, the court partly allowed the application by directing the applicants
(ladies, including a 65-year-old and a separately residing sister-in-law) to surrender before the competent court by a
specific date and apply for regular bail. Crucially, the court directed that the applicants "shall not be apprehended or
arrested" until the specified surrender date, providing a temporary protective window. This highlights the court's
discretion to balance the severity of the offense with humanitarian considerations, offering a limited period of
protection before surrender.

3.  ***BA.No. 670 of 2012, decided on 7th day of February 2012***
    -   **Court**: High Court of Kerala at Ernakulam
    -   **Bench**: Hon'ble Mr. Justice M. Sasidharan Nambiar
-   **Key Legal Principle**: This judgment demonstrates a straightforward rejection of an anticipatory bail application
where the court, after perusing the case diary, did not find it a fit case for anticipatory bail. The petitioner was
advised to surrender before the concerned Magistrate and seek regular bail. This reflects the principle that
anticipatory bail is not a matter of right and is granted only when the court is satisfied that the applicant deserves
such protection based on the facts and circumstances of the case.

4.  ***MCRC-20277-2016 (Guddu @ Bihari @ Naresh Singh v. The State of Madhya Pradesh), decided on 20-12-2016***
    -   **Court**: [Not explicitly stated, but likely a High Court given the MCRC format]
    -   **Bench**: Hon'ble Mr. Justice S.K. Seth
-   **Key Legal Principle**: This case emphasizes that the conduct of the accused, such as absconding, can be a
distinguishing factor in denying anticipatory bail. The court rejected the application, noting that the applicants were
absconding at the time of filing the challan, distinguishing their case from co-accused who had been granted
anticipatory bail. The court cited *State of M.P. v. Pradeep Sharma, AIR 2014 SC 626*, which likely deals with the
conduct of the accused as a ground for denying anticipatory bail. The applicants were, however, given the liberty to
surrender and apply for regular bail.

5.  ***C.R.M. 2852 of 2020 (Faju Sk alias Sekh Faju), decided on 11.03.2021***
    -   **Court**: High Court at Calcutta
    -   **Bench**: Hon'ble Mr. Justice Tapabrata Chakraborty, Hon'ble Mr. Justice Tirthankar Ghosh
-   **Key Legal Principle**: This judgment illustrates a conditional grant of anticipatory bail in an offense under the
Electricity Act, 2003. The court noted that the petitioner had paid a significant amount to the electricity company.
Consequently, the court directed the petitioner to surrender before the learned Special Court and pray for regular bail,
with a specific direction that the Special Court "would release the petitioner... on regular bail upon furnishing a bond
of Rs.10,000/-... and on condition that the petitioner shall attend the Special Court on each and every date." The order
of 'not to arrest' was continued for a limited period to allow the petitioner to approach the Special Court. This
highlights the court's power to issue specific directions for regular bail while disposing of an anticipatory bail
application, especially when some conditions (like payment) have been met.

### Summary of Principles from the Judgments:

-   **Discretionary Power**: Grant of anticipatory bail is a discretionary power of the court, not a right.
-   **Nature of Allegations**: The severity and nature of the allegations (e.g., dowry death) are crucial factors in deciding anticipatory bail applications.
-   **Conduct of Accused**: The conduct of the accused, such as absconding, can be a strong ground for denying anticipatory bail.
-   **Conditions and Directions**: Courts can impose conditions, including directing surrender and application for regular bail within a specified period, sometimes coupled with interim protection from arrest.
-   **Continuity of Bail**: An anticipatory bail order, once granted and complied with, can provide continuous protection, and a fresh regular bail may not be required if the conditions are met and the case is committed to a higher court.
-   **Humanitarian Considerations**: Courts may consider humanitarian aspects (e.g., age, gender) while deciding on interim protection, even if anticipatory bail is ultimately denied.

### PDF Links
-   *Radha Bai v. Ram Narayan, C.A. No. 5889 of 2009, decided on 22-11-2019*: https://api.sci.gov.in/supremecourt/2007/19564/19564_2007_7_1501_18424_Judgement_22-Nov-2019.pdf
-   *Sitabai & Anr. v. Ram Chandra, AIR 1970 SC 343, decided on 20-08-1969*: https://api.sci.gov.in/jonew/judis/1674.pdf
-   *Nagabhushanammal (D) Thru Lrs. v. C. Chandikeswaralingam, C.A. Nos. 1858-1859 of 2016, decided on 26-02-2016*: https://api.sci.gov.in/jonew/judis/43448.pdf
-   *Kalindi Damodar Garde (D) By Lrs. v. Manohar Laxman Kulkarni And Ors., C.A. Nos. 6642-6643 of 2010, decided on 07-02-2020*: https://api.sci.gov.in/supremecourt/2007/14460/14460_2007_11_1502_20374_Judgement_07-Feb-2020.pdf
-   *Rajinder Singh v. State of Jammu & Kashmir & Ors., C.A. No. 5269 of 2003, decided on 11-07-2008*: https://api.sci.gov.in/jonew/judis/32009.pdf
-   *Vineeta Sharma v. Rakesh Sharma, (2020) 9 SCC 1*: https://api.sci.gov.in/supremecourt/2010/37409/37409_2010_3_1501_23469_Judgement_11-Aug-2020.pdf
-   *ARJUN PANDITRAO KHOTKAR VS KAILASH KUSHANRAO GORANTYAL*: https://api.sci.gov.in/supremecourt/2017/39058/39058_2017_34_1501_22897_Judgement_14-Jul-2020.pdf
-   *ANVAR P.V. VS P.K.BASHEER .*: https://api.sci.gov.in/jonew/judis/41931.pdf
-   *THE STATE OF KARNATAKA VS T NASEER @ NASIR @ THANDIANTAVIDA NASEER @ UMARHAZI @ HAZI*: https://api.sci.gov.in/supremecourt/2022/19800/19800_2022_11_1504_48074_Judgement_06-Nov-2023.pdf

### SCI_JUDGMENT CITATIONS:
The Supreme Court has delivered several landmark judgments on anticipatory bail. Here are three significant cases:

#### *Balmukund Singh Gautam v. State of Madhya Pradesh*, Crl.A. No.-000885-000885 - 2026 ([Judgment PDF](https://api.sci.gov.in/supremecourt/2024/17445/17445_2024_15_1501_68382_Judgement_13-Feb-2026.pdf))

This recent judgment from 2026 discusses the principles of anticipatory bail, particularly in the context of an accused
who has been absconding. The Court reiterated that anticipatory bail means "bail in anticipation of arrest" and that the
discretion to grant it should be exercised judiciously. The case involved a situation where the High Court had directed
the accused to surrender and apply for regular bail, with a further direction to the trial court to grant bail on the
same day. The Supreme Court examined whether such a direction was appropriate, especially when the accused had been
absconding and had previous anticipatory bail applications dismissed.

#### *Sushila Aggarwal v. State (NCT of Delhi)*, SLP(Crl) No.-007281-007282 - 2017 ([Judgment PDF](https://api.sci.gov.in/supremecourt/2017/28027/28027_2017_Judgement_15-May-2018.pdf))

This 2018 judgment addresses the crucial question of whether an anticipatory bail order should be for a limited period.
The Court noted two divergent views on this matter. One line of judgments, relying on the Constitution Bench decision in
*Gurbaksh Singh Sibbia v. State of Punjab*, held that anticipatory bail should ordinarily continue till the trial of the
case. The other view, as seen in cases like *Salauddin Abdulsamad Shaikh v. State of Maharashtra*, suggested that
anticipatory bail orders should be of limited duration, requiring the accused to approach the regular court for bail
after investigation is complete or charge-sheet is filed. The judgment in *Sushila Aggarwal* delves into these
conflicting interpretations and their implications.

#### *Priya Indoria v. The State of Karnataka*, Crl.A. No.-003549-003552 - 2023 ([Judgment PDF](https://api.sci.gov.in/supremecourt/2023/7943/7943_2023_14_1501_48299_Judgement_20-Nov-2023.pdf))

This 2023 judgment addresses the complex issue of "extra-territorial anticipatory bail," i.e., whether a High Court or
Court of Session can grant anticipatory bail for an offence registered outside its territorial jurisdiction. The case
arose from a factual scenario where a complainant-wife filed an FIR in Rajasthan, while the accused-husband and his
family sought anticipatory bail in Bengaluru, Karnataka. The Supreme Court considered the divergent approaches of
various High Courts on this matter and the concept of "transit anticipatory bail" to provide interim relief to an
accused residing in a different state.

I think this information will help you more to understand the case:

### Key Legal Issues

-   **Balmukund Singh Gautam v. State of Madhya Pradesh**:
    -   Whether a High Court can direct a trial court to grant regular bail on the same day after the accused surrenders, especially when the accused has been absconding.
    -   The scope of discretion in granting anticipatory bail under Section 438 of the Code of Criminal Procedure, 1973 (CrPC).
    -   The impact of an accused being a proclaimed offender or absconding on their right to anticipatory bail.

-   **Sushila Aggarwal v. State (NCT of Delhi)**:
    -   Whether an order of anticipatory bail should be for a limited period or should continue till the conclusion of the trial.
    -   Reconciliation of conflicting judgments regarding the duration of anticipatory bail.
    -   Interpretation of Section 438 CrPC in light of the Constitution Bench decision in *Gurbaksh Singh Sibbia*.

-   **Priya Indoria v. The State of Karnataka**:
    -   The territorial jurisdiction of a High Court or Court of Session to grant anticipatory bail for an offence registered outside its territorial limits.
    -   The concept and validity of "extra-territorial anticipatory bail" or "transit anticipatory bail."
    -   The application of Section 438 CrPC in cases involving inter-state matrimonial disputes and criminal complaints.

### Detailed Narrative

-   **Balmukund Singh Gautam v. State of Madhya Pradesh**: The case originated from a political rivalry leading to three
FIRs. The accused in this appeal was absconding since the incident in 2017, and a reward was declared for his arrest.
His previous anticipatory bail applications were dismissed by the trial court and the High Court. However, after the
co-accused were acquitted in the main FIR, the High Court disposed of the accused's third anticipatory bail application,
directing him to surrender and the trial court to grant regular bail on the same day. The original complainant
challenged this order before the Supreme Court.

-   **Sushila Aggarwal v. State (NCT of Delhi)**: This case involved a reference to a larger bench to resolve the
conflict regarding the duration of anticipatory bail. The Court examined various precedents, including the Constitution
Bench decision in *Gurbaksh Singh Sibbia*, which suggested that anticipatory bail should ordinarily continue till the
trial. It also considered judgments like *Salauddin Abdulsamad Shaikh*, which advocated for limited duration
anticipatory bail, requiring the accused to seek regular bail after investigation.

-   **Priya Indoria v. The State of Karnataka**: The complainant-wife filed an FIR in Rajasthan for dowry harassment and
cruelty against her husband and his family, who resided in Bengaluru. The accused sought anticipatory bail from the
Sessions Judge in Bengaluru, which was granted. The complainant-wife challenged this grant of anticipatory bail, raising
the question of whether a court in one state could grant anticipatory bail for an FIR registered in another state. The
Supreme Court appointed an amicus curiae to assist in addressing the ramifications of extra-territorial anticipatory
bail.

### Court Observations

-   **Balmukund Singh Gautam v. State of Madhya Pradesh**: The Supreme Court observed that the High Court's direction to
the trial court to grant regular bail on the same day after surrender was problematic, especially given the accused's
history of absconding and the serious nature of the charges (including murder). The Court emphasized that the discretion
to grant bail should be exercised by the trial court based on the material before it, and a blanket direction to grant
bail could undermine the judicial process. The Court also noted that while the acquittal of co-accused could be a
relevant factor, it does not automatically entitle an absconding accused to anticipatory bail.

-   **Sushila Aggarwal v. State (NCT of Delhi)**: The Supreme Court, in its detailed analysis, affirmed that the
protection granted under Section 438 CrPC should not be limited to a fixed period. It held that the life of an
anticipatory bail order does not automatically expire at the time of filing of the charge sheet or at the time of taking
cognizance. The Court emphasized that the conditions for granting anticipatory bail should be reasonable and not unduly
restrictive. It clarified that the power to grant anticipatory bail is not to be used to bypass the regular court but to
protect an individual from unwarranted arrest.

-   **Priya Indoria v. The State of Karnataka**: The Supreme Court acknowledged the practical difficulties faced by
individuals in inter-state cases and the need for a mechanism to address apprehension of arrest in a different state.
The Court discussed the concept of "transit anticipatory bail" as an equitable and interim relief. It clarified that
while a court may grant transit anticipatory bail to enable an accused to approach the jurisdictional court, the
ultimate decision on regular anticipatory bail rests with the court having territorial jurisdiction over the offence.
The judgment provided guidelines for the exercise of such jurisdiction, emphasizing the need for a balance between
protecting individual liberty and ensuring effective investigation.

### Judgment & Direction

-   **Balmukund Singh Gautam v. State of Madhya Pradesh**: The Supreme Court set aside the High Court's order directing the trial court to grant regular bail on the same day. It held that such a direction was unsustainable in law. The Court directed the accused to surrender before the trial court, and the trial court was to consider his regular bail application on its own merits, without being influenced by the High Court's earlier direction.

-   **Sushila Aggarwal v. State (NCT of Delhi)**: The Supreme Court held that the protection granted under Section 438 CrPC should not be limited to a fixed period. It clarified that the anticipatory bail order would continue until the conclusion of the trial, unless there are specific reasons for its cancellation. The Court also laid down guidelines for the exercise of power under Section 438 CrPC, emphasizing the need for a balanced approach.

-   **Priya Indoria v. The State of Karnataka**: The Supreme Court allowed the appeals, setting aside the anticipatory
bail granted by the Bengaluru Sessions Court. It held that the Bengaluru court did not have the territorial jurisdiction
to grant anticipatory bail for an FIR registered in Rajasthan. The Court clarified the principles governing
extra-territorial anticipatory bail and emphasized that the appropriate course of action for an accused apprehending
arrest in a different state is to seek transit anticipatory bail to approach the jurisdictional court.

**PDF Links:**
-   *BALMUKUND SINGH GAUTAM VS STATE OF MADHYA PRADESH*: https://api.sci.gov.in/supremecourt/2024/17445/17445_2024_15_1501_68382_Judgement_13-Feb-2026.pdf
-   *SUSHILA AGGARWAL VS STATE (NCT OF DELHI)*: https://api.sci.gov.in/supremecourt/2017/28027/28027_2017_Judgement_15-May-2018.pdf
-   *PRIYA INDORIA VS THE STATE OF KARNATAKA*: https://api.sci.gov.in/supremecourt/2023/7943/7943_2023_14_1501_48299_Judgement_20-Nov-2023.pdf

### NEWACTS CITATIONS:
Section 65B of the Indian Evidence Act, 1872, deals with the admissibility of electronic records, and its corresponding new provision is Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

### Old Provision: Section 65B of Indian Evidence Act, 1872

(1) Notwithstanding anything contained in this Act, any information contained in an electronic record which is printed
on a paper, stored, recorded or copied in optical or magnetic media produced by a computer (hereinafter referred to as
the computer output) shall be deemed to be also a document, if the conditions mentioned in this section are satisfied in
relation to the information and computer in question and shall be admissible in any proceedings, without further proof
or production of the original, as evidence of any contents of the original or of any fact stated therein of which direct
evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer during the period over which the computer was used regularly to store or process information for the purposes of any activities regularly carried on over that period by the person having lawful control over the use of the computer;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer in the ordinary course of the said activities.

(3) Where over any period, the function of storing or processing information for the purposes of any activities regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by computers, whether-
(a) by a combination of computers operating over that period; or
(b) by different computers operating in succession over that period; or
(c) by different combinations of computers operating in succession over that period; or
(d) in any other manner involving the successive operation over that period, in whatever order, of one or more computers and one or more combinations of computers.
all the computers used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer; and references in this section to a computer shall be construed accordingly.

(4) In any proceedings where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things, that is to say,
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer;
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate,
and purporting to be signed by a person occupying a responsible official position in relation to the operation of the relevant device or the management of the relevant activities (whichever is appropriate) shall be evidence of any matter stated in the certificate; and for the purposes of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the person stating it.

(5) For the purposes of this section,
(a) information shall be taken to be supplied to a computer if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) whether in the course of activities carried on by any official, information is supplied with a view to its being stored or processed for the purposes of those activities by a computer operated otherwise than in the course of those activities, that information, if duly supplied to that computer, shall be taken to be supplied to it in the course of those activities;
(c) a computer output shall be taken to have been produced by a computer whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment.

Explanation: For the purposes of this section any reference to information being derived from other information shall be a reference to its being derived therefrom by calculation, comparison or any other process.

### New Provision: Section 63 of Bharatiya Sakshya Adhiniyam, 2023

(1) Notwithstanding anything contained in this Adhiniyam, any information contained in an electronic record which is
printed on paper, stored, recorded or copied in optical or magnetic media or semiconductor memory which is produced by a
computer or any communication device or otherwise stored, recorded or copied in any electronic form (hereinafter
referred to as the computer output) shall be deemed to be also a document, if the conditions mentioned in this section
are satisfied in relation to the information and computer in question and shall be admissible in any proceedings,
without further proof or production of the original, as evidence or any contents of the original or of any fact stated
therein of which direct evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer or communication device during the period over which the computer or communication device was used regularly to create, store or process information for the purposes of any activity regularly carried on over that period by the person having lawful control over the use of the computer or communication device;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer or communication device in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer or communication device was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer or communication device in the ordinary course of the said activities.

(3) Where over any period, the function of creating, storing or processing information for the purposes of any activity regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by means of one or more computers or communication device, whether:
(a) in standalone mode; or
(b) on a computer system; or
(c) on a computer network; or
(d) on a computer resource enabling information creation or providing information processing and storage; or
(e) through an intermediary,
all the computers or communication devices used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer or communication device; and references in this section to a computer or communication device shall be construed accordingly.

(4) In any proceeding where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things shall be submitted along with the electronic record at each instance where it is being submitted for admission, namely:
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer or a communication device referred to in clauses (a) to (e) of sub-section (3);
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate, and purporting to be
signed by a person in charge of the computer or communication device or the management of the relevant activities
(whichever is appropriate) and an expert shall be evidence of any matter stated in the certificate; and for the purposes
of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the
person stating it in the certificate specified in the Schedule.

(5) For the purposes of this section,:
(a) information shall be taken to be supplied to a computer or communication device if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) a computer output shall be taken to have been produced by a computer or communication device whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment or by other electronic means as referred to in clauses (a) to (e) of sub-section (3).

### Key Differences

-   **Scope of Devices**: The new provision explicitly includes "communication device" alongside "computer" throughout the section, broadening the scope of electronic records covered. It also adds "semiconductor memory" as a storage medium.
-   **Computer Output Definition**: The new provision expands the definition of "computer output" to include information "otherwise stored, recorded or copied in any electronic form."
-   **Activities in Sub-section (2)(a)**: The new provision adds "create" to the activities for which the computer or communication device was used regularly, i.e., "to create, store or process information."
-   **Complex Systems in Sub-section (3)**: The new provision significantly updates the description of how functions are performed by multiple devices, replacing the older clauses (a) to (d) with more modern terms like "standalone mode," "computer system," "computer network," "computer resource enabling information creation or providing information processing and storage," and "through an intermediary."
-   **Certificate Requirements in Sub-section (4)**: The new provision specifies that the certificate must be submitted "at each instance where it is being submitted for admission" and adds that it should be signed by "an expert" in addition to the person in charge. It also refers to a "certificate specified in the Schedule."
-   **Sub-section (5) Changes**: The new provision removes clause (b) of the old sub-section (5) and modifies clause (c) to clause (b), expanding the means by which a computer output is produced to include "other electronic means as referred to in clauses (a) to (e) of sub-section (3)."
-   **Explanation**: The Explanation from the old provision is omitted in the new one.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

Follow-up suggestions returned: `What are grounds for anticipatory bail?`, `How to apply for anticipatory bail?`, `What is the difference between bail and anticipatory bail?`

---

### Turn 6 — Medical negligence — plaintiff + defendant arguments (EN)

*Source section in `docs/test prompt.txt`: **Argument Generation***

#### 1. User prompt (verbatim)

```text
My client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the surgery, due to the negligence of the attending surgeon, Dr. Rakesh Nair, her bile duct was damaged. Post-surgery, she suffered from severe abdominal pain and had to be admitted again for corrective surgery at another hospital. She incurred additional medical expenses of ₹3.5 lakhs and suffered immense mental agony and physical pain. Despite repeated follow-ups, the hospital and the doctor refused to acknowledge their mistake or compensate her. She now seeks compensation of ₹25 lakhs for medical expenses, mental harassment, pain and suffering, and future treatment costs. give me arguments on behalf of plaintiff and defendant.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 785 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 6 |
| History turns loaded | 5 |
| Query rewritten by memory agent | False |
| Effective query after rewrite | *(unchanged)* |
| Agents planned | Scenario, Judgment, SCI_Judgment |
| Agents used | Scenario, Judgment, SCI_Judgment |
| Sources returned | 27 |
| Wall clock | 55.5s |
| First streamed token | 32.765s |

<details>
<summary>Sources cited back to the client (27)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | AI-Generated Legal Analysis | Scenario | None |
| 3 | judgment | nih.gov | Judgment | None |
| 4 | judgment | saslawchambers.com | Judgment | None |
| 5 | judgment | m3india.in | Judgment | None |
| 6 | judgment | bpasjournals.com | Judgment | None |
| 7 | judgment | lawweb.in | Judgment | None |
| 8 | judgment | asiindia.org | Judgment | None |
| 9 | judgment | ijlsi.com | Judgment | None |
| 10 | judgment | ijlmh.com | Judgment | None |
| 11 | judgment | medindiajournal.com | Judgment | None |
| 12 | judgment | nih.gov | Judgment | None |
| 13 | judgment | scribd.com | Judgment | None |
| 14 | judgment | legalkart.com | Judgment | None |
| 15 | judgment | delhimedicalnegligence.com | Judgment | None |
| 16 | judgment | coveryou.in | Judgment | None |
| 17 | sci_judgment | BOMBAY HOSPITAL AND MEDICAL RESEARCH CENTRE VS ASHA JAISWAL . | SCI_Judgment | None |
| 18 | sci_judgment | V. KISHAN RAO VS NIKHIL SUPER SPECIALITY HOSPITAL | SCI_Judgment | None |
| 19 | sci_judgment | SYAD AKBAR VS STATE OF KARNATAKA | SCI_Judgment | None |
| 20 | sci_judgment | DR. JAYSHREE UJWAL INGOLE VS STATE OF MAHARASHTRA | SCI_Judgment | None |
| 21 | sci_judgment | DR. HARSIH KUMAR KHURANA VS JOGINDER SINGH . | SCI_Judgment | None |
| 22 | sci_judgment | ARUN KUMAR MANGLIK VS CHIRAYU HEALTH AND MEDICARE PRIVATE LTD . | SCI_Judgment | None |
| 23 | sci_judgment | JACOB MATHEW VS STATE OF PUNJAB | SCI_Judgment | None |
| 24 | sci_judgment | MAHARAJA AGRASEN HOSPITAL . VS MASTER RISHABH SHARMA . | SCI_Judgment | None |
| 25 | sci_judgment | KUSUM SHARMA VS BATRA HOSPITAL &MED.RESEARCH CENTRE &ORS | SCI_Judgment | None |
| 26 | sci_judgment | YASH CHARITABLE TRUST VS UNION OF INDIA | SCI_Judgment | None |
| 27 | newacts | Indian Evidence Act 1872 - Section 65B | Newacts | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (45,051 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
## Arguments for the Plaintiff (Mrs. Anjali Deshmukh)

1.  **Existence of Duty of Care and its Breach:** The Defendants, XYZ Multispecialty Hospital and Dr. Rakesh Nair, owed
a paramount duty of care to Mrs. Anjali Deshmukh. This duty arose from the doctor-patient relationship established when
Mrs. Deshmukh sought medical treatment and underwent surgery at the Defendant hospital under the care of Defendant No.
2. The standard of care expected is that of a reasonably competent medical professional in that field. The negligent act
of damaging the bile duct during a routine gallbladder surgery (cholecystectomy) constitutes a clear breach of this
duty. Such an injury, particularly in a standard procedure, is indicative of a lack of reasonable care and skill,
falling below the accepted medical standards. The Supreme Court, in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu
Godbole, AIR 1969 SC 128*, affirmed that a doctor has a duty to bring to their task a reasonable degree of skill and
knowledge and must exercise a reasonable degree of care.

2.  **Causation and Direct Injury:** The severe abdominal pain, subsequent complications, and the necessity for a second, corrective surgery were directly and proximately caused by the negligent damage to the bile duct during the initial surgery performed by Dr. Rakesh Nair. The Plaintiff's deteriorating condition post-surgery, leading to admission to another hospital for diagnosis and repair, unequivocally links the injury to the Defendants' actions.

3.  **Application of *Res Ipsa Loquitur*:** The facts of the case strongly invoke the doctrine of *res ipsa loquitur*
(the thing speaks for itself). Damage to the bile duct during a gallbladder removal surgery is not an ordinary or
expected outcome in the absence of negligence. The surgical procedure and the instruments used were under the exclusive
control of Defendant No. 2. Therefore, the burden shifts to the Defendants to explain how such an injury occurred
without any negligence on their part.

4.  **Vicarious Liability of the Hospital:** Defendant No. 1, XYZ Multispecialty Hospital, is vicariously liable for the
negligent acts and omissions of its attending surgeon, Dr. Rakesh Nair, who was acting in the course of his employment
or professional engagement with the hospital. A hospital has a non-delegable duty to provide safe and competent medical
care. The Supreme Court, in *Savita Garg v. Director, National Heart Institute, (2004) 8 SCC 56*, has affirmed the
principle of vicarious liability of hospitals for the negligence of their doctors.

5.  **Inadequate Post-Operative Care and Diagnosis:** The Defendants' failure to promptly diagnose the bile duct injury despite the Plaintiff's severe and persistent post-operative symptoms further demonstrates a lapse in the standard of care. This delay in diagnosis exacerbated the Plaintiff's suffering and necessitated a more complex corrective procedure, constituting an additional act of negligence.

6.  **Entitlement to Comprehensive Damages:** The Plaintiff has suffered significant and quantifiable damages, including
actual medical expenses of ₹3.5 lakhs for corrective surgery and related treatment. Beyond pecuniary loss, she has
endured immense physical pain, suffering, mental agony, trauma, and a diminished quality of life due to the Defendants'
negligence. She is also entitled to compensation for anticipated future medical treatment and care. The total claim of
₹25 lakhs is a just and reasonable estimation of these losses. The Supreme Court in *Indian Medical Association v. V.P.
Shantha, (1995) 6 SCC 651*, recognized medical services as "service" and held medical practitioners and hospitals liable
for deficiency in service, which includes negligence leading to damages.

## Arguments for the Defendants (XYZ Multispecialty Hospital and Dr. Rakesh Nair)

1.  **Adherence to Standard of Care:** Dr. Rakesh Nair, Defendant No. 2, exercised reasonable care and skill consistent
with that of a reasonably competent surgeon in performing the cholecystectomy. The procedure was conducted in accordance
with established medical protocols. The standard of care in medical negligence cases does not require a doctor to
possess the highest degree of skill, but rather a reasonable degree of care and skill, as held in *Jacob Mathew v. State
of Punjab, (2005) 6 SCC 1*.

2.  **Known Surgical Complication:** Bile duct injury, while unfortunate, is a recognized and inherent risk or complication associated with cholecystectomy, even when performed with due care and skill. No surgeon can guarantee a complication-free outcome. The occurrence of a complication, by itself, does not automatically amount to medical negligence. The Plaintiff was likely informed of the potential risks associated with the surgery, and her consent was obtained.

3.  **Absence of Gross Negligence:** Even if an adverse outcome occurred, it was, at best, an error of judgment, which does not constitute actionable negligence. Medical negligence requires a higher threshold, demonstrating a lack of ordinary prudence or gross negligence. The Defendant No. 2 did not act with any recklessness or disregard for the patient's well-being.

4.  **Burden of Proof on Plaintiff:** The burden of proving medical negligence rests squarely on the Plaintiff. The Plaintiff must affirmatively demonstrate, through expert medical evidence, that the Defendants deviated from the accepted standard of care and that this deviation directly caused the alleged injury. Mere allegations or an unfavorable outcome are insufficient to establish negligence.

5.  **Hospital's Limited Responsibility:** Defendant No. 1, the hospital, provided the necessary infrastructure, equipment, and support staff. The hospital cannot be held solely responsible for the individual professional judgment and actions of an independent surgeon, particularly if it can demonstrate that it exercised due diligence in credentialing and providing a safe environment.

6.  **Exaggerated Claim for Damages:** The quantum of damages sought by the Plaintiff, particularly for mental agony and
future treatment costs, is speculative and excessive. The Plaintiff must provide cogent evidence to substantiate each
component of her claim. The medical expenses of ₹3.5 lakhs have been incurred, but the remaining claim of ₹21.5 lakhs
for non-pecuniary and future damages requires rigorous proof and is subject to judicial assessment based on established
principles of compensation.

7.  **Timely Post-Operative Management:** The Defendants provided appropriate post-operative care based on the symptoms presented. Any delay in diagnosis, if at all, was due to the complex nature of the complication and not a deliberate or negligent omission. All reasonable efforts were made to manage the Plaintiff's condition within the prevailing medical knowledge and resources.

### JUDGMENT CITATIONS:
The user has requested arguments on behalf of both the plaintiff and the defendant in a medical negligence case. However, as a Legal AI Assistant, I am strictly bound by the rule to provide answers only from the supplied context, which is stated to contain Indian court judgments. No such judgment context has been provided for this query.

Therefore, I am unable to provide the specific arguments as requested, as doing so would require using external knowledge or fabricating information, which is contrary to my instructions.

I think this information will help you more to understand the case:

### Key Legal Issues
Based on general principles of medical negligence in India, the key legal issues in such a case would typically include:
-   Whether the attending surgeon, Dr. Rakesh Nair, owed a duty of care to Mrs. Anjali Deshmukh.
-   Whether there was a breach of this duty of care by Dr. Rakesh Nair during the gallbladder surgery.
-   Whether the alleged breach of duty directly caused the damage to Mrs. Deshmukh's bile duct and subsequent suffering and expenses (causation).
-   Whether the principle of *res ipsa loquitur* is applicable, shifting the burden of proof to the defendants.
-   The quantum of compensation for medical expenses, mental harassment, pain and suffering, and future treatment costs.
-   Whether XYZ Multispecialty Hospital is vicariously liable for the actions of Dr. Rakesh Nair.

### Detailed Narrative
Mrs. Anjali Deshmukh, aged 42, underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024. During
the procedure, it is alleged that due to the negligence of Dr. Rakesh Nair, her bile duct was damaged. Following the
surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating a second admission for corrective surgery at a
different hospital. This corrective surgery and associated treatment led to additional medical expenses of ₹3.5 lakhs.
Furthermore, Mrs. Deshmukh claims to have suffered immense mental agony and physical pain. Despite repeated attempts to
engage with the hospital and Dr. Nair, they have reportedly refused to acknowledge any error or offer compensation. Mrs.
Deshmukh now seeks a total compensation of ₹25 lakhs, covering her medical expenses, mental harassment, pain and
suffering, and anticipated future treatment costs.

### As per the Court (General Principles of Medical Negligence in India)
In India, medical negligence cases are primarily governed by the law of torts, the Consumer Protection Act, 2019, and
certain provisions of the Indian Penal Code, 1860, although criminal liability for doctors is typically reserved for
cases of gross negligence. To establish medical negligence, the plaintiff generally needs to prove three elements: (i) a
legal duty of care owed by the medical professional to the patient, (ii) a breach of that duty, and (iii) that the
breach directly resulted in injury or damage to the patient. The standard of care expected from a medical professional
is that of a reasonably competent practitioner in that field, exercising reasonable skill and care. Mere unsuccessful
treatment or an unfavorable surgical outcome does not automatically establish negligence.

The doctrine of *res ipsa loquitur*, meaning "the thing speaks for itself," may be invoked in certain medical negligence
cases. This doctrine allows a presumption of negligence to arise, shifting the burden of proof to the healthcare
provider to demonstrate that no negligence occurred, provided the incident would not ordinarily happen without
negligence, and the accident was under the exclusive control of the defendant, with no contributory negligence from the
patient. However, courts apply *res ipsa loquitur* with caution in medical negligence cases, and it is not of universal
application. Even if *res ipsa loquitur* is established, the plaintiff must still prove all other elements of medical
negligence, including duty of care, breach of duty, causation, and damages.

Compensation in medical negligence cases aims to restore the victim to the position they would have been in had the negligence not occurred, covering financial losses, future medical expenses, and pain and suffering. The calculation of compensation can vary, but it generally includes medical costs, future treatment costs, and compensation for mental and physical pain.

### Additional Observations
Defenses in medical negligence cases often involve demonstrating that the standard of care was met, that the injury could have resulted from other factors, or that the patient contributed to the injury. Written evidence of consent to inherent risks can also be a defense. Meticulous clinical documentation is crucial for doctors in their defense. It is also important to note that the limitation period for filing medical negligence cases is generally two years.

### SCI_JUDGMENT CITATIONS:
Here are arguments on behalf of both the plaintiff (Mrs. Anjali Deshmukh) and the defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital), drawing upon principles of medical negligence established by the Supreme Court of India.

### Arguments for the Plaintiff (Mrs. Anjali Deshmukh)

**Key Legal Issues**
-   Breach of duty of care by the medical professional.
-   Application of the doctrine of *res ipsa loquitur* (the thing speaks for itself).
-   Causation of injury and suffering due to negligence.
-   Entitlement to compensation for medical expenses, pain, suffering, and mental agony.

**Detailed Narrative**
Mrs. Anjali Deshmukh underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024, under the care of
Dr. Rakesh Nair. During this procedure, her bile duct was damaged. This damage was not a known or accepted complication
that could occur despite due care. Following the surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating
a second admission for corrective surgery at a different hospital. This unforeseen complication and subsequent
corrective procedure resulted in additional medical expenses of ₹3.5 lakhs, along with immense physical pain and mental
agony. The hospital and Dr. Nair have failed to acknowledge their error or offer compensation, compelling Mrs. Deshmukh
to seek ₹25 lakhs for her losses.

**Court Observations**
The plaintiff's case rests on establishing medical negligence, which involves demonstrating a breach of the duty of care
owed by the medical professional. The Supreme Court in *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, and *Kusum
Sharma v. Batra Hospital & Med. Research Centre*, (2010) 3 SCC 480, established that a medical professional is expected
to bring a reasonable degree of skill and knowledge and must exercise a reasonable degree of care. Negligence occurs
when the professional's conduct falls below the standards of a reasonably competent practitioner in their field.

In this scenario, the damage to the bile duct during gallbladder surgery is an event that, in the ordinary course of
things, would not happen if proper care and skill were exercised. This situation strongly invokes the doctrine of *res
ipsa loquitur*, meaning "the thing speaks for itself." As held in *V. Kishan Rao v. Nikhil Super Speciality Hospital*,
(2010) 5 SCC 513, and reiterated in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC OnLine SC
1098, *res ipsa loquitur* is an evidential principle intended to assist a claimant who is unable to adduce specific
evidence as to how the accident occurred. If the accident is such that it does not happen in the ordinary course of
things with proper care, and the thing causing the injury was under the management of the defendant, then negligence can
be inferred in the absence of a reasonable explanation from the defendant. The damage to the bile duct during surgery, a
procedure directly controlled by Dr. Nair, suggests a prima facie case of negligence.

Furthermore, the subsequent severe pain, need for corrective surgery, and additional medical expenses directly resulted from the initial surgical error. This establishes the causal link between the alleged negligence and the damages suffered by Mrs. Deshmukh. The refusal of the hospital and doctor to acknowledge their mistake or compensate her further underscores the need for judicial intervention.

**Judgment & Direction**
The plaintiff seeks compensation for medical expenses, mental harassment, pain and suffering, and future treatment
costs. The Supreme Court in *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, (2019) 7 SCC 401,
recognized that compensation for medical negligence should be assessed considering various factors, including the loss
of quality of life and the economic equivalent of suffering. The additional medical expenses of ₹3.5 lakhs are a direct
and quantifiable loss. The mental agony and physical pain, though not easily quantifiable, are significant components of
suffering for which compensation is due. The total claim of ₹25 lakhs is a reasonable estimate for the comprehensive
losses incurred by Mrs. Deshmukh.

### Arguments for the Defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital)

**Key Legal Issues**
-   Standard of care in medical practice and the "Bolam test."
-   Absence of gross negligence.
-   Complications inherent in surgical procedures.
-   Requirement for expert evidence to prove negligence.

**Detailed Narrative**
Dr. Rakesh Nair and XYZ Multispecialty Hospital contend that the damage to Mrs. Anjali Deshmukh's bile duct during
gallbladder surgery was an unfortunate complication, not a result of negligence. They assert that Dr. Nair exercised the
requisite skill and care expected of a reasonably competent surgeon. The hospital provided all necessary facilities and
support. The subsequent pain and need for corrective surgery, while regrettable, do not automatically imply negligence,
as surgical procedures inherently carry risks and potential complications. The hospital and doctor maintain that they
acted in good faith and in accordance with accepted medical practices.

**Court Observations**
The defense relies on the established legal principle that a medical professional is not liable merely because a
treatment is unsuccessful or an adverse outcome occurs. As per *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, a
simple lack of care, an error of judgment, or an accident is not proof of negligence. The standard of care is that of an
ordinary competent medical practitioner exercising an ordinary degree of professional skill, as articulated in the
"Bolam test" and adopted by Indian courts. The Supreme Court in *Kusum Sharma v. Batra Hospital & Med. Research Centre*,
(2010) 3 SCC 480, emphasized that a doctor is not negligent if they act in accordance with a practice accepted as proper
by a responsible body of medical men skilled in that particular art, even if a body of adverse opinion exists.

Surgical procedures, especially those involving delicate organs like the gallbladder and bile duct, carry inherent
risks. Damage to the bile duct, while undesirable, can be a recognized complication even when the surgeon exercises due
care. The defense would argue that without specific expert evidence demonstrating a deviation from the accepted standard
of care, negligence cannot be presumed. The burden of proving negligence lies heavily on the claimant, and a mere
averment in a complaint, unsupported by expert medical opinion, is insufficient to establish negligence, as held in
*C.P. Sreekumar (Dr.), MS (Ortho) v. S. Ramanujam*, (2009) 7 SCC 130.

The doctrine of *res ipsa loquitur* should be applied with extreme caution in cases of professional negligence,
particularly for doctors. The Supreme Court in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC
OnLine SC 1098, reiterated that *res ipsa loquitur* cannot be applied merely because a patient has not responded
favorably to treatment or a surgery has failed. The defense would argue that the damage to the bile duct, while an
unfortunate outcome, does not "speak for itself" as negligence without further evidence of a specific negligent act or
omission by Dr. Nair. It could be a known, albeit rare, complication that occurred despite the exercise of reasonable
care.

**Judgment & Direction**
The defendants would argue for the dismissal of the compensation claim, asserting that Mrs. Deshmukh has failed to
establish a clear case of medical negligence. They would contend that the unfortunate outcome was a surgical
complication rather than a result of any breach of the standard of care by Dr. Nair or the hospital. They would also
emphasize that the claim for ₹25 lakhs is excessive and unsubstantiated without concrete proof of gross negligence and a
direct causal link that goes beyond inherent surgical risks.

**PDF Links:**
-   *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, 30-11-2021 (English): https://api.sci.gov.in/supremecourt/2010/4109/4109_2010_11_1501_31713_Judgement_30-Nov-2021.pdf
-   *V. Kishan Rao v. Nikhil Super Speciality Hospital*, 08-03-2010 (English): https://api.sci.gov.in/jonew/judis/36301.pdf
-   *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, 09-01-2019 (English): https://api.sci.gov.in/supremecourt/2016/27594/27594_2016_Judgement_09-Jan-2019.pdf
-   *Jacob Mathew v. State of Punjab*, 05-08-2005 (English): https://api.sci.gov.in/jonew/judis/27088.pdf

### NEWACTS CITATIONS:
Section 65B of the Indian Evidence Act, 1872, deals with the admissibility of electronic records, and its corresponding new provision is Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

### Old Provision: Section 65B of Indian Evidence Act, 1872

(1) Notwithstanding anything contained in this Act, any information contained in an electronic record which is printed
on a paper, stored, recorded or copied in optical or magnetic media produced by a computer (hereinafter referred to as
the computer output) shall be deemed to be also a document, if the conditions mentioned in this section are satisfied in
relation to the information and computer in question and shall be admissible in any proceedings, without further proof
or production of the original, as evidence of any contents of the original or of any fact stated therein of which direct
evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer during the period over which the computer was used regularly to store or process information for the purposes of any activities regularly carried on over that period by the person having lawful control over the use of the computer;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer in the ordinary course of the said activities.

(3) Where over any period, the function of storing or processing information for the purposes of any activities regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by computers, whether-
(a) by a combination of computers operating over that period; or
(b) by different computers operating in succession over that period; or
(c) by different combinations of computers operating in succession over that period; or
(d) in any other manner involving the successive operation over that period, in whatever order, of one or more computers and one or more combinations of computers.
all the computers used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer; and references in this section to a computer shall be construed accordingly.

(4) In any proceedings where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things, that is to say,
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer;
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate,
and purporting to be signed by a person occupying a responsible official position in relation to the operation of the relevant device or the management of the relevant activities (whichever is appropriate) shall be evidence of any matter stated in the certificate; and for the purposes of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the person stating it.

(5) For the purposes of this section,
(a) information shall be taken to be supplied to a computer if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) whether in the course of activities carried on by any official, information is supplied with a view to its being stored or processed for the purposes of those activities by a computer operated otherwise than in the course of those activities, that information, if duly supplied to that computer, shall be taken to be supplied to it in the course of those activities;
(c) a computer output shall be taken to have been produced by a computer whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment.

Explanation: For the purposes of this section any reference to information being derived from other information shall be a reference to its being derived therefrom by calculation, comparison or any other process.

### New Provision: Section 63 of Bharatiya Sakshya Adhiniyam, 2023

(1) Notwithstanding anything contained in this Adhiniyam, any information contained in an electronic record which is
printed on paper, stored, recorded or copied in optical or magnetic media or semiconductor memory which is produced by a
computer or any communication device or otherwise stored, recorded or copied in any electronic form (hereinafter
referred to as the computer output) shall be deemed to be also a document, if the conditions mentioned in this section
are satisfied in relation to the information and computer in question and shall be admissible in any proceedings,
without further proof or production of the original, as evidence or any contents of the original or of any fact stated
therein of which direct evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer or communication device during the period over which the computer or communication device was used regularly to create, store or process information for the purposes of any activity regularly carried on over that period by the person having lawful control over the use of the computer or communication device;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer or communication device in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer or communication device was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer or communication device in the ordinary course of the said activities.

(3) Where over any period, the function of creating, storing or processing information for the purposes of any activity regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by means of one or more computers or communication device, whether:
(a) in standalone mode; or
(b) on a computer system; or
(c) on a computer network; or
(d) on a computer resource enabling information creation or providing information processing and storage; or
(e) through an intermediary,
all the computers or communication devices used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer or communication device; and references in this section to a computer or communication device shall be construed accordingly.

(4) In any proceeding where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things shall be submitted along with the electronic record at each instance where it is being submitted for admission, namely:
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer or a communication device referred to in clauses (a) to (e) of sub-section (3);
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate, and purporting to be
signed by a person in charge of the computer or communication device or the management of the relevant activities
(whichever is appropriate) and an expert shall be evidence of any matter stated in the certificate; and for the purposes
of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the
person stating it in the certificate specified in the Schedule.

(5) For the purposes of this section,:
(a) information shall be taken to be supplied to a computer or communication device if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) a computer output shall be taken to have been produced by a computer or communication device whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment or by other electronic means as referred to in clauses (a) to (e) of sub-section (3).

### Key Differences

-   **Scope of Devices**: The new provision explicitly includes "communication device" alongside "computer" throughout the section, broadening the scope of electronic records covered. It also adds "semiconductor memory" as a storage medium.
-   **Computer Output Definition**: The new provision expands the definition of "computer output" to include information "otherwise stored, recorded or copied in any electronic form."
-   **Activities in Sub-section (2)(a)**: The new provision adds "create" to the activities for which the computer or communication device was used regularly, i.e., "to create, store or process information."
-   **Complex Systems in Sub-section (3)**: The new provision significantly updates the description of how functions are performed by multiple devices, replacing the older clauses (a) to (d) with more modern terms like "standalone mode," "computer system," "computer network," "computer resource enabling information creation or providing information processing and storage," and "through an intermediary."
-   **Certificate Requirements in Sub-section (4)**: The new provision specifies that the certificate must be submitted "at each instance where it is being submitted for admission" and adds that it should be signed by "an expert" in addition to the person in charge. It also refers to a "certificate specified in the Schedule."
-   **Sub-section (5) Changes**: The new provision removes clause (b) of the old sub-section (5) and modifies clause (c) to clause (b), expanding the means by which a computer output is produced to include "other electronic means as referred to in clauses (a) to (e) of sub-section (3)."
-   **Explanation**: The Explanation from the old provision is omitted in the new one.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

Follow-up suggestions returned: `What evidence proves negligence?`, `What is the statute of limitations?`, `Can hospital vicarious liability apply?`

---

### Turn 7 — Same medical-negligence argument prompt in Marathi

*Source section in `docs/test prompt.txt`: **Argument Generation***

#### 1. User prompt (verbatim)

```text
माझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टिस्पेशालिटी हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली. शस्त्रक्रियेदरम्यान, उपस्थित सर्जन डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे, त्यांच्या पित्तनलिकेचे नुकसान झाले. शस्त्रक्रियेनंतर, त्यांना तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसर्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. त्यांना ३.५ लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आणि प्रचंड मानसिक त्रास आणि शारीरिक वेदना सहन कराव्या लागल्या. वारंवार पाठपुरावा करूनही, रुग्णालय आणि डॉक्टरांनी त्यांची चूक मान्य करण्यास किंवा तिला भरपाई देण्यास नकार दिला. आता त्या वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास आणि भविष्यातील उपचार खर्चासाठी २५ लाख रुपयांची भरपाई मागत आहेत. मला युक्तिवाद द्या.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 764 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 7 |
| History turns loaded | 5 |
| Query rewritten by memory agent | False |
| Effective query after rewrite | *(unchanged)* |
| Agents planned | Scenario |
| Agents used | Scenario |
| Sources returned | 27 |
| Wall clock | 25.4s |
| First streamed token | —s |

<details>
<summary>Sources cited back to the client (27)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | AI-Generated Legal Analysis | Scenario | None |
| 3 | judgment | nih.gov | Judgment | None |
| 4 | judgment | saslawchambers.com | Judgment | None |
| 5 | judgment | m3india.in | Judgment | None |
| 6 | judgment | bpasjournals.com | Judgment | None |
| 7 | judgment | lawweb.in | Judgment | None |
| 8 | judgment | asiindia.org | Judgment | None |
| 9 | judgment | ijlsi.com | Judgment | None |
| 10 | judgment | ijlmh.com | Judgment | None |
| 11 | judgment | medindiajournal.com | Judgment | None |
| 12 | judgment | nih.gov | Judgment | None |
| 13 | judgment | scribd.com | Judgment | None |
| 14 | judgment | legalkart.com | Judgment | None |
| 15 | judgment | delhimedicalnegligence.com | Judgment | None |
| 16 | judgment | coveryou.in | Judgment | None |
| 17 | sci_judgment | BOMBAY HOSPITAL AND MEDICAL RESEARCH CENTRE VS ASHA JAISWAL . | SCI_Judgment | None |
| 18 | sci_judgment | V. KISHAN RAO VS NIKHIL SUPER SPECIALITY HOSPITAL | SCI_Judgment | None |
| 19 | sci_judgment | SYAD AKBAR VS STATE OF KARNATAKA | SCI_Judgment | None |
| 20 | sci_judgment | DR. JAYSHREE UJWAL INGOLE VS STATE OF MAHARASHTRA | SCI_Judgment | None |
| 21 | sci_judgment | DR. HARSIH KUMAR KHURANA VS JOGINDER SINGH . | SCI_Judgment | None |
| 22 | sci_judgment | ARUN KUMAR MANGLIK VS CHIRAYU HEALTH AND MEDICARE PRIVATE LTD . | SCI_Judgment | None |
| 23 | sci_judgment | JACOB MATHEW VS STATE OF PUNJAB | SCI_Judgment | None |
| 24 | sci_judgment | MAHARAJA AGRASEN HOSPITAL . VS MASTER RISHABH SHARMA . | SCI_Judgment | None |
| 25 | sci_judgment | KUSUM SHARMA VS BATRA HOSPITAL &MED.RESEARCH CENTRE &ORS | SCI_Judgment | None |
| 26 | sci_judgment | YASH CHARITABLE TRUST VS UNION OF INDIA | SCI_Judgment | None |
| 27 | newacts | Indian Evidence Act 1872 - Section 65B | Newacts | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (45,460 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
## वादी (श्रीमती अंजली देशमुख) यांच्या वतीने युक्तिवाद

1.  **कर्तव्याचे अस्तित्व आणि त्याचे उल्लंघन:** प्रतिवादी, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर, यांनी
श्रीमती अंजली देशमुख यांच्या प्रति अत्यंत काळजी घेण्याचे कर्तव्य बजावले. हे कर्तव्य श्रीमती देशमुख यांनी प्रतिवादी
रुग्णालयात प्रतिवादी क्रमांक 2 च्या देखरेखीखाली वैद्यकीय उपचार आणि शस्त्रक्रिया करून घेण्यास सुरुवात केल्यावर उद्भवले.
अपेक्षित काळजीचा दर्जा हा त्यांच्या संबंधित क्षेत्रातील वाजवी सक्षम वैद्यकीय व्यावसायिकाचा असतो. पित्तवाहिनीला नुकसान
पोहोचवणे, जी एक नियमित पित्तशयाच्या शस्त्रक्रिया (कोलेसिस्टेक्टॉमी) आहे, हे या कर्तव्याचे स्पष्ट उल्लंघन आहे. अशा
प्रकारची दुखापत, विशेषतः मानक प्रक्रियेत, वाजवी काळजी आणि कौशल्याच्या अभावाची सूचक आहे, जी स्वीकृत वैद्यकीय मानकांपेक्षा
कमी आहे. *डॉ. लक्ष्मण बाळकृष्ण जोशी विरुद्ध डॉ. त्रिंबक बापू गोडबोले, AIR 1969 SC 128* या प्रकरणात सर्वोच्च न्यायालयाने
असे प्रतिपादन केले आहे की, डॉक्टरांचे कर्तव्य आहे की त्यांनी आपल्या कामात वाजवी कौशल्य आणि ज्ञान वापरावे आणि वाजवी काळजी
घ्यावी.

2.  **कारणीभाव आणि थेट दुखापत:** तीव्र पोटदुखी, त्यानंतरच्या गुंतागुंत आणि दुसऱ्या दुरुस्त शस्त्रक्रियेची आवश्यकता हे सर्व डॉ. राकेश नायर यांनी केलेल्या सुरुवातीच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला झालेल्या निष्काळजीपणामुळे थेट आणि जवळचे परिणाम आहेत. शस्त्रक्रियेनंतर वादीची बिघडलेली प्रकृती, ज्यामुळे निदान आणि दुरुस्तीसाठी दुसऱ्या रुग्णालयात दाखल व्हावे लागले, हे दुखापतीचा प्रतिवादींच्या कृतीशी स्पष्ट संबंध दर्शवते.

3.  ***रेस इप्सा लोक्युटर* (Res Ipsa Loquitur) सिद्धांताचा वापर:** या प्रकरणातील तथ्ये *रेस इप्सा लोक्युटर* (गोष्ट
स्वतःच बोलते) या सिद्धांताला जोरदारपणे लागू होतात. पित्तशयाच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला होणारे नुकसान हे
निष्काळजीपणाच्या अनुपस्थितीत सामान्यतः घडणारी घटना नाही. शस्त्रक्रियेची प्रक्रिया आणि वापरलेली उपकरणे प्रतिवादी क्रमांक
2 च्या अनन्य नियंत्रणाखाली होती आणि वादी बेशुद्ध होती. म्हणून, हा सिद्धतेचा भार प्रतिवादींवर येतो की त्यांनी त्यांच्या
बाजूने कोणताही निष्काळजीपणा न करता असे नुकसान कसे झाले हे स्पष्ट करावे.

4.  **रुग्णालयाची प्रतिनिधी जबाबदारी:** प्रतिवादी क्रमांक 1, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल, त्यांचे उपस्थित सर्जन,
डॉ. राकेश नायर यांच्या निष्काळजीपणाच्या कृत्यांसाठी आणि चुकांसाठी प्रतिनिधी जबाबदार आहे, कारण ते रुग्णालयाच्या नोकरीत
किंवा व्यावसायिक संबंधात काम करत होते. रुग्णालयाचे आपल्या रुग्णांना सुरक्षित आणि सक्षम वैद्यकीय सेवा प्रदान करण्याचे
कर्तव्य आहे. *सविता गर्ग विरुद्ध संचालक, नॅशनल हार्ट इन्स्टिट्यूट, (2004) 8 SCC 56* या प्रकरणात सर्वोच्च न्यायालयाने
डॉक्टरांच्या निष्काळजीपणासाठी रुग्णालयांच्या प्रतिनिधी जबाबदारीच्या सिद्धांताला दुजोरा दिला आहे.

5.  **अपुरे शस्त्रक्रियेनंतरचे उपचार आणि निदान:** वादीच्या तीव्र आणि सततच्या शस्त्रक्रियेनंतरच्या लक्षणांवरूनही प्रतिवादींनी पित्तवाहिनीच्या दुखापतीचे त्वरित निदान करण्यात केलेली अक्षमता काळजीच्या मानकातील त्रुटी दर्शवते. निदानातील या विलंबाने वादीचे दुःख वाढवले आणि अधिक जटिल दुरुस्त प्रक्रियेची आवश्यकता निर्माण केली, जी निष्काळजीपणाची अतिरिक्त कृती ठरते.

6.  **व्यापक नुकसानभरपाईचा हक्क:** वादीला ₹3.5 लाख रुपयांच्या दुरुस्त शस्त्रक्रिया आणि संबंधित उपचारांसाठी झालेल्या
प्रत्यक्ष वैद्यकीय खर्चासह महत्त्वपूर्ण आणि मोजता येण्याजोग्या नुकसानीचा सामना करावा लागला आहे. आर्थिक
नुकसानीव्यतिरिक्त, प्रतिवादींच्या निष्काळजीपणामुळे तिला प्रचंड शारीरिक वेदना, दुःख, मानसिक यातना आणि जीवनाच्या गुणवत्तेत
घट झाली आहे. तिला अपेक्षित भविष्यातील वैद्यकीय उपचार आणि काळजीसाठी देखील नुकसानभरपाई मिळण्याचा हक्क आहे. *इंडियन मेडिकल
असोसिएशन विरुद्ध व्ही.पी. शांता, (1995) 6 SCC 651* या प्रकरणात सर्वोच्च न्यायालयाने वैद्यकीय सेवांना "सेवा" म्हणून
मान्यता दिली आणि वैद्यकीय व्यावसायिक आणि रुग्णालये सेवेतील त्रुटींसाठी जबाबदार असल्याचे म्हटले आहे, ज्यात
निष्काळजीपणामुळे होणारे नुकसान समाविष्ट आहे. एकूण ₹25 लाख रुपयांची मागणी ही या नुकसानीचे न्याय्य आणि वाजवी मूल्यांकन
आहे.

## प्रतिवादी (एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर) यांच्या वतीने युक्तिवाद

1.  **काळजीच्या मानकांचे पालन:** डॉ. राकेश नायर, प्रतिवादी क्रमांक 2, यांनी पित्तशयाच्या शस्त्रक्रिया करताना वाजवी सक्षम सर्जनकडून अपेक्षित वाजवी काळजी आणि कौशल्य वापरले. ही प्रक्रिया स्थापित वैद्यकीय प्रोटोकॉलनुसार पार पाडण्यात आली. वैद्यकीय निष्काळजीपणाच्या प्रकरणांमध्ये डॉक्टरांकडून सर्वोच्च कौशल्याची अपेक्षा केली जात नाही, तर वाजवी काळजी आणि कौशल्याची अपेक्षा केली जाते, जसे की *जेकब मॅथ्यू विरुद्ध पंजाब राज्य, (2005) 6 SCC 1* या प्रकरणात म्हटले आहे.

2.  **शस्त्रक्रियेतील अंतर्भूत गुंतागुंत:** पित्तवाहिनीला होणारे नुकसान, जरी दुर्दैवी असले तरी, कोलेसिस्टेक्टॉमीशी संबंधित एक ओळखला गेलेला आणि अंतर्भूत धोका किंवा गुंतागुंत आहे, जरी ती योग्य काळजी आणि कौशल्याने केली गेली असली तरी. कोणताही सर्जन गुंतागुंत-मुक्त परिणामाची हमी देऊ शकत नाही. केवळ गुंतागुंत झाल्याने आपोआप वैद्यकीय निष्काळजीपणा ठरत नाही. वादीला शस्त्रक्रियेशी संबंधित संभाव्य धोक्यांची माहिती दिली गेली असावी आणि तिची संमती घेण्यात आली असावी.

3.  **निर्णयातील चूक विरुद्ध निष्काळजीपणा:** जरी प्रतिकूल परिणाम झाला असला तरी, तो फारतर निर्णयातील चूक होती, जी कारवाई करण्यायोग्य निष्काळजीपणा ठरत नाही. वैद्यकीय निष्काळजीपणासाठी उच्च मर्यादा आवश्यक आहे, ज्यात सामान्य विवेकबुद्धीचा अभाव किंवा घोर निष्काळजीपणा दिसून येतो. प्रतिवादी क्रमांक 2 ने कोणतीही बेपर्वाई किंवा रुग्णाच्या हिताकडे दुर्लक्ष करण्याची कृती केली नाही.

4.  **वादीवर सिद्धतेचा भार:** वैद्यकीय निष्काळजीपणा सिद्ध करण्याचा भार पूर्णपणे वादीवर आहे. वादीने तज्ञ वैद्यकीय पुराव्याद्वारे हे निश्चितपणे दाखवून दिले पाहिजे की प्रतिवादींनी स्वीकृत काळजीच्या मानकापासून विचलन केले आणि या विचलनमुळेच कथित दुखापत झाली. केवळ आरोप किंवा प्रतिकूल परिणाम निष्काळजीपणा सिद्ध करण्यासाठी पुरेसे नाहीत.

5.  **रुग्णालयाची मर्यादित जबाबदारी:** प्रतिवादी क्रमांक 1, रुग्णालयाने आवश्यक पायाभूत सुविधा, उपकरणे आणि सहाय्यक कर्मचारी प्रदान केले. रुग्णालयाला एका स्वतंत्र सर्जनच्या वैयक्तिक व्यावसायिक निर्णयासाठी आणि कृतींसाठी पूर्णपणे जबाबदार धरले जाऊ शकत नाही, विशेषतः जर ते क्रेडेंशियल्स तपासण्यात आणि सुरक्षित वातावरण प्रदान करण्यात योग्य परिश्रम दाखवू शकले.

6.  **नुकसानभरपाईच्या दाव्याची अतिशयोक्ती:** वादीने मागणी केलेली नुकसानभरपाईची रक्कम, विशेषतः मानसिक यातना आणि भविष्यातील उपचार खर्चासाठी, अंदाजित आणि जास्त आहे. वादीने तिच्या दाव्याच्या प्रत्येक घटकाला पुष्टी देण्यासाठी ठोस पुरावे सादर करणे आवश्यक आहे. ₹3.5 लाख रुपयांचे वैद्यकीय खर्च झाले असले तरी, ₹21.5 लाख रुपयांच्या गैर-आर्थिक आणि भविष्यातील नुकसानीच्या दाव्यासाठी कठोर पुरावा आवश्यक आहे आणि नुकसानभरपाईच्या स्थापित तत्त्वांवर आधारित न्यायालयीन मूल्यांकनाच्या अधीन आहे.

7.  **वेळेवर शस्त्रक्रियेनंतरचे व्यवस्थापन:** प्रतिवादींनी सादर केलेल्या लक्षणांवर आधारित योग्य शस्त्रक्रियेनंतरची काळजी प्रदान केली. निदानातील कोणताही विलंब, जर असेल तर, गुंतागुंतीच्या स्वरूपामुळे होता आणि तो हेतुपुरस्सर किंवा निष्काळजीपणाचा अभाव नव्हता. प्रचलित वैद्यकीय ज्ञान आणि संसाधनांच्या मर्यादेत वादीची स्थिती व्यवस्थापित करण्यासाठी सर्व वाजवी प्रयत्न करण्यात आले.

## PDF Links
-   *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*: [https://api.sci.gov.in/jonew/judis/1674.pdf](https://api.sci.gov.in/jonew/judis/1674.pdf)
-   *Savita Garg v. Director, National Heart Institute, (2004) 8 SCC 56*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)
-   *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*: [https://api.sci.gov.in/jonew/judis/36301.pdf](https://api.sci.gov.in/jonew/judis/36301.pdf)
-   *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)

### JUDGMENT CITATIONS:
The user has requested arguments on behalf of both the plaintiff and the defendant in a medical negligence case. However, as a Legal AI Assistant, I am strictly bound by the rule to provide answers only from the supplied context, which is stated to contain Indian court judgments. No such judgment context has been provided for this query.

Therefore, I am unable to provide the specific arguments as requested, as doing so would require using external knowledge or fabricating information, which is contrary to my instructions.

I think this information will help you more to understand the case:

### Key Legal Issues
Based on general principles of medical negligence in India, the key legal issues in such a case would typically include:
-   Whether the attending surgeon, Dr. Rakesh Nair, owed a duty of care to Mrs. Anjali Deshmukh.
-   Whether there was a breach of this duty of care by Dr. Rakesh Nair during the gallbladder surgery.
-   Whether the alleged breach of duty directly caused the damage to Mrs. Deshmukh's bile duct and subsequent suffering and expenses (causation).
-   Whether the principle of *res ipsa loquitur* is applicable, shifting the burden of proof to the defendants.
-   The quantum of compensation for medical expenses, mental harassment, pain and suffering, and future treatment costs.
-   Whether XYZ Multispecialty Hospital is vicariously liable for the actions of Dr. Rakesh Nair.

### Detailed Narrative
Mrs. Anjali Deshmukh, aged 42, underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024. During
the procedure, it is alleged that due to the negligence of Dr. Rakesh Nair, her bile duct was damaged. Following the
surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating a second admission for corrective surgery at a
different hospital. This corrective surgery and associated treatment led to additional medical expenses of ₹3.5 lakhs.
Furthermore, Mrs. Deshmukh claims to have suffered immense mental agony and physical pain. Despite repeated attempts to
engage with the hospital and Dr. Nair, they have reportedly refused to acknowledge any error or offer compensation. Mrs.
Deshmukh now seeks a total compensation of ₹25 lakhs, covering her medical expenses, mental harassment, pain and
suffering, and anticipated future treatment costs.

### As per the Court (General Principles of Medical Negligence in India)
In India, medical negligence cases are primarily governed by the law of torts, the Consumer Protection Act, 2019, and
certain provisions of the Indian Penal Code, 1860, although criminal liability for doctors is typically reserved for
cases of gross negligence. To establish medical negligence, the plaintiff generally needs to prove three elements: (i) a
legal duty of care owed by the medical professional to the patient, (ii) a breach of that duty, and (iii) that the
breach directly resulted in injury or damage to the patient. The standard of care expected from a medical professional
is that of a reasonably competent practitioner in that field, exercising reasonable skill and care. Mere unsuccessful
treatment or an unfavorable surgical outcome does not automatically establish negligence.

The doctrine of *res ipsa loquitur*, meaning "the thing speaks for itself," may be invoked in certain medical negligence
cases. This doctrine allows a presumption of negligence to arise, shifting the burden of proof to the healthcare
provider to demonstrate that no negligence occurred, provided the incident would not ordinarily happen without
negligence, and the accident was under the exclusive control of the defendant, with no contributory negligence from the
patient. However, courts apply *res ipsa loquitur* with caution in medical negligence cases, and it is not of universal
application. Even if *res ipsa loquitur* is established, the plaintiff must still prove all other elements of medical
negligence, including duty of care, breach of duty, causation, and damages.

Compensation in medical negligence cases aims to restore the victim to the position they would have been in had the negligence not occurred, covering financial losses, future medical expenses, and pain and suffering. The calculation of compensation can vary, but it generally includes medical costs, future treatment costs, and compensation for mental and physical pain.

### Additional Observations
Defenses in medical negligence cases often involve demonstrating that the standard of care was met, that the injury could have resulted from other factors, or that the patient contributed to the injury. Written evidence of consent to inherent risks can also be a defense. Meticulous clinical documentation is crucial for doctors in their defense. It is also important to note that the limitation period for filing medical negligence cases is generally two years.

### SCI_JUDGMENT CITATIONS:
Here are arguments on behalf of both the plaintiff (Mrs. Anjali Deshmukh) and the defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital), drawing upon principles of medical negligence established by the Supreme Court of India.

### Arguments for the Plaintiff (Mrs. Anjali Deshmukh)

**Key Legal Issues**
-   Breach of duty of care by the medical professional.
-   Application of the doctrine of *res ipsa loquitur* (the thing speaks for itself).
-   Causation of injury and suffering due to negligence.
-   Entitlement to compensation for medical expenses, pain, suffering, and mental agony.

**Detailed Narrative**
Mrs. Anjali Deshmukh underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024, under the care of
Dr. Rakesh Nair. During this procedure, her bile duct was damaged. This damage was not a known or accepted complication
that could occur despite due care. Following the surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating
a second admission for corrective surgery at a different hospital. This unforeseen complication and subsequent
corrective procedure resulted in additional medical expenses of ₹3.5 lakhs, along with immense physical pain and mental
agony. The hospital and Dr. Nair have failed to acknowledge their error or offer compensation, compelling Mrs. Deshmukh
to seek ₹25 lakhs for her losses.

**Court Observations**
The plaintiff's case rests on establishing medical negligence, which involves demonstrating a breach of the duty of care
owed by the medical professional. The Supreme Court in *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, and *Kusum
Sharma v. Batra Hospital & Med. Research Centre*, (2010) 3 SCC 480, established that a medical professional is expected
to bring a reasonable degree of skill and knowledge and must exercise a reasonable degree of care. Negligence occurs
when the professional's conduct falls below the standards of a reasonably competent practitioner in their field.

In this scenario, the damage to the bile duct during gallbladder surgery is an event that, in the ordinary course of
things, would not happen if proper care and skill were exercised. This situation strongly invokes the doctrine of *res
ipsa loquitur*, meaning "the thing speaks for itself." As held in *V. Kishan Rao v. Nikhil Super Speciality Hospital*,
(2010) 5 SCC 513, and reiterated in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC OnLine SC
1098, *res ipsa loquitur* is an evidential principle intended to assist a claimant who is unable to adduce specific
evidence as to how the accident occurred. If the accident is such that it does not happen in the ordinary course of
things with proper care, and the thing causing the injury was under the management of the defendant, then negligence can
be inferred in the absence of a reasonable explanation from the defendant. The damage to the bile duct during surgery, a
procedure directly controlled by Dr. Nair, suggests a prima facie case of negligence.

Furthermore, the subsequent severe pain, need for corrective surgery, and additional medical expenses directly resulted from the initial surgical error. This establishes the causal link between the alleged negligence and the damages suffered by Mrs. Deshmukh. The refusal of the hospital and doctor to acknowledge their mistake or compensate her further underscores the need for judicial intervention.

**Judgment & Direction**
The plaintiff seeks compensation for medical expenses, mental harassment, pain and suffering, and future treatment
costs. The Supreme Court in *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, (2019) 7 SCC 401,
recognized that compensation for medical negligence should be assessed considering various factors, including the loss
of quality of life and the economic equivalent of suffering. The additional medical expenses of ₹3.5 lakhs are a direct
and quantifiable loss. The mental agony and physical pain, though not easily quantifiable, are significant components of
suffering for which compensation is due. The total claim of ₹25 lakhs is a reasonable estimate for the comprehensive
losses incurred by Mrs. Deshmukh.

### Arguments for the Defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital)

**Key Legal Issues**
-   Standard of care in medical practice and the "Bolam test."
-   Absence of gross negligence.
-   Complications inherent in surgical procedures.
-   Requirement for expert evidence to prove negligence.

**Detailed Narrative**
Dr. Rakesh Nair and XYZ Multispecialty Hospital contend that the damage to Mrs. Anjali Deshmukh's bile duct during
gallbladder surgery was an unfortunate complication, not a result of negligence. They assert that Dr. Nair exercised the
requisite skill and care expected of a reasonably competent surgeon. The hospital provided all necessary facilities and
support. The subsequent pain and need for corrective surgery, while regrettable, do not automatically imply negligence,
as surgical procedures inherently carry risks and potential complications. The hospital and doctor maintain that they
acted in good faith and in accordance with accepted medical practices.

**Court Observations**
The defense relies on the established legal principle that a medical professional is not liable merely because a
treatment is unsuccessful or an adverse outcome occurs. As per *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, a
simple lack of care, an error of judgment, or an accident is not proof of negligence. The standard of care is that of an
ordinary competent medical practitioner exercising an ordinary degree of professional skill, as articulated in the
"Bolam test" and adopted by Indian courts. The Supreme Court in *Kusum Sharma v. Batra Hospital & Med. Research Centre*,
(2010) 3 SCC 480, emphasized that a doctor is not negligent if they act in accordance with a practice accepted as proper
by a responsible body of medical men skilled in that particular art, even if a body of adverse opinion exists.

Surgical procedures, especially those involving delicate organs like the gallbladder and bile duct, carry inherent
risks. Damage to the bile duct, while undesirable, can be a recognized complication even when the surgeon exercises due
care. The defense would argue that without specific expert evidence demonstrating a deviation from the accepted standard
of care, negligence cannot be presumed. The burden of proving negligence lies heavily on the claimant, and a mere
averment in a complaint, unsupported by expert medical opinion, is insufficient to establish negligence, as held in
*C.P. Sreekumar (Dr.), MS (Ortho) v. S. Ramanujam*, (2009) 7 SCC 130.

The doctrine of *res ipsa loquitur* should be applied with extreme caution in cases of professional negligence,
particularly for doctors. The Supreme Court in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC
OnLine SC 1098, reiterated that *res ipsa loquitur* cannot be applied merely because a patient has not responded
favorably to treatment or a surgery has failed. The defense would argue that the damage to the bile duct, while an
unfortunate outcome, does not "speak for itself" as negligence without further evidence of a specific negligent act or
omission by Dr. Nair. It could be a known, albeit rare, complication that occurred despite the exercise of reasonable
care.

**Judgment & Direction**
The defendants would argue for the dismissal of the compensation claim, asserting that Mrs. Deshmukh has failed to
establish a clear case of medical negligence. They would contend that the unfortunate outcome was a surgical
complication rather than a result of any breach of the standard of care by Dr. Nair or the hospital. They would also
emphasize that the claim for ₹25 lakhs is excessive and unsubstantiated without concrete proof of gross negligence and a
direct causal link that goes beyond inherent surgical risks.

**PDF Links:**
-   *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, 30-11-2021 (English): https://api.sci.gov.in/supremecourt/2010/4109/4109_2010_11_1501_31713_Judgement_30-Nov-2021.pdf
-   *V. Kishan Rao v. Nikhil Super Speciality Hospital*, 08-03-2010 (English): https://api.sci.gov.in/jonew/judis/36301.pdf
-   *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, 09-01-2019 (English): https://api.sci.gov.in/supremecourt/2016/27594/27594_2016_Judgement_09-Jan-2019.pdf
-   *Jacob Mathew v. State of Punjab*, 05-08-2005 (English): https://api.sci.gov.in/jonew/judis/27088.pdf

### NEWACTS CITATIONS:
Section 65B of the Indian Evidence Act, 1872, deals with the admissibility of electronic records, and its corresponding new provision is Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

### Old Provision: Section 65B of Indian Evidence Act, 1872

(1) Notwithstanding anything contained in this Act, any information contained in an electronic record which is printed
on a paper, stored, recorded or copied in optical or magnetic media produced by a computer (hereinafter referred to as
the computer output) shall be deemed to be also a document, if the conditions mentioned in this section are satisfied in
relation to the information and computer in question and shall be admissible in any proceedings, without further proof
or production of the original, as evidence of any contents of the original or of any fact stated therein of which direct
evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer during the period over which the computer was used regularly to store or process information for the purposes of any activities regularly carried on over that period by the person having lawful control over the use of the computer;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer in the ordinary course of the said activities.

(3) Where over any period, the function of storing or processing information for the purposes of any activities regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by computers, whether-
(a) by a combination of computers operating over that period; or
(b) by different computers operating in succession over that period; or
(c) by different combinations of computers operating in succession over that period; or
(d) in any other manner involving the successive operation over that period, in whatever order, of one or more computers and one or more combinations of computers.
all the computers used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer; and references in this section to a computer shall be construed accordingly.

(4) In any proceedings where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things, that is to say,
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer;
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate,
and purporting to be signed by a person occupying a responsible official position in relation to the operation of the relevant device or the management of the relevant activities (whichever is appropriate) shall be evidence of any matter stated in the certificate; and for the purposes of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the person stating it.

(5) For the purposes of this section,
(a) information shall be taken to be supplied to a computer if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) whether in the course of activities carried on by any official, information is supplied with a view to its being stored or processed for the purposes of those activities by a computer operated otherwise than in the course of those activities, that information, if duly supplied to that computer, shall be taken to be supplied to it in the course of those activities;
(c) a computer output shall be taken to have been produced by a computer whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment.

Explanation: For the purposes of this section any reference to information being derived from other information shall be a reference to its being derived therefrom by calculation, comparison or any other process.

### New Provision: Section 63 of Bharatiya Sakshya Adhiniyam, 2023

(1) Notwithstanding anything contained in this Adhiniyam, any information contained in an electronic record which is
printed on paper, stored, recorded or copied in optical or magnetic media or semiconductor memory which is produced by a
computer or any communication device or otherwise stored, recorded or copied in any electronic form (hereinafter
referred to as the computer output) shall be deemed to be also a document, if the conditions mentioned in this section
are satisfied in relation to the information and computer in question and shall be admissible in any proceedings,
without further proof or production of the original, as evidence or any contents of the original or of any fact stated
therein of which direct evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer or communication device during the period over which the computer or communication device was used regularly to create, store or process information for the purposes of any activity regularly carried on over that period by the person having lawful control over the use of the computer or communication device;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer or communication device in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer or communication device was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer or communication device in the ordinary course of the said activities.

(3) Where over any period, the function of creating, storing or processing information for the purposes of any activity regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by means of one or more computers or communication device, whether:
(a) in standalone mode; or
(b) on a computer system; or
(c) on a computer network; or
(d) on a computer resource enabling information creation or providing information processing and storage; or
(e) through an intermediary,
all the computers or communication devices used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer or communication device; and references in this section to a computer or communication device shall be construed accordingly.

(4) In any proceeding where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things shall be submitted along with the electronic record at each instance where it is being submitted for admission, namely:
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer or a communication device referred to in clauses (a) to (e) of sub-section (3);
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate, and purporting to be
signed by a person in charge of the computer or communication device or the management of the relevant activities
(whichever is appropriate) and an expert shall be evidence of any matter stated in the certificate; and for the purposes
of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the
person stating it in the certificate specified in the Schedule.

(5) For the purposes of this section,:
(a) information shall be taken to be supplied to a computer or communication device if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) a computer output shall be taken to have been produced by a computer or communication device whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment or by other electronic means as referred to in clauses (a) to (e) of sub-section (3).

### Key Differences

-   **Scope of Devices**: The new provision explicitly includes "communication device" alongside "computer" throughout the section, broadening the scope of electronic records covered. It also adds "semiconductor memory" as a storage medium.
-   **Computer Output Definition**: The new provision expands the definition of "computer output" to include information "otherwise stored, recorded or copied in any electronic form."
-   **Activities in Sub-section (2)(a)**: The new provision adds "create" to the activities for which the computer or communication device was used regularly, i.e., "to create, store or process information."
-   **Complex Systems in Sub-section (3)**: The new provision significantly updates the description of how functions are performed by multiple devices, replacing the older clauses (a) to (d) with more modern terms like "standalone mode," "computer system," "computer network," "computer resource enabling information creation or providing information processing and storage," and "through an intermediary."
-   **Certificate Requirements in Sub-section (4)**: The new provision specifies that the certificate must be submitted "at each instance where it is being submitted for admission" and adds that it should be signed by "an expert" in addition to the person in charge. It also refers to a "certificate specified in the Schedule."
-   **Sub-section (5) Changes**: The new provision removes clause (b) of the old sub-section (5) and modifies clause (c) to clause (b), expanding the means by which a computer output is produced to include "other electronic means as referred to in clauses (a) to (e) of sub-section (3)."
-   **Explanation**: The Explanation from the old provision is omitted in the new one.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

Follow-up suggestions returned: `क्षतीपूर्तीसाठी काय पुरावे लागतील?`, `दावा दाखल करण्याची अंतिम मुदत काय?`, `रुग्णालय किंवा डॉक्टर जबाबदार धरले जातील?`

---

### Turn 8 — S.420 IPC — civil-not-criminal defence arguments

*Source section in `docs/test prompt.txt`: **Argument Generation***

#### 1. User prompt (verbatim)

```text
My client is facing a criminal case under Section 420 IPC for alleged cheating. However, there was no fraudulent intention from the beginning, and it was purely a civil dispute arising from a failed business agreement. Give me arguments to defend the client and emphasize that this is a case of civil liability, not criminal cheating.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 334 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 8 |
| History turns loaded | 5 |
| Query rewritten by memory agent | True |
| Effective query after rewrite | `My client is facing a criminal case under Section 420 Indian Penal Code 1860 for alleged cheating. However, there was no fraudulent intention from the beginning, and it was purely a civil dispute aris` |
| Agents planned | Newacts, Scenario, Judgment, SCI_Judgment |
| Agents used | Newacts, Scenario, Judgment, SCI_Judgment |
| Sources returned | 15 |
| Wall clock | 58.0s |
| First streamed token | 23.532s |

<details>
<summary>Sources cited back to the client (15)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | AI-Generated Legal Analysis | Scenario | None |
| 3 | judgment | CENTRAL BUREAU OF INVESTIGATION vs VIJAY KUMAR GUPTA | Judgment | 259.55185 |
| 4 | judgment | Prem Chand vs State of Rajasthan | Judgment | 249.21309 |
| 5 | judgment | Surendra Agrawal vs Hemlata Patel | Judgment | 245.63292 |
| 6 | judgment | Rajendra Singh vs The State of Rajasthan | Judgment | 245.38173 |
| 7 | judgment | NILAY S/O ARVINDBHAI SHETH vs SHRI KAPIL KISHORBHAI JOSHI | Judgment | 235.97122 |
| 8 | sci_judgment | AJAY MITRA VS STATE OF M.P. . | SCI_Judgment | None |
| 9 | sci_judgment | M.N.G BHARATEESH REDDY VS RAMESH RANGANATHAN | SCI_Judgment | None |
| 10 | sci_judgment | SATISHCHANDRA RATANLAL SHAH VS THE STATE OF GUJARAT | SCI_Judgment | None |
| 11 | sci_judgment | INDER CHAND BAGRI VS JAGADISH PRASAD BAGRI | SCI_Judgment | None |
| 12 | sci_judgment | J. VEDHASINGH VS R.M. GOVINDAN | SCI_Judgment | None |
| 13 | newacts | The Indian Penal Code, 1860 - Section 420 | Newacts | None |
| 14 | newacts | The Indian Penal Code, 1860 - Section 419 | Newacts | None |
| 15 | newacts | The Indian Penal Code, 1860 - Section 421 | Newacts | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (60,486 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
## DEFENSE ARGUMENTS AGAINST CHARGE UNDER SECTION 420 INDIAN PENAL CODE, 1860

The defense submits that the present case is fundamentally a civil dispute arising from a failed business agreement and does not constitute the offense of cheating under Section 420 of the Indian Penal Code, 1860. The essential ingredients for establishing criminal cheating, particularly the element of dishonest or fraudulent intention from the inception of the transaction, are conspicuously absent in the present matter.

### Absence of Dishonest Intention at Inception

1.  **Lack of *Mens Rea*:** For an offense under Section 420 of the Indian Penal Code, 1860, it is imperative to prove
that the accused had a dishonest or fraudulent intention at the very beginning of the transaction, i.e., from the moment
the promise or representation was made. A mere breach of contract or subsequent failure to fulfill a promise, in the
absence of such initial dishonest intention, does not automatically transform a civil wrong into a criminal offense. The
Supreme Court has consistently held that the distinction between a mere breach of contract and the offense of cheating
depends upon the intention of the accused at the time of inducement. If the intention to cheat was not present at the
inception, the offense of cheating cannot be said to have been committed.

2.  **Subsequent Failure Not Equivalent to Prior Deception:** The alleged inability or failure of the client to fulfill
the terms of the business agreement arose from unforeseen circumstances, commercial exigencies, or genuine business
difficulties, which developed *subsequent* to the formation of the agreement. Such a subsequent failure cannot be
retrospectively construed as a dishonest intention existing at the time the agreement was entered into. The Supreme
Court has clarified that a mere failure to keep a promise cannot be presumed to be an act of cheating.

3.  **Bona Fide Business Transaction:** The transaction between the parties was a bona fide business arrangement entered into with a genuine intention to execute the agreement and derive mutual commercial benefit. All actions taken by the client were in furtherance of the business objective, and any difficulties encountered were typical risks associated with commercial ventures. The client made genuine efforts to perform their obligations under the agreement.

### Nature of Dispute as Civil Liability

1.  **Breach of Contract, Not Criminal Deception:** The allegations, even if taken at face value, primarily disclose a
case of breach of contract, which falls squarely within the domain of civil law. The remedies for such a breach lie in
civil courts, where damages for non-performance or specific performance of the contract can be sought. The mere fact
that a party has suffered a loss in a business transaction does not automatically warrant the initiation of criminal
proceedings. The Supreme Court has repeatedly cautioned against converting civil disputes into criminal cases.

2.  **Availability of Civil Remedies:** The complainant has adequate and effective remedies available under civil law, including filing a suit for recovery of money, specific performance, or damages for breach of contract. Resorting to criminal proceedings under Section 420 of the Indian Penal Code, 1860, in such circumstances amounts to an abuse of the process of law, often with the ulterior motive of exerting pressure for recovery of money.

3.  **Absence of Inducement to Deliver Property by Deception:** Section 420 of the Indian Penal Code, 1860, requires
that the accused must have dishonestly induced the complainant to deliver any property or to make, alter, or destroy the
whole or any part of a valuable security. In the present case, any delivery of property or financial transaction
occurred within the framework of a commercial agreement, not as a result of any dishonest inducement or fraudulent
misrepresentation made at the inception by the client. The complainant willingly entered into the business arrangement,
presumably after due diligence and assessment of commercial risks.

### Judicial Precedents Distinguishing Civil and Criminal Liability

The Supreme Court of India has consistently drawn a clear distinction between a civil wrong and a criminal offense, particularly in cases involving allegations of cheating arising from contractual disputes:

-   In ***Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168***, the Supreme Court held that in order to
constitute an offence of cheating, the intention to cheat must be in existence at the time when the inducement was made.
If the intention was not dishonest at the time of making the promise but developed subsequently, it would not amount to
cheating. The Court observed that a mere breach of contract cannot give rise to criminal prosecution for cheating unless
fraudulent or dishonest intention is shown right at the beginning of the transaction.

-   In ***International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348***, the Supreme Court reiterated that for an offence under Section 420 of the Indian Penal Code, 1860, the element of deception and dishonest intention from the very beginning is crucial. The Court emphasized that a mere breach of contract or failure to pay money does not automatically lead to criminal prosecution for cheating.

-   In ***G. Sagar Suri v. State of U.P., (2000) 2 SCC 636***, the Supreme Court cautioned against converting purely civil disputes into criminal cases. The Court observed that it is not every breach of contract that would amount to the offence of cheating.

Therefore, based on the facts and the established legal principles, the defense submits that the allegations against the client do not fulfill the essential ingredients of criminal cheating under Section 420 of the Indian Penal Code, 1860, and the matter ought to be treated as a civil dispute.

### PDF Links
-   *Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168*: [https://api.sci.gov.in/jonew/judis/22759.pdf](https://api.sci.gov.in/jonew/judis/22759.pdf)
-   *International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348*: [https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf](https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf)

### JUDGMENT CITATIONS:
## वादी (श्रीमती अंजली देशमुख) यांच्या वतीने युक्तिवाद

1.  **कर्तव्याचे अस्तित्व आणि त्याचे उल्लंघन:** प्रतिवादी, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर, यांनी
श्रीमती अंजली देशमुख यांच्या प्रति अत्यंत काळजी घेण्याचे कर्तव्य बजावले. हे कर्तव्य श्रीमती देशमुख यांनी प्रतिवादी
रुग्णालयात प्रतिवादी क्रमांक 2 च्या देखरेखीखाली वैद्यकीय उपचार आणि शस्त्रक्रिया करून घेण्यास सुरुवात केल्यावर उद्भवले.
अपेक्षित काळजीचा दर्जा हा त्यांच्या संबंधित क्षेत्रातील वाजवी सक्षम वैद्यकीय व्यावसायिकाचा असतो. पित्तवाहिनीला नुकसान
पोहोचवणे, जी एक नियमित पित्तशयाच्या शस्त्रक्रिया (कोलेसिस्टेक्टॉमी) आहे, हे या कर्तव्याचे स्पष्ट उल्लंघन आहे. अशा
प्रकारची दुखापत, विशेषतः मानक प्रक्रियेत, वाजवी काळजी आणि कौशल्याच्या अभावाची सूचक आहे, जी स्वीकृत वैद्यकीय मानकांपेक्षा
कमी आहे. *डॉ. लक्ष्मण बाळकृष्ण जोशी विरुद्ध डॉ. त्रिंबक बापू गोडबोले, AIR 1969 SC 128* या प्रकरणात सर्वोच्च न्यायालयाने
असे प्रतिपादन केले आहे की, डॉक्टरांचे कर्तव्य आहे की त्यांनी आपल्या कामात वाजवी कौशल्य आणि ज्ञान वापरावे आणि वाजवी काळजी
घ्यावी.

2.  **कारणीभाव आणि थेट दुखापत:** तीव्र पोटदुखी, त्यानंतरच्या गुंतागुंत आणि दुसऱ्या दुरुस्त शस्त्रक्रियेची आवश्यकता हे सर्व डॉ. राकेश नायर यांनी केलेल्या सुरुवातीच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला झालेल्या निष्काळजीपणामुळे थेट आणि जवळचे परिणाम आहेत. शस्त्रक्रियेनंतर वादीची बिघडलेली प्रकृती, ज्यामुळे निदान आणि दुरुस्तीसाठी दुसऱ्या रुग्णालयात दाखल व्हावे लागले, हे दुखापतीचा प्रतिवादींच्या कृतीशी स्पष्ट संबंध दर्शवते.

3.  ***रेस इप्सा लोक्युटर* (Res Ipsa Loquitur) सिद्धांताचा वापर:** या प्रकरणातील तथ्ये *रेस इप्सा लोक्युटर* (गोष्ट
स्वतःच बोलते) या सिद्धांताला जोरदारपणे लागू होतात. पित्तशयाच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला होणारे नुकसान हे
निष्काळजीपणाच्या अनुपस्थितीत सामान्यतः घडणारी घटना नाही. शस्त्रक्रियेची प्रक्रिया आणि वापरलेली उपकरणे प्रतिवादी क्रमांक
2 च्या अनन्य नियंत्रणाखाली होती आणि वादी बेशुद्ध होती. म्हणून, हा सिद्धतेचा भार प्रतिवादींवर येतो की त्यांनी त्यांच्या
बाजूने कोणताही निष्काळजीपणा न करता असे नुकसान कसे झाले हे स्पष्ट करावे.

4.  **रुग्णालयाची प्रतिनिधी जबाबदारी:** प्रतिवादी क्रमांक 1, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल, त्यांचे उपस्थित सर्जन,
डॉ. राकेश नायर यांच्या निष्काळजीपणाच्या कृत्यांसाठी आणि चुकांसाठी प्रतिनिधी जबाबदार आहे, कारण ते रुग्णालयाच्या नोकरीत
किंवा व्यावसायिक संबंधात काम करत होते. रुग्णालयाचे आपल्या रुग्णांना सुरक्षित आणि सक्षम वैद्यकीय सेवा प्रदान करण्याचे
कर्तव्य आहे. *सविता गर्ग विरुद्ध संचालक, नॅशनल हार्ट इन्स्टिट्यूट, (2004) 8 SCC 56* या प्रकरणात सर्वोच्च न्यायालयाने
डॉक्टरांच्या निष्काळजीपणासाठी रुग्णालयांच्या प्रतिनिधी जबाबदारीच्या सिद्धांताला दुजोरा दिला आहे.

5.  **अपुरे शस्त्रक्रियेनंतरचे उपचार आणि निदान:** वादीच्या तीव्र आणि सततच्या शस्त्रक्रियेनंतरच्या लक्षणांवरूनही प्रतिवादींनी पित्तवाहिनीच्या दुखापतीचे त्वरित निदान करण्यात केलेली अक्षमता काळजीच्या मानकातील त्रुटी दर्शवते. निदानातील या विलंबाने वादीचे दुःख वाढवले आणि अधिक जटिल दुरुस्त प्रक्रियेची आवश्यकता निर्माण केली, जी निष्काळजीपणाची अतिरिक्त कृती ठरते.

6.  **व्यापक नुकसानभरपाईचा हक्क:** वादीला ₹3.5 लाख रुपयांच्या दुरुस्त शस्त्रक्रिया आणि संबंधित उपचारांसाठी झालेल्या
प्रत्यक्ष वैद्यकीय खर्चासह महत्त्वपूर्ण आणि मोजता येण्याजोग्या नुकसानीचा सामना करावा लागला आहे. आर्थिक
नुकसानीव्यतिरिक्त, प्रतिवादींच्या निष्काळजीपणामुळे तिला प्रचंड शारीरिक वेदना, दुःख, मानसिक यातना आणि जीवनाच्या गुणवत्तेत
घट झाली आहे. तिला अपेक्षित भविष्यातील वैद्यकीय उपचार आणि काळजीसाठी देखील नुकसानभरपाई मिळण्याचा हक्क आहे. *इंडियन मेडिकल
असोसिएशन विरुद्ध व्ही.पी. शांता, (1995) 6 SCC 651* या प्रकरणात सर्वोच्च न्यायालयाने वैद्यकीय सेवांना "सेवा" म्हणून
मान्यता दिली आणि वैद्यकीय व्यावसायिक आणि रुग्णालये सेवेतील त्रुटींसाठी जबाबदार असल्याचे म्हटले आहे, ज्यात
निष्काळजीपणामुळे होणारे नुकसान समाविष्ट आहे. एकूण ₹25 लाख रुपयांची मागणी ही या नुकसानीचे न्याय्य आणि वाजवी मूल्यांकन
आहे.

## प्रतिवादी (एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर) यांच्या वतीने युक्तिवाद

1.  **काळजीच्या मानकांचे पालन:** डॉ. राकेश नायर, प्रतिवादी क्रमांक 2, यांनी पित्तशयाच्या शस्त्रक्रिया करताना वाजवी सक्षम सर्जनकडून अपेक्षित वाजवी काळजी आणि कौशल्य वापरले. ही प्रक्रिया स्थापित वैद्यकीय प्रोटोकॉलनुसार पार पाडण्यात आली. वैद्यकीय निष्काळजीपणाच्या प्रकरणांमध्ये डॉक्टरांकडून सर्वोच्च कौशल्याची अपेक्षा केली जात नाही, तर वाजवी काळजी आणि कौशल्याची अपेक्षा केली जाते, जसे की *जेकब मॅथ्यू विरुद्ध पंजाब राज्य, (2005) 6 SCC 1* या प्रकरणात म्हटले आहे.

2.  **शस्त्रक्रियेतील अंतर्भूत गुंतागुंत:** पित्तवाहिनीला होणारे नुकसान, जरी दुर्दैवी असले तरी, कोलेसिस्टेक्टॉमीशी संबंधित एक ओळखला गेलेला आणि अंतर्भूत धोका किंवा गुंतागुंत आहे, जरी ती योग्य काळजी आणि कौशल्याने केली गेली असली तरी. कोणताही सर्जन गुंतागुंत-मुक्त परिणामाची हमी देऊ शकत नाही. केवळ गुंतागुंत झाल्याने आपोआप वैद्यकीय निष्काळजीपणा ठरत नाही. वादीला शस्त्रक्रियेशी संबंधित संभाव्य धोक्यांची माहिती दिली गेली असावी आणि तिची संमती घेण्यात आली असावी.

3.  **निर्णयातील चूक विरुद्ध निष्काळजीपणा:** जरी प्रतिकूल परिणाम झाला असला तरी, तो फारतर निर्णयातील चूक होती, जी कारवाई करण्यायोग्य निष्काळजीपणा ठरत नाही. वैद्यकीय निष्काळजीपणासाठी उच्च मर्यादा आवश्यक आहे, ज्यात सामान्य विवेकबुद्धीचा अभाव किंवा घोर निष्काळजीपणा दिसून येतो. प्रतिवादी क्रमांक 2 ने कोणतीही बेपर्वाई किंवा रुग्णाच्या हिताकडे दुर्लक्ष करण्याची कृती केली नाही.

4.  **वादीवर सिद्धतेचा भार:** वैद्यकीय निष्काळजीपणा सिद्ध करण्याचा भार पूर्णपणे वादीवर आहे. वादीने तज्ञ वैद्यकीय पुराव्याद्वारे हे निश्चितपणे दाखवून दिले पाहिजे की प्रतिवादींनी स्वीकृत काळजीच्या मानकापासून विचलन केले आणि या विचलनमुळेच कथित दुखापत झाली. केवळ आरोप किंवा प्रतिकूल परिणाम निष्काळजीपणा सिद्ध करण्यासाठी पुरेसे नाहीत.

5.  **रुग्णालयाची मर्यादित जबाबदारी:** प्रतिवादी क्रमांक 1, रुग्णालयाने आवश्यक पायाभूत सुविधा, उपकरणे आणि सहाय्यक कर्मचारी प्रदान केले. रुग्णालयाला एका स्वतंत्र सर्जनच्या वैयक्तिक व्यावसायिक निर्णयासाठी आणि कृतींसाठी पूर्णपणे जबाबदार धरले जाऊ शकत नाही, विशेषतः जर ते क्रेडेंशियल्स तपासण्यात आणि सुरक्षित वातावरण प्रदान करण्यात योग्य परिश्रम दाखवू शकले.

6.  **नुकसानभरपाईच्या दाव्याची अतिशयोक्ती:** वादीने मागणी केलेली नुकसानभरपाईची रक्कम, विशेषतः मानसिक यातना आणि भविष्यातील उपचार खर्चासाठी, अंदाजित आणि जास्त आहे. वादीने तिच्या दाव्याच्या प्रत्येक घटकाला पुष्टी देण्यासाठी ठोस पुरावे सादर करणे आवश्यक आहे. ₹3.5 लाख रुपयांचे वैद्यकीय खर्च झाले असले तरी, ₹21.5 लाख रुपयांच्या गैर-आर्थिक आणि भविष्यातील नुकसानीच्या दाव्यासाठी कठोर पुरावा आवश्यक आहे आणि नुकसानभरपाईच्या स्थापित तत्त्वांवर आधारित न्यायालयीन मूल्यांकनाच्या अधीन आहे.

7.  **वेळेवर शस्त्रक्रियेनंतरचे व्यवस्थापन:** प्रतिवादींनी सादर केलेल्या लक्षणांवर आधारित योग्य शस्त्रक्रियेनंतरची काळजी प्रदान केली. निदानातील कोणताही विलंब, जर असेल तर, गुंतागुंतीच्या स्वरूपामुळे होता आणि तो हेतुपुरस्सर किंवा निष्काळजीपणाचा अभाव नव्हता. प्रचलित वैद्यकीय ज्ञान आणि संसाधनांच्या मर्यादेत वादीची स्थिती व्यवस्थापित करण्यासाठी सर्व वाजवी प्रयत्न करण्यात आले.

## PDF Links
-   *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*: [https://api.sci.gov.in/jonew/judis/1674.pdf](https://api.sci.gov.in/jonew/judis/1674.pdf)
-   *Savita Garg v. Director, National Heart Institute, (2004) 8 SCC 56*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)
-   *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*: [https://api.sci.gov.in/jonew/judis/36301.pdf](https://api.sci.gov.in/jonew/judis/36301.pdf)
-   *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)

### JUDGMENT CITATIONS:
The user has requested arguments on behalf of both the plaintiff and the defendant in a medical negligence case. However, as a Legal AI Assistant, I am strictly bound by the rule to provide answers only from the supplied context, which is stated to contain Indian court judgments. No such judgment context has been provided for this query.

Therefore, I am unable to provide the specific arguments as requested, as doing so would require using external knowledge or fabricating information, which is contrary to my instructions.

I think this information will help you more to understand the case:

### Key Legal Issues
Based on general principles of medical negligence in India, the key legal issues in such a case would typically include:
-   Whether the attending surgeon, Dr. Rakesh Nair, owed a duty of care to Mrs. Anjali Deshmukh.
-   Whether there was a breach of this duty of care by Dr. Rakesh Nair during the gallbladder surgery.
-   Whether the alleged breach of duty directly caused the damage to Mrs. Deshmukh's bile duct and subsequent suffering and expenses (causation).
-   Whether the principle of *res ipsa loquitur* is applicable, shifting the burden of proof to the defendants.
-   The quantum of compensation for medical expenses, mental harassment, pain and suffering, and future treatment costs.
-   Whether XYZ Multispecialty Hospital is vicariously liable for the actions of Dr. Rakesh Nair.

### Detailed Narrative
Mrs. Anjali Deshmukh, aged 42, underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024. During
the procedure, it is alleged that due to the negligence of Dr. Rakesh Nair, her bile duct was damaged. Following the
surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating a second admission for corrective surgery at a
different hospital. This corrective surgery and associated treatment led to additional medical expenses of ₹3.5 lakhs.
Furthermore, Mrs. Deshmukh claims to have suffered immense mental agony and physical pain. Despite repeated attempts to
engage with the hospital and Dr. Nair, they have reportedly refused to acknowledge any error or offer compensation. Mrs.
Deshmukh now seeks a total compensation of ₹25 lakhs, covering her medical expenses, mental harassment, pain and
suffering, and anticipated future treatment costs.

### As per the Court (General Principles of Medical Negligence in India)
In India, medical negligence cases are primarily governed by the law of torts, the Consumer Protection Act, 2019, and
certain provisions of the Indian Penal Code, 1860, although criminal liability for doctors is typically reserved for
cases of gross negligence. To establish medical negligence, the plaintiff generally needs to prove three elements: (i) a
legal duty of care owed by the medical professional to the patient, (ii) a breach of that duty, and (iii) that the
breach directly resulted in injury or damage to the patient. The standard of care expected from a medical professional
is that of a reasonably competent practitioner in that field, exercising reasonable skill and care. Mere unsuccessful
treatment or an unfavorable surgical outcome does not automatically establish negligence.

The doctrine of *res ipsa loquitur*, meaning "the thing speaks for itself," may be invoked in certain medical negligence
cases. This doctrine allows a presumption of negligence to arise, shifting the burden of proof to the healthcare
provider to demonstrate that no negligence occurred, provided the incident would not ordinarily happen without
negligence, and the accident was under the exclusive control of the defendant, with no contributory negligence from the
patient. However, courts apply *res ipsa loquitur* with caution in medical negligence cases, and it is not of universal
application. Even if *res ipsa loquitur* is established, the plaintiff must still prove all other elements of medical
negligence, including duty of care, breach of duty, causation, and damages.

Compensation in medical negligence cases aims to restore the victim to the position they would have been in had the negligence not occurred, covering financial losses, future medical expenses, and pain and suffering. The calculation of compensation can vary, but it generally includes medical costs, future treatment costs, and compensation for mental and physical pain.

### Additional Observations
Defenses in medical negligence cases often involve demonstrating that the standard of care was met, that the injury could have resulted from other factors, or that the patient contributed to the injury. Written evidence of consent to inherent risks can also be a defense. Meticulous clinical documentation is crucial for doctors in their defense. It is also important to note that the limitation period for filing medical negligence cases is generally two years.

### SCI_JUDGMENT CITATIONS:
Here are arguments on behalf of both the plaintiff (Mrs. Anjali Deshmukh) and the defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital), drawing upon principles of medical negligence established by the Supreme Court of India.

### Arguments for the Plaintiff (Mrs. Anjali Deshmukh)

**Key Legal Issues**
-   Breach of duty of care by the medical professional.
-   Application of the doctrine of *res ipsa loquitur* (the thing speaks for itself).
-   Causation of injury and suffering due to negligence.
-   Entitlement to compensation for medical expenses, pain, suffering, and mental agony.

**Detailed Narrative**
Mrs. Anjali Deshmukh underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024, under the care of
Dr. Rakesh Nair. During this procedure, her bile duct was damaged. This damage was not a known or accepted complication
that could occur despite due care. Following the surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating
a second admission for corrective surgery at a different hospital. This unforeseen complication and subsequent
corrective procedure resulted in additional medical expenses of ₹3.5 lakhs, along with immense physical pain and mental
agony. The hospital and Dr. Nair have failed to acknowledge their error or offer compensation, compelling Mrs. Deshmukh
to seek ₹25 lakhs for her losses.

**Court Observations**
The plaintiff's case rests on establishing medical negligence, which involves demonstrating a breach of the duty of care
owed by the medical professional. The Supreme Court in *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, and *Kusum
Sharma v. Batra Hospital & Med. Research Centre*, (2010) 3 SCC 480, established that a medical professional is expected
to bring a reasonable degree of skill and knowledge and must exercise a reasonable degree of care. Negligence occurs
when the professional's conduct falls below the standards of a reasonably competent practitioner in their field.

In this scenario, the damage to the bile duct during gallbladder surgery is an event that, in the ordinary course of
things, would not happen if proper care and skill were exercised. This situation strongly invokes the doctrine of *res
ipsa loquitur*, meaning "the thing speaks for itself." As held in *V. Kishan Rao v. Nikhil Super Speciality Hospital*,
(2010) 5 SCC 513, and reiterated in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC OnLine SC
1098, *res ipsa loquitur* is an evidential principle intended to assist a claimant who is unable to adduce specific
evidence as to how the accident occurred. If the accident is such that it does not happen in the ordinary course of
things with proper care, and the thing causing the injury was under the management of the defendant, then negligence can
be inferred in the absence of a reasonable explanation from the defendant. The damage to the bile duct during surgery, a
procedure directly controlled by Dr. Nair, suggests a prima facie case of negligence.

Furthermore, the subsequent severe pain, need for corrective surgery, and additional medical expenses directly resulted from the initial surgical error. This establishes the causal link between the alleged negligence and the damages suffered by Mrs. Deshmukh. The refusal of the hospital and doctor to acknowledge their mistake or compensate her further underscores the need for judicial intervention.

**Judgment & Direction**
The plaintiff seeks compensation for medical expenses, mental harassment, pain and suffering, and future treatment
costs. The Supreme Court in *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, (2019) 7 SCC 401,
recognized that compensation for medical negligence should be assessed considering various factors, including the loss
of quality of life and the economic equivalent of suffering. The additional medical expenses of ₹3.5 lakhs are a direct
and quantifiable loss. The mental agony and physical pain, though not easily quantifiable, are significant components of
suffering for which compensation is due. The total claim of ₹25 lakhs is a reasonable estimate for the comprehensive
losses incurred by Mrs. Deshmukh.

### Arguments for the Defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital)

**Key Legal Issues**
-   Standard of care in medical practice and the "Bolam test."
-   Absence of gross negligence.
-   Complications inherent in surgical procedures.
-   Requirement for expert evidence to prove negligence.

**Detailed Narrative**
Dr. Rakesh Nair and XYZ Multispecialty Hospital contend that the damage to Mrs. Anjali Deshmukh's bile duct during
gallbladder surgery was an unfortunate complication, not a result of negligence. They assert that Dr. Nair exercised the
requisite skill and care expected of a reasonably competent surgeon. The hospital provided all necessary facilities and
support. The subsequent pain and need for corrective surgery, while regrettable, do not automatically imply negligence,
as surgical procedures inherently carry risks and potential complications. The hospital and doctor maintain that they
acted in good faith and in accordance with accepted medical practices.

**Court Observations**
The defense relies on the established legal principle that a medical professional is not liable merely because a
treatment is unsuccessful or an adverse outcome occurs. As per *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, a
simple lack of care, an error of judgment, or an accident is not proof of negligence. The standard of care is that of an
ordinary competent medical practitioner exercising an ordinary degree of professional skill, as articulated in the
"Bolam test" and adopted by Indian courts. The Supreme Court in *Kusum Sharma v. Batra Hospital & Med. Research Centre*,
(2010) 3 SCC 480, emphasized that a doctor is not negligent if they act in accordance with a practice accepted as proper
by a responsible body of medical men skilled in that particular art, even if a body of adverse opinion exists.

Surgical procedures, especially those involving delicate organs like the gallbladder and bile duct, carry inherent
risks. Damage to the bile duct, while undesirable, can be a recognized complication even when the surgeon exercises due
care. The defense would argue that without specific expert evidence demonstrating a deviation from the accepted standard
of care, negligence cannot be presumed. The burden of proving negligence lies heavily on the claimant, and a mere
averment in a complaint, unsupported by expert medical opinion, is insufficient to establish negligence, as held in
*C.P. Sreekumar (Dr.), MS (Ortho) v. S. Ramanujam*, (2009) 7 SCC 130.

The doctrine of *res ipsa loquitur* should be applied with extreme caution in cases of professional negligence,
particularly for doctors. The Supreme Court in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC
OnLine SC 1098, reiterated that *res ipsa loquitur* cannot be applied merely because a patient has not responded
favorably to treatment or a surgery has failed. The defense would argue that the damage to the bile duct, while an
unfortunate outcome, does not "speak for itself" as negligence without further evidence of a specific negligent act or
omission by Dr. Nair. It could be a known, albeit rare, complication that occurred despite the exercise of reasonable
care.

**Judgment & Direction**
The defendants would argue for the dismissal of the compensation claim, asserting that Mrs. Deshmukh has failed to
establish a clear case of medical negligence. They would contend that the unfortunate outcome was a surgical
complication rather than a result of any breach of the standard of care by Dr. Nair or the hospital. They would also
emphasize that the claim for ₹25 lakhs is excessive and unsubstantiated without concrete proof of gross negligence and a
direct causal link that goes beyond inherent surgical risks.

**PDF Links:**
-   *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, 30-11-2021 (English): https://api.sci.gov.in/supremecourt/2010/4109/4109_2010_11_1501_31713_Judgement_30-Nov-2021.pdf
-   *V. Kishan Rao v. Nikhil Super Speciality Hospital*, 08-03-2010 (English): https://api.sci.gov.in/jonew/judis/36301.pdf
-   *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, 09-01-2019 (English): https://api.sci.gov.in/supremecourt/2016/27594/27594_2016_Judgement_09-Jan-2019.pdf
-   *Jacob Mathew v. State of Punjab*, 05-08-2005 (English): https://api.sci.gov.in/jonew/judis/27088.pdf

### NEWACTS CITATIONS:
Section 65B of the Indian Evidence Act, 1872, deals with the admissibility of electronic records, and its corresponding new provision is Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

### Old Provision: Section 65B of Indian Evidence Act, 1872

(1) Notwithstanding anything contained in this Act, any information contained in an electronic record which is printed
on a paper, stored, recorded or copied in optical or magnetic media produced by a computer (hereinafter referred to as
the computer output) shall be deemed to be also a document, if the conditions mentioned in this section are satisfied in
relation to the information and computer in question and shall be admissible in any proceedings, without further proof
or production of the original, as evidence of any contents of the original or of any fact stated therein of which direct
evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer during the period over which the computer was used regularly to store or process information for the purposes of any activities regularly carried on over that period by the person having lawful control over the use of the computer;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer in the ordinary course of the said activities.

(3) Where over any period, the function of storing or processing information for the purposes of any activities regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by computers, whether-
(a) by a combination of computers operating over that period; or
(b) by different computers operating in succession over that period; or
(c) by different combinations of computers operating in succession over that period; or
(d) in any other manner involving the successive operation over that period, in whatever order, of one or more computers and one or more combinations of computers.
all the computers used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer; and references in this section to a computer shall be construed accordingly.

(4) In any proceedings where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things, that is to say,
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer;
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate,
and purporting to be signed by a person occupying a responsible official position in relation to the operation of the relevant device or the management of the relevant activities (whichever is appropriate) shall be evidence of any matter stated in the certificate; and for the purposes of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the person stating it.

(5) For the purposes of this section,
(a) information shall be taken to be supplied to a computer if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) whether in the course of activities carried on by any official, information is supplied with a view to its being stored or processed for the purposes of those activities by a computer operated otherwise than in the course of those activities, that information, if duly supplied to that computer, shall be taken to be supplied to it in the course of those activities;
(c) a computer output shall be taken to have been produced by a computer whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment.

Explanation: For the purposes of this section any reference to information being derived from other information shall be a reference to its being derived therefrom by calculation, comparison or any other process.

### New Provision: Section 63 of Bharatiya Sakshya Adhiniyam, 2023

(1) Notwithstanding anything contained in this Adhiniyam, any information contained in an electronic record which is
printed on paper, stored, recorded or copied in optical or magnetic media or semiconductor memory which is produced by a
computer or any communication device or otherwise stored, recorded or copied in any electronic form (hereinafter
referred to as the computer output) shall be deemed to be also a document, if the conditions mentioned in this section
are satisfied in relation to the information and computer in question and shall be admissible in any proceedings,
without further proof or production of the original, as evidence or any contents of the original or of any fact stated
therein of which direct evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer or communication device during the period over which the computer or communication device was used regularly to create, store or process information for the purposes of any activity regularly carried on over that period by the person having lawful control over the use of the computer or communication device;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer or communication device in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer or communication device was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer or communication device in the ordinary course of the said activities.

(3) Where over any period, the function of creating, storing or processing information for the purposes of any activity regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by means of one or more computers or communication device, whether:
(a) in standalone mode; or
(b) on a computer system; or
(c) on a computer network; or
(d) on a computer resource enabling information creation or providing information processing and storage; or
(e) through an intermediary,
all the computers or communication devices used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer or communication device; and references in this section to a computer or communication device shall be construed accordingly.

(4) In any proceeding where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things shall be submitted along with the electronic record at each instance where it is being submitted for admission, namely:
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer or a communication device referred to in clauses (a) to (e) of sub-section (3);
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate, and purporting to be
signed by a person in charge of the computer or communication device or the management of the relevant activities
(whichever is appropriate) and an expert shall be evidence of any matter stated in the certificate; and for the purposes
of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the
person stating it in the certificate specified in the Schedule.

(5) For the purposes of this section,:
(a) information shall be taken to be supplied to a computer or communication device if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) a computer output shall be taken to have been produced by a computer or communication device whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment or by other electronic means as referred to in clauses (a) to (e) of sub-section (3).

### Key Differences

-   **Scope of Devices**: The new provision explicitly includes "communication device" alongside "computer" throughout the section, broadening the scope of electronic records covered. It also adds "semiconductor memory" as a storage medium.
-   **Computer Output Definition**: The new provision expands the definition of "computer output" to include information "otherwise stored, recorded or copied in any electronic form."
-   **Activities in Sub-section (2)(a)**: The new provision adds "create" to the activities for which the computer or communication device was used regularly, i.e., "to create, store or process information."
-   **Complex Systems in Sub-section (3)**: The new provision significantly updates the description of how functions are performed by multiple devices, replacing the older clauses (a) to (d) with more modern terms like "standalone mode," "computer system," "computer network," "computer resource enabling information creation or providing information processing and storage," and "through an intermediary."
-   **Certificate Requirements in Sub-section (4)**: The new provision specifies that the certificate must be submitted "at each instance where it is being submitted for admission" and adds that it should be signed by "an expert" in addition to the person in charge. It also refers to a "certificate specified in the Schedule."
-   **Sub-section (5) Changes**: The new provision removes clause (b) of the old sub-section (5) and modifies clause (c) to clause (b), expanding the means by which a computer output is produced to include "other electronic means as referred to in clauses (a) to (e) of sub-section (3)."
-   **Explanation**: The Explanation from the old provision is omitted in the new one.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

### SCI_JUDGMENT CITATIONS:
The Supreme Court has consistently emphasized the distinction between a mere breach of contract, which is a civil wrong, and the criminal offense of cheating under Section 420 of the Indian Penal Code (IPC). The crucial element that differentiates the two is the presence of a fraudulent or dishonest intention (mens rea) right from the inception of the transaction.

In *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*, (2022) 8 SCC 696, the Supreme Court reiterated that fraudulent or dishonest intention is a precondition to constitute the offense of cheating. The Court observed that allegations primarily alluding to a breach of terms of a consultancy agreement would essentially be in the nature of a civil dispute.

Similarly, in *Satishchandra Ratanlal Shah v. The State of Gujarat*, (2019) 9 SCC 148, the Supreme Court held that the distinction between a mere breach of contract and cheating depends upon the fraudulent inducement and mens rea. The Court emphasized that mere inability to return a loan amount cannot give rise to criminal prosecution for cheating unless fraudulent or dishonest intention is shown right at the beginning of the transaction.

I think this information will help you more to understand the case:

### Key Legal Issues
-   Whether the ingredients of the offenses of cheating (Section 415 and 420 IPC) and criminal breach of trust (Section 405 IPC) are made out in cases arising from business agreements.
-   The distinction between a mere breach of contract and the criminal offense of cheating.
-   The necessity of proving fraudulent or dishonest intention (mens rea) at the inception of the transaction for an offense under Section 420 IPC.

### Detailed Narrative

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The first respondent (Ramesh Ranganathan), a consultant neurosurgeon, was employed by BGS Apollo Hospital. Differences
arose, and his services were terminated. He filed a criminal complaint alleging misuse of authority, defamation, and
irregularities in billing, leading to cognizance being taken under various sections of the IPC, including Section 420.
The Additional Sessions Judge set aside the cognizance, but the High Court partially restored it for Sections 405 and
420. The appellant (M.N.G Bharateesh Reddy) challenged this before the Supreme Court.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The appellant (Satishchandra Ratanlal Shah) obtained a loan of Rs. 27,00,000/- from the respondent no. 2's (Dharshan
Fiscal Pvt. Ltd.) money lending company. The appellant failed to repay the loan, leading to a criminal complaint under
Sections 406, 409, 417, 420, 294(b), and 506(2) of the IPC. Simultaneously, a summary civil suit for recovery of the
loan amount was also filed and was pending. The High Court dismissed the appellant's petition to quash the charges,
observing a prima facie case of cheating under Section 420 IPC, but not criminal breach of trust under Section 406 IPC.
The appellant then approached the Supreme Court.

### Court Observations

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court, referring to *Hridaya Ranjan Prasad Verma v. State of Bihar*, (2000) 4 SCC 168, and *Dalip Kaur v.
Jagnar Singh*, (2009) 14 SCC 696, emphasized that fraudulent or dishonest intention is a precondition for the offense of
cheating. The Court noted that a mere breach of contract does not amount to cheating unless a fraudulent or dishonest
intention is shown at the very beginning of the transaction. In this case, the Court found that the allegations
primarily stemmed from the termination of services and improper billing, which at most alluded to a breach of the
consultancy agreement, a civil dispute. The Court concluded that the ingredients of Sections 415 and 420 IPC were not
made out.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court reiterated that the distinction between a mere breach of contract and cheating depends on fraudulent
inducement and mens rea. The Court observed that the appellant was in an economic crisis and had approached the
respondent for a loan. The mere inability to return the loan amount, coupled with the pending civil suit for recovery,
could not automatically lead to a criminal prosecution for cheating without proof of fraudulent or dishonest intention
at the inception of the transaction. The Court cautioned against criminalizing civil disputes, such as breaches of
contractual obligations, and held that the legislature intended to criminalize only those breaches accompanied by
fraudulent, dishonest, or deceptive inducements.

### Judgment & Direction

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court allowed the appeal, setting aside the High Court's judgment to the extent it found a prima facie case under Sections 405 and 420 IPC. The criminal proceedings against the appellant were quashed.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court allowed the appeal, setting aside the impugned order of the High Court. The application filed by the appellant under Section 482 of the Code of Criminal Procedure, 1973, was allowed, and the proceedings initiated based on the FIR were quashed. The Court also expunged certain observations made by the High Court that were deemed unnecessary and potentially prejudicial to the accused.

**PDF Links:**
-   *M.N.G Bharateesh Reddy v. Ramesh Ranganathan, Crl.A. No.-001273-001273 - 2022* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2019/34821/34821_2019_3_19_37392_Judgement_18-Aug-2022.pdf))
-   *Satishchandra Ratanlal Shah v. The State of Gujarat, Crl.A. No.-000009-000009 - 2019* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2018/17145/17145_2018_Judgement_03-Jan-2019.pdf))

### NEWACTS CITATIONS:
Section 420 of the Indian Penal Code, 1860, deals with cheating and dishonestly inducing the delivery of property. This provision is now covered under Section 318(4) of the Bharatiya Nyaya Sanhita, 2023.

### Old Provision: Section 420 of The Indian Penal Code, 1860

Whoever cheats and thereby dishonestly induces the person deceived to deliver any property to any person, or to make, alter or destroy the whole or any part of a valuable security, or anything which is signed or sealed, and which is capable of being converted into a valuable security, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

### New Provision: Section 318(4) of Bharatiya Nyaya Sanhita, 2023

Whoever cheats and thereby dishonestly induces the person deceived to deliver any property to any person, or to make, alter or destroy the whole or any part of a valuable security, or anything which is signed or sealed, and which is capable of being converted into a valuable security, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

### Key Differences

The text of the provision remains identical in both the Indian Penal Code, 1860, and the Bharatiya Nyaya Sanhita, 2023, with the only change being the section number and the name of the Act.

Regarding the user's query about "cheating fraudulent intention mens rea civil dispute business agreement," it is important to emphasize that Section 420 IPC (now Section 318(4) BNS) deals with criminal cheating. For an offense under this section, the prosecution must prove a dishonest or fraudulent intention (mens rea) on the part of the accused *at the very inception* of the transaction.

In the context of a civil dispute or a business agreement, a mere breach of contract or failure to fulfill a promise
does not automatically amount to criminal cheating. The distinction lies in the presence of a dishonest intention to
deceive from the outset. If the intention to deceive was not present at the time the property was induced to be
delivered, but rather arose later due to unforeseen circumstances or a subsequent inability to fulfill the promise, it
would typically fall under the purview of a civil dispute (breach of contract) rather than criminal cheating.

Courts have consistently held that for Section 420 IPC to apply, the fraudulent or dishonest intention must be established from the very beginning of the transaction. If the intention was honest at the time of entering into the agreement, but circumstances later led to a failure to perform, it would generally be a civil wrong.


</details>

Follow-up suggestions returned: `What evidence proves no fraudulent intent?`, `How to prove it's a civil dispute?`, `What is the burden of proof?`

---

### Turn 9 — Defective product liability under CPA 2019

*Source section in `docs/test prompt.txt`: **Consumer Laws***

#### 1. User prompt (verbatim)

```text
Scenario 1: Defective Product Liability Prompt: Priya purchases a brand-new refrigerator from a well-known electronics store. After a week, it stops working, and the manufacturer refuses to repair or replace it, claiming that the issue was caused by improper installation. What legal options does Priya have under the Consumer Protection Act, 2019, to address this defect?
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 372 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | *(none)* |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 9 |
| History turns loaded | 5 |
| Query rewritten by memory agent | True |
| Effective query after rewrite | `Provide arguments to defend a client facing a criminal case under Section 420 of the Indian Penal Code, 1860, for alleged cheating. Emphasize that the case involves a civil dispute arising from a fail` |
| Agents planned | Newacts, Scenario |
| Agents used | Newacts, Scenario |
| Sources returned | 15 |
| Wall clock | 26.4s |
| First streamed token | 9.032s |

<details>
<summary>Sources cited back to the client (15)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | AI-Generated Legal Analysis | Scenario | None |
| 3 | judgment | CENTRAL BUREAU OF INVESTIGATION vs VIJAY KUMAR GUPTA | Judgment | 259.55185 |
| 4 | judgment | Prem Chand vs State of Rajasthan | Judgment | 249.21309 |
| 5 | judgment | Surendra Agrawal vs Hemlata Patel | Judgment | 245.63292 |
| 6 | judgment | Rajendra Singh vs The State of Rajasthan | Judgment | 245.38173 |
| 7 | judgment | NILAY S/O ARVINDBHAI SHETH vs SHRI KAPIL KISHORBHAI JOSHI | Judgment | 235.97122 |
| 8 | sci_judgment | AJAY MITRA VS STATE OF M.P. . | SCI_Judgment | None |
| 9 | sci_judgment | M.N.G BHARATEESH REDDY VS RAMESH RANGANATHAN | SCI_Judgment | None |
| 10 | sci_judgment | SATISHCHANDRA RATANLAL SHAH VS THE STATE OF GUJARAT | SCI_Judgment | None |
| 11 | sci_judgment | INDER CHAND BAGRI VS JAGADISH PRASAD BAGRI | SCI_Judgment | None |
| 12 | sci_judgment | J. VEDHASINGH VS R.M. GOVINDAN | SCI_Judgment | None |
| 13 | newacts | The Indian Penal Code, 1860 - Section 420 | Newacts | None |
| 14 | newacts | The Indian Penal Code, 1860 - Section 419 | Newacts | None |
| 15 | newacts | The Indian Penal Code, 1860 - Section 421 | Newacts | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (68,893 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
## DEFENSE ARGUMENTS AGAINST CHARGE UNDER SECTION 420 INDIAN PENAL CODE, 1860

The defense submits that the present case is fundamentally a civil dispute arising from a failed business agreement and does not constitute the offense of cheating under Section 420 of the Indian Penal Code, 1860. The essential ingredients for establishing criminal cheating, particularly the element of dishonest or fraudulent intention from the inception of the transaction, are conspicuously absent in the present matter.

### Absence of Dishonest Intention at Inception

1.  **Lack of *Mens Rea* at Inception:** For an offense under Section 420 of the Indian Penal Code, 1860, it is
imperative to prove that the accused had a dishonest or fraudulent intention at the very beginning of the transaction,
i.e., from the moment the promise or representation was made. A mere breach of contract or subsequent failure to fulfill
a promise, in the absence of such initial dishonest intention, does not automatically transform a civil wrong into a
criminal offense. The Supreme Court has consistently held that the distinction between a mere breach of contract and the
offense of cheating depends upon the intention of the accused at the time of inducement. If the intention to cheat was
not present at the inception, the offense of cheating cannot be said to have been committed.

2.  **Subsequent Failure Not Equivalent to Prior Deception:** The alleged inability or failure of the client to fulfill
the terms of the business agreement arose from unforeseen circumstances, commercial exigencies, or genuine business
difficulties, which developed *subsequent* to the formation of the agreement. Such a subsequent failure cannot be
retrospectively construed as a dishonest intention existing at the time the agreement was entered into. The Supreme
Court has clarified that a mere failure to keep a promise cannot be presumed to be an act of cheating.

3.  **Bona Fide Business Transaction:** The transaction between the parties was a bona fide business arrangement entered into with a genuine intention to execute the agreement and derive mutual commercial benefit. All actions taken by the client were in furtherance of the business objective, and any difficulties encountered were typical risks associated with commercial ventures. The client made genuine efforts to perform their obligations under the agreement.

### Nature of Dispute as Civil Liability

1.  **Breach of Contract, Not Criminal Deception:** The allegations, even if taken at face value, primarily disclose a
case of breach of contract, which falls squarely within the domain of civil law. The remedies for such a breach lie in
civil courts, where damages for non-performance or specific performance of the contract can be sought. The mere fact
that a party has suffered a loss in a business transaction does not automatically warrant the initiation of criminal
proceedings. The Supreme Court has repeatedly cautioned against converting civil disputes into criminal cases.

2.  **Availability of Civil Remedies:** The complainant has adequate and effective remedies available under civil law, including filing a suit for recovery of money, specific performance, or damages for breach of contract. Resorting to criminal proceedings under Section 420 of the Indian Penal Code, 1860, in such circumstances amounts to an abuse of the process of law, often with the ulterior motive of exerting pressure for recovery of money.

3.  **Absence of Inducement to Deliver Property by Deception:** Section 420 of the Indian Penal Code, 1860, requires
that the accused must have dishonestly induced the complainant to deliver any property or to make, alter, or destroy the
whole or any part of a valuable security. In the present case, any delivery of property or financial transaction
occurred within the framework of a commercial agreement, not as a result of any dishonest inducement or fraudulent
misrepresentation made at the inception by the client. The complainant willingly entered into the business arrangement,
presumably after due diligence and assessment of commercial risks.

### Judicial Precedents Distinguishing Civil and Criminal Liability

The Supreme Court of India has consistently drawn a clear distinction between a civil wrong and a criminal offense, particularly in cases involving allegations of cheating arising from contractual disputes:

-   In *Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168*, the Supreme Court held that in order to
constitute an offense of cheating, the intention to cheat must be in existence at the time when the inducement was made.
If the intention was not dishonest at the time of making the promise but developed subsequently, it would not amount to
cheating. The Court observed that a mere breach of contract cannot give rise to criminal prosecution for cheating unless
fraudulent or dishonest intention is shown right at the beginning of the transaction.

-   In *International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348*, the Supreme Court reiterated that for an offense under Section 420 of the Indian Penal Code, 1860, the element of deception and dishonest intention from the very beginning is crucial. The Court emphasized that a mere breach of contract or failure to pay money does not automatically lead to criminal prosecution for cheating.

-   In *G. Sagar Suri v. State of U.P., (2000) 2 SCC 636*, the Supreme Court cautioned against converting purely civil disputes into criminal cases. The Court observed that it is not every breach of contract that would amount to the offense of cheating.

Therefore, based on the facts and the established legal principles, the defense submits that the allegations against the client do not fulfill the essential ingredients of criminal cheating under Section 420 of the Indian Penal Code, 1860, and the matter ought to be treated as a civil dispute.

### PDF Links
-   *Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168*: [https://api.sci.gov.in/jonew/judis/22759.pdf](https://api.sci.gov.in/jonew/judis/22759.pdf)
-   *International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348*: [https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf](https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf)

### NEWACTS CITATIONS:
Section 420 of the Indian Penal Code, 1860, which deals with cheating and dishonestly inducing the delivery of property, is now covered under Section 318(4) of the Bharatiya Nyaya Sanhita, 2023.

### Old Provision: Section 420 of The Indian Penal Code, 1860

Whoever cheats and thereby dishonestly induces the person deceived to deliver any property to any person, or to make, alter or destroy the whole or any part of a valuable security, or anything which is signed or sealed, and which is capable of being converted into a valuable security, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

### New Provision: Section 318(4) of Bharatiya Nyaya Sanhita, 2023

Whoever cheats and thereby dishonestly induces the person deceived to deliver any property to any person, or to make, alter or destroy the whole or any part of a valuable security, or anything which is signed or sealed, and which is capable of being converted into a valuable security, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

### Key Differences

The text of the provision remains identical in both the Indian Penal Code, 1860, and the Bharatiya Nyaya Sanhita, 2023,
with the only change being the section number and the name of the Act. For an offense under this section, the
prosecution must prove a dishonest or fraudulent intention (mens rea) on the part of the accused at the very inception
of the transaction. In the context of a civil dispute or a business agreement, a mere breach of contract or failure to
fulfill a promise does not automatically amount to criminal cheating. The distinction lies in the presence of a
dishonest intention to deceive from the outset. If the intention to deceive was not present at the time the property was
induced to be delivered, but rather arose later due to unforeseen circumstances or a subsequent inability to fulfill the
promise, it would typically fall under the purview of a civil dispute (breach of contract) rather than criminal
cheating.

### JUDGMENT CITATIONS:
## वादी (श्रीमती अंजली देशमुख) यांच्या वतीने युक्तिवाद

1.  **कर्तव्याचे अस्तित्व आणि त्याचे उल्लंघन:** प्रतिवादी, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर, यांनी
श्रीमती अंजली देशमुख यांच्या प्रति अत्यंत काळजी घेण्याचे कर्तव्य बजावले. हे कर्तव्य श्रीमती देशमुख यांनी प्रतिवादी
रुग्णालयात प्रतिवादी क्रमांक 2 च्या देखरेखीखाली वैद्यकीय उपचार आणि शस्त्रक्रिया करून घेण्यास सुरुवात केल्यावर उद्भवले.
अपेक्षित काळजीचा दर्जा हा त्यांच्या संबंधित क्षेत्रातील वाजवी सक्षम वैद्यकीय व्यावसायिकाचा असतो. पित्तवाहिनीला नुकसान
पोहोचवणे, जी एक नियमित पित्तशयाच्या शस्त्रक्रिया (कोलेसिस्टेक्टॉमी) आहे, हे या कर्तव्याचे स्पष्ट उल्लंघन आहे. अशा
प्रकारची दुखापत, विशेषतः मानक प्रक्रियेत, वाजवी काळजी आणि कौशल्याच्या अभावाची सूचक आहे, जी स्वीकृत वैद्यकीय मानकांपेक्षा
कमी आहे. *डॉ. लक्ष्मण बाळकृष्ण जोशी विरुद्ध डॉ. त्रिंबक बापू गोडबोले, AIR 1969 SC 128* या प्रकरणात सर्वोच्च न्यायालयाने
असे प्रतिपादन केले आहे की, डॉक्टरांचे कर्तव्य आहे की त्यांनी आपल्या कामात वाजवी कौशल्य आणि ज्ञान वापरावे आणि वाजवी काळजी
घ्यावी.

2.  **कारणीभाव आणि थेट दुखापत:** तीव्र पोटदुखी, त्यानंतरच्या गुंतागुंत आणि दुसऱ्या दुरुस्त शस्त्रक्रियेची आवश्यकता हे सर्व डॉ. राकेश नायर यांनी केलेल्या सुरुवातीच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला झालेल्या निष्काळजीपणामुळे थेट आणि जवळचे परिणाम आहेत. शस्त्रक्रियेनंतर वादीची बिघडलेली प्रकृती, ज्यामुळे निदान आणि दुरुस्तीसाठी दुसऱ्या रुग्णालयात दाखल व्हावे लागले, हे दुखापतीचा प्रतिवादींच्या कृतीशी स्पष्ट संबंध दर्शवते.

3.  ***रेस इप्सा लोक्युटर* (Res Ipsa Loquitur) सिद्धांताचा वापर:** या प्रकरणातील तथ्ये *रेस इप्सा लोक्युटर* (गोष्ट
स्वतःच बोलते) या सिद्धांताला जोरदारपणे लागू होतात. पित्तशयाच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला होणारे नुकसान हे
निष्काळजीपणाच्या अनुपस्थितीत सामान्यतः घडणारी घटना नाही. शस्त्रक्रियेची प्रक्रिया आणि वापरलेली उपकरणे प्रतिवादी क्रमांक
2 च्या अनन्य नियंत्रणाखाली होती आणि वादी बेशुद्ध होती. म्हणून, हा सिद्धतेचा भार प्रतिवादींवर येतो की त्यांनी त्यांच्या
बाजूने कोणताही निष्काळजीपणा न करता असे नुकसान कसे झाले हे स्पष्ट करावे.

4.  **रुग्णालयाची प्रतिनिधी जबाबदारी:** प्रतिवादी क्रमांक 1, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल, त्यांचे उपस्थित सर्जन,
डॉ. राकेश नायर यांच्या निष्काळजीपणाच्या कृत्यांसाठी आणि चुकांसाठी प्रतिनिधी जबाबदार आहे, कारण ते रुग्णालयाच्या नोकरीत
किंवा व्यावसायिक संबंधात काम करत होते. रुग्णालयाचे आपल्या रुग्णांना सुरक्षित आणि सक्षम वैद्यकीय सेवा प्रदान करण्याचे
कर्तव्य आहे. *सविता गर्ग विरुद्ध संचालक, नॅशनल हार्ट इन्स्टिट्यूट, (2004) 8 SCC 56* या प्रकरणात सर्वोच्च न्यायालयाने
डॉक्टरांच्या निष्काळजीपणासाठी रुग्णालयांच्या प्रतिनिधी जबाबदारीच्या सिद्धांताला दुजोरा दिला आहे.

5.  **अपुरे शस्त्रक्रियेनंतरचे उपचार आणि निदान:** वादीच्या तीव्र आणि सततच्या शस्त्रक्रियेनंतरच्या लक्षणांवरूनही प्रतिवादींनी पित्तवाहिनीच्या दुखापतीचे त्वरित निदान करण्यात केलेली अक्षमता काळजीच्या मानकातील त्रुटी दर्शवते. निदानातील या विलंबाने वादीचे दुःख वाढवले आणि अधिक जटिल दुरुस्त प्रक्रियेची आवश्यकता निर्माण केली, जी निष्काळजीपणाची अतिरिक्त कृती ठरते.

6.  **व्यापक नुकसानभरपाईचा हक्क:** वादीला ₹3.5 लाख रुपयांच्या दुरुस्त शस्त्रक्रिया आणि संबंधित उपचारांसाठी झालेल्या
प्रत्यक्ष वैद्यकीय खर्चासह महत्त्वपूर्ण आणि मोजता येण्याजोग्या नुकसानीचा सामना करावा लागला आहे. आर्थिक
नुकसानीव्यतिरिक्त, प्रतिवादींच्या निष्काळजीपणामुळे तिला प्रचंड शारीरिक वेदना, दुःख, मानसिक यातना आणि जीवनाच्या गुणवत्तेत
घट झाली आहे. तिला अपेक्षित भविष्यातील वैद्यकीय उपचार आणि काळजीसाठी देखील नुकसानभरपाई मिळण्याचा हक्क आहे. *इंडियन मेडिकल
असोसिएशन विरुद्ध व्ही.पी. शांता, (1995) 6 SCC 651* या प्रकरणात सर्वोच्च न्यायालयाने वैद्यकीय सेवांना "सेवा" म्हणून
मान्यता दिली आणि वैद्यकीय व्यावसायिक आणि रुग्णालये सेवेतील त्रुटींसाठी जबाबदार असल्याचे म्हटले आहे, ज्यात
निष्काळजीपणामुळे होणारे नुकसान समाविष्ट आहे. एकूण ₹25 लाख रुपयांची मागणी ही या नुकसानीचे न्याय्य आणि वाजवी मूल्यांकन
आहे.

## प्रतिवादी (एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर) यांच्या वतीने युक्तिवाद

1.  **काळजीच्या मानकांचे पालन:** डॉ. राकेश नायर, प्रतिवादी क्रमांक 2, यांनी पित्तशयाच्या शस्त्रक्रिया करताना वाजवी सक्षम सर्जनकडून अपेक्षित वाजवी काळजी आणि कौशल्य वापरले. ही प्रक्रिया स्थापित वैद्यकीय प्रोटोकॉलनुसार पार पाडण्यात आली. वैद्यकीय निष्काळजीपणाच्या प्रकरणांमध्ये डॉक्टरांकडून सर्वोच्च कौशल्याची अपेक्षा केली जात नाही, तर वाजवी काळजी आणि कौशल्याची अपेक्षा केली जाते, जसे की *जेकब मॅथ्यू विरुद्ध पंजाब राज्य, (2005) 6 SCC 1* या प्रकरणात म्हटले आहे.

2.  **शस्त्रक्रियेतील अंतर्भूत गुंतागुंत:** पित्तवाहिनीला होणारे नुकसान, जरी दुर्दैवी असले तरी, कोलेसिस्टेक्टॉमीशी संबंधित एक ओळखला गेलेला आणि अंतर्भूत धोका किंवा गुंतागुंत आहे, जरी ती योग्य काळजी आणि कौशल्याने केली गेली असली तरी. कोणताही सर्जन गुंतागुंत-मुक्त परिणामाची हमी देऊ शकत नाही. केवळ गुंतागुंत झाल्याने आपोआप वैद्यकीय निष्काळजीपणा ठरत नाही. वादीला शस्त्रक्रियेशी संबंधित संभाव्य धोक्यांची माहिती दिली गेली असावी आणि तिची संमती घेण्यात आली असावी.

3.  **निर्णयातील चूक विरुद्ध निष्काळजीपणा:** जरी प्रतिकूल परिणाम झाला असला तरी, तो फारतर निर्णयातील चूक होती, जी कारवाई करण्यायोग्य निष्काळजीपणा ठरत नाही. वैद्यकीय निष्काळजीपणासाठी उच्च मर्यादा आवश्यक आहे, ज्यात सामान्य विवेकबुद्धीचा अभाव किंवा घोर निष्काळजीपणा दिसून येतो. प्रतिवादी क्रमांक 2 ने कोणतीही बेपर्वाई किंवा रुग्णाच्या हिताकडे दुर्लक्ष करण्याची कृती केली नाही.

4.  **वादीवर सिद्धतेचा भार:** वैद्यकीय निष्काळजीपणा सिद्ध करण्याचा भार पूर्णपणे वादीवर आहे. वादीने तज्ञ वैद्यकीय पुराव्याद्वारे हे निश्चितपणे दाखवून दिले पाहिजे की प्रतिवादींनी स्वीकृत काळजीच्या मानकापासून विचलन केले आणि या विचलनमुळेच कथित दुखापत झाली. केवळ आरोप किंवा प्रतिकूल परिणाम निष्काळजीपणा सिद्ध करण्यासाठी पुरेसे नाहीत.

5.  **रुग्णालयाची मर्यादित जबाबदारी:** प्रतिवादी क्रमांक 1, रुग्णालयाने आवश्यक पायाभूत सुविधा, उपकरणे आणि सहाय्यक कर्मचारी प्रदान केले. रुग्णालयाला एका स्वतंत्र सर्जनच्या वैयक्तिक व्यावसायिक निर्णयासाठी आणि कृतींसाठी पूर्णपणे जबाबदार धरले जाऊ शकत नाही, विशेषतः जर ते क्रेडेंशियल्स तपासण्यात आणि सुरक्षित वातावरण प्रदान करण्यात योग्य परिश्रम दाखवू शकले.

6.  **नुकसानभरपाईच्या दाव्याची अतिशयोक्ती:** वादीने मागणी केलेली नुकसानभरपाईची रक्कम, विशेषतः मानसिक यातना आणि भविष्यातील उपचार खर्चासाठी, अंदाजित आणि जास्त आहे. वादीने तिच्या दाव्याच्या प्रत्येक घटकाला पुष्टी देण्यासाठी ठोस पुरावे सादर करणे आवश्यक आहे. ₹3.5 लाख रुपयांचे वैद्यकीय खर्च झाले असले तरी, ₹21.5 लाख रुपयांच्या गैर-आर्थिक आणि भविष्यातील नुकसानीच्या दाव्यासाठी कठोर पुरावा आवश्यक आहे आणि नुकसानभरपाईच्या स्थापित तत्त्वांवर आधारित न्यायालयीन मूल्यांकनाच्या अधीन आहे.

7.  **वेळेवर शस्त्रक्रियेनंतरचे व्यवस्थापन:** प्रतिवादींनी सादर केलेल्या लक्षणांवर आधारित योग्य शस्त्रक्रियेनंतरची काळजी प्रदान केली. निदानातील कोणताही विलंब, जर असेल तर, गुंतागुंतीच्या स्वरूपामुळे होता आणि तो हेतुपुरस्सर किंवा निष्काळजीपणाचा अभाव नव्हता. प्रचलित वैद्यकीय ज्ञान आणि संसाधनांच्या मर्यादेत वादीची स्थिती व्यवस्थापित करण्यासाठी सर्व वाजवी प्रयत्न करण्यात आले.

## PDF Links
-   *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*: [https://api.sci.gov.in/jonew/judis/1674.pdf](https://api.sci.gov.in/jonew/judis/1674.pdf)
-   *Savita Garg v. Director, National Heart Institute, (2004) 8 SCC 56*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)
-   *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*: [https://api.sci.gov.in/jonew/judis/36301.pdf](https://api.sci.gov.in/jonew/judis/36301.pdf)
-   *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)

### JUDGMENT CITATIONS:
The user has requested arguments on behalf of both the plaintiff and the defendant in a medical negligence case. However, as a Legal AI Assistant, I am strictly bound by the rule to provide answers only from the supplied context, which is stated to contain Indian court judgments. No such judgment context has been provided for this query.

Therefore, I am unable to provide the specific arguments as requested, as doing so would require using external knowledge or fabricating information, which is contrary to my instructions.

I think this information will help you more to understand the case:

### Key Legal Issues
Based on general principles of medical negligence in India, the key legal issues in such a case would typically include:
-   Whether the attending surgeon, Dr. Rakesh Nair, owed a duty of care to Mrs. Anjali Deshmukh.
-   Whether there was a breach of this duty of care by Dr. Rakesh Nair during the gallbladder surgery.
-   Whether the alleged breach of duty directly caused the damage to Mrs. Deshmukh's bile duct and subsequent suffering and expenses (causation).
-   Whether the principle of *res ipsa loquitur* is applicable, shifting the burden of proof to the defendants.
-   The quantum of compensation for medical expenses, mental harassment, pain and suffering, and future treatment costs.
-   Whether XYZ Multispecialty Hospital is vicariously liable for the actions of Dr. Rakesh Nair.

### Detailed Narrative
Mrs. Anjali Deshmukh, aged 42, underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024. During
the procedure, it is alleged that due to the negligence of Dr. Rakesh Nair, her bile duct was damaged. Following the
surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating a second admission for corrective surgery at a
different hospital. This corrective surgery and associated treatment led to additional medical expenses of ₹3.5 lakhs.
Furthermore, Mrs. Deshmukh claims to have suffered immense mental agony and physical pain. Despite repeated attempts to
engage with the hospital and Dr. Nair, they have reportedly refused to acknowledge any error or offer compensation. Mrs.
Deshmukh now seeks a total compensation of ₹25 lakhs, covering her medical expenses, mental harassment, pain and
suffering, and anticipated future treatment costs.

### As per the Court (General Principles of Medical Negligence in India)
In India, medical negligence cases are primarily governed by the law of torts, the Consumer Protection Act, 2019, and
certain provisions of the Indian Penal Code, 1860, although criminal liability for doctors is typically reserved for
cases of gross negligence. To establish medical negligence, the plaintiff generally needs to prove three elements: (i) a
legal duty of care owed by the medical professional to the patient, (ii) a breach of that duty, and (iii) that the
breach directly resulted in injury or damage to the patient. The standard of care expected from a medical professional
is that of a reasonably competent practitioner in that field, exercising reasonable skill and care. Mere unsuccessful
treatment or an unfavorable surgical outcome does not automatically establish negligence.

The doctrine of *res ipsa loquitur*, meaning "the thing speaks for itself," may be invoked in certain medical negligence
cases. This doctrine allows a presumption of negligence to arise, shifting the burden of proof to the healthcare
provider to demonstrate that no negligence occurred, provided the incident would not ordinarily happen without
negligence, and the accident was under the exclusive control of the defendant, with no contributory negligence from the
patient. However, courts apply *res ipsa loquitur* with caution in medical negligence cases, and it is not of universal
application. Even if *res ipsa loquitur* is established, the plaintiff must still prove all other elements of medical
negligence, including duty of care, breach of duty, causation, and damages.

Compensation in medical negligence cases aims to restore the victim to the position they would have been in had the negligence not occurred, covering financial losses, future medical expenses, and pain and suffering. The calculation of compensation can vary, but it generally includes medical costs, future treatment costs, and compensation for mental and physical pain.

### Additional Observations
Defenses in medical negligence cases often involve demonstrating that the standard of care was met, that the injury could have resulted from other factors, or that the patient contributed to the injury. Written evidence of consent to inherent risks can also be a defense. Meticulous clinical documentation is crucial for doctors in their defense. It is also important to note that the limitation period for filing medical negligence cases is generally two years.

### SCI_JUDGMENT CITATIONS:
Here are arguments on behalf of both the plaintiff (Mrs. Anjali Deshmukh) and the defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital), drawing upon principles of medical negligence established by the Supreme Court of India.

### Arguments for the Plaintiff (Mrs. Anjali Deshmukh)

**Key Legal Issues**
-   Breach of duty of care by the medical professional.
-   Application of the doctrine of *res ipsa loquitur* (the thing speaks for itself).
-   Causation of injury and suffering due to negligence.
-   Entitlement to compensation for medical expenses, pain, suffering, and mental agony.

**Detailed Narrative**
Mrs. Anjali Deshmukh underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024, under the care of
Dr. Rakesh Nair. During this procedure, her bile duct was damaged. This damage was not a known or accepted complication
that could occur despite due care. Following the surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating
a second admission for corrective surgery at a different hospital. This unforeseen complication and subsequent
corrective procedure resulted in additional medical expenses of ₹3.5 lakhs, along with immense physical pain and mental
agony. The hospital and Dr. Nair have failed to acknowledge their error or offer compensation, compelling Mrs. Deshmukh
to seek ₹25 lakhs for her losses.

**Court Observations**
The plaintiff's case rests on establishing medical negligence, which involves demonstrating a breach of the duty of care
owed by the medical professional. The Supreme Court in *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, and *Kusum
Sharma v. Batra Hospital & Med. Research Centre*, (2010) 3 SCC 480, established that a medical professional is expected
to bring a reasonable degree of skill and knowledge and must exercise a reasonable degree of care. Negligence occurs
when the professional's conduct falls below the standards of a reasonably competent practitioner in their field.

In this scenario, the damage to the bile duct during gallbladder surgery is an event that, in the ordinary course of
things, would not happen if proper care and skill were exercised. This situation strongly invokes the doctrine of *res
ipsa loquitur*, meaning "the thing speaks for itself." As held in *V. Kishan Rao v. Nikhil Super Speciality Hospital*,
(2010) 5 SCC 513, and reiterated in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC OnLine SC
1098, *res ipsa loquitur* is an evidential principle intended to assist a claimant who is unable to adduce specific
evidence as to how the accident occurred. If the accident is such that it does not happen in the ordinary course of
things with proper care, and the thing causing the injury was under the management of the defendant, then negligence can
be inferred in the absence of a reasonable explanation from the defendant. The damage to the bile duct during surgery, a
procedure directly controlled by Dr. Nair, suggests a prima facie case of negligence.

Furthermore, the subsequent severe pain, need for corrective surgery, and additional medical expenses directly resulted from the initial surgical error. This establishes the causal link between the alleged negligence and the damages suffered by Mrs. Deshmukh. The refusal of the hospital and doctor to acknowledge their mistake or compensate her further underscores the need for judicial intervention.

**Judgment & Direction**
The plaintiff seeks compensation for medical expenses, mental harassment, pain and suffering, and future treatment
costs. The Supreme Court in *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, (2019) 7 SCC 401,
recognized that compensation for medical negligence should be assessed considering various factors, including the loss
of quality of life and the economic equivalent of suffering. The additional medical expenses of ₹3.5 lakhs are a direct
and quantifiable loss. The mental agony and physical pain, though not easily quantifiable, are significant components of
suffering for which compensation is due. The total claim of ₹25 lakhs is a reasonable estimate for the comprehensive
losses incurred by Mrs. Deshmukh.

### Arguments for the Defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital)

**Key Legal Issues**
-   Standard of care in medical practice and the "Bolam test."
-   Absence of gross negligence.
-   Complications inherent in surgical procedures.
-   Requirement for expert evidence to prove negligence.

**Detailed Narrative**
Dr. Rakesh Nair and XYZ Multispecialty Hospital contend that the damage to Mrs. Anjali Deshmukh's bile duct during
gallbladder surgery was an unfortunate complication, not a result of negligence. They assert that Dr. Nair exercised the
requisite skill and care expected of a reasonably competent surgeon. The hospital provided all necessary facilities and
support. The subsequent pain and need for corrective surgery, while regrettable, do not automatically imply negligence,
as surgical procedures inherently carry risks and potential complications. The hospital and doctor maintain that they
acted in good faith and in accordance with accepted medical practices.

**Court Observations**
The defense relies on the established legal principle that a medical professional is not liable merely because a
treatment is unsuccessful or an adverse outcome occurs. As per *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, a
simple lack of care, an error of judgment, or an accident is not proof of negligence. The standard of care is that of an
ordinary competent medical practitioner exercising an ordinary degree of professional skill, as articulated in the
"Bolam test" and adopted by Indian courts. The Supreme Court in *Kusum Sharma v. Batra Hospital & Med. Research Centre*,
(2010) 3 SCC 480, emphasized that a doctor is not negligent if they act in accordance with a practice accepted as proper
by a responsible body of medical men skilled in that particular art, even if a body of adverse opinion exists.

Surgical procedures, especially those involving delicate organs like the gallbladder and bile duct, carry inherent
risks. Damage to the bile duct, while undesirable, can be a recognized complication even when the surgeon exercises due
care. The defense would argue that without specific expert evidence demonstrating a deviation from the accepted standard
of care, negligence cannot be presumed. The burden of proving negligence lies heavily on the claimant, and a mere
averment in a complaint, unsupported by expert medical opinion, is insufficient to establish negligence, as held in
*C.P. Sreekumar (Dr.), MS (Ortho) v. S. Ramanujam*, (2009) 7 SCC 130.

The doctrine of *res ipsa loquitur* should be applied with extreme caution in cases of professional negligence,
particularly for doctors. The Supreme Court in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC
OnLine SC 1098, reiterated that *res ipsa loquitur* cannot be applied merely because a patient has not responded
favorably to treatment or a surgery has failed. The defense would argue that the damage to the bile duct, while an
unfortunate outcome, does not "speak for itself" as negligence without further evidence of a specific negligent act or
omission by Dr. Nair. It could be a known, albeit rare, complication that occurred despite the exercise of reasonable
care.

**Judgment & Direction**
The defendants would argue for the dismissal of the compensation claim, asserting that Mrs. Deshmukh has failed to
establish a clear case of medical negligence. They would contend that the unfortunate outcome was a surgical
complication rather than a result of any breach of the standard of care by Dr. Nair or the hospital. They would also
emphasize that the claim for ₹25 lakhs is excessive and unsubstantiated without concrete proof of gross negligence and a
direct causal link that goes beyond inherent surgical risks.

**PDF Links:**
-   *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, 30-11-2021 (English): https://api.sci.gov.in/supremecourt/2010/4109/4109_2010_11_1501_31713_Judgement_30-Nov-2021.pdf
-   *V. Kishan Rao v. Nikhil Super Speciality Hospital*, 08-03-2010 (English): https://api.sci.gov.in/jonew/judis/36301.pdf
-   *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, 09-01-2019 (English): https://api.sci.gov.in/supremecourt/2016/27594/27594_2016_Judgement_09-Jan-2019.pdf
-   *Jacob Mathew v. State of Punjab*, 05-08-2005 (English): https://api.sci.gov.in/jonew/judis/27088.pdf

### NEWACTS CITATIONS:
Section 65B of the Indian Evidence Act, 1872, deals with the admissibility of electronic records, and its corresponding new provision is Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

### Old Provision: Section 65B of Indian Evidence Act, 1872

(1) Notwithstanding anything contained in this Act, any information contained in an electronic record which is printed
on a paper, stored, recorded or copied in optical or magnetic media produced by a computer (hereinafter referred to as
the computer output) shall be deemed to be also a document, if the conditions mentioned in this section are satisfied in
relation to the information and computer in question and shall be admissible in any proceedings, without further proof
or production of the original, as evidence of any contents of the original or of any fact stated therein of which direct
evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer during the period over which the computer was used regularly to store or process information for the purposes of any activities regularly carried on over that period by the person having lawful control over the use of the computer;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer in the ordinary course of the said activities.

(3) Where over any period, the function of storing or processing information for the purposes of any activities regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by computers, whether-
(a) by a combination of computers operating over that period; or
(b) by different computers operating in succession over that period; or
(c) by different combinations of computers operating in succession over that period; or
(d) in any other manner involving the successive operation over that period, in whatever order, of one or more computers and one or more combinations of computers.
all the computers used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer; and references in this section to a computer shall be construed accordingly.

(4) In any proceedings where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things, that is to say,
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer;
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate,
and purporting to be signed by a person occupying a responsible official position in relation to the operation of the relevant device or the management of the relevant activities (whichever is appropriate) shall be evidence of any matter stated in the certificate; and for the purposes of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the person stating it.

(5) For the purposes of this section,
(a) information shall be taken to be supplied to a computer if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) whether in the course of activities carried on by any official, information is supplied with a view to its being stored or processed for the purposes of those activities by a computer operated otherwise than in the course of those activities, that information, if duly supplied to that computer, shall be taken to be supplied to it in the course of those activities;
(c) a computer output shall be taken to have been produced by a computer whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment.

Explanation: For the purposes of this section any reference to information being derived from other information shall be a reference to its being derived therefrom by calculation, comparison or any other process.

### New Provision: Section 63 of Bharatiya Sakshya Adhiniyam, 2023

(1) Notwithstanding anything contained in this Adhiniyam, any information contained in an electronic record which is
printed on paper, stored, recorded or copied in optical or magnetic media or semiconductor memory which is produced by a
computer or any communication device or otherwise stored, recorded or copied in any electronic form (hereinafter
referred to as the computer output) shall be deemed to be also a document, if the conditions mentioned in this section
are satisfied in relation to the information and computer in question and shall be admissible in any proceedings,
without further proof or production of the original, as evidence or any contents of the original or of any fact stated
therein of which direct evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer or communication device during the period over which the computer or communication device was used regularly to create, store or process information for the purposes of any activity regularly carried on over that period by the person having lawful control over the use of the computer or communication device;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer or communication device in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer or communication device was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer or communication device in the ordinary course of the said activities.

(3) Where over any period, the function of creating, storing or processing information for the purposes of any activity regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by means of one or more computers or communication device, whether:
(a) in standalone mode; or
(b) on a computer system; or
(c) on a computer network; or
(d) on a computer resource enabling information creation or providing information processing and storage; or
(e) through an intermediary,
all the computers or communication devices used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer or communication device; and references in this section to a computer or communication device shall be construed accordingly.

(4) In any proceeding where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things shall be submitted along with the electronic record at each instance where it is being submitted for admission, namely:
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer or a communication device referred to in clauses (a) to (e) of sub-section (3);
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate, and purporting to be
signed by a person in charge of the computer or communication device or the management of the relevant activities
(whichever is appropriate) and an expert shall be evidence of any matter stated in the certificate; and for the purposes
of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the
person stating it in the certificate specified in the Schedule.

(5) For the purposes of this section,:
(a) information shall be taken to be supplied to a computer or communication device if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) a computer output shall be taken to have been produced by a computer or communication device whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment or by other electronic means as referred to in clauses (a) to (e) of sub-section (3).

### Key Differences

-   **Scope of Devices**: The new provision explicitly includes "communication device" alongside "computer" throughout the section, broadening the scope of electronic records covered. It also adds "semiconductor memory" as a storage medium.
-   **Computer Output Definition**: The new provision expands the definition of "computer output" to include information "otherwise stored, recorded or copied in any electronic form."
-   **Activities in Sub-section (2)(a)**: The new provision adds "create" to the activities for which the computer or communication device was used regularly, i.e., "to create, store or process information."
-   **Complex Systems in Sub-section (3)**: The new provision significantly updates the description of how functions are performed by multiple devices, replacing the older clauses (a) to (d) with more modern terms like "standalone mode," "computer system," "computer network," "computer resource enabling information creation or providing information processing and storage," and "through an intermediary."
-   **Certificate Requirements in Sub-section (4)**: The new provision specifies that the certificate must be submitted "at each instance where it is being submitted for admission" and adds that it should be signed by "an expert" in addition to the person in charge. It also refers to a "certificate specified in the Schedule."
-   **Sub-section (5) Changes**: The new provision removes clause (b) of the old sub-section (5) and modifies clause (c) to clause (b), expanding the means by which a computer output is produced to include "other electronic means as referred to in clauses (a) to (e) of sub-section (3)."
-   **Explanation**: The Explanation from the old provision is omitted in the new one.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

### SCI_JUDGMENT CITATIONS:
The Supreme Court has consistently emphasized the distinction between a mere breach of contract, which is a civil wrong, and the criminal offense of cheating under Section 420 of the Indian Penal Code (IPC). The crucial element that differentiates the two is the presence of a fraudulent or dishonest intention (mens rea) right from the inception of the transaction.

In *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*, (2022) 8 SCC 696, the Supreme Court reiterated that fraudulent or dishonest intention is a precondition to constitute the offense of cheating. The Court observed that allegations primarily alluding to a breach of terms of a consultancy agreement would essentially be in the nature of a civil dispute.

Similarly, in *Satishchandra Ratanlal Shah v. The State of Gujarat*, (2019) 9 SCC 148, the Supreme Court held that the distinction between a mere breach of contract and cheating depends upon the fraudulent inducement and mens rea. The Court emphasized that mere inability to return a loan amount cannot give rise to criminal prosecution for cheating unless fraudulent or dishonest intention is shown right at the beginning of the transaction.

I think this information will help you more to understand the case:

### Key Legal Issues
-   Whether the ingredients of the offenses of cheating (Section 415 and 420 IPC) and criminal breach of trust (Section 405 IPC) are made out in cases arising from business agreements.
-   The distinction between a mere breach of contract and the criminal offense of cheating.
-   The necessity of proving fraudulent or dishonest intention (mens rea) at the inception of the transaction for an offense under Section 420 IPC.

### Detailed Narrative

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The first respondent (Ramesh Ranganathan), a consultant neurosurgeon, was employed by BGS Apollo Hospital. Differences
arose, and his services were terminated. He filed a criminal complaint alleging misuse of authority, defamation, and
irregularities in billing, leading to cognizance being taken under various sections of the IPC, including Section 420.
The Additional Sessions Judge set aside the cognizance, but the High Court partially restored it for Sections 405 and
420. The appellant (M.N.G Bharateesh Reddy) challenged this before the Supreme Court.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The appellant (Satishchandra Ratanlal Shah) obtained a loan of Rs. 27,00,000/- from the respondent no. 2's (Dharshan
Fiscal Pvt. Ltd.) money lending company. The appellant failed to repay the loan, leading to a criminal complaint under
Sections 406, 409, 417, 420, 294(b), and 506(2) of the IPC. Simultaneously, a summary civil suit for recovery of the
loan amount was also filed and was pending. The High Court dismissed the appellant's petition to quash the charges,
observing a prima facie case of cheating under Section 420 IPC, but not criminal breach of trust under Section 406 IPC.
The appellant then approached the Supreme Court.

### Court Observations

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court, referring to *Hridaya Ranjan Prasad Verma v. State of Bihar*, (2000) 4 SCC 168, and *Dalip Kaur v.
Jagnar Singh*, (2009) 14 SCC 696, emphasized that fraudulent or dishonest intention is a precondition for the offense of
cheating. The Court noted that a mere breach of contract does not amount to cheating unless a fraudulent or dishonest
intention is shown at the very beginning of the transaction. In this case, the Court found that the allegations
primarily stemmed from the termination of services and improper billing, which at most alluded to a breach of the
consultancy agreement, a civil dispute. The Court concluded that the ingredients of Sections 415 and 420 IPC were not
made out.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court reiterated that the distinction between a mere breach of contract and cheating depends on fraudulent
inducement and mens rea. The Court observed that the appellant was in an economic crisis and had approached the
respondent for a loan. The mere inability to return the loan amount, coupled with the pending civil suit for recovery,
could not automatically lead to a criminal prosecution for cheating without proof of fraudulent or dishonest intention
at the inception of the transaction. The Court cautioned against criminalizing civil disputes, such as breaches of
contractual obligations, and held that the legislature intended to criminalize only those breaches accompanied by
fraudulent, dishonest, or deceptive inducements.

### Judgment & Direction

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court allowed the appeal, setting aside the High Court's judgment to the extent it found a prima facie case under Sections 405 and 420 IPC. The criminal proceedings against the appellant were quashed.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court allowed the appeal, setting aside the impugned order of the High Court. The application filed by the appellant under Section 482 of the Code of Criminal Procedure, 1973, was allowed, and the proceedings initiated based on the FIR were quashed. The Court also expunged certain observations made by the High Court that were deemed unnecessary and potentially prejudicial to the accused.

**PDF Links:**
-   *M.N.G Bharateesh Reddy v. Ramesh Ranganathan, Crl.A. No.-001273-001273 - 2022* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2019/34821/34821_2019_3_19_37392_Judgement_18-Aug-2022.pdf))
-   *Satishchandra Ratanlal Shah v. The State of Gujarat, Crl.A. No.-000009-000009 - 2019* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2018/17145/17145_2018_Judgement_03-Jan-2019.pdf))

### NEWACTS CITATIONS:
## DEFENSE ARGUMENTS AGAINST CHARGE UNDER SECTION 420 INDIAN PENAL CODE, 1860

The defense submits that the present case is fundamentally a civil dispute arising from a failed business agreement and does not constitute the offense of cheating under Section 420 of the Indian Penal Code, 1860 (corresponding to Section 318(4) of the Bharatiya Nyaya Sanhita, 2023). The essential ingredients for establishing criminal cheating, particularly the element of dishonest or fraudulent intention from the inception of the transaction, are conspicuously absent in the present matter.

### Absence of Dishonest Intention at Inception

1.  **Lack of *Mens Rea*:** For an offense under Section 420 of the Indian Penal Code, 1860 (or Section 318(4) BNS,
2023), it is imperative to prove that the accused had a dishonest or fraudulent intention at the very beginning of the
transaction, i.e., from the moment the promise or representation was made. A mere breach of contract or subsequent
failure to fulfill a promise, in the absence of such initial dishonest intention, does not automatically transform a
civil wrong into a criminal offense. The Supreme Court has consistently held that the distinction between a mere breach
of contract and the offense of cheating depends upon the intention of the accused at the time of inducement. If the
intention to cheat was not present at the inception, the offense of cheating cannot be said to have been committed.

1.  **Breach of Contract, Not Criminal Deception:** The allegations, even if taken at face value, primarily disclose a
case of breach of contract, which falls squarely within the domain of civil law. The remedies for such a breach lie in
civil courts, where damages for non-performance or specific performance of the contract can be sought. The mere fact
that a party has suffered a loss in a business transaction does not automatically warrant the initiation of criminal
proceedings. The Supreme Court has repeatedly cautioned against converting civil disputes into criminal cases.

2.  **Availability of Civil Remedies:** The complainant has adequate and effective remedies available under civil law, including filing a suit for recovery of money, specific performance, or damages for breach of contract. Resorting to criminal proceedings under Section 420 of the Indian Penal Code, 1860 (or Section 318(4) BNS, 2023), in such circumstances amounts to an abuse of the process of law, often with the ulterior motive of exerting pressure for recovery of money.

3.  **Absence of Inducement to Deliver Property by Deception:** Section 420 of the Indian Penal Code, 1860 (or Section
318(4) BNS, 2023), requires that the accused must have dishonestly induced the complainant to deliver any property or to
make, alter, or destroy the whole or any part of a valuable security. In the present case, any delivery of property or
financial transaction occurred within the framework of a commercial agreement, not as a result of any dishonest
inducement or fraudulent misrepresentation made at the inception by the client. The complainant willingly entered into
the business arrangement, presumably after due diligence and assessment of commercial risks.

### Judicial Precedents Distinguishing Civil and Criminal Liability

The Supreme Court of India has consistently drawn a clear distinction between a civil wrong and a criminal offense, particularly in cases involving allegations of cheating arising from contractual disputes:

-   In ***Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168***, the Supreme Court held that in order to
constitute an offence of cheating, the intention to cheat must be in existence at the time when the inducement was made.
If the intention was not dishonest at the time of making the promise but developed subsequently, it would not amount to
cheating. The Court observed that a mere breach of contract cannot give rise to criminal prosecution for cheating unless
fraudulent or dishonest intention is shown right at the beginning of the transaction.

-   In ***International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348***, the Supreme Court reiterated that for an offence under Section 420 of the Indian Penal Code, 1860, the element of deception and dishonest intention from the very beginning is crucial. The Court emphasized that a mere breach of contract or failure to pay money does not automatically lead to criminal prosecution for cheating.

-   In ***G. Sagar Suri v. State of U.P., (2000) 2 SCC 636***, the Supreme Court cautioned against converting purely civil disputes into criminal cases. The Court observed that it is not every breach of contract that would amount to the offence of cheating.

Therefore, based on the facts and the established legal principles, the defense submits that the allegations against the client do not fulfill the essential ingredients of criminal cheating under Section 420 of the Indian Penal Code, 1860 (corresponding to Section 318(4) of the Bharatiya Nyaya Sanhita, 2023), and the matter ought to be treated as a civil dispute.

### PDF Links
-   *Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168*: [https://api.sci.gov.in/jonew/judis/22759.pdf](https://api.sci.gov.in/jonew/judis/22759.pdf)
-   *International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348*: [https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf](https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf)
### Key Legal Issues
-   Whether the ingredients of the offenses of cheating (Section 415 and 420 IPC) and criminal breach of trust (Section 405 IPC) are made out in cases arising from business agreements.
-   The distinction between a mere breach of contract and the criminal offense of cheating.
-   The necessity of proving fraudulent or dishonest intention (mens rea) at the inception of the transaction for an offense under Section 420 IPC.
#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court allowed the appeal, setting aside the High Court's judgment to the extent it found a prima facie case under Sections 405 and 420 IPC. The criminal proceedings against the appellant were quashed.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court allowed the appeal, setting aside the impugned order of the High Court. The application filed by the appellant under Section 482 of the Code of Criminal Procedure, 1973, was allowed, and the proceedings initiated based on the FIR were quashed. The Court also expunged certain observations made by the High Court that were deemed unnecessary and potentially prejudicial to the accused.

**PDF Links:**
-   *M.N.G Bharateesh Reddy v. Ramesh Ranganathan, Crl.A. No.-001273-001273 - 2022* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2019/34821/34821_2019_3_19_37392_Judgement_18-Aug-2022.pdf))
-   *Satishchandra Ratanlal Shah v. The State of Gujarat, Crl.A. No.-000009-000009 - 2019* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2018/17145/17145_2018_Judgement_03-Jan-2019.pdf))

### NEWACTS CITATIONS:
Section 420 of the Indian Penal Code, 1860, deals with cheating and dishonestly inducing the delivery of property. This provision is now covered under Section 318(4) of the Bharatiya Nyaya Sanhita, 2023.

The text of the provision remains identical in both the Indian Penal Code, 1860, and the Bharatiya Nyaya Sanhita, 2023, with the only change being the section number and the name of the Act.

Regarding the user's query about "cheating fraudulent intention mens rea civil dispute business agreement," it is important to emphasize that Section 420 IPC (now Section 318(4) BNS) deals with criminal cheating. For an offense under this section, the prosecution must prove a dishonest or fraudulent intention (mens rea) on the part of the accused *at the very inception* of the transaction.

In the context of a civil dispute or a business agreement, a mere breach of contract or failure to fulfill a promise
does not automatically amount to criminal cheating. The distinction lies in the presence of a dishonest intention to
deceive from the outset. If the intention to deceive was not present at the time the property was induced to be
delivered, but rather arose later due to unforeseen circumstances or a subsequent inability to fulfill the promise, it
would typically fall under the purview of a civil dispute (breach of contract) rather than criminal cheating.

Courts have consistently held that for Section 420 IPC to apply, the fraudulent or dishonest intention must be established from the very beginning of the transaction. If the intention was honest at the time of entering into the agreement, but circumstances later led to a failure to perform, it would generally be a civil wrong.


</details>

Follow-up suggestions returned: `What evidence is needed for Priya?`, `Can Priya sue the store too?`, `What is the statute of limitations?`

---

### Turn 10 — Review the attached draft (PDF built from turn 1)

*Source section in `docs/test prompt.txt`: **Pdf***

#### 1. User prompt (verbatim)

```text
Review this draft and suggest changes, if any.
```

#### 2. Exact API request

```http
POST https://api.lawttorney.com/pyapi/chat HTTP/1.1
Content-Type: multipart/form-data
X-API-Key: <redacted>
Accept: text/event-stream
```

Form fields:

| Field | Value |
|-------|-------|
| `query` | *(the user prompt above, 46 chars, unmodified)* |
| `globalThreadId` | `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9` |
| `files` | `prod_turn1_draft.pdf` — 2,013 bytes, `application/pdf` |

Reproduce with:

```bash
curl -N -X POST https://api.lawttorney.com/pyapi/chat \
  -H 'X-API-Key: <key>' \
  -F 'globalThreadId=1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9' \
  -F 'files=@prod_turn1_draft.pdf;type=application/pdf' \
  -F 'query=<prompt above>'
```

#### 3. What the server made of it

| Signal | Value |
|--------|-------|
| HTTP status | `200` |
| Conversation turn | 10 |
| History turns loaded | 5 |
| Query rewritten by memory agent | False |
| Effective query after rewrite | *(unchanged)* |
| Agents planned | Document |
| Agents used | Document |
| Sources returned | 15 |
| Wall clock | 20.1s |
| First streamed token | —s |

File processing events:

- `t+0.414s` — Processing uploaded files...
- `t+0.417s` — Compressing prod_turn1_draft.pdf...
- `t+0.419s` — Extracting text from prod_turn1_draft.pdf...
- `t+0.424s` — prod_turn1_draft.pdf: 4 pages
- `t+0.426s` — Embedding prod_turn1_draft.pdf into vector store...
- `t+0.869s` — Processed 1 pdf
- `t+0.869s` — Processed 1 pdf

<details>
<summary>Sources cited back to the client (15)</summary>

| # | Type | Title | Agent | Score |
|---|------|-------|-------|-------|
| 1 | drafting | Format of Suit for Medical Negligence  FORMAT 2  | Drafting | None |
| 2 | scenario | AI-Generated Legal Analysis | Scenario | None |
| 3 | judgment | CENTRAL BUREAU OF INVESTIGATION vs VIJAY KUMAR GUPTA | Judgment | 259.55185 |
| 4 | judgment | Prem Chand vs State of Rajasthan | Judgment | 249.21309 |
| 5 | judgment | Surendra Agrawal vs Hemlata Patel | Judgment | 245.63292 |
| 6 | judgment | Rajendra Singh vs The State of Rajasthan | Judgment | 245.38173 |
| 7 | judgment | NILAY S/O ARVINDBHAI SHETH vs SHRI KAPIL KISHORBHAI JOSHI | Judgment | 235.97122 |
| 8 | sci_judgment | AJAY MITRA VS STATE OF M.P. . | SCI_Judgment | None |
| 9 | sci_judgment | M.N.G BHARATEESH REDDY VS RAMESH RANGANATHAN | SCI_Judgment | None |
| 10 | sci_judgment | SATISHCHANDRA RATANLAL SHAH VS THE STATE OF GUJARAT | SCI_Judgment | None |
| 11 | sci_judgment | INDER CHAND BAGRI VS JAGADISH PRASAD BAGRI | SCI_Judgment | None |
| 12 | sci_judgment | J. VEDHASINGH VS R.M. GOVINDAN | SCI_Judgment | None |
| 13 | newacts | The Indian Penal Code, 1860 - Section 420 | Newacts | None |
| 14 | newacts | The Indian Penal Code, 1860 - Section 419 | Newacts | None |
| 15 | newacts | The Indian Penal Code, 1860 - Section 421 | Newacts | None |

</details>

#### 4. Exact API response

<details>
<summary>Full response text (68,893 chars) — click to expand</summary>

**IN THE COURT OF THE CIVIL JUDGE, SENIOR DIVISION, PUNE**

**Civil Suit No. __________ / 2024**

**IN THE MATTER OF:**

[Plaintiff Name]
Age: 42 years
Occupation: [Occupation]
Residing at: [Plaintiff's Full Address], Pune.

.....Plaintiff

**vs**

1. XYZ Multispecialty Hospital
Through its Director/Principal Officer
Address: [Hospital's Full Address]

2. [Defendant Doctor Name]
Age: [Age]
Occupation: Surgeon
Address: C/o XYZ Multispecialty Hospital,
[Hospital's Full Address]

.....Defendants

## SUIT FOR DAMAGES ARISING FROM MEDICAL NEGLIGENCE

## Facts of the Case

The Plaintiff respectfully states as follows:

1. That the Plaintiff, [Plaintiff Name], is a 42-year-old resident of Pune. The Defendant No. 1 is a multispecialty hospital providing medical services to the public, and the Defendant No. 2 is a surgeon employed by or associated with the Defendant No. 1 hospital.

2. That the Plaintiff was suffering from a gallbladder ailment and, upon medical advice, decided to undergo surgery for the removal of the gallbladder (cholecystectomy). The Plaintiff, having reposed faith and trust in the reputation of Defendant No. 1 hospital and the professed skill of Defendant No. 2, placed herself under their care for the said surgical procedure.

3. That on or about 12th March 2024, the Plaintiff was admitted to the Defendant No. 1 hospital and underwent the gallbladder surgery, which was performed by the Defendant No. 2. The Plaintiff had paid all the necessary fees and charges as demanded by the Defendants for the said procedure and associated medical care.

4. It is submitted that during the course of the said surgery, the Defendant No. 2, acting with gross negligence and a lack of reasonable care and skill expected of a prudent surgeon, caused significant iatrogenic damage to the Plaintiff's bile duct. This injury was not an ordinary or expected complication of the procedure when performed with due care.

5. That following the surgery, instead of recovering, the Plaintiff began to experience severe and debilitating abdominal pain, jaundice, and other serious complications, which were indicative of a post-operative complication directly resulting from the negligently performed surgery. The Plaintiff's condition deteriorated rapidly, causing her immense physical suffering and mental distress.

6. That despite the Plaintiff's deteriorating condition and repeated complaints of severe pain, the Defendants failed to diagnose the bile duct injury in a timely manner and failed to provide appropriate corrective treatment. The post-operative care provided by the Defendants was inadequate, casual, and fell far below the accepted standard of medical practice.

7. That due to the Defendants' failure to manage her condition, the Plaintiff had no option but to seek urgent medical attention at another hospital. Upon admission to the second hospital, diagnostic tests confirmed the severe injury to her bile duct, which necessitated a complex and high-risk corrective surgery to repair the damage.

8. That as a direct consequence of the Defendants' negligence, the Plaintiff was forced to undergo a second major surgery, prolonging her hospitalisation and recovery period. The Plaintiff has incurred additional medical expenses amounting to approximately Rs. 3,50,000/- (Rupees Three Lakh Fifty Thousand only) for the corrective surgery, subsequent treatment, and hospitalisation.

9. That the Plaintiff has endured significant physical pain, trauma, and mental agony due to the botched surgery, the subsequent complications, and the ordeal of a second corrective procedure. The negligence of the Defendants has adversely affected her quality of life, her ability to perform daily activities, and has caused immense stress to her and her family.

10. That the Plaintiff, through her representatives, sent a legal notice dated [Date of Legal Notice] to the Defendants, bringing their negligence to their notice and seeking compensation for the damages suffered. However, the Defendants, by their refusal dated [Date of Refusal], have wrongfully and unjustly refused to acknowledge their negligence or offer any compensation, thereby leaving the Plaintiff with no other remedy but to approach this Hon'ble Court.

## Legal Grounds and Case Law

11. It is submitted that the present suit is based on the following legal grounds, amongst others, which are pleaded without prejudice to one another:

a. **Existence of Duty of Care:** A doctor-patient relationship existed between the Plaintiff and the Defendants, which commenced when the Plaintiff entrusted her care to them for the gallbladder surgery. By undertaking the Plaintiff's treatment, the Defendants owed her a duty of care to act with the skill and diligence expected of a reasonably competent medical professional in their respective fields.

b. **Breach of Duty of Care:** The Defendants breached this duty of care through their negligent acts and omissions. The
act of causing damage to the Plaintiff's bile duct during a standard cholecystectomy is a clear deviation from the
accepted standards of surgical practice. This constitutes a patent failure to exercise the ordinary skill of a surgeon.
The Supreme Court in *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*, held that a doctor has
a duty to bring to his task a reasonable degree of skill and knowledge and must exercise a reasonable degree of care.
The Defendants have failed on both counts.

c. **Causation and Damage:** The breach of duty by the Defendants is the direct and proximate cause of the injuries and damages suffered by the Plaintiff. The bile duct injury, the subsequent severe pain, the need for corrective surgery, the additional financial burden of Rs. 3,50,000/-, and the immense physical and mental suffering are all direct consequences of the Defendants' negligence.

d. **Doctrine of *Res Ipsa Loquitur***: The facts of this case attract the doctrine of *res ipsa loquitur* (the thing speaks for itself). An injury to the bile duct is not an event that ordinarily occurs during a gallbladder surgery in the absence of negligence. The instrumentality (surgical equipment) was in the exclusive control of Defendant No. 2, and the Plaintiff was unconscious. Therefore, the burden of proof shifts to the Defendants to demonstrate that they were not negligent.

e. **Vicarious Liability of the Hospital:** The Defendant No. 1 hospital is vicariously liable for the negligent acts of
Defendant No. 2, who was acting in the course of his employment or engagement with the hospital. The hospital has a
non-delegable duty to provide safe and adequate medical care to its patients. The Supreme Court in *Savita Garg v.
Director, National Heart Institute, (2004) 8 SCC 56*, affirmed the principle of vicarious liability of hospitals for the
negligence of their doctors.

f. **Standard of Care as per Judicial Precedent:** The standard of care expected from a medical professional has been
well-established by the Hon'ble Supreme Court. In *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*, the Court, while
dealing with negligence, observed that a professional would be held liable for negligence if they did not possess the
requisite skill that they professed to have, or if they failed to exercise, with reasonable competence, the skill which
they did possess. The actions of Defendant No. 2 fall squarely within this definition of negligence.

g. **Medical Services as a "Service":** The Hon'ble Supreme Court in *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*, held that medical services are a "service" and that medical practitioners and hospitals are liable for deficiency in service. Although the said judgment was in the context of the Consumer Protection Act, the underlying principles regarding the duty of care and liability for negligence are equally applicable to a civil suit for damages in tort.

h. **Failure in Post-Operative Care:** The Defendants were also negligent in their post-operative management of the Plaintiff. Their failure to promptly diagnose the bile duct injury despite clear symptoms of complications amounts to a separate and distinct act of negligence, which exacerbated the Plaintiff's suffering and the extent of the damage.

i. **Entitlement to Damages:** The Plaintiff is entitled to be compensated for the damages suffered. These damages include:
i. **Special Damages:** The actual pecuniary loss suffered, including the medical expenses of Rs. 3,50,000/- for the corrective surgery and associated treatment.
ii. **General Damages:** Compensation for the physical pain, suffering, mental agony, trauma, loss of amenities, and the adverse impact on the Plaintiff's quality of life.
iii. **Future Damages:** Compensation for any anticipated future medical treatment, care, and expenses that may arise as a consequence of the injury.

j. **Quantum of Damages:** The Plaintiff has claimed a total compensation of Rs. 25,00,000/-. This amount is fair, just, and reasonable considering the severity of the negligence, the extent of the physical and mental suffering endured, the financial losses incurred, and the potential for future health complications.

k. **Jurisdiction:** This Hon'ble Court has the territorial jurisdiction to entertain and try this suit as the cause of action arose entirely within the jurisdiction of this Court, where the Defendants reside and carry on their profession and where the negligent surgery was performed. This Hon'ble Court also has the requisite pecuniary jurisdiction to adjudicate upon the matter.

l. **Limitation:** The cause of action arose on 12th March 2024, when the negligent surgery was performed and the injury was inflicted, and the cause of action is continuing. The suit, being filed today, is well within the period of limitation prescribed under the Limitation Act, 1963.

## Damages Claimed

12. That as a direct and proximate result of the gross negligence and breach of duty of care by the Defendants, the Plaintiff has suffered immense physical, mental, and financial harm. The Plaintiff is therefore entitled to claim damages from the Defendants, jointly and severally. The Plaintiff quantifies her claim for a total sum of Rs. 25,00,000/- (Rupees Twenty-Five Lakh only), the particulars of which are as follows:

| Sr. No. | Particulars | Amount (in Rs.) |
---
| (a) | **Special Damages:** Expenses incurred for corrective surgery, subsequent hospitalisation, medicines, diagnostic tests, and post-operative care at another hospital. | 3,50,000/- |
|---|---|---|
| (b) | **General Damages:** For immense physical pain, suffering, mental agony, shock, trauma, and distress caused by the botched surgery and the subsequent medical ordeal. | 18,00,000/- |
| (c) | **Future Damages:** For anticipated costs of future medical treatment, regular check-ups, and care that may be required as a long-term consequence of the bile duct injury. | 3,50,000/- |
| | **Total Claim** | **25,00,000/-** |

## Prayer

In light of the facts and circumstances stated above, the Plaintiff most respectfully prays that this Hon'ble Court be pleased to:

a) Pass a decree in favour of the Plaintiff and against the Defendants, jointly and severally, for a sum of Rs.
25,00,000/- (Rupees Twenty-Five Lakh only) towards damages, comprising of (i) Rs. 3,50,000/- towards Special Damages,
(ii) Rs. 18,00,000/- towards General Damages, and (iii) Rs. 3,50,000/- towards Future Damages; along with pre-suit
interest from the date of the legal notice, and pendente lite and future interest at the rate of 18% per annum from the
date of filing of this suit until the date of full and final realization, in accordance with Section 34 of the Code of
Civil Procedure, 1908;

b) Award the costs of this suit to the Plaintiff from the Defendants;

c) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, [Plaintiff Name], aged 42 years, Occupation: [Occupation], residing at [Plaintiff's Full Address], Pune, the Plaintiff abovenamed, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 12 are true and correct to the best of my knowledge, belief, and information, and I believe the same to be true.

Signed and verified at Pune on this ______ day of ____________, 2024.

Sd/-
([Plaintiff Name])
Plaintiff

## List of Documents

The Plaintiff relies upon the following documents, copies of which are filed herewith:
1. Medical records, bills, and discharge summary from Defendant No. 1 hospital.
2. Medical records, bills, and discharge summary from the second hospital where the corrective surgery was performed.
3. All investigation and diagnostic reports.
4. Receipts and bills for medicines and other medical expenses.
5. Office copy of the legal notice dated [Date of Legal Notice] sent to the Defendants.
6. Postal receipts and acknowledgements for the legal notice.
7. Reply from the Defendants dated [Date of Refusal], if any.

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
## DEFENSE ARGUMENTS AGAINST CHARGE UNDER SECTION 420 INDIAN PENAL CODE, 1860

The defense submits that the present case is fundamentally a civil dispute arising from a failed business agreement and does not constitute the offense of cheating under Section 420 of the Indian Penal Code, 1860. The essential ingredients for establishing criminal cheating, particularly the element of dishonest or fraudulent intention from the inception of the transaction, are conspicuously absent in the present matter.

### Absence of Dishonest Intention at Inception

1.  **Lack of *Mens Rea* at Inception:** For an offense under Section 420 of the Indian Penal Code, 1860, it is
imperative to prove that the accused had a dishonest or fraudulent intention at the very beginning of the transaction,
i.e., from the moment the promise or representation was made. A mere breach of contract or subsequent failure to fulfill
a promise, in the absence of such initial dishonest intention, does not automatically transform a civil wrong into a
criminal offense. The Supreme Court has consistently held that the distinction between a mere breach of contract and the
offense of cheating depends upon the intention of the accused at the time of inducement. If the intention to cheat was
not present at the inception, the offense of cheating cannot be said to have been committed.

2.  **Subsequent Failure Not Equivalent to Prior Deception:** The alleged inability or failure of the client to fulfill
the terms of the business agreement arose from unforeseen circumstances, commercial exigencies, or genuine business
difficulties, which developed *subsequent* to the formation of the agreement. Such a subsequent failure cannot be
retrospectively construed as a dishonest intention existing at the time the agreement was entered into. The Supreme
Court has clarified that a mere failure to keep a promise cannot be presumed to be an act of cheating.

3.  **Bona Fide Business Transaction:** The transaction between the parties was a bona fide business arrangement entered into with a genuine intention to execute the agreement and derive mutual commercial benefit. All actions taken by the client were in furtherance of the business objective, and any difficulties encountered were typical risks associated with commercial ventures. The client made genuine efforts to perform their obligations under the agreement.

### Nature of Dispute as Civil Liability

1.  **Breach of Contract, Not Criminal Deception:** The allegations, even if taken at face value, primarily disclose a
case of breach of contract, which falls squarely within the domain of civil law. The remedies for such a breach lie in
civil courts, where damages for non-performance or specific performance of the contract can be sought. The mere fact
that a party has suffered a loss in a business transaction does not automatically warrant the initiation of criminal
proceedings. The Supreme Court has repeatedly cautioned against converting civil disputes into criminal cases.

2.  **Availability of Civil Remedies:** The complainant has adequate and effective remedies available under civil law, including filing a suit for recovery of money, specific performance, or damages for breach of contract. Resorting to criminal proceedings under Section 420 of the Indian Penal Code, 1860, in such circumstances amounts to an abuse of the process of law, often with the ulterior motive of exerting pressure for recovery of money.

3.  **Absence of Inducement to Deliver Property by Deception:** Section 420 of the Indian Penal Code, 1860, requires
that the accused must have dishonestly induced the complainant to deliver any property or to make, alter, or destroy the
whole or any part of a valuable security. In the present case, any delivery of property or financial transaction
occurred within the framework of a commercial agreement, not as a result of any dishonest inducement or fraudulent
misrepresentation made at the inception by the client. The complainant willingly entered into the business arrangement,
presumably after due diligence and assessment of commercial risks.

### Judicial Precedents Distinguishing Civil and Criminal Liability

The Supreme Court of India has consistently drawn a clear distinction between a civil wrong and a criminal offense, particularly in cases involving allegations of cheating arising from contractual disputes:

-   In *Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168*, the Supreme Court held that in order to
constitute an offense of cheating, the intention to cheat must be in existence at the time when the inducement was made.
If the intention was not dishonest at the time of making the promise but developed subsequently, it would not amount to
cheating. The Court observed that a mere breach of contract cannot give rise to criminal prosecution for cheating unless
fraudulent or dishonest intention is shown right at the beginning of the transaction.

-   In *International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348*, the Supreme Court reiterated that for an offense under Section 420 of the Indian Penal Code, 1860, the element of deception and dishonest intention from the very beginning is crucial. The Court emphasized that a mere breach of contract or failure to pay money does not automatically lead to criminal prosecution for cheating.

-   In *G. Sagar Suri v. State of U.P., (2000) 2 SCC 636*, the Supreme Court cautioned against converting purely civil disputes into criminal cases. The Court observed that it is not every breach of contract that would amount to the offense of cheating.

Therefore, based on the facts and the established legal principles, the defense submits that the allegations against the client do not fulfill the essential ingredients of criminal cheating under Section 420 of the Indian Penal Code, 1860, and the matter ought to be treated as a civil dispute.

### PDF Links
-   *Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168*: [https://api.sci.gov.in/jonew/judis/22759.pdf](https://api.sci.gov.in/jonew/judis/22759.pdf)
-   *International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348*: [https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf](https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf)

### NEWACTS CITATIONS:
Section 420 of the Indian Penal Code, 1860, which deals with cheating and dishonestly inducing the delivery of property, is now covered under Section 318(4) of the Bharatiya Nyaya Sanhita, 2023.

### Old Provision: Section 420 of The Indian Penal Code, 1860

Whoever cheats and thereby dishonestly induces the person deceived to deliver any property to any person, or to make, alter or destroy the whole or any part of a valuable security, or anything which is signed or sealed, and which is capable of being converted into a valuable security, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

### New Provision: Section 318(4) of Bharatiya Nyaya Sanhita, 2023

Whoever cheats and thereby dishonestly induces the person deceived to deliver any property to any person, or to make, alter or destroy the whole or any part of a valuable security, or anything which is signed or sealed, and which is capable of being converted into a valuable security, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

### Key Differences

The text of the provision remains identical in both the Indian Penal Code, 1860, and the Bharatiya Nyaya Sanhita, 2023,
with the only change being the section number and the name of the Act. For an offense under this section, the
prosecution must prove a dishonest or fraudulent intention (mens rea) on the part of the accused at the very inception
of the transaction. In the context of a civil dispute or a business agreement, a mere breach of contract or failure to
fulfill a promise does not automatically amount to criminal cheating. The distinction lies in the presence of a
dishonest intention to deceive from the outset. If the intention to deceive was not present at the time the property was
induced to be delivered, but rather arose later due to unforeseen circumstances or a subsequent inability to fulfill the
promise, it would typically fall under the purview of a civil dispute (breach of contract) rather than criminal
cheating.

### JUDGMENT CITATIONS:
## वादी (श्रीमती अंजली देशमुख) यांच्या वतीने युक्तिवाद

1.  **कर्तव्याचे अस्तित्व आणि त्याचे उल्लंघन:** प्रतिवादी, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर, यांनी
श्रीमती अंजली देशमुख यांच्या प्रति अत्यंत काळजी घेण्याचे कर्तव्य बजावले. हे कर्तव्य श्रीमती देशमुख यांनी प्रतिवादी
रुग्णालयात प्रतिवादी क्रमांक 2 च्या देखरेखीखाली वैद्यकीय उपचार आणि शस्त्रक्रिया करून घेण्यास सुरुवात केल्यावर उद्भवले.
अपेक्षित काळजीचा दर्जा हा त्यांच्या संबंधित क्षेत्रातील वाजवी सक्षम वैद्यकीय व्यावसायिकाचा असतो. पित्तवाहिनीला नुकसान
पोहोचवणे, जी एक नियमित पित्तशयाच्या शस्त्रक्रिया (कोलेसिस्टेक्टॉमी) आहे, हे या कर्तव्याचे स्पष्ट उल्लंघन आहे. अशा
प्रकारची दुखापत, विशेषतः मानक प्रक्रियेत, वाजवी काळजी आणि कौशल्याच्या अभावाची सूचक आहे, जी स्वीकृत वैद्यकीय मानकांपेक्षा
कमी आहे. *डॉ. लक्ष्मण बाळकृष्ण जोशी विरुद्ध डॉ. त्रिंबक बापू गोडबोले, AIR 1969 SC 128* या प्रकरणात सर्वोच्च न्यायालयाने
असे प्रतिपादन केले आहे की, डॉक्टरांचे कर्तव्य आहे की त्यांनी आपल्या कामात वाजवी कौशल्य आणि ज्ञान वापरावे आणि वाजवी काळजी
घ्यावी.

2.  **कारणीभाव आणि थेट दुखापत:** तीव्र पोटदुखी, त्यानंतरच्या गुंतागुंत आणि दुसऱ्या दुरुस्त शस्त्रक्रियेची आवश्यकता हे सर्व डॉ. राकेश नायर यांनी केलेल्या सुरुवातीच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला झालेल्या निष्काळजीपणामुळे थेट आणि जवळचे परिणाम आहेत. शस्त्रक्रियेनंतर वादीची बिघडलेली प्रकृती, ज्यामुळे निदान आणि दुरुस्तीसाठी दुसऱ्या रुग्णालयात दाखल व्हावे लागले, हे दुखापतीचा प्रतिवादींच्या कृतीशी स्पष्ट संबंध दर्शवते.

3.  ***रेस इप्सा लोक्युटर* (Res Ipsa Loquitur) सिद्धांताचा वापर:** या प्रकरणातील तथ्ये *रेस इप्सा लोक्युटर* (गोष्ट
स्वतःच बोलते) या सिद्धांताला जोरदारपणे लागू होतात. पित्तशयाच्या शस्त्रक्रियेदरम्यान पित्तवाहिनीला होणारे नुकसान हे
निष्काळजीपणाच्या अनुपस्थितीत सामान्यतः घडणारी घटना नाही. शस्त्रक्रियेची प्रक्रिया आणि वापरलेली उपकरणे प्रतिवादी क्रमांक
2 च्या अनन्य नियंत्रणाखाली होती आणि वादी बेशुद्ध होती. म्हणून, हा सिद्धतेचा भार प्रतिवादींवर येतो की त्यांनी त्यांच्या
बाजूने कोणताही निष्काळजीपणा न करता असे नुकसान कसे झाले हे स्पष्ट करावे.

4.  **रुग्णालयाची प्रतिनिधी जबाबदारी:** प्रतिवादी क्रमांक 1, एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल, त्यांचे उपस्थित सर्जन,
डॉ. राकेश नायर यांच्या निष्काळजीपणाच्या कृत्यांसाठी आणि चुकांसाठी प्रतिनिधी जबाबदार आहे, कारण ते रुग्णालयाच्या नोकरीत
किंवा व्यावसायिक संबंधात काम करत होते. रुग्णालयाचे आपल्या रुग्णांना सुरक्षित आणि सक्षम वैद्यकीय सेवा प्रदान करण्याचे
कर्तव्य आहे. *सविता गर्ग विरुद्ध संचालक, नॅशनल हार्ट इन्स्टिट्यूट, (2004) 8 SCC 56* या प्रकरणात सर्वोच्च न्यायालयाने
डॉक्टरांच्या निष्काळजीपणासाठी रुग्णालयांच्या प्रतिनिधी जबाबदारीच्या सिद्धांताला दुजोरा दिला आहे.

5.  **अपुरे शस्त्रक्रियेनंतरचे उपचार आणि निदान:** वादीच्या तीव्र आणि सततच्या शस्त्रक्रियेनंतरच्या लक्षणांवरूनही प्रतिवादींनी पित्तवाहिनीच्या दुखापतीचे त्वरित निदान करण्यात केलेली अक्षमता काळजीच्या मानकातील त्रुटी दर्शवते. निदानातील या विलंबाने वादीचे दुःख वाढवले आणि अधिक जटिल दुरुस्त प्रक्रियेची आवश्यकता निर्माण केली, जी निष्काळजीपणाची अतिरिक्त कृती ठरते.

6.  **व्यापक नुकसानभरपाईचा हक्क:** वादीला ₹3.5 लाख रुपयांच्या दुरुस्त शस्त्रक्रिया आणि संबंधित उपचारांसाठी झालेल्या
प्रत्यक्ष वैद्यकीय खर्चासह महत्त्वपूर्ण आणि मोजता येण्याजोग्या नुकसानीचा सामना करावा लागला आहे. आर्थिक
नुकसानीव्यतिरिक्त, प्रतिवादींच्या निष्काळजीपणामुळे तिला प्रचंड शारीरिक वेदना, दुःख, मानसिक यातना आणि जीवनाच्या गुणवत्तेत
घट झाली आहे. तिला अपेक्षित भविष्यातील वैद्यकीय उपचार आणि काळजीसाठी देखील नुकसानभरपाई मिळण्याचा हक्क आहे. *इंडियन मेडिकल
असोसिएशन विरुद्ध व्ही.पी. शांता, (1995) 6 SCC 651* या प्रकरणात सर्वोच्च न्यायालयाने वैद्यकीय सेवांना "सेवा" म्हणून
मान्यता दिली आणि वैद्यकीय व्यावसायिक आणि रुग्णालये सेवेतील त्रुटींसाठी जबाबदार असल्याचे म्हटले आहे, ज्यात
निष्काळजीपणामुळे होणारे नुकसान समाविष्ट आहे. एकूण ₹25 लाख रुपयांची मागणी ही या नुकसानीचे न्याय्य आणि वाजवी मूल्यांकन
आहे.

## प्रतिवादी (एक्सवायझेड मल्टीस्पेशालिटी हॉस्पिटल आणि डॉ. राकेश नायर) यांच्या वतीने युक्तिवाद

1.  **काळजीच्या मानकांचे पालन:** डॉ. राकेश नायर, प्रतिवादी क्रमांक 2, यांनी पित्तशयाच्या शस्त्रक्रिया करताना वाजवी सक्षम सर्जनकडून अपेक्षित वाजवी काळजी आणि कौशल्य वापरले. ही प्रक्रिया स्थापित वैद्यकीय प्रोटोकॉलनुसार पार पाडण्यात आली. वैद्यकीय निष्काळजीपणाच्या प्रकरणांमध्ये डॉक्टरांकडून सर्वोच्च कौशल्याची अपेक्षा केली जात नाही, तर वाजवी काळजी आणि कौशल्याची अपेक्षा केली जाते, जसे की *जेकब मॅथ्यू विरुद्ध पंजाब राज्य, (2005) 6 SCC 1* या प्रकरणात म्हटले आहे.

2.  **शस्त्रक्रियेतील अंतर्भूत गुंतागुंत:** पित्तवाहिनीला होणारे नुकसान, जरी दुर्दैवी असले तरी, कोलेसिस्टेक्टॉमीशी संबंधित एक ओळखला गेलेला आणि अंतर्भूत धोका किंवा गुंतागुंत आहे, जरी ती योग्य काळजी आणि कौशल्याने केली गेली असली तरी. कोणताही सर्जन गुंतागुंत-मुक्त परिणामाची हमी देऊ शकत नाही. केवळ गुंतागुंत झाल्याने आपोआप वैद्यकीय निष्काळजीपणा ठरत नाही. वादीला शस्त्रक्रियेशी संबंधित संभाव्य धोक्यांची माहिती दिली गेली असावी आणि तिची संमती घेण्यात आली असावी.

3.  **निर्णयातील चूक विरुद्ध निष्काळजीपणा:** जरी प्रतिकूल परिणाम झाला असला तरी, तो फारतर निर्णयातील चूक होती, जी कारवाई करण्यायोग्य निष्काळजीपणा ठरत नाही. वैद्यकीय निष्काळजीपणासाठी उच्च मर्यादा आवश्यक आहे, ज्यात सामान्य विवेकबुद्धीचा अभाव किंवा घोर निष्काळजीपणा दिसून येतो. प्रतिवादी क्रमांक 2 ने कोणतीही बेपर्वाई किंवा रुग्णाच्या हिताकडे दुर्लक्ष करण्याची कृती केली नाही.

4.  **वादीवर सिद्धतेचा भार:** वैद्यकीय निष्काळजीपणा सिद्ध करण्याचा भार पूर्णपणे वादीवर आहे. वादीने तज्ञ वैद्यकीय पुराव्याद्वारे हे निश्चितपणे दाखवून दिले पाहिजे की प्रतिवादींनी स्वीकृत काळजीच्या मानकापासून विचलन केले आणि या विचलनमुळेच कथित दुखापत झाली. केवळ आरोप किंवा प्रतिकूल परिणाम निष्काळजीपणा सिद्ध करण्यासाठी पुरेसे नाहीत.

5.  **रुग्णालयाची मर्यादित जबाबदारी:** प्रतिवादी क्रमांक 1, रुग्णालयाने आवश्यक पायाभूत सुविधा, उपकरणे आणि सहाय्यक कर्मचारी प्रदान केले. रुग्णालयाला एका स्वतंत्र सर्जनच्या वैयक्तिक व्यावसायिक निर्णयासाठी आणि कृतींसाठी पूर्णपणे जबाबदार धरले जाऊ शकत नाही, विशेषतः जर ते क्रेडेंशियल्स तपासण्यात आणि सुरक्षित वातावरण प्रदान करण्यात योग्य परिश्रम दाखवू शकले.

6.  **नुकसानभरपाईच्या दाव्याची अतिशयोक्ती:** वादीने मागणी केलेली नुकसानभरपाईची रक्कम, विशेषतः मानसिक यातना आणि भविष्यातील उपचार खर्चासाठी, अंदाजित आणि जास्त आहे. वादीने तिच्या दाव्याच्या प्रत्येक घटकाला पुष्टी देण्यासाठी ठोस पुरावे सादर करणे आवश्यक आहे. ₹3.5 लाख रुपयांचे वैद्यकीय खर्च झाले असले तरी, ₹21.5 लाख रुपयांच्या गैर-आर्थिक आणि भविष्यातील नुकसानीच्या दाव्यासाठी कठोर पुरावा आवश्यक आहे आणि नुकसानभरपाईच्या स्थापित तत्त्वांवर आधारित न्यायालयीन मूल्यांकनाच्या अधीन आहे.

7.  **वेळेवर शस्त्रक्रियेनंतरचे व्यवस्थापन:** प्रतिवादींनी सादर केलेल्या लक्षणांवर आधारित योग्य शस्त्रक्रियेनंतरची काळजी प्रदान केली. निदानातील कोणताही विलंब, जर असेल तर, गुंतागुंतीच्या स्वरूपामुळे होता आणि तो हेतुपुरस्सर किंवा निष्काळजीपणाचा अभाव नव्हता. प्रचलित वैद्यकीय ज्ञान आणि संसाधनांच्या मर्यादेत वादीची स्थिती व्यवस्थापित करण्यासाठी सर्व वाजवी प्रयत्न करण्यात आले.

## PDF Links
-   *Dr. Laxman Balkrishna Joshi v. Dr. Trimbak Bapu Godbole, AIR 1969 SC 128*: [https://api.sci.gov.in/jonew/judis/1674.pdf](https://api.sci.gov.in/jonew/judis/1674.pdf)
-   *Savita Garg v. Director, National Heart Institute, (2004) 8 SCC 56*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)
-   *Indian Medical Association v. V.P. Shantha, (1995) 6 SCC 651*: [https://api.sci.gov.in/jonew/judis/36301.pdf](https://api.sci.gov.in/jonew/judis/36301.pdf)
-   *Jacob Mathew v. State of Punjab, (2005) 6 SCC 1*: [https://api.sci.gov.in/jonew/judis/27088.pdf](https://api.sci.gov.in/jonew/judis/27088.pdf)

### JUDGMENT CITATIONS:
The user has requested arguments on behalf of both the plaintiff and the defendant in a medical negligence case. However, as a Legal AI Assistant, I am strictly bound by the rule to provide answers only from the supplied context, which is stated to contain Indian court judgments. No such judgment context has been provided for this query.

Therefore, I am unable to provide the specific arguments as requested, as doing so would require using external knowledge or fabricating information, which is contrary to my instructions.

I think this information will help you more to understand the case:

### Key Legal Issues
Based on general principles of medical negligence in India, the key legal issues in such a case would typically include:
-   Whether the attending surgeon, Dr. Rakesh Nair, owed a duty of care to Mrs. Anjali Deshmukh.
-   Whether there was a breach of this duty of care by Dr. Rakesh Nair during the gallbladder surgery.
-   Whether the alleged breach of duty directly caused the damage to Mrs. Deshmukh's bile duct and subsequent suffering and expenses (causation).
-   Whether the principle of *res ipsa loquitur* is applicable, shifting the burden of proof to the defendants.
-   The quantum of compensation for medical expenses, mental harassment, pain and suffering, and future treatment costs.
-   Whether XYZ Multispecialty Hospital is vicariously liable for the actions of Dr. Rakesh Nair.

### Detailed Narrative
Mrs. Anjali Deshmukh, aged 42, underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024. During
the procedure, it is alleged that due to the negligence of Dr. Rakesh Nair, her bile duct was damaged. Following the
surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating a second admission for corrective surgery at a
different hospital. This corrective surgery and associated treatment led to additional medical expenses of ₹3.5 lakhs.
Furthermore, Mrs. Deshmukh claims to have suffered immense mental agony and physical pain. Despite repeated attempts to
engage with the hospital and Dr. Nair, they have reportedly refused to acknowledge any error or offer compensation. Mrs.
Deshmukh now seeks a total compensation of ₹25 lakhs, covering her medical expenses, mental harassment, pain and
suffering, and anticipated future treatment costs.

### As per the Court (General Principles of Medical Negligence in India)
In India, medical negligence cases are primarily governed by the law of torts, the Consumer Protection Act, 2019, and
certain provisions of the Indian Penal Code, 1860, although criminal liability for doctors is typically reserved for
cases of gross negligence. To establish medical negligence, the plaintiff generally needs to prove three elements: (i) a
legal duty of care owed by the medical professional to the patient, (ii) a breach of that duty, and (iii) that the
breach directly resulted in injury or damage to the patient. The standard of care expected from a medical professional
is that of a reasonably competent practitioner in that field, exercising reasonable skill and care. Mere unsuccessful
treatment or an unfavorable surgical outcome does not automatically establish negligence.

The doctrine of *res ipsa loquitur*, meaning "the thing speaks for itself," may be invoked in certain medical negligence
cases. This doctrine allows a presumption of negligence to arise, shifting the burden of proof to the healthcare
provider to demonstrate that no negligence occurred, provided the incident would not ordinarily happen without
negligence, and the accident was under the exclusive control of the defendant, with no contributory negligence from the
patient. However, courts apply *res ipsa loquitur* with caution in medical negligence cases, and it is not of universal
application. Even if *res ipsa loquitur* is established, the plaintiff must still prove all other elements of medical
negligence, including duty of care, breach of duty, causation, and damages.

Compensation in medical negligence cases aims to restore the victim to the position they would have been in had the negligence not occurred, covering financial losses, future medical expenses, and pain and suffering. The calculation of compensation can vary, but it generally includes medical costs, future treatment costs, and compensation for mental and physical pain.

### Additional Observations
Defenses in medical negligence cases often involve demonstrating that the standard of care was met, that the injury could have resulted from other factors, or that the patient contributed to the injury. Written evidence of consent to inherent risks can also be a defense. Meticulous clinical documentation is crucial for doctors in their defense. It is also important to note that the limitation period for filing medical negligence cases is generally two years.

### SCI_JUDGMENT CITATIONS:
Here are arguments on behalf of both the plaintiff (Mrs. Anjali Deshmukh) and the defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital), drawing upon principles of medical negligence established by the Supreme Court of India.

### Arguments for the Plaintiff (Mrs. Anjali Deshmukh)

**Key Legal Issues**
-   Breach of duty of care by the medical professional.
-   Application of the doctrine of *res ipsa loquitur* (the thing speaks for itself).
-   Causation of injury and suffering due to negligence.
-   Entitlement to compensation for medical expenses, pain, suffering, and mental agony.

**Detailed Narrative**
Mrs. Anjali Deshmukh underwent gallbladder surgery at XYZ Multispecialty Hospital on March 12, 2024, under the care of
Dr. Rakesh Nair. During this procedure, her bile duct was damaged. This damage was not a known or accepted complication
that could occur despite due care. Following the surgery, Mrs. Deshmukh experienced severe abdominal pain, necessitating
a second admission for corrective surgery at a different hospital. This unforeseen complication and subsequent
corrective procedure resulted in additional medical expenses of ₹3.5 lakhs, along with immense physical pain and mental
agony. The hospital and Dr. Nair have failed to acknowledge their error or offer compensation, compelling Mrs. Deshmukh
to seek ₹25 lakhs for her losses.

**Court Observations**
The plaintiff's case rests on establishing medical negligence, which involves demonstrating a breach of the duty of care
owed by the medical professional. The Supreme Court in *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, and *Kusum
Sharma v. Batra Hospital & Med. Research Centre*, (2010) 3 SCC 480, established that a medical professional is expected
to bring a reasonable degree of skill and knowledge and must exercise a reasonable degree of care. Negligence occurs
when the professional's conduct falls below the standards of a reasonably competent practitioner in their field.

In this scenario, the damage to the bile duct during gallbladder surgery is an event that, in the ordinary course of
things, would not happen if proper care and skill were exercised. This situation strongly invokes the doctrine of *res
ipsa loquitur*, meaning "the thing speaks for itself." As held in *V. Kishan Rao v. Nikhil Super Speciality Hospital*,
(2010) 5 SCC 513, and reiterated in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC OnLine SC
1098, *res ipsa loquitur* is an evidential principle intended to assist a claimant who is unable to adduce specific
evidence as to how the accident occurred. If the accident is such that it does not happen in the ordinary course of
things with proper care, and the thing causing the injury was under the management of the defendant, then negligence can
be inferred in the absence of a reasonable explanation from the defendant. The damage to the bile duct during surgery, a
procedure directly controlled by Dr. Nair, suggests a prima facie case of negligence.

Furthermore, the subsequent severe pain, need for corrective surgery, and additional medical expenses directly resulted from the initial surgical error. This establishes the causal link between the alleged negligence and the damages suffered by Mrs. Deshmukh. The refusal of the hospital and doctor to acknowledge their mistake or compensate her further underscores the need for judicial intervention.

**Judgment & Direction**
The plaintiff seeks compensation for medical expenses, mental harassment, pain and suffering, and future treatment
costs. The Supreme Court in *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, (2019) 7 SCC 401,
recognized that compensation for medical negligence should be assessed considering various factors, including the loss
of quality of life and the economic equivalent of suffering. The additional medical expenses of ₹3.5 lakhs are a direct
and quantifiable loss. The mental agony and physical pain, though not easily quantifiable, are significant components of
suffering for which compensation is due. The total claim of ₹25 lakhs is a reasonable estimate for the comprehensive
losses incurred by Mrs. Deshmukh.

### Arguments for the Defendant (Dr. Rakesh Nair and XYZ Multispecialty Hospital)

**Key Legal Issues**
-   Standard of care in medical practice and the "Bolam test."
-   Absence of gross negligence.
-   Complications inherent in surgical procedures.
-   Requirement for expert evidence to prove negligence.

**Detailed Narrative**
Dr. Rakesh Nair and XYZ Multispecialty Hospital contend that the damage to Mrs. Anjali Deshmukh's bile duct during
gallbladder surgery was an unfortunate complication, not a result of negligence. They assert that Dr. Nair exercised the
requisite skill and care expected of a reasonably competent surgeon. The hospital provided all necessary facilities and
support. The subsequent pain and need for corrective surgery, while regrettable, do not automatically imply negligence,
as surgical procedures inherently carry risks and potential complications. The hospital and doctor maintain that they
acted in good faith and in accordance with accepted medical practices.

**Court Observations**
The defense relies on the established legal principle that a medical professional is not liable merely because a
treatment is unsuccessful or an adverse outcome occurs. As per *Jacob Mathew v. State of Punjab*, (2005) 6 SCC 1, a
simple lack of care, an error of judgment, or an accident is not proof of negligence. The standard of care is that of an
ordinary competent medical practitioner exercising an ordinary degree of professional skill, as articulated in the
"Bolam test" and adopted by Indian courts. The Supreme Court in *Kusum Sharma v. Batra Hospital & Med. Research Centre*,
(2010) 3 SCC 480, emphasized that a doctor is not negligent if they act in accordance with a practice accepted as proper
by a responsible body of medical men skilled in that particular art, even if a body of adverse opinion exists.

Surgical procedures, especially those involving delicate organs like the gallbladder and bile duct, carry inherent
risks. Damage to the bile duct, while undesirable, can be a recognized complication even when the surgeon exercises due
care. The defense would argue that without specific expert evidence demonstrating a deviation from the accepted standard
of care, negligence cannot be presumed. The burden of proving negligence lies heavily on the claimant, and a mere
averment in a complaint, unsupported by expert medical opinion, is insufficient to establish negligence, as held in
*C.P. Sreekumar (Dr.), MS (Ortho) v. S. Ramanujam*, (2009) 7 SCC 130.

The doctrine of *res ipsa loquitur* should be applied with extreme caution in cases of professional negligence,
particularly for doctors. The Supreme Court in *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, (2021) SCC
OnLine SC 1098, reiterated that *res ipsa loquitur* cannot be applied merely because a patient has not responded
favorably to treatment or a surgery has failed. The defense would argue that the damage to the bile duct, while an
unfortunate outcome, does not "speak for itself" as negligence without further evidence of a specific negligent act or
omission by Dr. Nair. It could be a known, albeit rare, complication that occurred despite the exercise of reasonable
care.

**Judgment & Direction**
The defendants would argue for the dismissal of the compensation claim, asserting that Mrs. Deshmukh has failed to
establish a clear case of medical negligence. They would contend that the unfortunate outcome was a surgical
complication rather than a result of any breach of the standard of care by Dr. Nair or the hospital. They would also
emphasize that the claim for ₹25 lakhs is excessive and unsubstantiated without concrete proof of gross negligence and a
direct causal link that goes beyond inherent surgical risks.

**PDF Links:**
-   *Bombay Hospital and Medical Research Centre v. Asha Jaiswal*, 30-11-2021 (English): https://api.sci.gov.in/supremecourt/2010/4109/4109_2010_11_1501_31713_Judgement_30-Nov-2021.pdf
-   *V. Kishan Rao v. Nikhil Super Speciality Hospital*, 08-03-2010 (English): https://api.sci.gov.in/jonew/judis/36301.pdf
-   *Arun Kumar Manglik v. Chirayu Health and Medicare Private Ltd.*, 09-01-2019 (English): https://api.sci.gov.in/supremecourt/2016/27594/27594_2016_Judgement_09-Jan-2019.pdf
-   *Jacob Mathew v. State of Punjab*, 05-08-2005 (English): https://api.sci.gov.in/jonew/judis/27088.pdf

### NEWACTS CITATIONS:
Section 65B of the Indian Evidence Act, 1872, deals with the admissibility of electronic records, and its corresponding new provision is Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

### Old Provision: Section 65B of Indian Evidence Act, 1872

(1) Notwithstanding anything contained in this Act, any information contained in an electronic record which is printed
on a paper, stored, recorded or copied in optical or magnetic media produced by a computer (hereinafter referred to as
the computer output) shall be deemed to be also a document, if the conditions mentioned in this section are satisfied in
relation to the information and computer in question and shall be admissible in any proceedings, without further proof
or production of the original, as evidence of any contents of the original or of any fact stated therein of which direct
evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer during the period over which the computer was used regularly to store or process information for the purposes of any activities regularly carried on over that period by the person having lawful control over the use of the computer;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer in the ordinary course of the said activities.

(3) Where over any period, the function of storing or processing information for the purposes of any activities regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by computers, whether-
(a) by a combination of computers operating over that period; or
(b) by different computers operating in succession over that period; or
(c) by different combinations of computers operating in succession over that period; or
(d) in any other manner involving the successive operation over that period, in whatever order, of one or more computers and one or more combinations of computers.
all the computers used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer; and references in this section to a computer shall be construed accordingly.

(4) In any proceedings where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things, that is to say,
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer;
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate,
and purporting to be signed by a person occupying a responsible official position in relation to the operation of the relevant device or the management of the relevant activities (whichever is appropriate) shall be evidence of any matter stated in the certificate; and for the purposes of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the person stating it.

(5) For the purposes of this section,
(a) information shall be taken to be supplied to a computer if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) whether in the course of activities carried on by any official, information is supplied with a view to its being stored or processed for the purposes of those activities by a computer operated otherwise than in the course of those activities, that information, if duly supplied to that computer, shall be taken to be supplied to it in the course of those activities;
(c) a computer output shall be taken to have been produced by a computer whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment.

Explanation: For the purposes of this section any reference to information being derived from other information shall be a reference to its being derived therefrom by calculation, comparison or any other process.

### New Provision: Section 63 of Bharatiya Sakshya Adhiniyam, 2023

(1) Notwithstanding anything contained in this Adhiniyam, any information contained in an electronic record which is
printed on paper, stored, recorded or copied in optical or magnetic media or semiconductor memory which is produced by a
computer or any communication device or otherwise stored, recorded or copied in any electronic form (hereinafter
referred to as the computer output) shall be deemed to be also a document, if the conditions mentioned in this section
are satisfied in relation to the information and computer in question and shall be admissible in any proceedings,
without further proof or production of the original, as evidence or any contents of the original or of any fact stated
therein of which direct evidence would be admissible.

(2) The conditions referred to in sub-section (1) in respect of a computer output shall be the following, namely:
(a) the computer output containing the information was produced by the computer or communication device during the period over which the computer or communication device was used regularly to create, store or process information for the purposes of any activity regularly carried on over that period by the person having lawful control over the use of the computer or communication device;
(b) during the said period, information of the kind contained in the electronic record or of the kind from which the information so contained is derived was regularly fed into the computer or communication device in the ordinary course of the said activities;
(c) throughout the material part of the said period, the computer or communication device was operating properly or, if not, then in respect of any period in which it was not operating properly or was out of operation during that part of the period, was not such as to affect the electronic record or the accuracy of its contents; and
(d) the information contained in the electronic record reproduces or is derived from such information fed into the computer or communication device in the ordinary course of the said activities.

(3) Where over any period, the function of creating, storing or processing information for the purposes of any activity regularly carried on over that period as mentioned in clause (a) of sub-section (2) was regularly performed by means of one or more computers or communication device, whether:
(a) in standalone mode; or
(b) on a computer system; or
(c) on a computer network; or
(d) on a computer resource enabling information creation or providing information processing and storage; or
(e) through an intermediary,
all the computers or communication devices used for that purpose during that period shall be treated for the purposes of this section as constituting a single computer or communication device; and references in this section to a computer or communication device shall be construed accordingly.

(4) In any proceeding where it is desired to give a statement in evidence by virtue of this section, a certificate doing any of the following things shall be submitted along with the electronic record at each instance where it is being submitted for admission, namely:
(a) identifying the electronic record containing the statement and describing the manner in which it was produced;
(b) giving such particulars of any device involved in the production of that electronic record as may be appropriate for the purpose of showing that the electronic record was produced by a computer or a communication device referred to in clauses (a) to (e) of sub-section (3);
(c) dealing with any of the matters to which the conditions mentioned in sub-section (2) relate, and purporting to be
signed by a person in charge of the computer or communication device or the management of the relevant activities
(whichever is appropriate) and an expert shall be evidence of any matter stated in the certificate; and for the purposes
of this sub-section it shall be sufficient for a matter to be stated to the best of the knowledge and belief of the
person stating it in the certificate specified in the Schedule.

(5) For the purposes of this section,:
(a) information shall be taken to be supplied to a computer or communication device if it is supplied thereto in any appropriate form and whether it is so supplied directly or (with or without human intervention) by means of any appropriate equipment;
(b) a computer output shall be taken to have been produced by a computer or communication device whether it was produced by it directly or (with or without human intervention) by means of any appropriate equipment or by other electronic means as referred to in clauses (a) to (e) of sub-section (3).

### Key Differences

-   **Scope of Devices**: The new provision explicitly includes "communication device" alongside "computer" throughout the section, broadening the scope of electronic records covered. It also adds "semiconductor memory" as a storage medium.
-   **Computer Output Definition**: The new provision expands the definition of "computer output" to include information "otherwise stored, recorded or copied in any electronic form."
-   **Activities in Sub-section (2)(a)**: The new provision adds "create" to the activities for which the computer or communication device was used regularly, i.e., "to create, store or process information."
-   **Complex Systems in Sub-section (3)**: The new provision significantly updates the description of how functions are performed by multiple devices, replacing the older clauses (a) to (d) with more modern terms like "standalone mode," "computer system," "computer network," "computer resource enabling information creation or providing information processing and storage," and "through an intermediary."
-   **Certificate Requirements in Sub-section (4)**: The new provision specifies that the certificate must be submitted "at each instance where it is being submitted for admission" and adds that it should be signed by "an expert" in addition to the person in charge. It also refers to a "certificate specified in the Schedule."
-   **Sub-section (5) Changes**: The new provision removes clause (b) of the old sub-section (5) and modifies clause (c) to clause (b), expanding the means by which a computer output is produced to include "other electronic means as referred to in clauses (a) to (e) of sub-section (3)."
-   **Explanation**: The Explanation from the old provision is omitted in the new one.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

### SCI_JUDGMENT CITATIONS:
The Supreme Court has consistently emphasized the distinction between a mere breach of contract, which is a civil wrong, and the criminal offense of cheating under Section 420 of the Indian Penal Code (IPC). The crucial element that differentiates the two is the presence of a fraudulent or dishonest intention (mens rea) right from the inception of the transaction.

In *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*, (2022) 8 SCC 696, the Supreme Court reiterated that fraudulent or dishonest intention is a precondition to constitute the offense of cheating. The Court observed that allegations primarily alluding to a breach of terms of a consultancy agreement would essentially be in the nature of a civil dispute.

Similarly, in *Satishchandra Ratanlal Shah v. The State of Gujarat*, (2019) 9 SCC 148, the Supreme Court held that the distinction between a mere breach of contract and cheating depends upon the fraudulent inducement and mens rea. The Court emphasized that mere inability to return a loan amount cannot give rise to criminal prosecution for cheating unless fraudulent or dishonest intention is shown right at the beginning of the transaction.

I think this information will help you more to understand the case:

### Key Legal Issues
-   Whether the ingredients of the offenses of cheating (Section 415 and 420 IPC) and criminal breach of trust (Section 405 IPC) are made out in cases arising from business agreements.
-   The distinction between a mere breach of contract and the criminal offense of cheating.
-   The necessity of proving fraudulent or dishonest intention (mens rea) at the inception of the transaction for an offense under Section 420 IPC.

### Detailed Narrative

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The first respondent (Ramesh Ranganathan), a consultant neurosurgeon, was employed by BGS Apollo Hospital. Differences
arose, and his services were terminated. He filed a criminal complaint alleging misuse of authority, defamation, and
irregularities in billing, leading to cognizance being taken under various sections of the IPC, including Section 420.
The Additional Sessions Judge set aside the cognizance, but the High Court partially restored it for Sections 405 and
420. The appellant (M.N.G Bharateesh Reddy) challenged this before the Supreme Court.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The appellant (Satishchandra Ratanlal Shah) obtained a loan of Rs. 27,00,000/- from the respondent no. 2's (Dharshan
Fiscal Pvt. Ltd.) money lending company. The appellant failed to repay the loan, leading to a criminal complaint under
Sections 406, 409, 417, 420, 294(b), and 506(2) of the IPC. Simultaneously, a summary civil suit for recovery of the
loan amount was also filed and was pending. The High Court dismissed the appellant's petition to quash the charges,
observing a prima facie case of cheating under Section 420 IPC, but not criminal breach of trust under Section 406 IPC.
The appellant then approached the Supreme Court.

### Court Observations

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court, referring to *Hridaya Ranjan Prasad Verma v. State of Bihar*, (2000) 4 SCC 168, and *Dalip Kaur v.
Jagnar Singh*, (2009) 14 SCC 696, emphasized that fraudulent or dishonest intention is a precondition for the offense of
cheating. The Court noted that a mere breach of contract does not amount to cheating unless a fraudulent or dishonest
intention is shown at the very beginning of the transaction. In this case, the Court found that the allegations
primarily stemmed from the termination of services and improper billing, which at most alluded to a breach of the
consultancy agreement, a civil dispute. The Court concluded that the ingredients of Sections 415 and 420 IPC were not
made out.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court reiterated that the distinction between a mere breach of contract and cheating depends on fraudulent
inducement and mens rea. The Court observed that the appellant was in an economic crisis and had approached the
respondent for a loan. The mere inability to return the loan amount, coupled with the pending civil suit for recovery,
could not automatically lead to a criminal prosecution for cheating without proof of fraudulent or dishonest intention
at the inception of the transaction. The Court cautioned against criminalizing civil disputes, such as breaches of
contractual obligations, and held that the legislature intended to criminalize only those breaches accompanied by
fraudulent, dishonest, or deceptive inducements.

### Judgment & Direction

#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court allowed the appeal, setting aside the High Court's judgment to the extent it found a prima facie case under Sections 405 and 420 IPC. The criminal proceedings against the appellant were quashed.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court allowed the appeal, setting aside the impugned order of the High Court. The application filed by the appellant under Section 482 of the Code of Criminal Procedure, 1973, was allowed, and the proceedings initiated based on the FIR were quashed. The Court also expunged certain observations made by the High Court that were deemed unnecessary and potentially prejudicial to the accused.

**PDF Links:**
-   *M.N.G Bharateesh Reddy v. Ramesh Ranganathan, Crl.A. No.-001273-001273 - 2022* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2019/34821/34821_2019_3_19_37392_Judgement_18-Aug-2022.pdf))
-   *Satishchandra Ratanlal Shah v. The State of Gujarat, Crl.A. No.-000009-000009 - 2019* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2018/17145/17145_2018_Judgement_03-Jan-2019.pdf))

### NEWACTS CITATIONS:
## DEFENSE ARGUMENTS AGAINST CHARGE UNDER SECTION 420 INDIAN PENAL CODE, 1860

The defense submits that the present case is fundamentally a civil dispute arising from a failed business agreement and does not constitute the offense of cheating under Section 420 of the Indian Penal Code, 1860 (corresponding to Section 318(4) of the Bharatiya Nyaya Sanhita, 2023). The essential ingredients for establishing criminal cheating, particularly the element of dishonest or fraudulent intention from the inception of the transaction, are conspicuously absent in the present matter.

### Absence of Dishonest Intention at Inception

1.  **Lack of *Mens Rea*:** For an offense under Section 420 of the Indian Penal Code, 1860 (or Section 318(4) BNS,
2023), it is imperative to prove that the accused had a dishonest or fraudulent intention at the very beginning of the
transaction, i.e., from the moment the promise or representation was made. A mere breach of contract or subsequent
failure to fulfill a promise, in the absence of such initial dishonest intention, does not automatically transform a
civil wrong into a criminal offense. The Supreme Court has consistently held that the distinction between a mere breach
of contract and the offense of cheating depends upon the intention of the accused at the time of inducement. If the
intention to cheat was not present at the inception, the offense of cheating cannot be said to have been committed.

1.  **Breach of Contract, Not Criminal Deception:** The allegations, even if taken at face value, primarily disclose a
case of breach of contract, which falls squarely within the domain of civil law. The remedies for such a breach lie in
civil courts, where damages for non-performance or specific performance of the contract can be sought. The mere fact
that a party has suffered a loss in a business transaction does not automatically warrant the initiation of criminal
proceedings. The Supreme Court has repeatedly cautioned against converting civil disputes into criminal cases.

2.  **Availability of Civil Remedies:** The complainant has adequate and effective remedies available under civil law, including filing a suit for recovery of money, specific performance, or damages for breach of contract. Resorting to criminal proceedings under Section 420 of the Indian Penal Code, 1860 (or Section 318(4) BNS, 2023), in such circumstances amounts to an abuse of the process of law, often with the ulterior motive of exerting pressure for recovery of money.

3.  **Absence of Inducement to Deliver Property by Deception:** Section 420 of the Indian Penal Code, 1860 (or Section
318(4) BNS, 2023), requires that the accused must have dishonestly induced the complainant to deliver any property or to
make, alter, or destroy the whole or any part of a valuable security. In the present case, any delivery of property or
financial transaction occurred within the framework of a commercial agreement, not as a result of any dishonest
inducement or fraudulent misrepresentation made at the inception by the client. The complainant willingly entered into
the business arrangement, presumably after due diligence and assessment of commercial risks.

### Judicial Precedents Distinguishing Civil and Criminal Liability

The Supreme Court of India has consistently drawn a clear distinction between a civil wrong and a criminal offense, particularly in cases involving allegations of cheating arising from contractual disputes:

-   In ***Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168***, the Supreme Court held that in order to
constitute an offence of cheating, the intention to cheat must be in existence at the time when the inducement was made.
If the intention was not dishonest at the time of making the promise but developed subsequently, it would not amount to
cheating. The Court observed that a mere breach of contract cannot give rise to criminal prosecution for cheating unless
fraudulent or dishonest intention is shown right at the beginning of the transaction.

-   In ***International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348***, the Supreme Court reiterated that for an offence under Section 420 of the Indian Penal Code, 1860, the element of deception and dishonest intention from the very beginning is crucial. The Court emphasized that a mere breach of contract or failure to pay money does not automatically lead to criminal prosecution for cheating.

-   In ***G. Sagar Suri v. State of U.P., (2000) 2 SCC 636***, the Supreme Court cautioned against converting purely civil disputes into criminal cases. The Court observed that it is not every breach of contract that would amount to the offence of cheating.

Therefore, based on the facts and the established legal principles, the defense submits that the allegations against the client do not fulfill the essential ingredients of criminal cheating under Section 420 of the Indian Penal Code, 1860 (corresponding to Section 318(4) of the Bharatiya Nyaya Sanhita, 2023), and the matter ought to be treated as a civil dispute.

### PDF Links
-   *Hridaya Ranjan Prasad Verma v. State of Bihar, (2000) 4 SCC 168*: [https://api.sci.gov.in/jonew/judis/22759.pdf](https://api.sci.gov.in/jonew/judis/22759.pdf)
-   *International Advanced Research Centre for Powder Metallurgy and New Materials (ARCI) v. Nimra Cerglass Technics (P) Ltd., (2016) 1 SCC 348*: [https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf](https://api.sci.gov.in/supremecourt/2014/19225/19225_2014_Judgement_17-Dec-2015.pdf)
### Key Legal Issues
-   Whether the ingredients of the offenses of cheating (Section 415 and 420 IPC) and criminal breach of trust (Section 405 IPC) are made out in cases arising from business agreements.
-   The distinction between a mere breach of contract and the criminal offense of cheating.
-   The necessity of proving fraudulent or dishonest intention (mens rea) at the inception of the transaction for an offense under Section 420 IPC.
#### *M.N.G Bharateesh Reddy v. Ramesh Ranganathan*
The Supreme Court allowed the appeal, setting aside the High Court's judgment to the extent it found a prima facie case under Sections 405 and 420 IPC. The criminal proceedings against the appellant were quashed.

#### *Satishchandra Ratanlal Shah v. The State of Gujarat*
The Supreme Court allowed the appeal, setting aside the impugned order of the High Court. The application filed by the appellant under Section 482 of the Code of Criminal Procedure, 1973, was allowed, and the proceedings initiated based on the FIR were quashed. The Court also expunged certain observations made by the High Court that were deemed unnecessary and potentially prejudicial to the accused.

**PDF Links:**
-   *M.N.G Bharateesh Reddy v. Ramesh Ranganathan, Crl.A. No.-001273-001273 - 2022* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2019/34821/34821_2019_3_19_37392_Judgement_18-Aug-2022.pdf))
-   *Satishchandra Ratanlal Shah v. The State of Gujarat, Crl.A. No.-000009-000009 - 2019* ([Judgment PDF](https://api.sci.gov.in/supremecourt/2018/17145/17145_2018_Judgement_03-Jan-2019.pdf))

### NEWACTS CITATIONS:
Section 420 of the Indian Penal Code, 1860, deals with cheating and dishonestly inducing the delivery of property. This provision is now covered under Section 318(4) of the Bharatiya Nyaya Sanhita, 2023.

The text of the provision remains identical in both the Indian Penal Code, 1860, and the Bharatiya Nyaya Sanhita, 2023, with the only change being the section number and the name of the Act.

Regarding the user's query about "cheating fraudulent intention mens rea civil dispute business agreement," it is important to emphasize that Section 420 IPC (now Section 318(4) BNS) deals with criminal cheating. For an offense under this section, the prosecution must prove a dishonest or fraudulent intention (mens rea) on the part of the accused *at the very inception* of the transaction.

In the context of a civil dispute or a business agreement, a mere breach of contract or failure to fulfill a promise
does not automatically amount to criminal cheating. The distinction lies in the presence of a dishonest intention to
deceive from the outset. If the intention to deceive was not present at the time the property was induced to be
delivered, but rather arose later due to unforeseen circumstances or a subsequent inability to fulfill the promise, it
would typically fall under the purview of a civil dispute (breach of contract) rather than criminal cheating.

Courts have consistently held that for Section 420 IPC to apply, the fraudulent or dishonest intention must be established from the very beginning of the transaction. If the intention was honest at the time of entering into the agreement, but circumstances later led to a failure to perform, it would generally be a civil wrong.


</details>

Follow-up suggestions returned: `What's the purpose of this suit?`, `What legal standard applies here?`, `What evidence is needed?`

---
