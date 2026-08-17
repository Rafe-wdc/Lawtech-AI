# Same-Thread API Test Report - test prompt.txt

**Date**: 2026-08-17
**Source**: [test prompt.txt](../../test%20prompt.txt) - two labeled prompts.
**Turns**: 2 (turn 1 = full body of `prompt 1:`, turn 2 = literal `Convert above text into marathi`)
**Same thread**: yes - one `globalThreadId` reused across both turns per endpoint.
**Language override**: NONE (`preferred_language` field omitted from payload) - the pipeline must auto-detect + intent-extract.

---

## Endpoint: PROD

- **URL**: `https://api.lawttorney.com/pyapi/search`
- **thread_id**: `d16a557d-6d86-4e32-a945-9b7390c2fa7a`
- **run started**: 2026-08-17T14:32:33.412340+00:00

### Summary

| Turn | Label | Status | Time (s) | Agents | Chars | Devanagari | Latin |
|------|-------|--------|----------|--------|-------|------------|-------|
| 1 | prompt-1-mega | 200 | 103.78 | Drafting | 9663 | 0 | 7566 |
| 2 | prompt-2-convert-to-marathi | 200 | 38.94 | Drafting, Scenario | 17798 | 6195 | 7890 |

### Turn 1 - prompt-1-mega

- **Started**: 2026-08-17T14:32:33.412366+00:00
- **Elapsed**: 103.78 s
- **HTTP Status**: 200

#### Request

```http
POST https://api.lawttorney.com/pyapi/search
X-API-Key: ***
Content-Type: application/json

{
  "Promptquery": "prompt 1: Research Prompt: Section 125 of CrPC\n \nScenario:\n \nDraft a civil suit for partition along with a prayer for temporary injunction. Facts: Rupa is an adopted daughter of the deceased owner. She has no siblings. Her father died 5 years ago, leaving behind two flats in Pune. The only legal heirs are Rupa and her mother. Her name is not yet on the property documents. Her mother is attempting to dispossess her and has expressed intent to transfer the properties to her cousin. The suit should include legal provisions for partition of the properties, declaration of her share, and a temporary injunction restraining the mother or any third party from selling, transferring, or creating third-party rights in the properties until disposal of the suit.\n \nDraft a civil suit for damages arising from medical negligence, incorporating detailed legal reasoning and relevant case laws. Scenario: My client, Mrs. Anjali Deshmukh, aged 42, and a resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the procedure, the attending surgeon, Dr. Rakesh Nair, negligently caused damage to her bile duct. Following the surgery, she experienced severe abdominal pain and had to be admitted to another hospital for corrective surgery. As a result, she incurred additional medical expenses amounting to ₹3.5 lakhs and endured significant physical pain and mental agony. Despite repeated follow-ups, both the hospital and the doctor have refused to acknowledge their negligence or offer any compensation. Mrs. Deshmukh now seeks damages of ₹25 lakhs, covering medical expenses, mental harassment, pain and suffering, and anticipated costs of future treatment.\n \nCitations:\n \n1. Rupa is a adopted child. She doesnt have any siblings. her father passed away 5 years ago. She and her mothers are only legal heirs. Her father had 2 flats in pune. Her mother told her to leave the house and her mother says that she'll transfer the property to her cousins name. now rupa is scared that she won't get any share in the properties (her name is still not on the legal papers of the properties). Give me 5 plus Case law or Citations According to the Scenario.\n \n2. Provide case laws on the admissibility of electronic evidence under Section 65B of the Indian Evidence Act.\n \n3. Provide citations or case laws related to anticipatory bail\n \nArgument Generation:\n \nMy client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the surgery, due to the negligence of the attending surgeon, Dr. Rakesh Nair, her bile duct was damaged. Post-surgery, she suffered from severe abdominal pain and had to be admitted again for corrective surgery at another hospital. She incurred additional medical expenses of ₹3.5 lakhs and suffered immense mental agony and physical pain. Despite repeated follow-ups, the hospital and the doctor refused to acknowledge their mistake or compensate her. She now seeks compensation of ₹25 lakhs for medical expenses, mental harassment, pain and suffering, and future treatment costs. give me arguments on behalf of plaintiff and defendant.\n \nमाझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टीस्पेशालिटी हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली. शस्त्रक्रियेदरम्यान, उपस्थित सर्जन डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे, त्यांच्या पित्तनलिकेचे नुकसान झाले. शस्त्रक्रियेनंतर, त्यांना तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसऱ्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. त्यांना ३.५ लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आणि प्रचंड मानसिक त्रास आणि शारीरिक वेदना सहन कराव्या लागल्या. वारंवार पाठपुरावा करूनही, रुग्णालय आणि डॉक्टरांनी त्यांची चूक मान्य करण्यास किंवा तिला भरपाई देण्यास नकार दिला. आता त्या वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास आणि भविष्यातील उपचार खर्चासाठी २५ लाख रुपयांची भरपाई मागत आहेत. मला युक्तिवाद द्या.\n \nMy client is facing a criminal case under Section 420 IPC for alleged cheating. However, there was no fraudulent intention from the beginning, and it was purely a civil dispute arising from a failed business agreement. Give me arguments to defend the client and emphasize that this is a case of civil liability, not criminal cheating.\n \nConsumer Laws:\n \nScenario 1: Defective Product Liability Prompt: Priya purchases a brand-new refrigerator from a well-known electronics store. After a week, it stops working, and the manufacturer refuses to repair or replace it, claiming that the issue was caused by improper installation. What legal options does Priya have under the Consumer Protection Act, 2019, to address this defect?\n \nPdf : Review this draft and suggest changes, if any.",
  "globalThreadId": "d16a557d-6d86-4e32-a945-9b7390c2fa7a"
}
```

#### Response (JSON)

```json
{
  "globalThreadId": "d16a557d-6d86-4e32-a945-9b7390c2fa7a",
  "result": "## Cause Title\n\nIN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE\n\nCIVIL SUIT NO. ______ OF 2024\n\nRupa D/o. Late [Deceased Father's Name],\nAge: [Age of Rupa],\nOccupation: [Occupation of Rupa],\nResiding at: [Address of Rupa], Pune.\n\n.....Plaintiff\n\n**vs**\n\n[Name of Mother] W/o. Late [Deceased Father's Name],\nAge: [Age of Mother],\nOccupation: [Occupation of Mother],\nResiding at: [Address of Mother], Pune.\n\n.....Defendant\n\n## Suit for Partition and Temporary Injunction\n\nSUIT FOR PARTITION, DECLARATION OF SHARE, PERMANENT AND TEMPORARY INJUNCTION\n\nThe Plaintiff above-named submits as under:\n\n## Facts of the Case\n\n1. That the Plaintiff is the adopted daughter of Late [Deceased Father's Name] and the Defendant, [Name of Mother]. The Plaintiff has no siblings. The parties are Hindus and are governed by Hindu Law.\n\n2. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate approximately 5 years ago, leaving behind the Plaintiff and the Defendant as his only Class I legal heirs.\n\n3. That during his lifetime, the deceased father of the Plaintiff was the absolute owner of two residential flats situated in Pune, the full particulars of which are described in the Schedule of Property annexed hereto and hereinafter referred to as the \"Suit Properties\".\n\n4. That upon the demise of the Plaintiff's father, the Plaintiff and the Defendant, being the sole legal heirs, succeeded to the Suit Properties jointly and have been in joint possession thereof. The Plaintiff and Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties.\n\n5. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the family home and has explicitly threatened to transfer the Suit Properties to her cousin, thereby depriving the Plaintiff of her rightful and legitimate share.\n\n6. That the Plaintiff's name has not yet been mutated in the official property records, and the Defendant is taking undue advantage of this fact to illegally and wrongfully exclude the Plaintiff from her inheritance.\n\n7. That the cause of action arose recently when the Defendant threatened to dispossess the Plaintiff and alienate the Suit Properties. The Plaintiff demanded her share, but the Defendant has refused to partition the properties, necessitating the filing of the present suit.\n\n## Legal Provisions\n\n8. That the parties to the suit are Hindus, and the succession to the estate of the deceased is governed by the provisions of the Hindu Succession Act, 1956.\n\n9. That the Plaintiff, being the adopted daughter, has all the rights and privileges of a natural-born daughter in the adoptive family. As per Section 12 of the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption.\n\n10. That as per Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves upon the heirs specified in Class I of the Schedule. The Plaintiff (daughter) and the Defendant (widow) are the only Class I heirs of the deceased.\n\n11. That consequently, the Plaintiff and the Defendant are each entitled to an equal one-half (1/2) share in the Suit Properties left by the deceased.\n\n12. That in light of the Defendant's attempts to create third-party rights and alienate the Suit Properties, it is imperative to grant a temporary injunction restraining the Defendant, her agents, or anyone acting on her behalf from selling, transferring, mortgaging, or creating any third-party interest in the Suit Properties pending the final disposal of this suit, as provided under Order XXXIX Rules 1 and 2 of the Code of Civil Procedure, 1908.\n\n## Plaintiff's Entitlement and Share\n\n13. That the Plaintiff submits that as the legally adopted daughter of the Late [Deceased Father's Name], she is conferred with all the rights and status of a natural-born daughter within the adoptive family. This status is unequivocally established under Section 12 of the Hindu Adoptions and Maintenance Act, 1956, which severs all ties with the birth family and creates corresponding rights in the adoptive family.\n\n14. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate. Therefore, the succession to his self-acquired properties is governed by the rules of intestate succession laid down in the Hindu Succession Act, 1956.\n\n15. That in accordance with Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves firstly upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the daughter, and the Defendant, being the widow of the deceased, are the only two surviving Class I legal heirs.\n\n16. That by virtue of the aforesaid provisions of law, the Plaintiff and the Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties. The Plaintiff is, therefore, a co-owner of the Suit Properties along with the Defendant and is in joint possession of the same.\n\n## Grounds for Temporary Injunction\n\n17. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the Suit Properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no legal right to deny the Plaintiff her legitimate share.\n\n18. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. Granting an injunction would only require the Defendant to maintain the status quo of the properties until the rights of the parties are adjudicated, causing no prejudice to the Defendant. Conversely, refusing the injunction would expose the Plaintiff to the risk of losing her rightful inheritance through the Defendant's wrongful acts.\n\n19. That the Plaintiff will suffer irreparable loss and injury if a temporary injunction is not granted. The Defendant\nhas explicitly threatened to alienate the Suit Properties by transferring them to her cousin. If the Defendant is not\nrestrained by an order of this Hon'ble Court and succeeds in creating third-party interests, it will lead to a\nmultiplicity of legal proceedings and render any decree passed in this suit futile. The loss of the Plaintiff's share in\nthe family's residential properties cannot be adequately compensated in monetary terms.\n\n20. That it is therefore just, equitable, and necessary in the interest of justice that the Defendant, her agents, servants, or any person claiming through her be restrained by an order of temporary injunction from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties described in the Schedule hereto, pending the final hearing and disposal of this suit.\n\n## Jurisdiction and Valuation\n\n21. That the Suit Properties are situated in Pune, which is within the territorial and pecuniary jurisdiction of this Hon'ble Court. Therefore, this Hon'ble Court has the jurisdiction to entertain, try, and dispose of the present suit.\n\n22. That for the purpose of jurisdiction, the suit is valued at the market value of the Plaintiff's one-half (1/2) share in the Suit Properties, which is estimated at Rs. [Value of Plaintiff's 1/2 Share].\n\n23. That for the purpose of court fees, as the Plaintiff is in joint possession of the Suit Properties, the claim for partition is valued under Section 6(1)(vii) of the Maharashtra Court Fees Act, 1959, and the requisite fixed court fee has been paid thereon. The relief of injunction is a consequential relief, and no separate court fee is payable.\n\n## Prayer\n\nWHEREFORE, the Plaintiff most humbly prays that this Hon'ble Court may be pleased to:\n\n(a) Pass a preliminary decree for partition of the Suit Properties described in the Schedule hereto, declaring that the Plaintiff is entitled to an equal, undivided one-half (1/2) share in the said properties.\n\n(b) Direct that the Suit Properties be partitioned by metes and bounds, and thereafter pass a final decree putting the Plaintiff in separate, vacant, and peaceful possession of her one-half (1/2) share.\n\n(c) Pass a decree of permanent injunction restraining the Defendant, her agents, servants, or any person claiming through or under her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.\n\n(d) Pending the hearing and final disposal of this suit, grant a temporary injunction restraining the Defendant, her agents, servants, or any person claiming through her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.\n\n(e) Award the costs of this suit to the Plaintiff from the Defendant.\n\n(f) Grant such other and further reliefs as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case.\n\n## Verification\n\nI, Rupa, the Plaintiff above-named, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 23 are true and correct to the best of my knowledge, information, and belief, and that I have concealed nothing material therefrom.\n\nVerified at Pune on this ______ day of _______________, 2024.\n\n(Rupa)\nPLAINTIFF\n\n\n---\n**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.",
  "token_usage": {
    "input_tokens": 80612,
    "output_tokens": 11823,
    "total_tokens": 92435,
    "cache_read_tokens": 35260,
    "cache_creation_tokens": 0,
    "reasoning_tokens": 7636,
    "cost_usd": 0.254799,
    "by_agent": {
      "Orchestrator": {
        "input": 9233,
        "output": 1530,
        "total": 10763,
        "cache_read": 6648,
        "calls": 2,
        "cost_usd": 0.001535
      },
      "Drafting": {
        "input": 71379,
        "output": 10293,
        "total": 81672,
        "cache_read": 28612,
        "calls": 7,
        "cost_usd": 0.253263
      }
    },
    "calls": [
      {
        "agent": "Orchestrator",
        "step": "classify_and_plan",
        "input": 2430,
        "output": 93,
        "total": 2523,
        "cache_read": 845,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.00028
      },
      {
        "agent": "Orchestrator",
        "step": "extract_user_intent",
        "input": 6803,
        "output": 1437,
        "total": 8240,
        "cache_read": 5803,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.001255
      },
      {
        "agent": "Drafting",
        "step": "pick_reference",
        "input": 4740,
        "output": 71,
        "total": 4811,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000502
      },
      {
        "agent": "Drafting",
        "step": "fanout_judge",
        "input": 3596,
        "output": 519,
        "total": 4115,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000567
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 11704,
        "output": 1751,
        "total": 13455,
        "cache_read": 4086,
        "cache_creation": 0,
        "reasoning": 1573,
        "cost_usd": 0.04787
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 11894,
        "output": 2078,
        "total": 13972,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 1388,
        "cost_usd": 0.049528
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 12588,
        "output": 1959,
        "total": 14547,
        "cache_read": 8175,
        "cache_creation": 0,
        "reasoning": 1319,
        "cost_usd": 0.048515
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 13220,
        "output": 1791,
        "total": 15011,
        "cache_read": 8175,
        "cache_creation": 0,
        "reasoning": 1316,
        "cost_usd": 0.047595
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 13637,
        "output": 2124,
        "total": 15761,
        "cache_read": 8176,
        "cache_creation": 0,
        "reasoning": 2040,
        "cost_usd": 0.058686
      }
    ]
  },
  "source": [
    {
      "source_type": "drafting",
      "title": "SUIT FOR PARTITION AND SEPARATE POSSESSION",
      "content": [
        "Draft Heading: Specimen/Draft/Format of SUIT FOR PARTITION AND SEPARATE POSSESSION.\nDraft Content: IN THE COURT OF THE PRINCIPAL CIVIL JUDGE, (City Name), (District Name), (State Name)\n                                                                                SUIT NO. _________ OF 2023\n\n(Plaint"
      ],
      "doc_link": null,
      "file_name": "/content/formats_and_all_drafts/SUIT FOR PARTITION AND SEPARATE POSSESSION.csv",
      "agent_name": "Drafting",
      "relevance_score": null,
      "template_type": "SUIT FOR PARTITION AND SEPARATE POSSESSION"
    }
  ],
  "agents_used": [
    "Drafting"
  ],
  "effective_query": "prompt 1: Research Prompt: Section 125 of Code of Criminal Procedure 1973\n \nScenario:\n \nDraft a civil suit for partition along with a prayer for temporary injunction. Facts: Rupa is an adopted daughter of the deceased owner. She has no siblings. Her father died 5 years ago, leaving behind two flats in Pune. The only legal heirs are Rupa and her mother. Her name is not yet on the property documents. Her mother is attempting to dispossess her and has expressed intent to transfer the properties to her cousin. The suit should include legal provisions for partition of the properties, declaration of her share, and a temporary injunction restraining the mother or any third party from selling, transferring, or creating third-party rights in the properties until disposal of the suit.\n \nDraft a civil suit for damages arising from medical negligence, incorporating detailed legal reasoning and relevant case laws. Scenario: My client, Mrs. Anjali Deshmukh, aged 42, and a resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the procedure, the attending surgeon, Dr. Rakesh Nair, negligently caused damage to her bile duct. Following the surgery, she experienced severe abdominal pain and had to be admitted to another hospital for corrective surgery. As a result, she incurred additional medical expenses amounting to ₹3.5 lakhs and endured significant physical pain and mental agony. Despite repeated follow-ups, both the hospital and the doctor have refused to acknowledge their negligence or offer any compensation. Mrs. Deshmukh now seeks damages of ₹25 lakhs, covering medical expenses, mental harassment, pain and suffering, and anticipated costs of future treatment.\n \nCitations:\n \n1. Rupa is a adopted child. She doesnt have any siblings. her father passed away 5 years ago. She and her mothers are only legal heirs. Her father had 2 flats in pune. Her mother told her to leave the house and her mother says that she'll transfer the property to her cousins name. now rupa is scared that she won't get any share in the properties (her name is still not on the legal papers of the properties). Give me 5 plus Case law or Citations According to the Scenario.\n \n2. Provide case laws on the admissibility of electronic evidence under Section 65B of the Indian Evidence Act.\n \n3. Provide citations or case laws related to anticipatory bail\n \nArgument Generation:\n \nMy client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the surgery, due to the negligence of the attending surgeon, Dr. Rakesh Nair, her bile duct was damaged. Post-surgery, she suffered from severe abdominal pain and had to be admitted again for corrective surgery at another hospital. She incurred additional medical expenses of ₹3.5 lakhs and suffered immense mental agony and physical pain. Despite repeated follow-ups, the hospital and the doctor refused to acknowledge their mistake or compensate her. She now seeks compensation of ₹25 lakhs for medical expenses, mental harassment, pain and suffering, and future treatment costs. give me arguments on behalf of plaintiff and defendant.\n \nमाझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टीस्पेशालिटी हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली. शस्त्रक्रियेदरम्यान, उपस्थित सर्जन डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे, त्यांच्या पित्तनलिकेचे नुकसान झाले. शस्त्रक्रियेनंतर, त्यांना तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसऱ्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. त्यांना ३.५ लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आणि प्रचंड मानसिक त्रास आणि शारीरिक वेदना सहन कराव्या लागल्या. वारंवार पाठपुरावा करूनही, रुग्णालय आणि डॉक्टरांनी त्यांची चूक मान्य करण्यास किंवा तिला भरपाई देण्यास नकार दिला. आता त्या वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास आणि भविष्यातील उपचार खर्चासाठी २५ लाख रुपयांची भरपाई मागत आहेत. मला युक्तिवाद द्या.\n \nMy client is facing a criminal case under Section 420 Indian Penal Code 1860 for alleged cheating. However, there was no fraudulent intention from the beginning, and it was purely a civil dispute arising from a failed business agreement. Give me arguments to defend the client and emphasize that this is a case of civil liability, not criminal cheating.\n \nConsumer Laws:\n \nScenario 1: Defective Product Liability Prompt: Priya purchases a brand-new refrigerator from a well-known electronics store. After a week, it stops working, and the manufacturer refuses to repair or replace it, claiming that the issue was caused by improper installation. What legal options does Priya have under the Consumer Protection Act, 2019, to address this defect?\n \nPdf : Review this draft and suggest changes, if any.",
  "query_rewritten": true,
  "conversation_turn": 1,
  "followup_suggestions": [
    "What are Rupa's inheritance rights?",
    "How to prove mother's intent to transfer?",
    "What is the timeline for injunction?"
  ]
}
```

#### Result (rendered markdown)

<details>
<summary>Full response - 9663 chars</summary>

## Cause Title

IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE

CIVIL SUIT NO. ______ OF 2024

Rupa D/o. Late [Deceased Father's Name],
Age: [Age of Rupa],
Occupation: [Occupation of Rupa],
Residing at: [Address of Rupa], Pune.

.....Plaintiff

**vs**

[Name of Mother] W/o. Late [Deceased Father's Name],
Age: [Age of Mother],
Occupation: [Occupation of Mother],
Residing at: [Address of Mother], Pune.

.....Defendant

## Suit for Partition and Temporary Injunction

SUIT FOR PARTITION, DECLARATION OF SHARE, PERMANENT AND TEMPORARY INJUNCTION

The Plaintiff above-named submits as under:

## Facts of the Case

1. That the Plaintiff is the adopted daughter of Late [Deceased Father's Name] and the Defendant, [Name of Mother]. The Plaintiff has no siblings. The parties are Hindus and are governed by Hindu Law.

2. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate approximately 5 years ago, leaving behind the Plaintiff and the Defendant as his only Class I legal heirs.

3. That during his lifetime, the deceased father of the Plaintiff was the absolute owner of two residential flats situated in Pune, the full particulars of which are described in the Schedule of Property annexed hereto and hereinafter referred to as the "Suit Properties".

4. That upon the demise of the Plaintiff's father, the Plaintiff and the Defendant, being the sole legal heirs, succeeded to the Suit Properties jointly and have been in joint possession thereof. The Plaintiff and Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties.

5. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the family home and has explicitly threatened to transfer the Suit Properties to her cousin, thereby depriving the Plaintiff of her rightful and legitimate share.

6. That the Plaintiff's name has not yet been mutated in the official property records, and the Defendant is taking undue advantage of this fact to illegally and wrongfully exclude the Plaintiff from her inheritance.

7. That the cause of action arose recently when the Defendant threatened to dispossess the Plaintiff and alienate the Suit Properties. The Plaintiff demanded her share, but the Defendant has refused to partition the properties, necessitating the filing of the present suit.

## Legal Provisions

8. That the parties to the suit are Hindus, and the succession to the estate of the deceased is governed by the provisions of the Hindu Succession Act, 1956.

9. That the Plaintiff, being the adopted daughter, has all the rights and privileges of a natural-born daughter in the adoptive family. As per Section 12 of the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption.

10. That as per Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves upon the heirs specified in Class I of the Schedule. The Plaintiff (daughter) and the Defendant (widow) are the only Class I heirs of the deceased.

11. That consequently, the Plaintiff and the Defendant are each entitled to an equal one-half (1/2) share in the Suit Properties left by the deceased.

12. That in light of the Defendant's attempts to create third-party rights and alienate the Suit Properties, it is imperative to grant a temporary injunction restraining the Defendant, her agents, or anyone acting on her behalf from selling, transferring, mortgaging, or creating any third-party interest in the Suit Properties pending the final disposal of this suit, as provided under Order XXXIX Rules 1 and 2 of the Code of Civil Procedure, 1908.

## Plaintiff's Entitlement and Share

13. That the Plaintiff submits that as the legally adopted daughter of the Late [Deceased Father's Name], she is conferred with all the rights and status of a natural-born daughter within the adoptive family. This status is unequivocally established under Section 12 of the Hindu Adoptions and Maintenance Act, 1956, which severs all ties with the birth family and creates corresponding rights in the adoptive family.

14. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate. Therefore, the succession to his self-acquired properties is governed by the rules of intestate succession laid down in the Hindu Succession Act, 1956.

15. That in accordance with Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves firstly upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the daughter, and the Defendant, being the widow of the deceased, are the only two surviving Class I legal heirs.

16. That by virtue of the aforesaid provisions of law, the Plaintiff and the Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties. The Plaintiff is, therefore, a co-owner of the Suit Properties along with the Defendant and is in joint possession of the same.

## Grounds for Temporary Injunction

17. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the Suit Properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no legal right to deny the Plaintiff her legitimate share.

18. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. Granting an injunction would only require the Defendant to maintain the status quo of the properties until the rights of the parties are adjudicated, causing no prejudice to the Defendant. Conversely, refusing the injunction would expose the Plaintiff to the risk of losing her rightful inheritance through the Defendant's wrongful acts.

19. That the Plaintiff will suffer irreparable loss and injury if a temporary injunction is not granted. The Defendant
has explicitly threatened to alienate the Suit Properties by transferring them to her cousin. If the Defendant is not
restrained by an order of this Hon'ble Court and succeeds in creating third-party interests, it will lead to a
multiplicity of legal proceedings and render any decree passed in this suit futile. The loss of the Plaintiff's share in
the family's residential properties cannot be adequately compensated in monetary terms.

20. That it is therefore just, equitable, and necessary in the interest of justice that the Defendant, her agents, servants, or any person claiming through her be restrained by an order of temporary injunction from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties described in the Schedule hereto, pending the final hearing and disposal of this suit.

## Jurisdiction and Valuation

21. That the Suit Properties are situated in Pune, which is within the territorial and pecuniary jurisdiction of this Hon'ble Court. Therefore, this Hon'ble Court has the jurisdiction to entertain, try, and dispose of the present suit.

22. That for the purpose of jurisdiction, the suit is valued at the market value of the Plaintiff's one-half (1/2) share in the Suit Properties, which is estimated at Rs. [Value of Plaintiff's 1/2 Share].

23. That for the purpose of court fees, as the Plaintiff is in joint possession of the Suit Properties, the claim for partition is valued under Section 6(1)(vii) of the Maharashtra Court Fees Act, 1959, and the requisite fixed court fee has been paid thereon. The relief of injunction is a consequential relief, and no separate court fee is payable.

## Prayer

WHEREFORE, the Plaintiff most humbly prays that this Hon'ble Court may be pleased to:

(a) Pass a preliminary decree for partition of the Suit Properties described in the Schedule hereto, declaring that the Plaintiff is entitled to an equal, undivided one-half (1/2) share in the said properties.

(b) Direct that the Suit Properties be partitioned by metes and bounds, and thereafter pass a final decree putting the Plaintiff in separate, vacant, and peaceful possession of her one-half (1/2) share.

(c) Pass a decree of permanent injunction restraining the Defendant, her agents, servants, or any person claiming through or under her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.

(d) Pending the hearing and final disposal of this suit, grant a temporary injunction restraining the Defendant, her agents, servants, or any person claiming through her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.

(e) Award the costs of this suit to the Plaintiff from the Defendant.

(f) Grant such other and further reliefs as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case.

## Verification

I, Rupa, the Plaintiff above-named, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 23 are true and correct to the best of my knowledge, information, and belief, and that I have concealed nothing material therefrom.

Verified at Pune on this ______ day of _______________, 2024.

(Rupa)
PLAINTIFF


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

---

### Turn 2 - prompt-2-convert-to-marathi

- **Started**: 2026-08-17T14:34:17.196531+00:00
- **Elapsed**: 38.94 s
- **HTTP Status**: 200

#### Request

```http
POST https://api.lawttorney.com/pyapi/search
X-API-Key: ***
Content-Type: application/json

{
  "Promptquery": "Convert above text into marathi",
  "globalThreadId": "d16a557d-6d86-4e32-a945-9b7390c2fa7a"
}
```

#### Response (JSON)

```json
{
  "globalThreadId": "d16a557d-6d86-4e32-a945-9b7390c2fa7a",
  "result": "## Cause Title\n\nIN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE\n\nCIVIL SUIT NO. ______ OF 2024\n\nRupa D/o. Late [Deceased Father's Name],\nAge: [Age of Rupa],\nOccupation: [Occupation of Rupa],\nResiding at: [Address of Rupa], Pune.\n\n.....Plaintiff\n\n**vs**\n\n[Name of Mother] W/o. Late [Deceased Father's Name],\nAge: [Age of Mother],\nOccupation: [Occupation of Mother],\nResiding at: [Address of Mother], Pune.\n\n.....Defendant\n\n## Suit for Partition and Temporary Injunction\n\nSUIT FOR PARTITION, DECLARATION OF SHARE, PERMANENT AND TEMPORARY INJUNCTION\n\nThe Plaintiff above-named submits as under:\n\n## Facts of the Case\n\n1. That the Plaintiff is the adopted daughter of Late [Deceased Father's Name] and the Defendant, [Name of Mother]. The Plaintiff has no siblings. The parties are Hindus and are governed by Hindu Law.\n\n2. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate approximately 5 years ago, leaving behind the Plaintiff and the Defendant as his only Class I legal heirs.\n\n3. That during his lifetime, the deceased father of the Plaintiff was the absolute owner of two residential flats situated in Pune, the full particulars of which are described in the Schedule of Property annexed hereto and hereinafter referred to as the \"Suit Properties\".\n\n4. That upon the demise of the Plaintiff's father, the Plaintiff and the Defendant, being the sole legal heirs, succeeded to the Suit Properties jointly and have been in joint possession thereof. The Plaintiff and Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties.\n\n5. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the family home and has explicitly threatened to transfer the Suit Properties to her cousin, thereby depriving the Plaintiff of her rightful and legitimate share.\n\n6. That the Plaintiff's name has not yet been mutated in the official property records, and the Defendant is taking undue advantage of this fact to illegally and wrongfully exclude the Plaintiff from her inheritance.\n\n7. That the cause of action arose recently when the Defendant threatened to dispossess the Plaintiff and alienate the Suit Properties. The Plaintiff demanded her share, but the Defendant has refused to partition the properties, necessitating the filing of the present suit.\n\n## Legal Provisions\n\n8. That the parties to the suit are Hindus, and the succession to the estate of the deceased is governed by the provisions of the Hindu Succession Act, 1956.\n\n9. That the Plaintiff, being the adopted daughter, has all the rights and privileges of a natural-born daughter in the adoptive family. As per Section 12 of the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption.\n\n10. That as per Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves upon the heirs specified in Class I of the Schedule. The Plaintiff (daughter) and the Defendant (widow) are the only Class I heirs of the deceased.\n\n11. That consequently, the Plaintiff and the Defendant are each entitled to an equal one-half (1/2) share in the Suit Properties left by the deceased.\n\n12. That in light of the Defendant's attempts to create third-party rights and alienate the Suit Properties, it is imperative to grant a temporary injunction restraining the Defendant, her agents, or anyone acting on her behalf from selling, transferring, mortgaging, or creating any third-party interest in the Suit Properties pending the final disposal of this suit, as provided under Order XXXIX Rules 1 and 2 of the Code of Civil Procedure, 1908.\n\n## Plaintiff's Entitlement and Share\n\n13. That the Plaintiff submits that as the legally adopted daughter of the Late [Deceased Father's Name], she is conferred with all the rights and status of a natural-born daughter within the adoptive family. This status is unequivocally established under Section 12 of the Hindu Adoptions and Maintenance Act, 1956, which severs all ties with the birth family and creates corresponding rights in the adoptive family.\n\n14. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate. Therefore, the succession to his self-acquired properties is governed by the rules of intestate succession laid down in the Hindu Succession Act, 1956.\n\n15. That in accordance with Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves firstly upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the daughter, and the Defendant, being the widow of the deceased, are the only two surviving Class I legal heirs.\n\n16. That by virtue of the aforesaid provisions of law, the Plaintiff and the Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties. The Plaintiff is, therefore, a co-owner of the Suit Properties along with the Defendant and is in joint possession of the same.\n\n## Grounds for Temporary Injunction\n\n17. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the Suit Properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no legal right to deny the Plaintiff her legitimate share.\n\n18. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. Granting an injunction would only require the Defendant to maintain the status quo of the properties until the rights of the parties are adjudicated, causing no prejudice to the Defendant. Conversely, refusing the injunction would expose the Plaintiff to the risk of losing her rightful inheritance through the Defendant's wrongful acts.\n\n19. That the Plaintiff will suffer irreparable loss and injury if a temporary injunction is not granted. The Defendant\nhas explicitly threatened to alienate the Suit Properties by transferring them to her cousin. If the Defendant is not\nrestrained by an order of this Hon'ble Court and succeeds in creating third-party interests, it will lead to a\nmultiplicity of legal proceedings and render any decree passed in this suit futile. The loss of the Plaintiff's share in\nthe family's residential properties cannot be adequately compensated in monetary terms.\n\n20. That it is therefore just, equitable, and necessary in the interest of justice that the Defendant, her agents, servants, or any person claiming through her be restrained by an order of temporary injunction from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties described in the Schedule hereto, pending the final hearing and disposal of this suit.\n\n## Jurisdiction and Valuation\n\n21. That the Suit Properties are situated in Pune, which is within the territorial and pecuniary jurisdiction of this Hon'ble Court. Therefore, this Hon'ble Court has the jurisdiction to entertain, try, and dispose of the present suit.\n\n22. That for the purpose of jurisdiction, the suit is valued at the market value of the Plaintiff's one-half (1/2) share in the Suit Properties, which is estimated at Rs. [Value of Plaintiff's 1/2 Share].\n\n23. That for the purpose of court fees, as the Plaintiff is in joint possession of the Suit Properties, the claim for partition is valued under Section 6(1)(vii) of the Maharashtra Court Fees Act, 1959, and the requisite fixed court fee has been paid thereon. The relief of injunction is a consequential relief, and no separate court fee is payable.\n\n## Prayer\n\nWHEREFORE, the Plaintiff most humbly prays that this Hon'ble Court may be pleased to:\n\n(a) Pass a preliminary decree for partition of the Suit Properties described in the Schedule hereto, declaring that the Plaintiff is entitled to an equal, undivided one-half (1/2) share in the said properties.\n\n(b) Direct that the Suit Properties be partitioned by metes and bounds, and thereafter pass a final decree putting the Plaintiff in separate, vacant, and peaceful possession of her one-half (1/2) share.\n\n(c) Pass a decree of permanent injunction restraining the Defendant, her agents, servants, or any person claiming through or under her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.\n\n(d) Pending the hearing and final disposal of this suit, grant a temporary injunction restraining the Defendant, her agents, servants, or any person claiming through her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.\n\n(e) Award the costs of this suit to the Plaintiff from the Defendant.\n\n(f) Grant such other and further reliefs as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case.\n\n## Verification\n\nI, Rupa, the Plaintiff above-named, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 23 are true and correct to the best of my knowledge, information, and belief, and that I have concealed nothing material therefrom.\n\nVerified at Pune on this ______ day of _______________, 2024.\n\n(Rupa)\nPLAINTIFF\n\n---\n\n## REFERENCES & CITATIONS\n\n\n### SCENARIO CITATIONS:\nसिव्हिल जज सीनियर डिव्हिजन, पुणे यांच्या न्यायालयात\n\nसिव्हिल सूट क्रमांक ______ ऑफ 2024\n\nरूपा डी/ओ. कै. [मृत वडिलांचे नाव],\nवय: [रूपाचे वय],\nव्यवसाय: [रूपाचा व्यवसाय],\nरा. [रूपाचा पत्ता], पुणे.\n\n.....वादी\n\n**विरुद्ध**\n\n[आईचे नाव] डब्लू/ओ. कै. [मृत वडिलांचे नाव],\nवय: [आईचे वय],\nव्यवसाय: [आईचा व्यवसाय],\nरा. [आईचा पत्ता], पुणे.\n\n.....प्रतिवादी\n\n## विभागणी आणि तात्पुरत्या मनाई हुकुमासाठीचा दावा\n\nविभागणी, हक्काची घोषणा, कायमस्वरूपी आणि तात्पुरत्या मनाई हुकुमासाठीचा दावा\n\nवरील नमूद वादी खालीलप्रमाणे विनंती करते:\n\n## प्रकरणाची तथ्ये\n\n1.  वादी कै. [मृत वडिलांचे नाव] आणि प्रतिवादी, [आईचे नाव] यांची दत्तक कन्या आहे. वादीला भावंड नाहीत. पक्षकार हिंदू असून हिंदू कायद्याने शासित आहेत.\n\n2.  वादीचे दत्तक वडील, कै. [मृत वडिलांचे नाव], अंदाजे 5 वर्षांपूर्वी मृत्युपत्र न करता मरण पावले, त्यांच्यामागे वादी आणि प्रतिवादी हे त्यांचे एकमेव वर्ग I कायदेशीर वारस म्हणून राहिले.\n\n3.  त्यांच्या हयातीत, वादीचे मृत वडील पुणे येथे असलेल्या दोन निवासी सदनिकांचे पूर्ण मालक होते, ज्यांचे संपूर्ण तपशील यासोबत जोडलेल्या मालमत्तेच्या परिशिष्टात वर्णन केले आहे आणि यापुढे त्यांना \"दावा मालमत्ता\" असे संबोधले जाईल.\n\n4.  वादीच्या वडिलांच्या निधनानंतर, वादी आणि प्रतिवादी, एकमेव कायदेशीर वारस असल्याने, दावा मालमत्तेचे संयुक्तपणे वारसदार झाले आणि त्यांच्या संयुक्त ताब्यात आहेत. वादी आणि प्रतिवादी प्रत्येकी दावा मालमत्तेमध्ये समान, अविभाजित एक-अर्धा (1/2) हक्काचे पात्र आहेत.\n\n5.  अलीकडे, प्रतिवादीचे वादीप्रतीचे वर्तन शत्रुत्वपूर्ण झाले आहे. प्रतिवादी वादीला कौटुंबिक घरातून बेदखल करण्याचा प्रयत्न करत आहे आणि दावा मालमत्ता तिच्या चुलत भावाच्या नावावर हस्तांतरित करण्याची स्पष्ट धमकी दिली आहे, ज्यामुळे वादीला तिच्या कायदेशीर आणि वैध हक्कापासून वंचित ठेवले जात आहे.\n\n6.  वादीचे नाव अद्याप अधिकृत मालमत्ता नोंदींमध्ये समाविष्ट झालेले नाही आणि प्रतिवादी या वस्तुस्थितीचा गैरफायदा घेऊन वादीला तिच्या वारसा हक्कापासून बेकायदेशीरपणे आणि चुकीच्या पद्धतीने वगळत आहे.\n\n7.  जेव्हा प्रतिवादीने वादीला बेदखल करण्याची आणि दावा मालमत्तेची विल्हेवाट लावण्याची धमकी दिली, तेव्हा दावा कारण नुकतेच उद्भवले. वादीने तिच्या हक्काची मागणी केली, परंतु प्रतिवादीने मालमत्तेची विभागणी करण्यास नकार दिला, ज्यामुळे हा दावा दाखल करणे आवश्यक झाले आहे.\n\n## कायदेशीर तरतुदी\n\n8.  दाव्यातील पक्षकार हिंदू आहेत आणि मृताच्या मालमत्तेचे उत्तराधिकार Hindu Succession Act, 1956 च्या तरतुदींद्वारे नियंत्रित केले जाते.\n\n9.  वादी, दत्तक कन्या असल्याने, दत्तक कुटुंबात नैसर्गिकरित्या जन्मलेल्या कन्येचे सर्व हक्क आणि विशेषाधिकार तिला प्राप्त आहेत. Section 12 of the Hindu Adoptions and Maintenance Act, 1956 नुसार, दत्तक मूल दत्तक घेतल्याच्या तारखेपासून सर्व हेतूंसाठी त्याच्या दत्तक वडिलांचे किंवा आईचे मूल मानले जाते.\n\n10. Section 8 of the Hindu Succession Act, 1956 नुसार, मृत्युपत्र न करता मरण पावलेल्या हिंदू पुरुषाची मालमत्ता अनुसूचीतील वर्ग I मध्ये नमूद केलेल्या वारसांना मिळते. वादी (कन्या) आणि प्रतिवादी (विधवा) हे मृताचे एकमेव वर्ग I वारस आहेत.\n\n11. परिणामी, वादी आणि प्रतिवादी प्रत्येकी मृताने मागे ठेवलेल्या दावा मालमत्तेमध्ये समान एक-अर्धा (1/2) हक्काचे पात्र आहेत.\n\n12. प्रतिवादीने तृतीय-पक्ष हक्क निर्माण करण्याचा आणि दावा मालमत्तेची विल्हेवाट लावण्याचा प्रयत्न केल्यामुळे, प्रतिवादी, तिचे एजंट किंवा तिच्या वतीने कार्य करणार्‍या कोणालाही या दाव्याच्या अंतिम निकालापर्यंत दावा मालमत्तेची विक्री, हस्तांतरण, गहाण ठेवणे किंवा कोणताही तृतीय-पक्ष हितसंबंध निर्माण करण्यापासून रोखण्यासाठी तात्पुरता मनाई हुकूम देणे आवश्यक आहे, जसे की Order XXXIX Rules 1 and 2 of the Code of Civil Procedure, 1908 अंतर्गत प्रदान केले आहे.\n\n## वादीचा हक्क आणि हिस्सा\n\n13. वादी असे सादर करते की, कै. [मृत वडिलांचे नाव] यांची कायदेशीररित्या दत्तक कन्या असल्याने, तिला दत्तक कुटुंबात नैसर्गिकरित्या जन्मलेल्या कन्येचे सर्व हक्क आणि दर्जा प्राप्त आहे. हा दर्जा Section 12 of the Hindu Adoptions and Maintenance Act, 1956 अंतर्गत स्पष्टपणे स्थापित केला आहे, जो जन्म कुटुंबाशी असलेले सर्व संबंध तोडतो आणि दत्तक कुटुंबात संबंधित हक्क निर्माण करतो.\n\n14. वादीचे दत्तक वडील, कै. [मृत वडिलांचे नाव], मृत्युपत्र न करता मरण पावले. त्यामुळे, त्यांच्या स्व-अधिग्रहित मालमत्तेचे उत्तराधिकार Hindu Succession Act, 1956 मध्ये नमूद केलेल्या मृत्युपत्रहीन उत्तराधिकाराच्या नियमांनुसार नियंत्रित केले जाते.\n\n15. Section 8 of the Hindu Succession Act, 1956 नुसार, मृत्युपत्र न करता मरण पावलेल्या हिंदू पुरुषाची मालमत्ता सर्वप्रथम उक्त अधिनियमाच्या अनुसूचीतील वर्ग I मध्ये नमूद केलेल्या वारसांना मिळते. वादी, कन्या असल्याने, आणि प्रतिवादी, मृताची विधवा असल्याने, हे एकमेव दोन हयात वर्ग I कायदेशीर वारस आहेत.\n\n16. कायद्याच्या उपरोक्त तरतुदींच्या आधारे, वादी आणि प्रतिवादी प्रत्येकी दावा मालमत्तेमध्ये समान, अविभाजित एक-अर्धा (1/2) हक्काचे पात्र आहेत. त्यामुळे, वादी प्रतिवादीसोबत दावा मालमत्तेची सह-मालक आहे आणि तिच्या संयुक्त ताब्यात आहे.\n\n## तात्पुरत्या मनाई हुकुमाची कारणे\n\n17. वादीच्या बाजूने एक मजबूत प्रथमदर्शनी प्रकरण आहे, कारण दावा मालमत्तेमधील तिच्या एक-अर्धा (1/2) हक्काची पात्रता Hindu Succession Act, 1956 च्या तरतुदींद्वारे स्पष्टपणे स्थापित झाली आहे. प्रतिवादीला वादीचा कायदेशीर हिस्सा नाकारण्याचा कोणताही कायदेशीर अधिकार नाही.\n\n18. सोयीचा समतोल मोठ्या प्रमाणात वादीच्या बाजूने आहे. मनाई हुकूम दिल्यास प्रतिवादीला पक्षकारांच्या हक्कांचा निर्णय होईपर्यंत मालमत्तेची सद्यस्थिती राखणे आवश्यक होईल, ज्यामुळे प्रतिवादीचे कोणतेही नुकसान होणार नाही. याउलट, मनाई हुकूम नाकारल्यास वादीला प्रतिवादीच्या चुकीच्या कृत्यांमुळे तिचा कायदेशीर वारसा गमावण्याचा धोका निर्माण होईल.\n\n19. तात्पुरता मनाई हुकूम न दिल्यास वादीला भरून न येणारे नुकसान आणि इजा होईल. प्रतिवादीने दावा मालमत्ता तिच्या चुलत\nभावाच्या नावावर हस्तांतरित करून त्यांची विल्हेवाट लावण्याची स्पष्ट धमकी दिली आहे. जर प्रतिवादीला या माननीय न्यायालयाच्या\nआदेशाने रोखले नाही आणि तिने तृतीय-पक्ष हितसंबंध निर्माण करण्यात यश मिळवले, तर यामुळे कायदेशीर कार्यवाहीची वाढ होईल आणि\nया दाव्यात पारित केलेला कोणताही हुकूम निरर्थक ठरेल. कौटुंबिक निवासी मालमत्तेतील वादीच्या हक्काचे नुकसान आर्थिकदृष्ट्या\nपुरेसे भरून काढता येणार नाही.\n\n20. त्यामुळे, न्याय हितासाठी हे न्यायसंगत, न्याय्य आणि आवश्यक आहे की प्रतिवादी, तिचे एजंट, नोकर किंवा तिच्याद्वारे दावा करणारी कोणतीही व्यक्ती या दाव्याच्या अंतिम सुनावणी आणि निकालापर्यंत, यासोबत जोडलेल्या अनुसूचीमध्ये वर्णन केलेल्या दावा मालमत्तेची विक्री, हस्तांतरण, विल्हेवाट लावणे, गहाण ठेवणे, भेट देणे किंवा कोणत्याही स्वरूपाचे तृतीय-पक्ष हक्क किंवा हितसंबंध निर्माण करण्यापासून तात्पुरत्या मनाई हुकुमाने रोखले जावे.\n\n## अधिकारक्षेत्र आणि मूल्यांकन\n\n21. दावा मालमत्ता पुणे येथे स्थित आहे, जे या माननीय न्यायालयाच्या प्रादेशिक आणि आर्थिक अधिकारक्षेत्रात येते. त्यामुळे, या माननीय न्यायालयाला हा दावा स्वीकारण्याचा, त्याची सुनावणी करण्याचा आणि त्याचा निपटारा करण्याचा अधिकार आहे.\n\n22. अधिकारक्षेत्राच्या उद्देशासाठी, दावा मालमत्तेमधील वादीच्या एक-अर्धा (1/2) हक्काच्या बाजार मूल्यावर दाव्याचे मूल्यांकन केले आहे, जे अंदाजे Rs. [वादीच्या 1/2 हक्काचे मूल्य] आहे.\n\n23. न्यायालयीन शुल्काच्या उद्देशासाठी, वादी दावा मालमत्तेच्या संयुक्त ताब्यात असल्याने, विभागणीचा दावा Section 6(1)(vii) of the Maharashtra Court Fees Act, 1959 अंतर्गत मूल्यांकित केला आहे आणि त्यावर आवश्यक निश्चित न्यायालयीन शुल्क भरले आहे. मनाई हुकुमाचा दिलासा हा परिणामी दिलासा आहे आणि त्यावर कोणतेही स्वतंत्र न्यायालयीन शुल्क देय नाही.\n\n## प्रार्थना\n\nयास्तव, वादी अत्यंत नम्रपणे प्रार्थना करते की, या माननीय न्यायालयाने कृपया खालीलप्रमाणे आदेश द्यावे:\n\n(अ) यासोबत जोडलेल्या अनुसूचीमध्ये वर्णन केलेल्या दावा मालमत्तेच्या विभागणीसाठी प्रारंभिक हुकूम पारित करावा, ज्यात वादी उक्त मालमत्तेमध्ये समान, अविभाजित एक-अर्धा (1/2) हक्कासाठी पात्र आहे असे घोषित करावे.\n\n(ब) दावा मालमत्तेची सीमा निश्चित करून विभागणी करण्याचे निर्देश द्यावे आणि त्यानंतर वादीला तिच्या एक-अर्धा (1/2) हक्काच्या स्वतंत्र, रिक्त आणि शांततापूर्ण ताब्यात ठेवण्यासाठी अंतिम हुकूम पारित करावा.\n\n(क) प्रतिवादी, तिचे एजंट, नोकर किंवा तिच्याद्वारे किंवा तिच्या अंतर्गत दावा करणारी कोणतीही व्यक्ती यांना दावा मालमत्तेची विक्री, हस्तांतरण, विल्हेवाट लावणे, गहाण ठेवणे, भेट देणे किंवा कोणत्याही स्वरूपाचे तृतीय-पक्ष हक्क किंवा हितसंबंध निर्माण करण्यापासून कायमस्वरूपी मनाई हुकुमाचा आदेश द्यावा.\n\n(ड) या दाव्याच्या सुनावणी आणि अंतिम निकालापर्यंत, प्रतिवादी, तिचे एजंट, नोकर किंवा तिच्याद्वारे दावा करणारी कोणतीही व्यक्ती यांना दावा मालमत्तेची विक्री, हस्तांतरण, विल्हेवाट लावणे, गहाण ठेवणे, भेट देणे किंवा कोणत्याही स्वरूपाचे तृतीय-पक्ष हक्क किंवा हितसंबंध निर्माण करण्यापासून तात्पुरता मनाई हुकूम मंजूर करावा.\n\n(इ) या दाव्याचा खर्च प्रतिवादीकडून वादीला मिळावा\n\n\n---\n**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.",
  "token_usage": {
    "input_tokens": 9746,
    "output_tokens": 338,
    "total_tokens": 10084,
    "cache_read_tokens": 6140,
    "cache_creation_tokens": 0,
    "reasoning_tokens": 0,
    "cost_usd": 0.00111,
    "by_agent": {
      "Memory": {
        "input": 2581,
        "output": 10,
        "total": 2591,
        "cache_read": 0,
        "calls": 1,
        "cost_usd": 0.000262
      },
      "Orchestrator": {
        "input": 7165,
        "output": 328,
        "total": 7493,
        "cache_read": 6140,
        "calls": 2,
        "cost_usd": 0.000848
      }
    },
    "calls": [
      {
        "agent": "Memory",
        "step": "rewrite_query",
        "input": 2581,
        "output": 10,
        "total": 2591,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000262
      },
      {
        "agent": "Orchestrator",
        "step": "classify_and_plan",
        "input": 1374,
        "output": 61,
        "total": 1435,
        "cache_read": 746,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000162
      },
      {
        "agent": "Orchestrator",
        "step": "extract_user_intent",
        "input": 5791,
        "output": 267,
        "total": 6058,
        "cache_read": 5394,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000686
      }
    ]
  },
  "source": [
    {
      "source_type": "drafting",
      "title": "SUIT FOR PARTITION AND SEPARATE POSSESSION",
      "content": [
        "Draft Heading: Specimen/Draft/Format of SUIT FOR PARTITION AND SEPARATE POSSESSION.\nDraft Content: IN THE COURT OF THE PRINCIPAL CIVIL JUDGE, (City Name), (District Name), (State Name)\n                                                                                SUIT NO. _________ OF 2023\n\n(Plaint"
      ],
      "doc_link": null,
      "file_name": "/content/formats_and_all_drafts/SUIT FOR PARTITION AND SEPARATE POSSESSION.csv",
      "agent_name": "Drafting",
      "relevance_score": null,
      "template_type": "SUIT FOR PARTITION AND SEPARATE POSSESSION"
    },
    {
      "source_type": "drafting",
      "title": "SUIT FOR PARTITION AND SEPARATE POSSESSION",
      "content": [
        "Draft Heading: Specimen/Draft/Format of SUIT FOR PARTITION AND SEPARATE POSSESSION.\nDraft Content: IN THE COURT OF THE PRINCIPAL CIVIL JUDGE, (City Name), (District Name), (State Name)\n                                                                                SUIT NO. _________ OF 2023\n\n(Plaint"
      ],
      "doc_link": null,
      "file_name": "/content/formats_and_all_drafts/SUIT FOR PARTITION AND SEPARATE POSSESSION.csv",
      "agent_name": "Drafting",
      "relevance_score": null,
      "template_type": "SUIT FOR PARTITION AND SEPARATE POSSESSION"
    },
    {
      "source_type": "scenario",
      "title": "AI-Generated Legal Analysis",
      "content": [
        "Response generated by AI with web search grounding"
      ],
      "doc_link": null,
      "file_name": null,
      "agent_name": "Scenario",
      "relevance_score": null
    }
  ],
  "agents_used": [
    "Drafting",
    "Scenario"
  ],
  "effective_query": "वरील मजकूर मराठीत रूपांतरित करा.",
  "query_rewritten": true,
  "conversation_turn": 2,
  "followup_suggestions": [
    "What is partition suit?",
    "What's a temporary injunction?",
    "Who is the Civil Judge?"
  ]
}
```

#### Result (rendered markdown)

<details>
<summary>Full response - 17798 chars</summary>

## Cause Title

IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE

CIVIL SUIT NO. ______ OF 2024

Rupa D/o. Late [Deceased Father's Name],
Age: [Age of Rupa],
Occupation: [Occupation of Rupa],
Residing at: [Address of Rupa], Pune.

.....Plaintiff

**vs**

[Name of Mother] W/o. Late [Deceased Father's Name],
Age: [Age of Mother],
Occupation: [Occupation of Mother],
Residing at: [Address of Mother], Pune.

.....Defendant

## Suit for Partition and Temporary Injunction

SUIT FOR PARTITION, DECLARATION OF SHARE, PERMANENT AND TEMPORARY INJUNCTION

The Plaintiff above-named submits as under:

## Facts of the Case

1. That the Plaintiff is the adopted daughter of Late [Deceased Father's Name] and the Defendant, [Name of Mother]. The Plaintiff has no siblings. The parties are Hindus and are governed by Hindu Law.

2. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate approximately 5 years ago, leaving behind the Plaintiff and the Defendant as his only Class I legal heirs.

3. That during his lifetime, the deceased father of the Plaintiff was the absolute owner of two residential flats situated in Pune, the full particulars of which are described in the Schedule of Property annexed hereto and hereinafter referred to as the "Suit Properties".

4. That upon the demise of the Plaintiff's father, the Plaintiff and the Defendant, being the sole legal heirs, succeeded to the Suit Properties jointly and have been in joint possession thereof. The Plaintiff and Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties.

5. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the family home and has explicitly threatened to transfer the Suit Properties to her cousin, thereby depriving the Plaintiff of her rightful and legitimate share.

6. That the Plaintiff's name has not yet been mutated in the official property records, and the Defendant is taking undue advantage of this fact to illegally and wrongfully exclude the Plaintiff from her inheritance.

7. That the cause of action arose recently when the Defendant threatened to dispossess the Plaintiff and alienate the Suit Properties. The Plaintiff demanded her share, but the Defendant has refused to partition the properties, necessitating the filing of the present suit.

## Legal Provisions

8. That the parties to the suit are Hindus, and the succession to the estate of the deceased is governed by the provisions of the Hindu Succession Act, 1956.

9. That the Plaintiff, being the adopted daughter, has all the rights and privileges of a natural-born daughter in the adoptive family. As per Section 12 of the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption.

10. That as per Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves upon the heirs specified in Class I of the Schedule. The Plaintiff (daughter) and the Defendant (widow) are the only Class I heirs of the deceased.

11. That consequently, the Plaintiff and the Defendant are each entitled to an equal one-half (1/2) share in the Suit Properties left by the deceased.

12. That in light of the Defendant's attempts to create third-party rights and alienate the Suit Properties, it is imperative to grant a temporary injunction restraining the Defendant, her agents, or anyone acting on her behalf from selling, transferring, mortgaging, or creating any third-party interest in the Suit Properties pending the final disposal of this suit, as provided under Order XXXIX Rules 1 and 2 of the Code of Civil Procedure, 1908.

## Plaintiff's Entitlement and Share

13. That the Plaintiff submits that as the legally adopted daughter of the Late [Deceased Father's Name], she is conferred with all the rights and status of a natural-born daughter within the adoptive family. This status is unequivocally established under Section 12 of the Hindu Adoptions and Maintenance Act, 1956, which severs all ties with the birth family and creates corresponding rights in the adoptive family.

14. That the Plaintiff's adoptive father, Late [Deceased Father's Name], passed away intestate. Therefore, the succession to his self-acquired properties is governed by the rules of intestate succession laid down in the Hindu Succession Act, 1956.

15. That in accordance with Section 8 of the Hindu Succession Act, 1956, the property of a Hindu male dying intestate devolves firstly upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the daughter, and the Defendant, being the widow of the deceased, are the only two surviving Class I legal heirs.

16. That by virtue of the aforesaid provisions of law, the Plaintiff and the Defendant are each entitled to an equal, undivided one-half (1/2) share in the Suit Properties. The Plaintiff is, therefore, a co-owner of the Suit Properties along with the Defendant and is in joint possession of the same.

## Grounds for Temporary Injunction

17. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the Suit Properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no legal right to deny the Plaintiff her legitimate share.

18. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. Granting an injunction would only require the Defendant to maintain the status quo of the properties until the rights of the parties are adjudicated, causing no prejudice to the Defendant. Conversely, refusing the injunction would expose the Plaintiff to the risk of losing her rightful inheritance through the Defendant's wrongful acts.

19. That the Plaintiff will suffer irreparable loss and injury if a temporary injunction is not granted. The Defendant
has explicitly threatened to alienate the Suit Properties by transferring them to her cousin. If the Defendant is not
restrained by an order of this Hon'ble Court and succeeds in creating third-party interests, it will lead to a
multiplicity of legal proceedings and render any decree passed in this suit futile. The loss of the Plaintiff's share in
the family's residential properties cannot be adequately compensated in monetary terms.

20. That it is therefore just, equitable, and necessary in the interest of justice that the Defendant, her agents, servants, or any person claiming through her be restrained by an order of temporary injunction from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties described in the Schedule hereto, pending the final hearing and disposal of this suit.

## Jurisdiction and Valuation

21. That the Suit Properties are situated in Pune, which is within the territorial and pecuniary jurisdiction of this Hon'ble Court. Therefore, this Hon'ble Court has the jurisdiction to entertain, try, and dispose of the present suit.

22. That for the purpose of jurisdiction, the suit is valued at the market value of the Plaintiff's one-half (1/2) share in the Suit Properties, which is estimated at Rs. [Value of Plaintiff's 1/2 Share].

23. That for the purpose of court fees, as the Plaintiff is in joint possession of the Suit Properties, the claim for partition is valued under Section 6(1)(vii) of the Maharashtra Court Fees Act, 1959, and the requisite fixed court fee has been paid thereon. The relief of injunction is a consequential relief, and no separate court fee is payable.

## Prayer

WHEREFORE, the Plaintiff most humbly prays that this Hon'ble Court may be pleased to:

(a) Pass a preliminary decree for partition of the Suit Properties described in the Schedule hereto, declaring that the Plaintiff is entitled to an equal, undivided one-half (1/2) share in the said properties.

(b) Direct that the Suit Properties be partitioned by metes and bounds, and thereafter pass a final decree putting the Plaintiff in separate, vacant, and peaceful possession of her one-half (1/2) share.

(c) Pass a decree of permanent injunction restraining the Defendant, her agents, servants, or any person claiming through or under her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.

(d) Pending the hearing and final disposal of this suit, grant a temporary injunction restraining the Defendant, her agents, servants, or any person claiming through her, from selling, transferring, alienating, mortgaging, gifting, or creating any third-party rights or interest of any nature whatsoever in the Suit Properties.

(e) Award the costs of this suit to the Plaintiff from the Defendant.

(f) Grant such other and further reliefs as this Hon'ble Court may deem fit and proper in the facts and circumstances of the case.

## Verification

I, Rupa, the Plaintiff above-named, do hereby verify that the contents of the foregoing plaint in paragraphs 1 to 23 are true and correct to the best of my knowledge, information, and belief, and that I have concealed nothing material therefrom.

Verified at Pune on this ______ day of _______________, 2024.

(Rupa)
PLAINTIFF

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
सिव्हिल जज सीनियर डिव्हिजन, पुणे यांच्या न्यायालयात

सिव्हिल सूट क्रमांक ______ ऑफ 2024

रूपा डी/ओ. कै. [मृत वडिलांचे नाव],
वय: [रूपाचे वय],
व्यवसाय: [रूपाचा व्यवसाय],
रा. [रूपाचा पत्ता], पुणे.

.....वादी

**विरुद्ध**

[आईचे नाव] डब्लू/ओ. कै. [मृत वडिलांचे नाव],
वय: [आईचे वय],
व्यवसाय: [आईचा व्यवसाय],
रा. [आईचा पत्ता], पुणे.

.....प्रतिवादी

## विभागणी आणि तात्पुरत्या मनाई हुकुमासाठीचा दावा

विभागणी, हक्काची घोषणा, कायमस्वरूपी आणि तात्पुरत्या मनाई हुकुमासाठीचा दावा

वरील नमूद वादी खालीलप्रमाणे विनंती करते:

## प्रकरणाची तथ्ये

1.  वादी कै. [मृत वडिलांचे नाव] आणि प्रतिवादी, [आईचे नाव] यांची दत्तक कन्या आहे. वादीला भावंड नाहीत. पक्षकार हिंदू असून हिंदू कायद्याने शासित आहेत.

2.  वादीचे दत्तक वडील, कै. [मृत वडिलांचे नाव], अंदाजे 5 वर्षांपूर्वी मृत्युपत्र न करता मरण पावले, त्यांच्यामागे वादी आणि प्रतिवादी हे त्यांचे एकमेव वर्ग I कायदेशीर वारस म्हणून राहिले.

3.  त्यांच्या हयातीत, वादीचे मृत वडील पुणे येथे असलेल्या दोन निवासी सदनिकांचे पूर्ण मालक होते, ज्यांचे संपूर्ण तपशील यासोबत जोडलेल्या मालमत्तेच्या परिशिष्टात वर्णन केले आहे आणि यापुढे त्यांना "दावा मालमत्ता" असे संबोधले जाईल.

4.  वादीच्या वडिलांच्या निधनानंतर, वादी आणि प्रतिवादी, एकमेव कायदेशीर वारस असल्याने, दावा मालमत्तेचे संयुक्तपणे वारसदार झाले आणि त्यांच्या संयुक्त ताब्यात आहेत. वादी आणि प्रतिवादी प्रत्येकी दावा मालमत्तेमध्ये समान, अविभाजित एक-अर्धा (1/2) हक्काचे पात्र आहेत.

5.  अलीकडे, प्रतिवादीचे वादीप्रतीचे वर्तन शत्रुत्वपूर्ण झाले आहे. प्रतिवादी वादीला कौटुंबिक घरातून बेदखल करण्याचा प्रयत्न करत आहे आणि दावा मालमत्ता तिच्या चुलत भावाच्या नावावर हस्तांतरित करण्याची स्पष्ट धमकी दिली आहे, ज्यामुळे वादीला तिच्या कायदेशीर आणि वैध हक्कापासून वंचित ठेवले जात आहे.

6.  वादीचे नाव अद्याप अधिकृत मालमत्ता नोंदींमध्ये समाविष्ट झालेले नाही आणि प्रतिवादी या वस्तुस्थितीचा गैरफायदा घेऊन वादीला तिच्या वारसा हक्कापासून बेकायदेशीरपणे आणि चुकीच्या पद्धतीने वगळत आहे.

7.  जेव्हा प्रतिवादीने वादीला बेदखल करण्याची आणि दावा मालमत्तेची विल्हेवाट लावण्याची धमकी दिली, तेव्हा दावा कारण नुकतेच उद्भवले. वादीने तिच्या हक्काची मागणी केली, परंतु प्रतिवादीने मालमत्तेची विभागणी करण्यास नकार दिला, ज्यामुळे हा दावा दाखल करणे आवश्यक झाले आहे.

## कायदेशीर तरतुदी

8.  दाव्यातील पक्षकार हिंदू आहेत आणि मृताच्या मालमत्तेचे उत्तराधिकार Hindu Succession Act, 1956 च्या तरतुदींद्वारे नियंत्रित केले जाते.

9.  वादी, दत्तक कन्या असल्याने, दत्तक कुटुंबात नैसर्गिकरित्या जन्मलेल्या कन्येचे सर्व हक्क आणि विशेषाधिकार तिला प्राप्त आहेत. Section 12 of the Hindu Adoptions and Maintenance Act, 1956 नुसार, दत्तक मूल दत्तक घेतल्याच्या तारखेपासून सर्व हेतूंसाठी त्याच्या दत्तक वडिलांचे किंवा आईचे मूल मानले जाते.

10. Section 8 of the Hindu Succession Act, 1956 नुसार, मृत्युपत्र न करता मरण पावलेल्या हिंदू पुरुषाची मालमत्ता अनुसूचीतील वर्ग I मध्ये नमूद केलेल्या वारसांना मिळते. वादी (कन्या) आणि प्रतिवादी (विधवा) हे मृताचे एकमेव वर्ग I वारस आहेत.

11. परिणामी, वादी आणि प्रतिवादी प्रत्येकी मृताने मागे ठेवलेल्या दावा मालमत्तेमध्ये समान एक-अर्धा (1/2) हक्काचे पात्र आहेत.

12. प्रतिवादीने तृतीय-पक्ष हक्क निर्माण करण्याचा आणि दावा मालमत्तेची विल्हेवाट लावण्याचा प्रयत्न केल्यामुळे, प्रतिवादी, तिचे एजंट किंवा तिच्या वतीने कार्य करणार्‍या कोणालाही या दाव्याच्या अंतिम निकालापर्यंत दावा मालमत्तेची विक्री, हस्तांतरण, गहाण ठेवणे किंवा कोणताही तृतीय-पक्ष हितसंबंध निर्माण करण्यापासून रोखण्यासाठी तात्पुरता मनाई हुकूम देणे आवश्यक आहे, जसे की Order XXXIX Rules 1 and 2 of the Code of Civil Procedure, 1908 अंतर्गत प्रदान केले आहे.

## वादीचा हक्क आणि हिस्सा

13. वादी असे सादर करते की, कै. [मृत वडिलांचे नाव] यांची कायदेशीररित्या दत्तक कन्या असल्याने, तिला दत्तक कुटुंबात नैसर्गिकरित्या जन्मलेल्या कन्येचे सर्व हक्क आणि दर्जा प्राप्त आहे. हा दर्जा Section 12 of the Hindu Adoptions and Maintenance Act, 1956 अंतर्गत स्पष्टपणे स्थापित केला आहे, जो जन्म कुटुंबाशी असलेले सर्व संबंध तोडतो आणि दत्तक कुटुंबात संबंधित हक्क निर्माण करतो.

14. वादीचे दत्तक वडील, कै. [मृत वडिलांचे नाव], मृत्युपत्र न करता मरण पावले. त्यामुळे, त्यांच्या स्व-अधिग्रहित मालमत्तेचे उत्तराधिकार Hindu Succession Act, 1956 मध्ये नमूद केलेल्या मृत्युपत्रहीन उत्तराधिकाराच्या नियमांनुसार नियंत्रित केले जाते.

15. Section 8 of the Hindu Succession Act, 1956 नुसार, मृत्युपत्र न करता मरण पावलेल्या हिंदू पुरुषाची मालमत्ता सर्वप्रथम उक्त अधिनियमाच्या अनुसूचीतील वर्ग I मध्ये नमूद केलेल्या वारसांना मिळते. वादी, कन्या असल्याने, आणि प्रतिवादी, मृताची विधवा असल्याने, हे एकमेव दोन हयात वर्ग I कायदेशीर वारस आहेत.

16. कायद्याच्या उपरोक्त तरतुदींच्या आधारे, वादी आणि प्रतिवादी प्रत्येकी दावा मालमत्तेमध्ये समान, अविभाजित एक-अर्धा (1/2) हक्काचे पात्र आहेत. त्यामुळे, वादी प्रतिवादीसोबत दावा मालमत्तेची सह-मालक आहे आणि तिच्या संयुक्त ताब्यात आहे.

## तात्पुरत्या मनाई हुकुमाची कारणे

17. वादीच्या बाजूने एक मजबूत प्रथमदर्शनी प्रकरण आहे, कारण दावा मालमत्तेमधील तिच्या एक-अर्धा (1/2) हक्काची पात्रता Hindu Succession Act, 1956 च्या तरतुदींद्वारे स्पष्टपणे स्थापित झाली आहे. प्रतिवादीला वादीचा कायदेशीर हिस्सा नाकारण्याचा कोणताही कायदेशीर अधिकार नाही.

18. सोयीचा समतोल मोठ्या प्रमाणात वादीच्या बाजूने आहे. मनाई हुकूम दिल्यास प्रतिवादीला पक्षकारांच्या हक्कांचा निर्णय होईपर्यंत मालमत्तेची सद्यस्थिती राखणे आवश्यक होईल, ज्यामुळे प्रतिवादीचे कोणतेही नुकसान होणार नाही. याउलट, मनाई हुकूम नाकारल्यास वादीला प्रतिवादीच्या चुकीच्या कृत्यांमुळे तिचा कायदेशीर वारसा गमावण्याचा धोका निर्माण होईल.

19. तात्पुरता मनाई हुकूम न दिल्यास वादीला भरून न येणारे नुकसान आणि इजा होईल. प्रतिवादीने दावा मालमत्ता तिच्या चुलत
भावाच्या नावावर हस्तांतरित करून त्यांची विल्हेवाट लावण्याची स्पष्ट धमकी दिली आहे. जर प्रतिवादीला या माननीय न्यायालयाच्या
आदेशाने रोखले नाही आणि तिने तृतीय-पक्ष हितसंबंध निर्माण करण्यात यश मिळवले, तर यामुळे कायदेशीर कार्यवाहीची वाढ होईल आणि
या दाव्यात पारित केलेला कोणताही हुकूम निरर्थक ठरेल. कौटुंबिक निवासी मालमत्तेतील वादीच्या हक्काचे नुकसान आर्थिकदृष्ट्या
पुरेसे भरून काढता येणार नाही.

20. त्यामुळे, न्याय हितासाठी हे न्यायसंगत, न्याय्य आणि आवश्यक आहे की प्रतिवादी, तिचे एजंट, नोकर किंवा तिच्याद्वारे दावा करणारी कोणतीही व्यक्ती या दाव्याच्या अंतिम सुनावणी आणि निकालापर्यंत, यासोबत जोडलेल्या अनुसूचीमध्ये वर्णन केलेल्या दावा मालमत्तेची विक्री, हस्तांतरण, विल्हेवाट लावणे, गहाण ठेवणे, भेट देणे किंवा कोणत्याही स्वरूपाचे तृतीय-पक्ष हक्क किंवा हितसंबंध निर्माण करण्यापासून तात्पुरत्या मनाई हुकुमाने रोखले जावे.

## अधिकारक्षेत्र आणि मूल्यांकन

21. दावा मालमत्ता पुणे येथे स्थित आहे, जे या माननीय न्यायालयाच्या प्रादेशिक आणि आर्थिक अधिकारक्षेत्रात येते. त्यामुळे, या माननीय न्यायालयाला हा दावा स्वीकारण्याचा, त्याची सुनावणी करण्याचा आणि त्याचा निपटारा करण्याचा अधिकार आहे.

22. अधिकारक्षेत्राच्या उद्देशासाठी, दावा मालमत्तेमधील वादीच्या एक-अर्धा (1/2) हक्काच्या बाजार मूल्यावर दाव्याचे मूल्यांकन केले आहे, जे अंदाजे Rs. [वादीच्या 1/2 हक्काचे मूल्य] आहे.

23. न्यायालयीन शुल्काच्या उद्देशासाठी, वादी दावा मालमत्तेच्या संयुक्त ताब्यात असल्याने, विभागणीचा दावा Section 6(1)(vii) of the Maharashtra Court Fees Act, 1959 अंतर्गत मूल्यांकित केला आहे आणि त्यावर आवश्यक निश्चित न्यायालयीन शुल्क भरले आहे. मनाई हुकुमाचा दिलासा हा परिणामी दिलासा आहे आणि त्यावर कोणतेही स्वतंत्र न्यायालयीन शुल्क देय नाही.

## प्रार्थना

यास्तव, वादी अत्यंत नम्रपणे प्रार्थना करते की, या माननीय न्यायालयाने कृपया खालीलप्रमाणे आदेश द्यावे:

(अ) यासोबत जोडलेल्या अनुसूचीमध्ये वर्णन केलेल्या दावा मालमत्तेच्या विभागणीसाठी प्रारंभिक हुकूम पारित करावा, ज्यात वादी उक्त मालमत्तेमध्ये समान, अविभाजित एक-अर्धा (1/2) हक्कासाठी पात्र आहे असे घोषित करावे.

(ब) दावा मालमत्तेची सीमा निश्चित करून विभागणी करण्याचे निर्देश द्यावे आणि त्यानंतर वादीला तिच्या एक-अर्धा (1/2) हक्काच्या स्वतंत्र, रिक्त आणि शांततापूर्ण ताब्यात ठेवण्यासाठी अंतिम हुकूम पारित करावा.

(क) प्रतिवादी, तिचे एजंट, नोकर किंवा तिच्याद्वारे किंवा तिच्या अंतर्गत दावा करणारी कोणतीही व्यक्ती यांना दावा मालमत्तेची विक्री, हस्तांतरण, विल्हेवाट लावणे, गहाण ठेवणे, भेट देणे किंवा कोणत्याही स्वरूपाचे तृतीय-पक्ष हक्क किंवा हितसंबंध निर्माण करण्यापासून कायमस्वरूपी मनाई हुकुमाचा आदेश द्यावा.

(ड) या दाव्याच्या सुनावणी आणि अंतिम निकालापर्यंत, प्रतिवादी, तिचे एजंट, नोकर किंवा तिच्याद्वारे दावा करणारी कोणतीही व्यक्ती यांना दावा मालमत्तेची विक्री, हस्तांतरण, विल्हेवाट लावणे, गहाण ठेवणे, भेट देणे किंवा कोणत्याही स्वरूपाचे तृतीय-पक्ष हक्क किंवा हितसंबंध निर्माण करण्यापासून तात्पुरता मनाई हुकूम मंजूर करावा.

(इ) या दाव्याचा खर्च प्रतिवादीकडून वादीला मिळावा


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

---

## Endpoint: LOCAL

- **URL**: `http://localhost:5000/pyapi/search`
- **thread_id**: `2a6a8314-5c1d-4e6f-896b-76b9fe96fc96`
- **run started**: 2026-08-17T14:32:38.720716+00:00

### Summary

| Turn | Label | Status | Time (s) | Agents | Chars | Devanagari | Latin |
|------|-------|--------|----------|--------|-------|------------|-------|
| 1 | prompt-1-mega | 200 | 116.14 | Drafting | 12084 | 0 | 9443 |
| 2 | prompt-2-convert-to-marathi | 200 | 16.44 | Drafting, Scenario | 15420 | 2694 | 9524 |

### Turn 1 - prompt-1-mega

- **Started**: 2026-08-17T14:32:38.720742+00:00
- **Elapsed**: 116.14 s
- **HTTP Status**: 200

#### Request

```http
POST http://localhost:5000/pyapi/search
X-API-Key: ***
Content-Type: application/json

{
  "Promptquery": "prompt 1: Research Prompt: Section 125 of CrPC\n \nScenario:\n \nDraft a civil suit for partition along with a prayer for temporary injunction. Facts: Rupa is an adopted daughter of the deceased owner. She has no siblings. Her father died 5 years ago, leaving behind two flats in Pune. The only legal heirs are Rupa and her mother. Her name is not yet on the property documents. Her mother is attempting to dispossess her and has expressed intent to transfer the properties to her cousin. The suit should include legal provisions for partition of the properties, declaration of her share, and a temporary injunction restraining the mother or any third party from selling, transferring, or creating third-party rights in the properties until disposal of the suit.\n \nDraft a civil suit for damages arising from medical negligence, incorporating detailed legal reasoning and relevant case laws. Scenario: My client, Mrs. Anjali Deshmukh, aged 42, and a resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the procedure, the attending surgeon, Dr. Rakesh Nair, negligently caused damage to her bile duct. Following the surgery, she experienced severe abdominal pain and had to be admitted to another hospital for corrective surgery. As a result, she incurred additional medical expenses amounting to ₹3.5 lakhs and endured significant physical pain and mental agony. Despite repeated follow-ups, both the hospital and the doctor have refused to acknowledge their negligence or offer any compensation. Mrs. Deshmukh now seeks damages of ₹25 lakhs, covering medical expenses, mental harassment, pain and suffering, and anticipated costs of future treatment.\n \nCitations:\n \n1. Rupa is a adopted child. She doesnt have any siblings. her father passed away 5 years ago. She and her mothers are only legal heirs. Her father had 2 flats in pune. Her mother told her to leave the house and her mother says that she'll transfer the property to her cousins name. now rupa is scared that she won't get any share in the properties (her name is still not on the legal papers of the properties). Give me 5 plus Case law or Citations According to the Scenario.\n \n2. Provide case laws on the admissibility of electronic evidence under Section 65B of the Indian Evidence Act.\n \n3. Provide citations or case laws related to anticipatory bail\n \nArgument Generation:\n \nMy client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the surgery, due to the negligence of the attending surgeon, Dr. Rakesh Nair, her bile duct was damaged. Post-surgery, she suffered from severe abdominal pain and had to be admitted again for corrective surgery at another hospital. She incurred additional medical expenses of ₹3.5 lakhs and suffered immense mental agony and physical pain. Despite repeated follow-ups, the hospital and the doctor refused to acknowledge their mistake or compensate her. She now seeks compensation of ₹25 lakhs for medical expenses, mental harassment, pain and suffering, and future treatment costs. give me arguments on behalf of plaintiff and defendant.\n \nमाझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टीस्पेशालिटी हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली. शस्त्रक्रियेदरम्यान, उपस्थित सर्जन डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे, त्यांच्या पित्तनलिकेचे नुकसान झाले. शस्त्रक्रियेनंतर, त्यांना तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसऱ्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. त्यांना ३.५ लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आणि प्रचंड मानसिक त्रास आणि शारीरिक वेदना सहन कराव्या लागल्या. वारंवार पाठपुरावा करूनही, रुग्णालय आणि डॉक्टरांनी त्यांची चूक मान्य करण्यास किंवा तिला भरपाई देण्यास नकार दिला. आता त्या वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास आणि भविष्यातील उपचार खर्चासाठी २५ लाख रुपयांची भरपाई मागत आहेत. मला युक्तिवाद द्या.\n \nMy client is facing a criminal case under Section 420 IPC for alleged cheating. However, there was no fraudulent intention from the beginning, and it was purely a civil dispute arising from a failed business agreement. Give me arguments to defend the client and emphasize that this is a case of civil liability, not criminal cheating.\n \nConsumer Laws:\n \nScenario 1: Defective Product Liability Prompt: Priya purchases a brand-new refrigerator from a well-known electronics store. After a week, it stops working, and the manufacturer refuses to repair or replace it, claiming that the issue was caused by improper installation. What legal options does Priya have under the Consumer Protection Act, 2019, to address this defect?\n \nPdf : Review this draft and suggest changes, if any.",
  "globalThreadId": "2a6a8314-5c1d-4e6f-896b-76b9fe96fc96"
}
```

#### Response (JSON)

```json
{
  "globalThreadId": "2a6a8314-5c1d-4e6f-896b-76b9fe96fc96",
  "result": "## Cause Title\n\nIN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE\n\nCIVIL SUIT NO. ______ OF 2024\n\nRupa\nD/o. Late [Deceased Father's Name]\nAge: [Plaintiff's Age], Occupation: [Plaintiff's Occupation]\nResiding at: [Plaintiff's Address], Pune.\n\n.....Plaintiff\n\n**vs**\n\n[Mother's Name]\nW/o. Late [Deceased Father's Name]\nAge: [Defendant's Age], Occupation: [Defendant's Occupation]\nResiding at: [Defendant's Address], Pune.\n\n.....Defendant\n\n## SUIT FOR PARTITION, DECLARATION, AND PERMANENT AND TEMPORARY INJUNCTION\n\nTHE PLAINTIFF ABOVE-NAMED MOST RESPECTFULLY SHOWETH:\n\nThat the Plaintiff files the present suit for partition of the suit properties, for a declaration of her one-half (1/2)\nundivided share in the same, for permanent injunction restraining the Defendant, her agents, or any person claiming\nthrough her from alienating, transferring, or creating any third-party rights in the suit properties, and for a\ntemporary injunction to the same effect during the pendency of this suit under the provisions of the Hindu Succession\nAct, 1956, and the Code of Civil Procedure, 1908.\n\n## Facts of the Case\n\n1. That the Plaintiff is the adopted daughter of the Defendant and her late husband, [Deceased Father's Name]. The Plaintiff has no siblings and has been residing with the Defendant since her childhood.\n\n2. That the Plaintiff’s adoptive father, the late [Deceased Father's Name], passed away intestate approximately five years ago, leaving behind the Plaintiff and the Defendant as his only legal heirs.\n\n3. That during his lifetime, the Plaintiff's father had acquired two residential flats situated in Pune out of his own earnings and savings. The said properties, hereinafter referred to as the \"suit properties\", are more particularly described in the Schedule of Properties annexed hereto and marked as **Schedule 'A'**.\n\n4. That upon the demise of the Plaintiff's father, the suit properties devolved upon his Class I legal heirs as per the provisions of the Hindu Succession Act, 1956. The Plaintiff, being the adopted daughter, and the Defendant, being the widow, are the sole Class I heirs and are thus entitled to succeed to his estate.\n\n5. That in accordance with the law of succession, the Plaintiff and the Defendant each have an equal, undivided one-half (1/2) share, right, title, and interest in the suit properties. Since the death of her father, the Plaintiff has been in joint and constructive possession of the suit properties along with the Defendant.\n\n6. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the suit properties and has explicitly threatened to evict her.\n\n7. That the Plaintiff is given to understand that the Defendant, with a mala fide intention to deprive the Plaintiff of her legitimate share, is making attempts to alienate, sell, or transfer the suit properties in favour of her cousin. The Defendant has expressed her intention to do so, thereby creating a serious threat to the Plaintiff's rights.\n\n8. That the Plaintiff's name has not yet been mutated or entered into the official records pertaining to the suit properties. Taking undue advantage of this fact, the Defendant is actively pursuing the illegal sale of the properties to third parties.\n\n9. That if the Defendant succeeds in creating any third-party rights, charge, or encumbrance over the suit properties, it will cause irreparable loss and injury to the Plaintiff and will lead to a multiplicity of legal proceedings. Hence, the present suit is being filed.\n\n## Legal Provisions\n\n10. That the Plaintiff is entitled to file the present suit and seek the reliefs claimed herein under the following provisions of law, inter alia:\n\na. **The Hindu Succession Act, 1956:** The parties are Hindus, and the succession to the estate of the deceased is\ngoverned by this Act. Under Section 8 of the said Act, the property of a male Hindu dying intestate devolves upon the\nheirs specified in Class I of the Schedule. The Plaintiff as an adopted daughter and the Defendant as the widow are\nClass I heirs. Under Section 10 of the said Act, the Plaintiff and the Defendant are entitled to an equal one-half (1/2)\nshare each in the estate of the deceased.\n\nb. **The Specific Relief Act, 1963:** The Plaintiff is entitled to seek a declaration of her one-half (1/2) share in the suit properties under Section 34 of the said Act. Further, the Plaintiff is entitled to a decree of permanent injunction under Section 38 of the said Act to prevent the Defendant from infringing upon her rights by alienating the suit properties.\n\nc. **The Code of Civil Procedure, 1908:** This Hon'ble Court has the jurisdiction to try and entertain the present suit for partition. The Plaintiff is entitled to seek temporary and ad-interim injunctions under Order XXXIX, Rules 1 and 2, read with Section 151 of the Code, to restrain the Defendant from alienating or creating third-party rights in the suit properties during the pendency of this suit, in order to preserve the subject matter of the suit.\n\n## Plaintiff's Entitlement and Share\n\n11. That the parties to the suit are Hindus and are governed by the Mitakshara School of Hindu Law. The succession to the self-acquired properties of the Plaintiff's late father, [Deceased Father's Name], who died intestate, is governed by the provisions of the Hindu Succession Act, 1956.\n\n12. That as per Section 8 of the Hindu Succession Act, 1956, the property of a male Hindu dying intestate devolves upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the adopted daughter, and the Defendant, being the widow of the deceased, are the only two Class I legal heirs.\n\n13. That under the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption. Consequently, the Plaintiff has the same rights and entitlements in the property of her adoptive father as a natural-born daughter would have.\n\n14. That in accordance with Section 10, Rule 1 of the Hindu Succession Act, 1956, the widow and the children of the intestate shall each take one share. As the deceased is survived only by his widow (the Defendant) and one daughter (the Plaintiff), both are entitled to an equal share in his estate.\n\n15. That by virtue of the aforesaid provisions of law, the Plaintiff is legally entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties more particularly described in Schedule 'A' hereto. The Plaintiff is in joint and constructive possession of the said properties along with the Defendant.\n\n## Grounds for Temporary Injunction\n\n16. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the suit properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no right to deal with the entire property to the exclusion of the Plaintiff.\n\n17. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. If a temporary injunction is not\ngranted, the Defendant is likely to act upon her threats and alienate or create third-party rights in the suit\nproperties. This would lead to a multiplicity of proceedings and would compel the Plaintiff to litigate against bona\nfide purchasers, causing immense hardship and prejudice. Conversely, the Defendant will suffer no prejudice if she is\nrestrained from dealing with the property until the rights of the parties are finally adjudicated by this Hon'ble Court.\n\n18. That the Plaintiff will suffer irreparable loss and injury if the temporary injunction is not granted. The suit properties are residential flats and constitute the Plaintiff's rightful inheritance. If the Defendant succeeds in selling or encumbering the properties, the Plaintiff may be wrongfully and permanently deprived of her share. Such a loss cannot be compensated in terms of money.\n\n19. That the Defendant's mala fide intentions are evident from her hostile conduct, her threats to dispossess the Plaintiff, and her explicit declaration to transfer the properties to her cousin. The Defendant is attempting to take undue advantage of the fact that the Plaintiff's name has not yet been mutated in the property records.\n\n20. That it is therefore just, equitable, and necessary in the interest of justice that this Hon'ble Court be pleased to\ngrant an order of temporary injunction under Order XXXIX, Rules 1 and 2 of the Code of Civil Procedure, 1908,\nrestraining the Defendant, her agents, servants, or any person claiming through her from selling, transferring,\nalienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties described in\nSchedule 'A' during the pendency of the present suit.\n\n## Jurisdiction and Valuation\n\n21. That the cause of action for filing the present suit arose recently when the Defendant's behaviour turned hostile, and she threatened to dispossess the Plaintiff and alienate the suit properties to her cousin, thereby denying the Plaintiff's legitimate share. The cause of action is a continuing one and arises every day the Defendant persists in her illegal threats. The suit as filed is well within the period of limitation prescribed by law.\n\n22. That the suit properties, as described in Schedule 'A' hereto, are situated in Pune. Therefore, this Hon'ble Court has the necessary territorial and pecuniary jurisdiction to entertain, try, and adjudicate upon the present suit.\n\n23. That for the purposes of court fees and jurisdiction, the suit is valued as under:\na. For the relief of partition and separate possession of the Plaintiff's one-half (1/2) share, the suit is valued at Rs. [Value of Plaintiff's 1/2 Share], being half of the market value of the suit properties. Since the Plaintiff is in joint and constructive possession, a fixed court fee is paid as per the Maharashtra Court Fees Act, 1959.\nb. For the relief of declaration of share, the same is ancillary to the relief of partition, and no separate court fee is payable.\nc. For the relief of permanent injunction, the claim is valued at Rs. 1,000/-, and the prescribed court fee is paid thereon.\nThe Plaintiff undertakes to pay any deficit court fee as and when directed by this Hon'ble Court.\n\n## Prayer\n\nIt is, therefore, most humbly and respectfully prayed that this Hon'ble Court may be pleased to:\n\n(a) Pass a preliminary decree declaring that the Plaintiff is entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties, more particularly described in the Schedule of Properties annexed hereto as **Schedule 'A'**.\n\n(b) Direct the partition of the suit properties by metes and bounds, and upon such partition, pass a final decree putting the Plaintiff into separate and vacant possession of her one-half (1/2) share.\n\n(c) Pass a decree for permanent injunction, perpetually restraining the Defendant, her agents, servants, assigns, or any person claiming through her, from selling, transferring, alienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties in any manner whatsoever.\n\n(d) Award the costs of the suit to the Plaintiff to be recovered from the Defendant.\n\n(e) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.\n\n## Verification\n\nI, Rupa, the Plaintiff above-named, do hereby solemnly state and verify that the contents of the foregoing plaint, specifically paragraphs 1 to 23, are true and correct to the best of my knowledge, information, and belief, and that nothing material has been concealed therefrom.\n\nVerified at Pune on this ______ day of _______________, 2024.\n\n**PLAINTIFF**\n(Rupa)\n\n\n---\n**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.",
  "token_usage": {
    "input_tokens": 82038,
    "output_tokens": 12572,
    "total_tokens": 94610,
    "cache_read_tokens": 60247,
    "cache_creation_tokens": 0,
    "reasoning_tokens": 7820,
    "cost_usd": 0.265959,
    "by_agent": {
      "Orchestrator": {
        "input": 9233,
        "output": 1525,
        "total": 10758,
        "cache_read": 8074,
        "calls": 2,
        "cost_usd": 0.001533
      },
      "Drafting": {
        "input": 72805,
        "output": 11047,
        "total": 83852,
        "cache_read": 52173,
        "calls": 7,
        "cost_usd": 0.264425
      }
    },
    "calls": [
      {
        "agent": "Orchestrator",
        "step": "classify_and_plan",
        "input": 2430,
        "output": 88,
        "total": 2518,
        "cache_read": 1691,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000278
      },
      {
        "agent": "Orchestrator",
        "step": "extract_user_intent",
        "input": 6803,
        "output": 1437,
        "total": 8240,
        "cache_read": 6383,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.001255
      },
      {
        "agent": "Drafting",
        "step": "pick_reference",
        "input": 4740,
        "output": 71,
        "total": 4811,
        "cache_read": 3815,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000502
      },
      {
        "agent": "Drafting",
        "step": "fanout_judge",
        "input": 3596,
        "output": 519,
        "total": 4115,
        "cache_read": 3395,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000567
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 11704,
        "output": 1844,
        "total": 13548,
        "cache_read": 8173,
        "cache_creation": 0,
        "reasoning": 1554,
        "cost_usd": 0.04861
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 12006,
        "output": 2619,
        "total": 14625,
        "cache_read": 8174,
        "cache_creation": 0,
        "reasoning": 1751,
        "cost_usd": 0.058708
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 12878,
        "output": 2242,
        "total": 15120,
        "cache_read": 8175,
        "cache_creation": 0,
        "reasoning": 1431,
        "cost_usd": 0.052827
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 13681,
        "output": 2972,
        "total": 16653,
        "cache_read": 8176,
        "cache_creation": 0,
        "reasoning": 2395,
        "cost_usd": 0.070771
      },
      {
        "agent": "Drafting",
        "step": "generate_section_pair",
        "input": 14200,
        "output": 780,
        "total": 14980,
        "cache_read": 12265,
        "cache_creation": 0,
        "reasoning": 689,
        "cost_usd": 0.03244
      }
    ]
  },
  "source": [
    {
      "source_type": "drafting",
      "title": "SUIT FOR PARTITION AND SEPARATE POSSESSION",
      "content": [
        "Draft Heading: Specimen/Draft/Format of SUIT FOR PARTITION AND SEPARATE POSSESSION.\nDraft Content: IN THE COURT OF THE PRINCIPAL CIVIL JUDGE, (City Name), (District Name), (State Name)\n                                                                                SUIT NO. _________ OF 2023\n\n(Plaint"
      ],
      "doc_link": null,
      "file_name": "/content/formats_and_all_drafts/SUIT FOR PARTITION AND SEPARATE POSSESSION.csv",
      "agent_name": "Drafting",
      "relevance_score": null,
      "template_type": "SUIT FOR PARTITION AND SEPARATE POSSESSION"
    }
  ],
  "agents_used": [
    "Drafting"
  ],
  "effective_query": "prompt 1: Research Prompt: Section 125 of Code of Criminal Procedure 1973\n \nScenario:\n \nDraft a civil suit for partition along with a prayer for temporary injunction. Facts: Rupa is an adopted daughter of the deceased owner. She has no siblings. Her father died 5 years ago, leaving behind two flats in Pune. The only legal heirs are Rupa and her mother. Her name is not yet on the property documents. Her mother is attempting to dispossess her and has expressed intent to transfer the properties to her cousin. The suit should include legal provisions for partition of the properties, declaration of her share, and a temporary injunction restraining the mother or any third party from selling, transferring, or creating third-party rights in the properties until disposal of the suit.\n \nDraft a civil suit for damages arising from medical negligence, incorporating detailed legal reasoning and relevant case laws. Scenario: My client, Mrs. Anjali Deshmukh, aged 42, and a resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the procedure, the attending surgeon, Dr. Rakesh Nair, negligently caused damage to her bile duct. Following the surgery, she experienced severe abdominal pain and had to be admitted to another hospital for corrective surgery. As a result, she incurred additional medical expenses amounting to ₹3.5 lakhs and endured significant physical pain and mental agony. Despite repeated follow-ups, both the hospital and the doctor have refused to acknowledge their negligence or offer any compensation. Mrs. Deshmukh now seeks damages of ₹25 lakhs, covering medical expenses, mental harassment, pain and suffering, and anticipated costs of future treatment.\n \nCitations:\n \n1. Rupa is a adopted child. She doesnt have any siblings. her father passed away 5 years ago. She and her mothers are only legal heirs. Her father had 2 flats in pune. Her mother told her to leave the house and her mother says that she'll transfer the property to her cousins name. now rupa is scared that she won't get any share in the properties (her name is still not on the legal papers of the properties). Give me 5 plus Case law or Citations According to the Scenario.\n \n2. Provide case laws on the admissibility of electronic evidence under Section 65B of the Indian Evidence Act.\n \n3. Provide citations or case laws related to anticipatory bail\n \nArgument Generation:\n \nMy client, Mrs. Anjali Deshmukh, aged 42, resident of Pune, underwent gallbladder surgery at XYZ Multispecialty Hospital on 12th March 2024. During the surgery, due to the negligence of the attending surgeon, Dr. Rakesh Nair, her bile duct was damaged. Post-surgery, she suffered from severe abdominal pain and had to be admitted again for corrective surgery at another hospital. She incurred additional medical expenses of ₹3.5 lakhs and suffered immense mental agony and physical pain. Despite repeated follow-ups, the hospital and the doctor refused to acknowledge their mistake or compensate her. She now seeks compensation of ₹25 lakhs for medical expenses, mental harassment, pain and suffering, and future treatment costs. give me arguments on behalf of plaintiff and defendant.\n \nमाझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टीस्पेशालिटी हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली. शस्त्रक्रियेदरम्यान, उपस्थित सर्जन डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे, त्यांच्या पित्तनलिकेचे नुकसान झाले. शस्त्रक्रियेनंतर, त्यांना तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसऱ्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. त्यांना ३.५ लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आणि प्रचंड मानसिक त्रास आणि शारीरिक वेदना सहन कराव्या लागल्या. वारंवार पाठपुरावा करूनही, रुग्णालय आणि डॉक्टरांनी त्यांची चूक मान्य करण्यास किंवा तिला भरपाई देण्यास नकार दिला. आता त्या वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास आणि भविष्यातील उपचार खर्चासाठी २५ लाख रुपयांची भरपाई मागत आहेत. मला युक्तिवाद द्या.\n \nMy client is facing a criminal case under Section 420 Indian Penal Code 1860 for alleged cheating. However, there was no fraudulent intention from the beginning, and it was purely a civil dispute arising from a failed business agreement. Give me arguments to defend the client and emphasize that this is a case of civil liability, not criminal cheating.\n \nConsumer Laws:\n \nScenario 1: Defective Product Liability Prompt: Priya purchases a brand-new refrigerator from a well-known electronics store. After a week, it stops working, and the manufacturer refuses to repair or replace it, claiming that the issue was caused by improper installation. What legal options does Priya have under the Consumer Protection Act, 2019, to address this defect?\n \nPdf : Review this draft and suggest changes, if any.",
  "query_rewritten": true,
  "conversation_turn": 1,
  "followup_suggestions": [
    "What is the limitation period for partition?",
    "Can mother transfer property to cousin?",
    "What is the court fee for partition?"
  ]
}
```

#### Result (rendered markdown)

<details>
<summary>Full response - 12084 chars</summary>

## Cause Title

IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE

CIVIL SUIT NO. ______ OF 2024

Rupa
D/o. Late [Deceased Father's Name]
Age: [Plaintiff's Age], Occupation: [Plaintiff's Occupation]
Residing at: [Plaintiff's Address], Pune.

.....Plaintiff

**vs**

[Mother's Name]
W/o. Late [Deceased Father's Name]
Age: [Defendant's Age], Occupation: [Defendant's Occupation]
Residing at: [Defendant's Address], Pune.

.....Defendant

## SUIT FOR PARTITION, DECLARATION, AND PERMANENT AND TEMPORARY INJUNCTION

THE PLAINTIFF ABOVE-NAMED MOST RESPECTFULLY SHOWETH:

That the Plaintiff files the present suit for partition of the suit properties, for a declaration of her one-half (1/2)
undivided share in the same, for permanent injunction restraining the Defendant, her agents, or any person claiming
through her from alienating, transferring, or creating any third-party rights in the suit properties, and for a
temporary injunction to the same effect during the pendency of this suit under the provisions of the Hindu Succession
Act, 1956, and the Code of Civil Procedure, 1908.

## Facts of the Case

1. That the Plaintiff is the adopted daughter of the Defendant and her late husband, [Deceased Father's Name]. The Plaintiff has no siblings and has been residing with the Defendant since her childhood.

2. That the Plaintiff’s adoptive father, the late [Deceased Father's Name], passed away intestate approximately five years ago, leaving behind the Plaintiff and the Defendant as his only legal heirs.

3. That during his lifetime, the Plaintiff's father had acquired two residential flats situated in Pune out of his own earnings and savings. The said properties, hereinafter referred to as the "suit properties", are more particularly described in the Schedule of Properties annexed hereto and marked as **Schedule 'A'**.

4. That upon the demise of the Plaintiff's father, the suit properties devolved upon his Class I legal heirs as per the provisions of the Hindu Succession Act, 1956. The Plaintiff, being the adopted daughter, and the Defendant, being the widow, are the sole Class I heirs and are thus entitled to succeed to his estate.

5. That in accordance with the law of succession, the Plaintiff and the Defendant each have an equal, undivided one-half (1/2) share, right, title, and interest in the suit properties. Since the death of her father, the Plaintiff has been in joint and constructive possession of the suit properties along with the Defendant.

6. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the suit properties and has explicitly threatened to evict her.

7. That the Plaintiff is given to understand that the Defendant, with a mala fide intention to deprive the Plaintiff of her legitimate share, is making attempts to alienate, sell, or transfer the suit properties in favour of her cousin. The Defendant has expressed her intention to do so, thereby creating a serious threat to the Plaintiff's rights.

8. That the Plaintiff's name has not yet been mutated or entered into the official records pertaining to the suit properties. Taking undue advantage of this fact, the Defendant is actively pursuing the illegal sale of the properties to third parties.

9. That if the Defendant succeeds in creating any third-party rights, charge, or encumbrance over the suit properties, it will cause irreparable loss and injury to the Plaintiff and will lead to a multiplicity of legal proceedings. Hence, the present suit is being filed.

## Legal Provisions

10. That the Plaintiff is entitled to file the present suit and seek the reliefs claimed herein under the following provisions of law, inter alia:

a. **The Hindu Succession Act, 1956:** The parties are Hindus, and the succession to the estate of the deceased is
governed by this Act. Under Section 8 of the said Act, the property of a male Hindu dying intestate devolves upon the
heirs specified in Class I of the Schedule. The Plaintiff as an adopted daughter and the Defendant as the widow are
Class I heirs. Under Section 10 of the said Act, the Plaintiff and the Defendant are entitled to an equal one-half (1/2)
share each in the estate of the deceased.

b. **The Specific Relief Act, 1963:** The Plaintiff is entitled to seek a declaration of her one-half (1/2) share in the suit properties under Section 34 of the said Act. Further, the Plaintiff is entitled to a decree of permanent injunction under Section 38 of the said Act to prevent the Defendant from infringing upon her rights by alienating the suit properties.

c. **The Code of Civil Procedure, 1908:** This Hon'ble Court has the jurisdiction to try and entertain the present suit for partition. The Plaintiff is entitled to seek temporary and ad-interim injunctions under Order XXXIX, Rules 1 and 2, read with Section 151 of the Code, to restrain the Defendant from alienating or creating third-party rights in the suit properties during the pendency of this suit, in order to preserve the subject matter of the suit.

## Plaintiff's Entitlement and Share

11. That the parties to the suit are Hindus and are governed by the Mitakshara School of Hindu Law. The succession to the self-acquired properties of the Plaintiff's late father, [Deceased Father's Name], who died intestate, is governed by the provisions of the Hindu Succession Act, 1956.

12. That as per Section 8 of the Hindu Succession Act, 1956, the property of a male Hindu dying intestate devolves upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the adopted daughter, and the Defendant, being the widow of the deceased, are the only two Class I legal heirs.

13. That under the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption. Consequently, the Plaintiff has the same rights and entitlements in the property of her adoptive father as a natural-born daughter would have.

14. That in accordance with Section 10, Rule 1 of the Hindu Succession Act, 1956, the widow and the children of the intestate shall each take one share. As the deceased is survived only by his widow (the Defendant) and one daughter (the Plaintiff), both are entitled to an equal share in his estate.

15. That by virtue of the aforesaid provisions of law, the Plaintiff is legally entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties more particularly described in Schedule 'A' hereto. The Plaintiff is in joint and constructive possession of the said properties along with the Defendant.

## Grounds for Temporary Injunction

16. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the suit properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no right to deal with the entire property to the exclusion of the Plaintiff.

17. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. If a temporary injunction is not
granted, the Defendant is likely to act upon her threats and alienate or create third-party rights in the suit
properties. This would lead to a multiplicity of proceedings and would compel the Plaintiff to litigate against bona
fide purchasers, causing immense hardship and prejudice. Conversely, the Defendant will suffer no prejudice if she is
restrained from dealing with the property until the rights of the parties are finally adjudicated by this Hon'ble Court.

18. That the Plaintiff will suffer irreparable loss and injury if the temporary injunction is not granted. The suit properties are residential flats and constitute the Plaintiff's rightful inheritance. If the Defendant succeeds in selling or encumbering the properties, the Plaintiff may be wrongfully and permanently deprived of her share. Such a loss cannot be compensated in terms of money.

19. That the Defendant's mala fide intentions are evident from her hostile conduct, her threats to dispossess the Plaintiff, and her explicit declaration to transfer the properties to her cousin. The Defendant is attempting to take undue advantage of the fact that the Plaintiff's name has not yet been mutated in the property records.

20. That it is therefore just, equitable, and necessary in the interest of justice that this Hon'ble Court be pleased to
grant an order of temporary injunction under Order XXXIX, Rules 1 and 2 of the Code of Civil Procedure, 1908,
restraining the Defendant, her agents, servants, or any person claiming through her from selling, transferring,
alienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties described in
Schedule 'A' during the pendency of the present suit.

## Jurisdiction and Valuation

21. That the cause of action for filing the present suit arose recently when the Defendant's behaviour turned hostile, and she threatened to dispossess the Plaintiff and alienate the suit properties to her cousin, thereby denying the Plaintiff's legitimate share. The cause of action is a continuing one and arises every day the Defendant persists in her illegal threats. The suit as filed is well within the period of limitation prescribed by law.

22. That the suit properties, as described in Schedule 'A' hereto, are situated in Pune. Therefore, this Hon'ble Court has the necessary territorial and pecuniary jurisdiction to entertain, try, and adjudicate upon the present suit.

23. That for the purposes of court fees and jurisdiction, the suit is valued as under:
a. For the relief of partition and separate possession of the Plaintiff's one-half (1/2) share, the suit is valued at Rs. [Value of Plaintiff's 1/2 Share], being half of the market value of the suit properties. Since the Plaintiff is in joint and constructive possession, a fixed court fee is paid as per the Maharashtra Court Fees Act, 1959.
b. For the relief of declaration of share, the same is ancillary to the relief of partition, and no separate court fee is payable.
c. For the relief of permanent injunction, the claim is valued at Rs. 1,000/-, and the prescribed court fee is paid thereon.
The Plaintiff undertakes to pay any deficit court fee as and when directed by this Hon'ble Court.

## Prayer

It is, therefore, most humbly and respectfully prayed that this Hon'ble Court may be pleased to:

(a) Pass a preliminary decree declaring that the Plaintiff is entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties, more particularly described in the Schedule of Properties annexed hereto as **Schedule 'A'**.

(b) Direct the partition of the suit properties by metes and bounds, and upon such partition, pass a final decree putting the Plaintiff into separate and vacant possession of her one-half (1/2) share.

(c) Pass a decree for permanent injunction, perpetually restraining the Defendant, her agents, servants, assigns, or any person claiming through her, from selling, transferring, alienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties in any manner whatsoever.

(d) Award the costs of the suit to the Plaintiff to be recovered from the Defendant.

(e) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, Rupa, the Plaintiff above-named, do hereby solemnly state and verify that the contents of the foregoing plaint, specifically paragraphs 1 to 23, are true and correct to the best of my knowledge, information, and belief, and that nothing material has been concealed therefrom.

Verified at Pune on this ______ day of _______________, 2024.

**PLAINTIFF**
(Rupa)


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

---

### Turn 2 - prompt-2-convert-to-marathi

- **Started**: 2026-08-17T14:34:34.858561+00:00
- **Elapsed**: 16.44 s
- **HTTP Status**: 200

#### Request

```http
POST http://localhost:5000/pyapi/search
X-API-Key: ***
Content-Type: application/json

{
  "Promptquery": "Convert above text into marathi",
  "globalThreadId": "2a6a8314-5c1d-4e6f-896b-76b9fe96fc96"
}
```

#### Response (JSON)

```json
{
  "globalThreadId": "2a6a8314-5c1d-4e6f-896b-76b9fe96fc96",
  "result": "## Cause Title\n\nIN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE\n\nCIVIL SUIT NO. ______ OF 2024\n\nRupa\nD/o. Late [Deceased Father's Name]\nAge: [Plaintiff's Age], Occupation: [Plaintiff's Occupation]\nResiding at: [Plaintiff's Address], Pune.\n\n.....Plaintiff\n\n**vs**\n\n[Mother's Name]\nW/o. Late [Deceased Father's Name]\nAge: [Defendant's Age], Occupation: [Defendant's Occupation]\nResiding at: [Defendant's Address], Pune.\n\n.....Defendant\n\n## SUIT FOR PARTITION, DECLARATION, AND PERMANENT AND TEMPORARY INJUNCTION\n\nTHE PLAINTIFF ABOVE-NAMED MOST RESPECTFULLY SHOWETH:\n\nThat the Plaintiff files the present suit for partition of the suit properties, for a declaration of her one-half (1/2)\nundivided share in the same, for permanent injunction restraining the Defendant, her agents, or any person claiming\nthrough her from alienating, transferring, or creating any third-party rights in the suit properties, and for a\ntemporary injunction to the same effect during the pendency of this suit under the provisions of the Hindu Succession\nAct, 1956, and the Code of Civil Procedure, 1908.\n\n## Facts of the Case\n\n1. That the Plaintiff is the adopted daughter of the Defendant and her late husband, [Deceased Father's Name]. The Plaintiff has no siblings and has been residing with the Defendant since her childhood.\n\n2. That the Plaintiff’s adoptive father, the late [Deceased Father's Name], passed away intestate approximately five years ago, leaving behind the Plaintiff and the Defendant as his only legal heirs.\n\n3. That during his lifetime, the Plaintiff's father had acquired two residential flats situated in Pune out of his own earnings and savings. The said properties, hereinafter referred to as the \"suit properties\", are more particularly described in the Schedule of Properties annexed hereto and marked as **Schedule 'A'**.\n\n4. That upon the demise of the Plaintiff's father, the suit properties devolved upon his Class I legal heirs as per the provisions of the Hindu Succession Act, 1956. The Plaintiff, being the adopted daughter, and the Defendant, being the widow, are the sole Class I heirs and are thus entitled to succeed to his estate.\n\n5. That in accordance with the law of succession, the Plaintiff and the Defendant each have an equal, undivided one-half (1/2) share, right, title, and interest in the suit properties. Since the death of her father, the Plaintiff has been in joint and constructive possession of the suit properties along with the Defendant.\n\n6. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the suit properties and has explicitly threatened to evict her.\n\n7. That the Plaintiff is given to understand that the Defendant, with a mala fide intention to deprive the Plaintiff of her legitimate share, is making attempts to alienate, sell, or transfer the suit properties in favour of her cousin. The Defendant has expressed her intention to do so, thereby creating a serious threat to the Plaintiff's rights.\n\n8. That the Plaintiff's name has not yet been mutated or entered into the official records pertaining to the suit properties. Taking undue advantage of this fact, the Defendant is actively pursuing the illegal sale of the properties to third parties.\n\n9. That if the Defendant succeeds in creating any third-party rights, charge, or encumbrance over the suit properties, it will cause irreparable loss and injury to the Plaintiff and will lead to a multiplicity of legal proceedings. Hence, the present suit is being filed.\n\n## Legal Provisions\n\n10. That the Plaintiff is entitled to file the present suit and seek the reliefs claimed herein under the following provisions of law, inter alia:\n\na. **The Hindu Succession Act, 1956:** The parties are Hindus, and the succession to the estate of the deceased is\ngoverned by this Act. Under Section 8 of the said Act, the property of a male Hindu dying intestate devolves upon the\nheirs specified in Class I of the Schedule. The Plaintiff as an adopted daughter and the Defendant as the widow are\nClass I heirs. Under Section 10 of the said Act, the Plaintiff and the Defendant are entitled to an equal one-half (1/2)\nshare each in the estate of the deceased.\n\nb. **The Specific Relief Act, 1963:** The Plaintiff is entitled to seek a declaration of her one-half (1/2) share in the suit properties under Section 34 of the said Act. Further, the Plaintiff is entitled to a decree of permanent injunction under Section 38 of the said Act to prevent the Defendant from infringing upon her rights by alienating the suit properties.\n\nc. **The Code of Civil Procedure, 1908:** This Hon'ble Court has the jurisdiction to try and entertain the present suit for partition. The Plaintiff is entitled to seek temporary and ad-interim injunctions under Order XXXIX, Rules 1 and 2, read with Section 151 of the Code, to restrain the Defendant from alienating or creating third-party rights in the suit properties during the pendency of this suit, in order to preserve the subject matter of the suit.\n\n## Plaintiff's Entitlement and Share\n\n11. That the parties to the suit are Hindus and are governed by the Mitakshara School of Hindu Law. The succession to the self-acquired properties of the Plaintiff's late father, [Deceased Father's Name], who died intestate, is governed by the provisions of the Hindu Succession Act, 1956.\n\n12. That as per Section 8 of the Hindu Succession Act, 1956, the property of a male Hindu dying intestate devolves upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the adopted daughter, and the Defendant, being the widow of the deceased, are the only two Class I legal heirs.\n\n13. That under the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption. Consequently, the Plaintiff has the same rights and entitlements in the property of her adoptive father as a natural-born daughter would have.\n\n14. That in accordance with Section 10, Rule 1 of the Hindu Succession Act, 1956, the widow and the children of the intestate shall each take one share. As the deceased is survived only by his widow (the Defendant) and one daughter (the Plaintiff), both are entitled to an equal share in his estate.\n\n15. That by virtue of the aforesaid provisions of law, the Plaintiff is legally entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties more particularly described in Schedule 'A' hereto. The Plaintiff is in joint and constructive possession of the said properties along with the Defendant.\n\n## Grounds for Temporary Injunction\n\n16. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the suit properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no right to deal with the entire property to the exclusion of the Plaintiff.\n\n17. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. If a temporary injunction is not\ngranted, the Defendant is likely to act upon her threats and alienate or create third-party rights in the suit\nproperties. This would lead to a multiplicity of proceedings and would compel the Plaintiff to litigate against bona\nfide purchasers, causing immense hardship and prejudice. Conversely, the Defendant will suffer no prejudice if she is\nrestrained from dealing with the property until the rights of the parties are finally adjudicated by this Hon'ble Court.\n\n18. That the Plaintiff will suffer irreparable loss and injury if the temporary injunction is not granted. The suit properties are residential flats and constitute the Plaintiff's rightful inheritance. If the Defendant succeeds in selling or encumbering the properties, the Plaintiff may be wrongfully and permanently deprived of her share. Such a loss cannot be compensated in terms of money.\n\n19. That the Defendant's mala fide intentions are evident from her hostile conduct, her threats to dispossess the Plaintiff, and her explicit declaration to transfer the properties to her cousin. The Defendant is attempting to take undue advantage of the fact that the Plaintiff's name has not yet been mutated in the property records.\n\n20. That it is therefore just, equitable, and necessary in the interest of justice that this Hon'ble Court be pleased to\ngrant an order of temporary injunction under Order XXXIX, Rules 1 and 2 of the Code of Civil Procedure, 1908,\nrestraining the Defendant, her agents, servants, or any person claiming through her from selling, transferring,\nalienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties described in\nSchedule 'A' during the pendency of the present suit.\n\n## Jurisdiction and Valuation\n\n21. That the cause of action for filing the present suit arose recently when the Defendant's behaviour turned hostile, and she threatened to dispossess the Plaintiff and alienate the suit properties to her cousin, thereby denying the Plaintiff's legitimate share. The cause of action is a continuing one and arises every day the Defendant persists in her illegal threats. The suit as filed is well within the period of limitation prescribed by law.\n\n22. That the suit properties, as described in Schedule 'A' hereto, are situated in Pune. Therefore, this Hon'ble Court has the necessary territorial and pecuniary jurisdiction to entertain, try, and adjudicate upon the present suit.\n\n23. That for the purposes of court fees and jurisdiction, the suit is valued as under:\na. For the relief of partition and separate possession of the Plaintiff's one-half (1/2) share, the suit is valued at Rs. [Value of Plaintiff's 1/2 Share], being half of the market value of the suit properties. Since the Plaintiff is in joint and constructive possession, a fixed court fee is paid as per the Maharashtra Court Fees Act, 1959.\nb. For the relief of declaration of share, the same is ancillary to the relief of partition, and no separate court fee is payable.\nc. For the relief of permanent injunction, the claim is valued at Rs. 1,000/-, and the prescribed court fee is paid thereon.\nThe Plaintiff undertakes to pay any deficit court fee as and when directed by this Hon'ble Court.\n\n## Prayer\n\nIt is, therefore, most humbly and respectfully prayed that this Hon'ble Court may be pleased to:\n\n(a) Pass a preliminary decree declaring that the Plaintiff is entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties, more particularly described in the Schedule of Properties annexed hereto as **Schedule 'A'**.\n\n(b) Direct the partition of the suit properties by metes and bounds, and upon such partition, pass a final decree putting the Plaintiff into separate and vacant possession of her one-half (1/2) share.\n\n(c) Pass a decree for permanent injunction, perpetually restraining the Defendant, her agents, servants, assigns, or any person claiming through her, from selling, transferring, alienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties in any manner whatsoever.\n\n(d) Award the costs of the suit to the Plaintiff to be recovered from the Defendant.\n\n(e) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.\n\n## Verification\n\nI, Rupa, the Plaintiff above-named, do hereby solemnly state and verify that the contents of the foregoing plaint, specifically paragraphs 1 to 23, are true and correct to the best of my knowledge, information, and belief, and that nothing material has been concealed therefrom.\n\nVerified at Pune on this ______ day of _______________, 2024.\n\n**PLAINTIFF**\n(Rupa)\n\n---\n\n## REFERENCES & CITATIONS\n\n\n### SCENARIO CITATIONS:\n### वादीच्या वतीने युक्तिवाद\n\n१. वादी, श्रीमती अंजली देशमुख, यांना पित्ताशयाच्या शस्त्रक्रियेसाठी प्रतिवादी XYZ मल्टीस्पेशालिटी हॉस्पिटलमध्ये दाखल करण्यात आले होते आणि उपस्थित सर्जन, डॉ. राकेश नायर यांनी शस्त्रक्रिया केली. यामुळे प्रतिवादी आणि वादी यांच्यात एक व्यावसायिक संबंध प्रस्थापित झाला, ज्यातून प्रतिवादींवर वादीच्या आरोग्याची काळजी घेण्याचे कर्तव्य (duty of care) निर्माण झाले.\n\n२. शस्त्रक्रियेदरम्यान, डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे वादीच्या पित्तनलिकेचे नुकसान झाले. वैद्यकीय निष्काळजीपणा म्हणजे एखाद्या वैद्यकीय व्यावसायिकाने योग्य काळजी आणि कौशल्य वापरण्यात कसूर करणे, जे अशा परिस्थितीत सामान्यतः अपेक्षित असते. पित्तनलिकेचे नुकसान हे शस्त्रक्रियेतील मानक कार्यपद्धतीचे उल्लंघन दर्शवते, ज्यामुळे निष्काळजीपणा सिद्ध होतो.\n\n३. पित्तनलिकेच्या नुकसानीमुळे वादीला शस्त्रक्रियेनंतर तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसऱ्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. ही घटना थेट प्रतिवादीच्या निष्काळजी कृतीमुळे घडली असून, त्यामुळे वादीला शारीरिक इजा आणि आर्थिक नुकसान झाले आहे.\n\n४. वादीला सुधारात्मक शस्त्रक्रियेसाठी ₹3.5 लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आहे. याव्यतिरिक्त, त्यांना प्रचंड शारीरिक वेदना, मानसिक त्रास आणि मनस्ताप सहन करावा लागला आहे. हे सर्व नुकसान प्रतिवादीच्या निष्काळजीपणामुळे झाले आहे.\n\n५. प्रतिवादी रुग्णालय आणि डॉक्टरांनी त्यांच्या निष्काळजीपणाची कबुली देण्यास किंवा वादीला भरपाई देण्यास वारंवार पाठपुरावा करूनही नकार दिला आहे, ज्यामुळे वादीला कायदेशीर मार्गाने न्याय मिळवणे आवश्यक झाले आहे.\n\n६. वादी ₹25 लाख रुपयांची भरपाई मागण्यास पात्र आहे, ज्यात वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास तसेच भविष्यातील उपचारांचा अपेक्षित खर्च यांचा समावेश आहे. वैद्यकीय निष्काळजीपणामुळे झालेल्या नुकसानीसाठी पीडित व्यक्तीला भरपाई मिळण्याचा हक्क आहे.\n\n### प्रतिवादीच्या वतीने युक्तिवाद\n\n१. डॉ. राकेश नायर यांनी श्रीमती अंजली देशमुख यांच्या पित्ताशयाची शस्त्रक्रिया (cholecystectomy) करताना योग्य काळजी आणि कौशल्य वापरले होते, जे त्यांच्या व्यावसायिक क्षमतेनुसार अपेक्षित होते. शस्त्रक्रिया वैद्यकीय मानकांनुसार आणि प्रचलित कार्यपद्धतीनुसार करण्यात आली.\n\n२. पित्तनलिकेचे नुकसान, जरी दुर्दैवी असले तरी, पित्ताशयाच्या शस्त्रक्रियेमध्ये एक ज्ञात आणि स्वीकारलेली गुंतागुंत (known complication) आहे. प्रत्येक गुंतागुंत ही निष्काळजीपणाचा पुरावा नसते. डॉक्टरांनी सर्व आवश्यक खबरदारी घेतली होती आणि संभाव्य गुंतागुंतीबद्दल वादीला माहिती दिली होती.\n\n३. डॉ. नायर यांनी शस्त्रक्रियेदरम्यान कोणतीही निष्काळजी कृती केली नाही. वैद्यकीय व्यवसायात, प्रत्येक शस्त्रक्रिया गुंतागुंतीशिवाय पूर्ण होईल याची हमी देता येत नाही. डॉक्टरांनी त्यांच्या क्षमतेनुसार सर्वोत्तम प्रयत्न केले.\n\n४. वादीला झालेला त्रास आणि अतिरिक्त खर्च हा केवळ शस्त्रक्रियेतील गुंतागुंतीचा परिणाम असू शकतो, डॉ. नायर यांच्या निष्काळजीपणाचा नाही. वैद्यकीय प्रक्रियेमध्ये काहीवेळा अनपेक्षित परिणाम येऊ शकतात, जे डॉक्टरांच्या नियंत्रणाबाहेर असतात.\n\n५. वादीने मागितलेली ₹25 लाख रुपयांची भरपाई अवास्तव आणि अतिशयोक्तीपूर्ण आहे. वैद्यकीय खर्चासाठी ₹3.5 लाख रुपये वगळता, मानसिक त्रास, वेदना आणि भविष्यातील उपचारांसाठी मागितलेली रक्कम कोणत्याही ठोस पुराव्यावर आधारित नाही. वादीने हे सिद्ध केले पाहिजे की त्यांना किती मानसिक त्रास झाला आणि भविष्यातील उपचारांचा नेमका खर्च किती असेल.\n\n६. प्रतिवादी रुग्णालय आणि डॉ. नायर यांनी वादीच्या उपचारात कोणतीही कसूर केली नाही. त्यांनी वैद्यकीय नैतिकता आणि व्यावसायिक जबाबदाऱ्यांचे पालन केले आहे. त्यामुळे, निष्काळजीपणाचा आरोप निराधार आहे.\n\n\n---\n**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.",
  "token_usage": {
    "input_tokens": 9947,
    "output_tokens": 515,
    "total_tokens": 10462,
    "cache_read_tokens": 6164,
    "cache_creation_tokens": 0,
    "reasoning_tokens": 0,
    "cost_usd": 0.001201,
    "by_agent": {
      "Memory": {
        "input": 2597,
        "output": 197,
        "total": 2794,
        "cache_read": 0,
        "calls": 1,
        "cost_usd": 0.000338
      },
      "Orchestrator": {
        "input": 7350,
        "output": 318,
        "total": 7668,
        "cache_read": 6164,
        "calls": 2,
        "cost_usd": 0.000862
      }
    },
    "calls": [
      {
        "agent": "Memory",
        "step": "rewrite_query",
        "input": 2597,
        "output": 197,
        "total": 2794,
        "cache_read": 0,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000338
      },
      {
        "agent": "Orchestrator",
        "step": "classify_and_plan",
        "input": 1559,
        "output": 51,
        "total": 1610,
        "cache_read": 770,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000176
      },
      {
        "agent": "Orchestrator",
        "step": "extract_user_intent",
        "input": 5791,
        "output": 267,
        "total": 6058,
        "cache_read": 5394,
        "cache_creation": 0,
        "reasoning": 0,
        "cost_usd": 0.000686
      }
    ]
  },
  "source": [
    {
      "source_type": "drafting",
      "title": "SUIT FOR PARTITION AND SEPARATE POSSESSION",
      "content": [
        "Draft Heading: Specimen/Draft/Format of SUIT FOR PARTITION AND SEPARATE POSSESSION.\nDraft Content: IN THE COURT OF THE PRINCIPAL CIVIL JUDGE, (City Name), (District Name), (State Name)\n                                                                                SUIT NO. _________ OF 2023\n\n(Plaint"
      ],
      "doc_link": null,
      "file_name": "/content/formats_and_all_drafts/SUIT FOR PARTITION AND SEPARATE POSSESSION.csv",
      "agent_name": "Drafting",
      "relevance_score": null,
      "template_type": "SUIT FOR PARTITION AND SEPARATE POSSESSION"
    },
    {
      "source_type": "drafting",
      "title": "SUIT FOR PARTITION AND SEPARATE POSSESSION",
      "content": [
        "Draft Heading: Specimen/Draft/Format of SUIT FOR PARTITION AND SEPARATE POSSESSION.\nDraft Content: IN THE COURT OF THE PRINCIPAL CIVIL JUDGE, (City Name), (District Name), (State Name)\n                                                                                SUIT NO. _________ OF 2023\n\n(Plaint"
      ],
      "doc_link": null,
      "file_name": "/content/formats_and_all_drafts/SUIT FOR PARTITION AND SEPARATE POSSESSION.csv",
      "agent_name": "Drafting",
      "relevance_score": null,
      "template_type": "SUIT FOR PARTITION AND SEPARATE POSSESSION"
    },
    {
      "source_type": "scenario",
      "title": "AI-Generated Legal Analysis",
      "content": [
        "Response generated by AI with web search grounding"
      ],
      "doc_link": null,
      "file_name": null,
      "agent_name": "Scenario",
      "relevance_score": null
    }
  ],
  "agents_used": [
    "Drafting",
    "Scenario"
  ],
  "effective_query": "माझ्या क्लायंट, श्रीमती अंजली देशमुख, वय ४२, पुण्यातील रहिवासी, यांची १२ मार्च २०२४ रोजी XYZ मल्टीस्पेशालिटी हॉस्पिटलमध्ये पित्ताशयाची शस्त्रक्रिया झाली. शस्त्रक्रियेदरम्यान, उपस्थित सर्जन डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे, त्यांच्या पित्तनलिकेचे नुकसान झाले. शस्त्रक्रियेनंतर, त्यांना तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसऱ्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. त्यांना ३.५ लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आणि प्रचंड मानसिक त्रास आणि शारीरिक वेदना सहन कराव्या लागल्या. वारंवार पाठपुरावा करूनही, रुग्णालय आणि डॉक्टरांनी त्यांची चूक मान्य करण्यास किंवा तिला भरपाई देण्यास नकार दिला. आता त्या वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास आणि भविष्यातील उपचार खर्चासाठी २५ लाख रुपयांची भरपाई मागत आहेत. मला युक्तिवाद द्या.",
  "query_rewritten": true,
  "conversation_turn": 2,
  "followup_suggestions": [
    "What documents are needed?",
    "What are the court fees?",
    "How long does this take?"
  ]
}
```

#### Result (rendered markdown)

<details>
<summary>Full response - 15420 chars</summary>

## Cause Title

IN THE COURT OF THE CIVIL JUDGE SENIOR DIVISION, PUNE

CIVIL SUIT NO. ______ OF 2024

Rupa
D/o. Late [Deceased Father's Name]
Age: [Plaintiff's Age], Occupation: [Plaintiff's Occupation]
Residing at: [Plaintiff's Address], Pune.

.....Plaintiff

**vs**

[Mother's Name]
W/o. Late [Deceased Father's Name]
Age: [Defendant's Age], Occupation: [Defendant's Occupation]
Residing at: [Defendant's Address], Pune.

.....Defendant

## SUIT FOR PARTITION, DECLARATION, AND PERMANENT AND TEMPORARY INJUNCTION

THE PLAINTIFF ABOVE-NAMED MOST RESPECTFULLY SHOWETH:

That the Plaintiff files the present suit for partition of the suit properties, for a declaration of her one-half (1/2)
undivided share in the same, for permanent injunction restraining the Defendant, her agents, or any person claiming
through her from alienating, transferring, or creating any third-party rights in the suit properties, and for a
temporary injunction to the same effect during the pendency of this suit under the provisions of the Hindu Succession
Act, 1956, and the Code of Civil Procedure, 1908.

## Facts of the Case

1. That the Plaintiff is the adopted daughter of the Defendant and her late husband, [Deceased Father's Name]. The Plaintiff has no siblings and has been residing with the Defendant since her childhood.

2. That the Plaintiff’s adoptive father, the late [Deceased Father's Name], passed away intestate approximately five years ago, leaving behind the Plaintiff and the Defendant as his only legal heirs.

3. That during his lifetime, the Plaintiff's father had acquired two residential flats situated in Pune out of his own earnings and savings. The said properties, hereinafter referred to as the "suit properties", are more particularly described in the Schedule of Properties annexed hereto and marked as **Schedule 'A'**.

4. That upon the demise of the Plaintiff's father, the suit properties devolved upon his Class I legal heirs as per the provisions of the Hindu Succession Act, 1956. The Plaintiff, being the adopted daughter, and the Defendant, being the widow, are the sole Class I heirs and are thus entitled to succeed to his estate.

5. That in accordance with the law of succession, the Plaintiff and the Defendant each have an equal, undivided one-half (1/2) share, right, title, and interest in the suit properties. Since the death of her father, the Plaintiff has been in joint and constructive possession of the suit properties along with the Defendant.

6. That recently, the Defendant's behaviour towards the Plaintiff has become hostile. The Defendant has been attempting to dispossess the Plaintiff from the suit properties and has explicitly threatened to evict her.

7. That the Plaintiff is given to understand that the Defendant, with a mala fide intention to deprive the Plaintiff of her legitimate share, is making attempts to alienate, sell, or transfer the suit properties in favour of her cousin. The Defendant has expressed her intention to do so, thereby creating a serious threat to the Plaintiff's rights.

8. That the Plaintiff's name has not yet been mutated or entered into the official records pertaining to the suit properties. Taking undue advantage of this fact, the Defendant is actively pursuing the illegal sale of the properties to third parties.

9. That if the Defendant succeeds in creating any third-party rights, charge, or encumbrance over the suit properties, it will cause irreparable loss and injury to the Plaintiff and will lead to a multiplicity of legal proceedings. Hence, the present suit is being filed.

## Legal Provisions

10. That the Plaintiff is entitled to file the present suit and seek the reliefs claimed herein under the following provisions of law, inter alia:

a. **The Hindu Succession Act, 1956:** The parties are Hindus, and the succession to the estate of the deceased is
governed by this Act. Under Section 8 of the said Act, the property of a male Hindu dying intestate devolves upon the
heirs specified in Class I of the Schedule. The Plaintiff as an adopted daughter and the Defendant as the widow are
Class I heirs. Under Section 10 of the said Act, the Plaintiff and the Defendant are entitled to an equal one-half (1/2)
share each in the estate of the deceased.

b. **The Specific Relief Act, 1963:** The Plaintiff is entitled to seek a declaration of her one-half (1/2) share in the suit properties under Section 34 of the said Act. Further, the Plaintiff is entitled to a decree of permanent injunction under Section 38 of the said Act to prevent the Defendant from infringing upon her rights by alienating the suit properties.

c. **The Code of Civil Procedure, 1908:** This Hon'ble Court has the jurisdiction to try and entertain the present suit for partition. The Plaintiff is entitled to seek temporary and ad-interim injunctions under Order XXXIX, Rules 1 and 2, read with Section 151 of the Code, to restrain the Defendant from alienating or creating third-party rights in the suit properties during the pendency of this suit, in order to preserve the subject matter of the suit.

## Plaintiff's Entitlement and Share

11. That the parties to the suit are Hindus and are governed by the Mitakshara School of Hindu Law. The succession to the self-acquired properties of the Plaintiff's late father, [Deceased Father's Name], who died intestate, is governed by the provisions of the Hindu Succession Act, 1956.

12. That as per Section 8 of the Hindu Succession Act, 1956, the property of a male Hindu dying intestate devolves upon the heirs specified in Class I of the Schedule to the said Act. The Plaintiff, being the adopted daughter, and the Defendant, being the widow of the deceased, are the only two Class I legal heirs.

13. That under the Hindu Adoptions and Maintenance Act, 1956, an adopted child is deemed to be the child of his or her adoptive father or mother for all purposes with effect from the date of the adoption. Consequently, the Plaintiff has the same rights and entitlements in the property of her adoptive father as a natural-born daughter would have.

14. That in accordance with Section 10, Rule 1 of the Hindu Succession Act, 1956, the widow and the children of the intestate shall each take one share. As the deceased is survived only by his widow (the Defendant) and one daughter (the Plaintiff), both are entitled to an equal share in his estate.

15. That by virtue of the aforesaid provisions of law, the Plaintiff is legally entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties more particularly described in Schedule 'A' hereto. The Plaintiff is in joint and constructive possession of the said properties along with the Defendant.

## Grounds for Temporary Injunction

16. That the Plaintiff has a strong prima facie case in her favour, as her entitlement to a one-half (1/2) share in the suit properties is clearly established by the provisions of the Hindu Succession Act, 1956. The Defendant has no right to deal with the entire property to the exclusion of the Plaintiff.

17. That the balance of convenience lies overwhelmingly in favour of the Plaintiff. If a temporary injunction is not
granted, the Defendant is likely to act upon her threats and alienate or create third-party rights in the suit
properties. This would lead to a multiplicity of proceedings and would compel the Plaintiff to litigate against bona
fide purchasers, causing immense hardship and prejudice. Conversely, the Defendant will suffer no prejudice if she is
restrained from dealing with the property until the rights of the parties are finally adjudicated by this Hon'ble Court.

18. That the Plaintiff will suffer irreparable loss and injury if the temporary injunction is not granted. The suit properties are residential flats and constitute the Plaintiff's rightful inheritance. If the Defendant succeeds in selling or encumbering the properties, the Plaintiff may be wrongfully and permanently deprived of her share. Such a loss cannot be compensated in terms of money.

19. That the Defendant's mala fide intentions are evident from her hostile conduct, her threats to dispossess the Plaintiff, and her explicit declaration to transfer the properties to her cousin. The Defendant is attempting to take undue advantage of the fact that the Plaintiff's name has not yet been mutated in the property records.

20. That it is therefore just, equitable, and necessary in the interest of justice that this Hon'ble Court be pleased to
grant an order of temporary injunction under Order XXXIX, Rules 1 and 2 of the Code of Civil Procedure, 1908,
restraining the Defendant, her agents, servants, or any person claiming through her from selling, transferring,
alienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties described in
Schedule 'A' during the pendency of the present suit.

## Jurisdiction and Valuation

21. That the cause of action for filing the present suit arose recently when the Defendant's behaviour turned hostile, and she threatened to dispossess the Plaintiff and alienate the suit properties to her cousin, thereby denying the Plaintiff's legitimate share. The cause of action is a continuing one and arises every day the Defendant persists in her illegal threats. The suit as filed is well within the period of limitation prescribed by law.

22. That the suit properties, as described in Schedule 'A' hereto, are situated in Pune. Therefore, this Hon'ble Court has the necessary territorial and pecuniary jurisdiction to entertain, try, and adjudicate upon the present suit.

23. That for the purposes of court fees and jurisdiction, the suit is valued as under:
a. For the relief of partition and separate possession of the Plaintiff's one-half (1/2) share, the suit is valued at Rs. [Value of Plaintiff's 1/2 Share], being half of the market value of the suit properties. Since the Plaintiff is in joint and constructive possession, a fixed court fee is paid as per the Maharashtra Court Fees Act, 1959.
b. For the relief of declaration of share, the same is ancillary to the relief of partition, and no separate court fee is payable.
c. For the relief of permanent injunction, the claim is valued at Rs. 1,000/-, and the prescribed court fee is paid thereon.
The Plaintiff undertakes to pay any deficit court fee as and when directed by this Hon'ble Court.

## Prayer

It is, therefore, most humbly and respectfully prayed that this Hon'ble Court may be pleased to:

(a) Pass a preliminary decree declaring that the Plaintiff is entitled to a one-half (1/2) undivided share, right, title, and interest in the suit properties, more particularly described in the Schedule of Properties annexed hereto as **Schedule 'A'**.

(b) Direct the partition of the suit properties by metes and bounds, and upon such partition, pass a final decree putting the Plaintiff into separate and vacant possession of her one-half (1/2) share.

(c) Pass a decree for permanent injunction, perpetually restraining the Defendant, her agents, servants, assigns, or any person claiming through her, from selling, transferring, alienating, mortgaging, or creating any third-party rights, title, or interest in the suit properties in any manner whatsoever.

(d) Award the costs of the suit to the Plaintiff to be recovered from the Defendant.

(e) Grant any other and further relief as this Hon'ble Court may deem fit and proper in the interest of justice and equity.

## Verification

I, Rupa, the Plaintiff above-named, do hereby solemnly state and verify that the contents of the foregoing plaint, specifically paragraphs 1 to 23, are true and correct to the best of my knowledge, information, and belief, and that nothing material has been concealed therefrom.

Verified at Pune on this ______ day of _______________, 2024.

**PLAINTIFF**
(Rupa)

---

## REFERENCES & CITATIONS


### SCENARIO CITATIONS:
### वादीच्या वतीने युक्तिवाद

१. वादी, श्रीमती अंजली देशमुख, यांना पित्ताशयाच्या शस्त्रक्रियेसाठी प्रतिवादी XYZ मल्टीस्पेशालिटी हॉस्पिटलमध्ये दाखल करण्यात आले होते आणि उपस्थित सर्जन, डॉ. राकेश नायर यांनी शस्त्रक्रिया केली. यामुळे प्रतिवादी आणि वादी यांच्यात एक व्यावसायिक संबंध प्रस्थापित झाला, ज्यातून प्रतिवादींवर वादीच्या आरोग्याची काळजी घेण्याचे कर्तव्य (duty of care) निर्माण झाले.

२. शस्त्रक्रियेदरम्यान, डॉ. राकेश नायर यांच्या निष्काळजीपणामुळे वादीच्या पित्तनलिकेचे नुकसान झाले. वैद्यकीय निष्काळजीपणा म्हणजे एखाद्या वैद्यकीय व्यावसायिकाने योग्य काळजी आणि कौशल्य वापरण्यात कसूर करणे, जे अशा परिस्थितीत सामान्यतः अपेक्षित असते. पित्तनलिकेचे नुकसान हे शस्त्रक्रियेतील मानक कार्यपद्धतीचे उल्लंघन दर्शवते, ज्यामुळे निष्काळजीपणा सिद्ध होतो.

३. पित्तनलिकेच्या नुकसानीमुळे वादीला शस्त्रक्रियेनंतर तीव्र पोटदुखीचा त्रास झाला आणि त्यांना दुसऱ्या रुग्णालयात सुधारात्मक शस्त्रक्रियेसाठी पुन्हा दाखल करावे लागले. ही घटना थेट प्रतिवादीच्या निष्काळजी कृतीमुळे घडली असून, त्यामुळे वादीला शारीरिक इजा आणि आर्थिक नुकसान झाले आहे.

४. वादीला सुधारात्मक शस्त्रक्रियेसाठी ₹3.5 लाख रुपयांचा अतिरिक्त वैद्यकीय खर्च आला आहे. याव्यतिरिक्त, त्यांना प्रचंड शारीरिक वेदना, मानसिक त्रास आणि मनस्ताप सहन करावा लागला आहे. हे सर्व नुकसान प्रतिवादीच्या निष्काळजीपणामुळे झाले आहे.

५. प्रतिवादी रुग्णालय आणि डॉक्टरांनी त्यांच्या निष्काळजीपणाची कबुली देण्यास किंवा वादीला भरपाई देण्यास वारंवार पाठपुरावा करूनही नकार दिला आहे, ज्यामुळे वादीला कायदेशीर मार्गाने न्याय मिळवणे आवश्यक झाले आहे.

६. वादी ₹25 लाख रुपयांची भरपाई मागण्यास पात्र आहे, ज्यात वैद्यकीय खर्च, मानसिक छळ, वेदना आणि त्रास तसेच भविष्यातील उपचारांचा अपेक्षित खर्च यांचा समावेश आहे. वैद्यकीय निष्काळजीपणामुळे झालेल्या नुकसानीसाठी पीडित व्यक्तीला भरपाई मिळण्याचा हक्क आहे.

### प्रतिवादीच्या वतीने युक्तिवाद

१. डॉ. राकेश नायर यांनी श्रीमती अंजली देशमुख यांच्या पित्ताशयाची शस्त्रक्रिया (cholecystectomy) करताना योग्य काळजी आणि कौशल्य वापरले होते, जे त्यांच्या व्यावसायिक क्षमतेनुसार अपेक्षित होते. शस्त्रक्रिया वैद्यकीय मानकांनुसार आणि प्रचलित कार्यपद्धतीनुसार करण्यात आली.

२. पित्तनलिकेचे नुकसान, जरी दुर्दैवी असले तरी, पित्ताशयाच्या शस्त्रक्रियेमध्ये एक ज्ञात आणि स्वीकारलेली गुंतागुंत (known complication) आहे. प्रत्येक गुंतागुंत ही निष्काळजीपणाचा पुरावा नसते. डॉक्टरांनी सर्व आवश्यक खबरदारी घेतली होती आणि संभाव्य गुंतागुंतीबद्दल वादीला माहिती दिली होती.

३. डॉ. नायर यांनी शस्त्रक्रियेदरम्यान कोणतीही निष्काळजी कृती केली नाही. वैद्यकीय व्यवसायात, प्रत्येक शस्त्रक्रिया गुंतागुंतीशिवाय पूर्ण होईल याची हमी देता येत नाही. डॉक्टरांनी त्यांच्या क्षमतेनुसार सर्वोत्तम प्रयत्न केले.

४. वादीला झालेला त्रास आणि अतिरिक्त खर्च हा केवळ शस्त्रक्रियेतील गुंतागुंतीचा परिणाम असू शकतो, डॉ. नायर यांच्या निष्काळजीपणाचा नाही. वैद्यकीय प्रक्रियेमध्ये काहीवेळा अनपेक्षित परिणाम येऊ शकतात, जे डॉक्टरांच्या नियंत्रणाबाहेर असतात.

५. वादीने मागितलेली ₹25 लाख रुपयांची भरपाई अवास्तव आणि अतिशयोक्तीपूर्ण आहे. वैद्यकीय खर्चासाठी ₹3.5 लाख रुपये वगळता, मानसिक त्रास, वेदना आणि भविष्यातील उपचारांसाठी मागितलेली रक्कम कोणत्याही ठोस पुराव्यावर आधारित नाही. वादीने हे सिद्ध केले पाहिजे की त्यांना किती मानसिक त्रास झाला आणि भविष्यातील उपचारांचा नेमका खर्च किती असेल.

६. प्रतिवादी रुग्णालय आणि डॉ. नायर यांनी वादीच्या उपचारात कोणतीही कसूर केली नाही. त्यांनी वैद्यकीय नैतिकता आणि व्यावसायिक जबाबदाऱ्यांचे पालन केले आहे. त्यामुळे, निष्काळजीपणाचा आरोप निराधार आहे.


---
**Disclaimer:** This response is generated by an AI assistant and is for informational purposes only. It does not constitute legal advice. Please consult a qualified legal professional for advice specific to your situation.

</details>

---
