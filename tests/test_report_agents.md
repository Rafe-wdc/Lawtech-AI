# Agent Test Report — 2026-02-25 12:39:57

**Concurrency:** 3 parallel requests  
**Total Prompts:** 100 single + 3 multi-turn sequences  
**Wall Clock Time:** 1568.3s  
**Agents Tested:** Newacts, Legislation, Judgment, SCI_Judgment, Constitution, Maxim, Legal_Concepts, Scenario  
**Excluded:** Drafting, Document  

## Summary

| Status | Count | Pct |
|--------|-------|-----|
| PASS | 95 | 95% |
| WEAK | 0 | 0% |
| FAIL | 4 | 4% |
| ERROR | 1 | 1% |

## Per-Agent Breakdown

| Agent Group | Total | PASS | WEAK | FAIL | ERROR | Avg Time |
|-------------|-------|------|------|------|-------|----------|
| Constitution | 8 | 8 | 0 | 0 | 0 | 46.0s |
| Edge Case | 6 | 6 | 0 | 0 | 0 | 32.6s |
| Judgment | 14 | 14 | 0 | 0 | 0 | 39.4s |
| Legal_Concepts | 4 | 3 | 0 | 1 | 0 | 35.0s |
| Legislation | 14 | 14 | 0 | 0 | 0 | 24.6s |
| Maxim | 8 | 8 | 0 | 0 | 0 | 38.0s |
| Multi-Agent | 10 | 9 | 0 | 0 | 1 | 67.1s |
| Newacts | 18 | 17 | 0 | 1 | 0 | 30.6s |
| SCI | 10 | 10 | 0 | 0 | 0 | 44.9s |
| Scenario | 8 | 6 | 0 | 2 | 0 | 51.4s |

## Timing

| Metric | Value |
|--------|-------|
| Wall clock | 1568.3s |
| Sum of all requests | 3983.6s |
| Speedup vs sequential | 2.5x |
| Avg latency | 39.8s |
| Min latency | 4.6s |
| Max latency | 180.0s |
| Median latency | 37.3s |

## Single-Turn Results

| # | Status | Category | Prompt | Agent(s) | Time | Resp |
|---|--------|----------|--------|----------|------|------|
| 1 | PASS | Newacts-Single | Section 302 of IPC | Newacts | 34.9s | 744 |
| 2 | PASS | Newacts-Single | Section 35 of BNS | Newacts | 34.6s | 587 |
| 3 | PASS | Newacts-Single | What is Section 438 of CrPC? | Newacts | 34.9s | 2043 |
| 4 | PASS | Newacts-Single | Section 173 of BNSS | Newacts | 23.8s | 2292 |
| 5 | PASS | Newacts-Multi | Sections 302 and 307 of IPC | Newacts | 23.7s | 4418 |
| 6 | FAIL | Newacts-Multi | Compare Section 154 and Section 161 of CrPC | Legislation | 23.8s | 4306 |
| 7 | PASS | Newacts-Multi | Explain Sections 64, 65 and 66 of BSA | Newacts | 32.7s | 2134 |
| 8 | PASS | Newacts-Topic | punishment for theft in BNS | Newacts | 33.0s | 4565 |
| 9 | PASS | Newacts-Topic | bail provisions under BNSS | Newacts | 39.3s | 20526 |
| 10 | PASS | Newacts-Topic | electronic evidence rules in BSA | Newacts | 34.6s | 11141 |
| 11 | PASS | Newacts-Mapping | What is the equivalent of Section 498a of IPC in B... | Newacts | 33.1s | 691 |
| 12 | PASS | Newacts-Mapping | Section 125 CrPC new law equivalent | Newacts, Legislation | 51.3s | 9734 |
| 13 | PASS | Newacts-Subsection | Section 3(5) of Bharatiya Nyaya Sanhita | Newacts | 24.3s | 250 |
| 14 | PASS | Newacts-Subsection | IEA Section 65b | Newacts | 43.9s | 6681 |
| 15 | PASS | Newacts-Nearby | What does Section 100 of BNS say? | Newacts | 22.6s | 2058 |
| 16 | PASS | Newacts-Nearby | What comes after section 35 of BNS? | Newacts | 20.9s | 162 |
| 17 | PASS | Newacts-Range | Sections 302, 304, 304a, 307 and 376 of IPC | Newacts | 19.8s | 9655 |
| 18 | PASS | Newacts-Single | Section 420 IPC punishment for cheating | Newacts | 19.4s | 1050 |
| 19 | PASS | Legislation-Single | Section 138 of Negotiable Instruments Act | Legislation | 18.0s | 1125 |
| 20 | PASS | Legislation-Single | Section 9 of Arbitration Act | Legislation | 21.0s | 2138 |
| 21 | PASS | Legislation-Single | Section 23 of Indian Contract Act | Legislation | 21.0s | 678 |
| 22 | PASS | Legislation-Single | Section 34 of Indian Contract Act | Legislation | 21.2s | 705 |
| 23 | PASS | Legislation-Multi | Sections 44 and 45 of Transfer of Property Act | Legislation | 21.8s | 1376 |
| 24 | PASS | Legislation-Multi | Explain Sections 3, 4 and 5 of Consumer Protection... | Legislation | 21.4s | 1110 |
| 25 | PASS | Legislation-Range | Sections 10 to 15 of Companies Act | Legislation | 27.4s | 12913 |
| 26 | PASS | Legislation-Topic | director duties under companies act | Legislation | 19.4s | 701 |
| 27 | PASS | Legislation-Topic | tenant rights in rent control legislation | Legislation, Judgment | 44.4s | 10076 |
| 28 | PASS | Legislation-Topic | minimum wages provisions in labour law | Legislation | 37.3s | 1351 |
| 29 | PASS | Legislation-Subsection | Section 138(1) of Negotiable Instruments Act | Legislation | 25.4s | 1005 |
| 30 | PASS | Legislation-Single | Section 18 of RERA Act | Legislation | 31.0s | 2517 |
| 31 | PASS | Legislation-Single | Section 12 of Domestic Violence Act | Legislation | 17.7s | 1227 |
| 32 | PASS | Legislation-NonSection | Rule 3 of Maharashtra Rent Control Rules | Legislation | 17.4s | 176 |
| 33 | PASS | Judgment-Topic | cases on anticipatory bail | Judgment | 33.4s | 5455 |
| 34 | PASS | Judgment-Topic | dowry harassment case law | Judgment | 34.4s | 14579 |
| 35 | PASS | Judgment-Topic | cheque bounce cases under Section 138 | Legislation, Judgment | 43.6s | 6972 |
| 36 | PASS | Judgment-Topic | property dispute judgments | Judgment | 27.8s | 4870 |
| 37 | PASS | Judgment-Topic | cases on medical negligence | Judgment | 32.3s | 10031 |
| 38 | PASS | Judgment-Topic | land acquisition compensation judgments | Judgment, Legislation | 53.0s | 10200 |
| 39 | PASS | Judgment-Party | Kirloskar vs Kirloskar property dispute | Judgment, Scenario | 46.1s | 9277 |
| 40 | PASS | Judgment-Party | State of Maharashtra vs Suresh | Judgment | 21.2s | 4312 |
| 41 | PASS | Judgment-CaseType | quashing of FIR in cyber crime cases | Scenario, Judgment | 58.2s | 8352 |
| 42 | PASS | Judgment-CaseType | writ petition cases on fundamental rights | Constitution, Judgment | 45.0s | 6430 |
| 43 | PASS | Judgment-Provision | cases invoking Section 498A IPC | Judgment, Newacts | 38.8s | 6020 |
| 44 | PASS | Judgment-Provision | judgments under Section 138 NI Act | Judgment, Legislation | 45.2s | 9549 |
| 45 | PASS | Judgment-Court | Bombay High Court cases on rent dispute | Judgment | 37.8s | 12864 |
| 46 | PASS | Judgment-Court | Delhi High Court cybercrime judgments | Judgment | 34.1s | 15097 |
| 47 | PASS | SCI-Topic | Supreme Court cases on right to privacy | SCI_Judgment, Constitution | 41.5s | 4448 |
| 48 | PASS | SCI-Topic | SC judgment on Article 21 right to life | SCI_Judgment, Constitution | 39.9s | 6073 |
| 49 | PASS | SCI-Topic | Supreme Court ruling on triple talaq | SCI_Judgment, Judgment | 35.2s | 4580 |
| 50 | PASS | SCI-Topic | SC cases on bail conditions | SCI_Judgment | 24.4s | 2158 |
| 51 | PASS | SCI-Topic | Supreme Court judgments on environmental protectio... | SCI_Judgment | 24.3s | 2288 |
| 52 | PASS | SCI-Landmark | Supreme Court landmark judgment on Aadhaar privacy | SCI_Judgment, Constitution | 50.6s | 4494 |
| 53 | PASS | SCI-Landmark | Kesavananda Bharati vs State of Kerala | SCI_Judgment, Constitution | 55.7s | 3958 |
| 54 | PASS | SCI-Landmark | Maneka Gandhi vs Union of India case | SCI_Judgment, Constitution | 55.6s | 4060 |
| 55 | PASS | SCI-Topic | SC precedents on freedom of speech Article 19 | SCI_Judgment, Constitution | 59.5s | 6794 |
| 56 | PASS | SCI-Topic | Supreme Court on reservation and equality | SCI_Judgment, Constitution | 62.0s | 7027 |
| 57 | PASS | Constitution | Article 21 of Indian Constitution | Constitution | 48.4s | 218 |
| 58 | PASS | Constitution | Fundamental rights under Part III of Constitution | Constitution | 35.6s | 2343 |
| 59 | PASS | Constitution | Article 14 right to equality | Constitution | 37.3s | 220 |
| 60 | PASS | Constitution | What are fundamental duties under Article 51A? | Constitution | 36.2s | 1307 |
| 61 | PASS | Constitution | Directive principles of state policy | Constitution | 46.0s | 2359 |
| 62 | PASS | Constitution | Article 32 writ jurisdiction of Supreme Court | Constitution, SCI_Judgment | 61.1s | 2875 |
| 63 | PASS | Constitution | Article 19(1)(a) freedom of expression | Constitution | 43.3s | 475 |
| 64 | PASS | Constitution | What is Article 226 and its scope? | Constitution, Judgment | 59.8s | 4722 |
| 65 | PASS | Maxim | What is audi alteram partem? | Maxim | 31.6s | 472 |
| 66 | PASS | Maxim | Explain the doctrine of res judicata | Maxim | 35.1s | 462 |
| 67 | PASS | Maxim | Meaning of caveat emptor in law | Maxim, Legal_Concepts | 36.9s | 4473 |
| 68 | PASS | Maxim | What is estoppel in legal terms? | Maxim | 35.5s | 412 |
| 69 | PASS | Maxim | Doctrine of ultra vires | Maxim, Constitution | 55.2s | 6160 |
| 70 | PASS | Maxim | Explain nemo judex in causa sua | Maxim | 32.0s | 535 |
| 71 | PASS | Maxim | habeas corpus meaning and legal significance | Legal_Concepts | 28.4s | 4100 |
| 72 | PASS | Maxim | What is the doctrine of proportionality? | Maxim | 49.1s | 3912 |
| 73 | FAIL | Legal_Concepts | define murder | Newacts | 34.5s | 1876 |
| 74 | PASS | Legal_Concepts | What is the difference between bail and anticipato... | Legal_Concepts | 31.1s | 4787 |
| 75 | PASS | Legal_Concepts | Explain FIR and its importance | Legal_Concepts | 22.9s | 4175 |
| 76 | PASS | Legal_Concepts | theft | Newacts, Judgment | 51.6s | 6306 |
| 77 | PASS | Scenario | My landlord is refusing to return my security depo... | Scenario, Judgment, Legislation | 58.6s | 8143 |
| 78 | PASS | Scenario | My employer terminated me without any notice or se... | Scenario, Legislation | 58.7s | 6522 |
| 79 | PASS | Scenario | I received a legal notice for defamation on social... | Scenario, Judgment, Legal_Concepts | 65.1s | 8411 |
| 80 | PASS | Scenario | Can police arrest someone without an FIR? What are... | Scenario, Legislation, Constitution | 53.4s | 8505 |
| 81 | PASS | Scenario | What is the procedure to file a consumer complaint... | Scenario, Legislation | 44.6s | 6434 |
| 82 | PASS | Scenario-Long | My neighbour is encroaching on my land and has con... | Scenario, Judgment, Legislation | 50.2s | 7291 |
| 83 | FAIL | Scenario-News | What are the latest changes in GST laws in India? | Legislation | 40.5s | 7469 |
| 84 | FAIL | Scenario-News | Is cryptocurrency legal in India? What are the rec... | Legislation, Judgment | 40.0s | 6110 |
| 85 | PASS | Multi-Constitution+Judgment | Explain Article 21 with landmark Supreme Court jud... | Constitution, SCI_Judgment | 49.1s | 3906 |
| 86 | PASS | Multi-Constitution+Judgment | Article 14 equality with related case laws | Constitution, Judgment | 50.6s | 9352 |
| 87 | ERROR | Multi-Newacts+SCI | Section 438 BNSS with Supreme Court precedents on ... | — | 180.0s | 0 |
| 88 | PASS | Multi-Newacts+SCI | BNS Section 302 murder with relevant SC judgments | Newacts, SCI_Judgment | 69.6s | 6113 |
| 89 | PASS | Multi-Legislation+Judgment | Section 138 NI Act with important case laws | Legislation, Judgment | 47.1s | 5729 |
| 90 | PASS | Multi-Legislation+Judgment | RERA Section 18 with recent court judgments on del... | Legislation, Judgment | 51.9s | 8396 |
| 91 | PASS | Multi-Constitution+Maxim | Article 14 and res judicata | Constitution, Maxim | 64.4s | 4057 |
| 92 | PASS | Multi-Scenario+Newacts | I was arrested without a warrant and the police di... | Newacts, Judgment | 50.8s | 3847 |
| 93 | PASS | Multi-Newacts+SCI | Compare old Section 302 IPC with new BNS equivalen... | Newacts, SCI_Judgment | 51.5s | 4804 |
| 94 | PASS | Multi-Mixed | What are the legal provisions for cybercrime in In... | Legislation, Judgment | 55.8s | 9491 |
| 95 | PASS | Edge-Abbreviation | sec 302 ipc | Newacts | 23.5s | 721 |
| 96 | PASS | Edge-Abbreviation | art 21 constitution | Constitution | 39.4s | 167 |
| 97 | PASS | Edge-Short | Anticipatory bail | Scenario, Newacts, Judgment | 50.0s | 10000 |
| 98 | PASS | Edge-Complex | What happens if someone commits forgery of a gover... | Scenario, Newacts | 70.5s | 8003 |
| 99 | PASS | Edge-NonLegal | hello | — | 7.5s | 72 |
| 100 | PASS | Edge-NonLegal | Tell me about the weather today | — | 4.6s | 72 |

## Multi-Turn Results

### Newacts Follow-up — PASS
Thread: `50eccdcd-f1c...`

| Turn | Prompt | Status | Agents | Time | Rewritten? |
|------|--------|--------|--------|------|------------|
| 1 | Section 302 of IPC | PASS | Newacts | 20.3s | Yes |
| 2 | What is its equivalent in BNS? | PASS | Newacts, Legislation | 27.3s | Yes |
| 3 | What is the punishment for this section? | PASS | Newacts, Legislation | 27.0s | Yes |

### Judgment Follow-up — PASS
Thread: `71e66c3f-071...`

| Turn | Prompt | Status | Agents | Time | Rewritten? |
|------|--------|--------|--------|------|------------|
| 1 | cases on anticipatory bail in India | PASS | Judgment, SCI_Judgment | 38.8s | No |
| 2 | What did the Supreme Court say in the most recent ... | PASS | Judgment, SCI_Judgment, Newacts | 46.3s | Yes |

### Legislation Follow-up — PASS
Thread: `ed94cd7a-1a4...`

| Turn | Prompt | Status | Agents | Time | Rewritten? |
|------|--------|--------|--------|------|------------|
| 1 | Section 138 of Negotiable Instruments Act | PASS | Legislation | 18.4s | No |
| 2 | What are the defences available to the accused und... | PASS | Legislation, Judgment | 41.8s | Yes |

## Non-PASS Details

### #6 — FAIL — Newacts-Multi

**Query:** `Compare Section 154 and Section 161 of CrPC`  
**Expected:** Newacts  
**Actual:** Legislation  
**Time:** 23.8s | **Response:** 4306 chars  

> Here's a comparison of Section 154 and Section 161 of the Code of Criminal Procedure, 1973:  **Section 154: Information in cognizable cases**  -   **Purpose:** This section deals with the initial recording of information about the commission of a cognizable offense. It outlines the procedure for an ...

---

### #73 — FAIL — Legal_Concepts

**Query:** `define murder`  
**Expected:** Legal_Concepts  
**Actual:** Newacts  
**Time:** 34.5s | **Response:** 1876 chars  

> **New Provision:**  **Section 101 of Bharatiya Nyaya Sanhita, 2023: Murder**  Except in the cases hereinafter excepted, culpable homicide is murder, (a) if the act by which the death is caused is done with the intention of causing death; or (b) if the act by which the death is caused is done with th...

---

### #83 — FAIL — Scenario-News

**Query:** `What are the latest changes in GST laws in India?`  
**Expected:** Scenario  
**Actual:** Legislation  
**Time:** 40.5s | **Response:** 7469 chars  

> The Goods and Services Tax (GST) Act of 2017 in India has undergone several significant changes, particularly with reforms enacted in late 2025 and early 2026, aimed at simplifying the tax structure, easing compliance, and rationalizing rates. These changes stem largely from the 56th GST Council Mee...

---

### #84 — FAIL — Scenario-News

**Query:** `Is cryptocurrency legal in India? What are the recent court rulings?`  
**Expected:** Scenario  
**Actual:** Legislation, Judgment  
**Time:** 40.0s | **Response:** 6110 chars  

> ## Legality of Cryptocurrency in India and Recent Court Rulings  ### Legal Status of Cryptocurrency in India  Based on the provided court judgments, **cryptocurrency is not legally recognized in India, but it is also not illegal**. This nuanced position was articulated by the learned counsel for the...

---

### #87 — ERROR — Multi-Newacts+SCI

**Query:** `Section 438 BNSS with Supreme Court precedents on anticipatory bail`  
**Expected:** Newacts | SCI_Judgment  
**Actual:** —  
**Time:** 180.0s | **Response:** 0 chars  
**Error:** `TIMEOUT`  

---
