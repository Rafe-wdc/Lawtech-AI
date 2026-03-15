# Agent Test Report — 2026-03-15 11:42:42

**Concurrency:** 3 parallel requests  
**Total Prompts:** 100 single + 5 multi-turn sequences  
**Wall Clock Time:** 257.3s  
**Agents Tested:** Newacts, Legislation, Judgment, SCI_Judgment, Constitution, Maxim, Legal_Concepts, Scenario  
**Excluded:** Drafting, Document  

## Summary

| Status | Count | Pct |
|--------|-------|-----|
| PASS | 95 | 95% |
| WEAK | 5 | 5% |
| FAIL | 0 | 0% |
| ERROR | 0 | 0% |

## Per-Agent Breakdown

| Agent Group | Total | PASS | WEAK | FAIL | ERROR | Avg Time |
|-------------|-------|------|------|------|-------|----------|
| Constitution | 8 | 8 | 0 | 0 | 0 | 2.1s |
| Edge Case | 6 | 4 | 2 | 0 | 0 | 2.1s |
| Judgment | 14 | 14 | 0 | 0 | 0 | 2.1s |
| Legal_Concepts | 4 | 4 | 0 | 0 | 0 | 2.1s |
| Legislation | 14 | 13 | 1 | 0 | 0 | 2.1s |
| Maxim | 8 | 8 | 0 | 0 | 0 | 2.1s |
| Multi-Agent | 10 | 10 | 0 | 0 | 0 | 2.1s |
| Newacts | 18 | 17 | 1 | 0 | 0 | 2.1s |
| SCI | 10 | 9 | 1 | 0 | 0 | 2.0s |
| Scenario | 8 | 8 | 0 | 0 | 0 | 2.1s |

## Timing

| Metric | Value |
|--------|-------|
| Wall clock | 257.3s |
| Sum of all requests | 206.7s |
| Speedup vs sequential | 0.8x |
| Avg latency | 2.1s |
| Min latency | 2.0s |
| Max latency | 2.1s |
| Median latency | 2.1s |

## Keyword Coverage

| Metric | Value |
|--------|-------|
| Tests with keywords | 97 |
| Avg keyword coverage | 94% |
| Full coverage (100%) | 81/97 |

## Single-Turn Results

| # | Status | Category | Prompt | Agent(s) | Time | KW% | Resp |
|---|--------|----------|--------|----------|------|-----|------|
| 1 | PASS | Newacts-Mapping | Section 302 of IPC and its equivalent in BNS | Newacts, Legislation | 2.0s | 60% | 1469 |
| 2 | PASS | Newacts-Mapping | Section 420 IPC mapped to new BNS provision | Newacts, Legislation | 2.0s | 60% | 2539 |
| 3 | WEAK | Newacts-Mapping | What replaced Section 498A IPC in BNS? | Newacts, Legislation | 2.0s | 33% | 718 |
| 4 | PASS | Newacts-Mapping | Section 376 IPC equivalent in Bharatiya Nyaya Sanh... | Newacts, Legislation | 2.1s | 100% | 9803 |
| 5 | PASS | Newacts-Mapping | Section 304A IPC in new criminal code | Newacts, Legislation | 2.1s | 100% | 3398 |
| 6 | PASS | Newacts-Mapping | Section 34 IPC common intention in BNS | Newacts, Legislation | 2.1s | 75% | 4782 |
| 7 | PASS | Newacts-Mapping | CrPC Section 154 FIR equivalent in BNSS | Newacts | 2.1s | 100% | 8350 |
| 8 | PASS | Newacts-Mapping | Section 41 CrPC arrest without warrant in BNSS | Newacts, Legislation | 2.1s | 100% | 2182 |
| 9 | PASS | Newacts-Mapping | Section 125 CrPC maintenance in BNSS | Newacts, Legislation | 2.1s | 75% | 4781 |
| 10 | PASS | Newacts-Mapping | CrPC Section 482 inherent powers in BNSS | Newacts, Legislation | 2.1s | 100% | 5132 |
| 11 | PASS | Newacts-Mapping | Section 438 CrPC anticipatory bail in BNSS | Newacts, Legislation | 2.1s | 100% | 8648 |
| 12 | PASS | Newacts-Mapping | Indian Evidence Act Section 65B in BSA | Newacts | 2.0s | 100% | 4179 |
| 13 | PASS | Newacts-Mapping | Section 27 Indian Evidence Act in BSA | Newacts, Legislation | 2.0s | 100% | 5995 |
| 14 | PASS | Newacts-Mapping | IEA Section 45 expert opinion in Bharatiya Sakshya... | Newacts, Legislation | 2.0s | 100% | 2881 |
| 15 | PASS | Newacts-Mapping | Section 32 dying declaration IEA to BSA mapping | Newacts, Legislation | 2.1s | 100% | 4636 |
| 16 | PASS | Newacts-Topic | All changes from IPC to BNS overview | Newacts | 2.1s | 100% | 5708 |
| 17 | PASS | Newacts-Mapping | Section 307 IPC attempt to murder in BNS | Newacts, Legislation | 2.1s | 100% | 6474 |
| 18 | PASS | Newacts-Mapping | Section 379 IPC theft provision in BNS | Newacts, Legislation | 2.1s | 100% | 6762 |
| 19 | PASS | Legislation-Single | Section 138 of Negotiable Instruments Act | Legislation | 2.1s | 100% | 1791 |
| 20 | WEAK | Legislation-Single | Section 73 of Indian Contract Act 1872 | Legislation | 2.0s | 50% | 97 |
| 21 | PASS | Legislation-Single | Section 9 of Arbitration and Conciliation Act 1996 | Legislation | 2.0s | 100% | 2018 |
| 22 | PASS | Legislation-Single | Section 12 of POCSO Act | Newacts | 2.1s | 100% | 978 |
| 23 | PASS | Legislation-Single | Section 66A of Information Technology Act | Legislation | 2.1s | 100% | 1720 |
| 24 | PASS | Legislation-Single | Section 7 of IBC insolvency proceedings | Legislation | 2.0s | 75% | 365 |
| 25 | PASS | Legislation-Single | Section 34 of Arbitration Act award | Legislation | 2.1s | 100% | 2404 |
| 26 | PASS | Legislation-Single | Section 14 of Hindu Succession Act | Legislation | 2.1s | 75% | 1118 |
| 27 | PASS | Legislation-Single | Section 13 of Hindu Marriage Act grounds for divor... | Legislation | 2.1s | 100% | 2966 |
| 28 | PASS | Legislation-Single | Section 125 Motor Vehicles Act third party insuran... | Legislation | 2.0s | 100% | 2143 |
| 29 | PASS | Legislation-Single | Section 24 of RERA Act | Legislation | 2.1s | 67% | 877 |
| 30 | PASS | Legislation-Single | Section 3 of Dowry Prohibition Act 1961 | Legislation | 2.1s | 75% | 694 |
| 31 | PASS | Legislation-Single | Section 498A Indian Penal Code cruelty by husband | Newacts | 2.1s | 100% | 1969 |
| 32 | PASS | Legislation-Single | Section 302 IPC punishment for murder | Newacts | 2.1s | 75% | 722 |
| 33 | PASS | Judgment-Provision | Landmark judgments on Section 498A IPC cruelty | Newacts, SCI_Judgment, Judgment | 2.1s | 100% | 7584 |
| 34 | PASS | Judgment-Provision | Court decisions on Section 138 NI Act cheque bounc... | Legislation, Judgment | 2.0s | 100% | 5654 |
| 35 | PASS | Judgment-Topic | Judgments on anticipatory bail under CrPC | Judgment, Newacts | 2.0s | 100% | 9089 |
| 36 | PASS | Judgment-Provision | High Court judgments on dowry death Section 304B | Judgment, Legislation | 2.1s | 100% | 9962 |
| 37 | PASS | Judgment-Provision | Bail jurisprudence under Section 439 CrPC | Judgment, Legislation, Newacts | 2.1s | 100% | 8378 |
| 38 | PASS | Judgment-Topic | Motor accident compensation tribunal judgments | Judgment | 2.0s | 100% | 8913 |
| 39 | PASS | Judgment-Topic | Judgments on land acquisition and compensation | Judgment | 2.1s | 100% | 12225 |
| 40 | PASS | Judgment-Topic | Court orders on domestic violence Protection of Wo... | Legislation, Judgment | 2.1s | 100% | 10838 |
| 41 | PASS | Judgment-Provision | Cybercrime judgments under IT Act Section 66 | Judgment, Legislation | 2.1s | 100% | 5629 |
| 42 | PASS | Judgment-Topic | Judgments on medical negligence by doctors | Judgment | 2.1s | 100% | 10408 |
| 43 | PASS | Judgment-CaseType | High Court decisions on writ of habeas corpus | Judgment | 2.1s | 100% | 8813 |
| 44 | PASS | Judgment-Topic | Consumer protection judgments on deficiency of ser... | Judgment | 2.0s | 100% | 12241 |
| 45 | PASS | Judgment-Topic | Court rulings on specific performance of contract | Judgment | 2.1s | 100% | 13381 |
| 46 | PASS | Judgment-Provision | Judgments on quashing of FIR under Section 482 CrP... | Judgment, Newacts | 2.1s | 100% | 6886 |
| 47 | PASS | SCI-Landmark | Kesavananda Bharati vs State of Kerala Supreme Cou... | SCI_Judgment | 2.1s | 100% | 1444 |
| 48 | PASS | SCI-Landmark | Maneka Gandhi vs Union of India judgment | SCI_Judgment | 2.1s | 100% | 2181 |
| 49 | PASS | SCI-Landmark | Vishaka vs State of Rajasthan sexual harassment | SCI_Judgment | 2.0s | 100% | 3121 |
| 50 | PASS | SCI-Landmark | K.S. Puttaswamy vs Union of India privacy | SCI_Judgment | 2.1s | 100% | 2208 |
| 51 | PASS | SCI-Landmark | Navtej Singh Johar vs Union of India Section 377 | SCI_Judgment, Legislation | 2.1s | 100% | 3958 |
| 52 | PASS | SCI-Landmark | MC Mehta vs Union of India environmental law | SCI_Judgment | 2.0s | 100% | 3573 |
| 53 | PASS | SCI-Landmark | Shreya Singhal vs Union of India Section 66A | SCI_Judgment, Legislation | 2.0s | 100% | 3883 |
| 54 | PASS | SCI-Landmark | DK Basu vs State of West Bengal custodial death | SCI_Judgment | 2.0s | 67% | 2148 |
| 55 | WEAK | SCI-Landmark | Indian Young Lawyers Association vs State of Keral... | SCI_Judgment | 2.0s | 50% | 1506 |
| 56 | PASS | SCI-Landmark | Arnesh Kumar vs State of Bihar arrest guidelines | SCI_Judgment | 2.1s | 100% | 3159 |
| 57 | PASS | Constitution | Article 14 of Indian Constitution right to equalit... | Constitution | 2.1s | 100% | 12825 |
| 58 | PASS | Constitution | Article 19 freedom of speech and expression | Constitution | 2.0s | 100% | 11617 |
| 59 | PASS | Constitution | Article 21 right to life and personal liberty | Constitution | 2.0s | 100% | 13542 |
| 60 | PASS | Constitution | Article 32 right to constitutional remedies | Constitution, Scenario | 2.1s | 100% | 10970 |
| 61 | PASS | Constitution | Article 226 power of High Courts to issue writs | Constitution | 2.1s | 100% | 17523 |
| 62 | PASS | Constitution | Fundamental Duties under Article 51A | Constitution | 2.1s | 100% | 11484 |
| 63 | PASS | Constitution | Directive Principles of State Policy Article 39 | Constitution | 2.1s | 100% | 12400 |
| 64 | PASS | Constitution | Article 370 special status of Jammu and Kashmir | Constitution | 2.1s | 100% | 13509 |
| 65 | PASS | Maxim | Explain the legal maxim Res Judicata | Maxim | 2.1s | 100% | 10932 |
| 66 | PASS | Maxim | Audi alteram partem meaning and application | Maxim | 2.0s | 100% | 8537 |
| 67 | PASS | Maxim | Nemo judex in causa sua maxim | Maxim | 2.0s | 100% | 8939 |
| 68 | PASS | Maxim | Actus reus non facit reum nisi mens sit rea | Maxim | 2.1s | 100% | 11714 |
| 69 | PASS | Maxim | Ignorantia juris non excusat legal principle | Maxim | 2.1s | 100% | 12340 |
| 70 | PASS | Maxim | Ubi jus ibi remedium meaning | Maxim | 2.0s | 100% | 11233 |
| 71 | PASS | Maxim | Salus populi suprema lex meaning in Indian law | Maxim | 2.1s | 100% | 11769 |
| 72 | PASS | Maxim | Ratio decidendi and obiter dicta distinction | Maxim | 2.1s | 100% | 12744 |
| 73 | PASS | Legal_Concepts | What is the doctrine of basic structure in Indian ... | Constitution | 2.1s | 100% | 16280 |
| 74 | PASS | Legal_Concepts | Explain doctrine of separation of powers in India | Maxim | 2.1s | 100% | 9654 |
| 75 | PASS | Legal_Concepts | What is judicial review in Indian legal system | Legal_Concepts, Judgment | 2.1s | 100% | 9994 |
| 76 | PASS | Legal_Concepts | Explain the concept of natural justice principles | Maxim | 2.1s | 100% | 11669 |
| 77 | PASS | Scenario | A tenant has not paid rent for 6 months. What lega... | Scenario, Legislation | 2.1s | 100% | 8475 |
| 78 | PASS | Scenario | An employee was terminated without notice. What ar... | Scenario, Legislation | 2.1s | 100% | 6133 |
| 79 | PASS | Scenario | A doctor operated on the wrong patient. What are t... | Scenario, Judgment | 2.1s | 100% | 10918 |
| 80 | PASS | Scenario | A person was arrested without a warrant for a bail... | Newacts, Scenario | 2.1s | 100% | 7845 |
| 81 | PASS | Scenario | My neighbour constructed a wall blocking my sunlig... | Scenario, Legislation | 2.0s | 100% | 6156 |
| 82 | PASS | Scenario | A company is unable to pay its debts. What is the ... | Legal_Concepts | 2.0s | 100% | 5445 |
| 83 | PASS | Scenario | A minor signed a contract. Is it enforceable? | Scenario, Legal_Concepts | 2.1s | 100% | 6854 |
| 84 | PASS | Scenario | Someone posted defamatory content about me on soci... | Scenario, Legislation | 2.1s | 100% | 9343 |
| 85 | PASS | Multi-Newacts+SCI | Section 302 IPC with Supreme Court landmark judgme... | Newacts, SCI_Judgment, Judgment | 2.0s | 100% | 9181 |
| 86 | PASS | Multi-Newacts+SCI | Section 438 BNSS with Supreme Court precedents on ... | Newacts, SCI_Judgment, Judgment | 2.1s | 100% | 4523 |
| 87 | PASS | Multi-Constitution+SCI | Article 21 Constitution with landmark Supreme Cour... | Constitution, SCI_Judgment | 2.1s | 100% | 12117 |
| 88 | PASS | Multi-Newacts+Judgment | Section 498A IPC equivalent in BNS with High Court... | Newacts, Judgment | 2.1s | 67% | 7544 |
| 89 | PASS | Multi-Legislation+Judgment | Cheque bounce Section 138 NI Act with court judgme... | Legislation, Judgment | 2.0s | 100% | 9715 |
| 90 | PASS | Multi-Legislation+SCI | POCSO Act provisions with Supreme Court guidelines | Newacts, SCI_Judgment | 2.1s | 100% | 9026 |
| 91 | PASS | Multi-Legislation+Judgment | Dowry Prohibition Act with Section 304B IPC and co... | Newacts, SCI_Judgment | 2.0s | 100% | 4086 |
| 92 | PASS | Multi-Constitution+SCI | Right to privacy Article 21 with Puttaswamy judgme... | Constitution, SCI_Judgment, Judgment | 2.0s | 100% | 11527 |
| 93 | PASS | Multi-Legislation+SCI | Arbitration Act Section 11 appointment of arbitrat... | Legislation, SCI_Judgment | 2.1s | 100% | 7762 |
| 94 | PASS | Multi-Legislation+Judgment | Consumer Protection Act 2019 with landmark consume... | Legislation, Judgment | 2.1s | 100% | 10066 |
| 95 | PASS | Edge-NonLegal | Hello | Non_legal | 2.1s | — | 275 |
| 96 | PASS | Edge-NonLegal | Thank you for the information | Non_legal | 2.0s | — | 191 |
| 97 | PASS | Edge-NonLegal | What is the weather today? | Non_legal | 2.0s | — | 384 |
| 98 | WEAK | Edge-Hindi | धारा 302 आईपीसी में सजा का प्रावधान | Newacts | 2.1s | 50% | 823 |
| 99 | WEAK | Edge-Hinglish | Section 302 IPC aur BNS mein kya badlav hai | Newacts, Legislation | 2.1s | 33% | 2246 |
| 100 | PASS | Edge-Drafting | Draft a legal notice for non-payment of dues | Drafting, Judgment, Legislation | 2.0s | 100% | 20136 |

## Multi-Turn Results

### Newacts Follow-up — FAIL
Thread: `b4779634-fdf...`

| Turn | Prompt | Status | Agents | KW% | Time | Rewritten? |
|------|--------|--------|--------|-----|------|------------|
| 1 | Section 302 IPC and its BNS equivalent | WEAK | Newacts, Legislation | 50% | 2.0s | No |
| 2 | What about the punishment difference between old a... | PASS | Newacts | 100% | 18.8s | No |
| 3 | How does Section 307 IPC map to BNS? | PASS | Newacts, Legislation | 75% | 31.7s | Yes |

### Legislation Drill-down — PASS
Thread: `55878fe0-5c6...`

| Turn | Prompt | Status | Agents | KW% | Time | Rewritten? |
|------|--------|--------|--------|-----|------|------------|
| 1 | Section 138 of Negotiable Instruments Act | PASS | Legislation | 100% | 2.1s | No |
| 2 | What is the limitation period for filing under thi... | PASS | Legislation | 100% | 11.3s | No |

### Constitution to Judgment — PASS
Thread: `0800eb04-d52...`

| Turn | Prompt | Status | Agents | KW% | Time | Rewritten? |
|------|--------|--------|--------|-----|------|------------|
| 1 | Article 21 right to life and personal liberty | PASS | Constitution | 100% | 2.1s | No |
| 2 | What are the landmark Supreme Court cases expandin... | PASS | SCI_Judgment, Judgment | 100% | 89.3s | No |

### Scenario to Legislation — FAIL
Thread: `42df5a08-4a3...`

| Turn | Prompt | Status | Agents | KW% | Time | Rewritten? |
|------|--------|--------|--------|-----|------|------------|
| 1 | A person was cheated online by a fake website. Wha... | PASS | Scenario, Newacts | 100% | 2.0s | No |
| 2 | Which specific sections of IT Act apply here? | WEAK | Legislation | 50% | 12.4s | Yes |

### Maxim to Application — PASS
Thread: `2c655875-f2a...`

| Turn | Prompt | Status | Agents | KW% | Time | Rewritten? |
|------|--------|--------|--------|-----|------|------------|
| 1 | Explain the maxim Res Judicata | PASS | Maxim | 100% | 2.1s | No |
| 2 | How has the Supreme Court applied this principle? | PASS | SCI_Judgment | 100% | 13.7s | No |

## Non-PASS Details

### #3 — WEAK — Newacts-Mapping

**Query:** `What replaced Section 498A IPC in BNS?`  
**Expected:** Newacts  
**Actual:** Newacts, Legislation  
**Time:** 2.0s | **Response:** 718 chars  
**Keyword Coverage:** 33% | **Missing:** BNS, 85  

> ## Bharatiya Nyaya Sanhita, 2023 में Indian Penal Code, 1860 की धारा 498A का प्रतिस्थापन  उपलब्ध जानकारी के अनुसार, Bharatiya Nyaya Sanhita, 2023 में Indian Penal Code, 1860 की धारा 498A (पति या पति के रिश्तेदार द्वारा महिला के प्रति क्रूरता) के प्रतिस्थापन के संबंध में किसी विशिष्ट धारा का उल्लेख न...

---

### #20 — WEAK — Legislation-Single

**Query:** `Section 73 of Indian Contract Act 1872`  
**Expected:** Legislation  
**Actual:** Legislation  
**Time:** 2.0s | **Response:** 97 chars  
**Keyword Coverage:** 50% | **Missing:** damages, breach  

> Section 73 of The Indian Contract Act, 1872 is not found in the provided legislation provisions. ...

---

### #55 — WEAK — SCI-Landmark

**Query:** `Indian Young Lawyers Association vs State of Kerala Sabarimala`  
**Expected:** SCI_Judgment  
**Actual:** SCI_Judgment  
**Time:** 2.0s | **Response:** 1506 chars  
**Keyword Coverage:** 50% | **Missing:** women  

> The case you are looking for is:  **INDIAN YOUNG LAWYERS ASSOCIATION VS THE STATE OF KERALA**  -   **DB ID**: 36530 -   **Case No**: W.P.(C) No.-000373-000373 - 2006 -   **Judgment Date**: 13-10-2017 -   **Bench**: HON'BLE THE CHIEF JUSTICE, HON'BLE MR. JUSTICE ROHINTON FALI NARIMAN, HON'BLE MR. JUS...

---

### #98 — WEAK — Edge-Hindi

**Query:** `धारा 302 आईपीसी में सजा का प्रावधान`  
**Expected:** Any  
**Actual:** Newacts  
**Time:** 2.1s | **Response:** 823 chars  
**Keyword Coverage:** 50% | **Missing:** IPC  

> भारतीय दंड संहिता, 1860 (Indian Penal Code, 1860) की धारा 302 के तहत हत्या के लिए दंड का प्रावधान निम्नलिखित है:  **पुराना प्रावधान (Old Provision):** भारतीय दंड संहिता, 1860 (Indian Penal Code, 1860) की धारा 302 के अनुसार: "जो कोई हत्या करेगा, उसे मृत्यु दंड या आजीवन कारावास से दंडित किया जाएगा, और...

---

### #99 — WEAK — Edge-Hinglish

**Query:** `Section 302 IPC aur BNS mein kya badlav hai`  
**Expected:** Any  
**Actual:** Newacts, Legislation  
**Time:** 2.1s | **Response:** 2246 chars  
**Keyword Coverage:** 33% | **Missing:** IPC, BNS  

> भारतीय दंड संहिता, 1860 की Section 302 और भारतीय न्याय संहिता, 2023 की Section 103 के बीच तुलनात्मक विवरण निम्नलिखित तालिका में प्रस्तुत किया गया है:  | विशेषता (Feature) | भारतीय दंड संहिता, 1860 (Indian Penal Code, 1860) | भारतीय न्याय संहिता, 2023 (Bharatiya Nyaya Sanhita, 2023) | | :------------...

---
