# Agent Test Report — 2026-03-15 10:33:38

**Concurrency:** 3 parallel requests  
**Total Prompts:** 100 single + 3 multi-turn sequences  
**Wall Clock Time:** 1126.0s  
**Agents Tested:** Newacts, Legislation, Judgment, SCI_Judgment, Constitution, Maxim, Legal_Concepts, Scenario  
**Excluded:** Drafting, Document  

## Summary

| Status | Count | Pct |
|--------|-------|-----|
| PASS | 100 | 100% |
| WEAK | 0 | 0% |
| FAIL | 0 | 0% |
| ERROR | 0 | 0% |

## Per-Agent Breakdown

| Agent Group | Total | PASS | WEAK | FAIL | ERROR | Avg Time |
|-------------|-------|------|------|------|-------|----------|
| Constitution | 8 | 8 | 0 | 0 | 0 | 42.2s |
| Edge Case | 6 | 6 | 0 | 0 | 0 | 22.7s |
| Judgment | 14 | 14 | 0 | 0 | 0 | 2.4s |
| Legal_Concepts | 4 | 4 | 0 | 0 | 0 | 10.9s |
| Legislation | 14 | 14 | 0 | 0 | 0 | 2.1s |
| Maxim | 8 | 8 | 0 | 0 | 0 | 39.6s |
| Multi-Agent | 10 | 10 | 0 | 0 | 0 | 62.1s |
| Newacts | 18 | 18 | 0 | 0 | 0 | 6.4s |
| SCI | 10 | 10 | 0 | 0 | 0 | 40.1s |
| Scenario | 8 | 8 | 0 | 0 | 0 | 63.6s |

## Timing

| Metric | Value |
|--------|-------|
| Wall clock | 1126.0s |
| Sum of all requests | 2542.8s |
| Speedup vs sequential | 2.3x |
| Avg latency | 25.4s |
| Min latency | 2.0s |
| Max latency | 115.4s |
| Median latency | 3.6s |

## Single-Turn Results

| # | Status | Category | Prompt | Agent(s) | Time | Resp |
|---|--------|----------|--------|----------|------|------|
| 1 | PASS | Newacts-Single | Section 302 of IPC | Newacts | 8.5s | 678 |
| 2 | PASS | Newacts-Single | Section 35 of BNS | Newacts | 8.5s | 570 |
| 3 | PASS | Newacts-Single | What is Section 438 of CrPC? | Newacts | 8.5s | 8811 |
| 4 | PASS | Newacts-Single | Section 173 of BNSS | Newacts | 2.1s | 2479 |
| 5 | PASS | Newacts-Multi | Sections 302 and 307 of IPC | Newacts | 2.1s | 4409 |
| 6 | PASS | Newacts-Multi | Compare Section 154 and Section 161 of CrPC | Newacts | 2.1s | 6206 |
| 7 | PASS | Newacts-Multi | Explain Sections 64, 65 and 66 of BSA | Newacts | 2.0s | 2306 |
| 8 | PASS | Newacts-Topic | punishment for theft in BNS | Newacts | 2.0s | 3781 |
| 9 | PASS | Newacts-Topic | bail provisions under BNSS | Newacts | 2.1s | 5035 |
| 10 | PASS | Newacts-Topic | electronic evidence rules in BSA | Newacts, Legal_Concepts | 2.1s | 12467 |
| 11 | PASS | Newacts-Mapping | What is the equivalent of Section 498a of IPC in B... | Newacts | 2.1s | 1099 |
| 12 | PASS | Newacts-Mapping | Section 125 CrPC new law equivalent | Newacts, Legislation | 2.0s | 14315 |
| 13 | PASS | Newacts-Subsection | Section 3(5) of Bharatiya Nyaya Sanhita | Newacts | 2.2s | 246 |
| 14 | PASS | Newacts-Subsection | IEA Section 65b | Newacts | 2.2s | 5407 |
| 15 | PASS | Newacts-Nearby | What does Section 100 of BNS say? | Newacts | 2.2s | 2019 |
| 16 | PASS | Newacts-Nearby | What comes after section 35 of BNS? | Newacts | 21.7s | 1359 |
| 17 | PASS | Newacts-Range | Sections 302, 304, 304a, 307 and 376 of IPC | Newacts | 21.7s | 13794 |
| 18 | PASS | Newacts-Single | Section 420 IPC punishment for cheating | Newacts | 21.7s | 1094 |
| 19 | PASS | Legislation-Single | Section 138 of Negotiable Instruments Act | Legislation | 2.1s | 1791 |
| 20 | PASS | Legislation-Single | Section 9 of Arbitration Act | Legislation | 2.1s | 2018 |
| 21 | PASS | Legislation-Single | Section 23 of Indian Contract Act | Legislation | 2.1s | 2971 |
| 22 | PASS | Legislation-Single | Section 34 of Indian Contract Act | Legislation | 2.1s | 700 |
| 23 | PASS | Legislation-Multi | Sections 44 and 45 of Transfer of Property Act | Legislation | 2.1s | 1807 |
| 24 | PASS | Legislation-Multi | Explain Sections 3, 4 and 5 of Consumer Protection... | Legislation | 2.1s | 1239 |
| 25 | PASS | Legislation-Range | Sections 10 to 15 of Companies Act | Legislation | 2.1s | 10228 |
| 26 | PASS | Legislation-Topic | director duties under companies act | Legislation | 2.0s | 962 |
| 27 | PASS | Legislation-Topic | tenant rights in rent control legislation | Legislation | 2.0s | 3721 |
| 28 | PASS | Legislation-Topic | minimum wages provisions in labour law | Legislation | 2.1s | 4171 |
| 29 | PASS | Legislation-Subsection | Section 138(1) of Negotiable Instruments Act | Legislation | 2.0s | 1987 |
| 30 | PASS | Legislation-Single | Section 18 of RERA Act | Legislation | 2.1s | 2773 |
| 31 | PASS | Legislation-Single | Section 12 of Domestic Violence Act | Legislation | 2.1s | 1827 |
| 32 | PASS | Legislation-NonSection | Rule 3 of Maharashtra Rent Control Rules | Legislation | 2.0s | 224 |
| 33 | PASS | Judgment-Topic | cases on anticipatory bail | Judgment | 2.0s | 1479 |
| 34 | PASS | Judgment-Topic | dowry harassment case law | Judgment | 3.1s | 25489 |
| 35 | PASS | Judgment-Topic | cheque bounce cases under Section 138 | Newacts, Judgment | 3.2s | 8760 |
| 36 | PASS | Judgment-Topic | property dispute judgments | Judgment | 3.1s | 7252 |
| 37 | PASS | Judgment-Topic | cases on medical negligence | Judgment | 2.1s | 9472 |
| 38 | PASS | Judgment-Topic | land acquisition compensation judgments | Judgment | 2.1s | 8684 |
| 39 | PASS | Judgment-Party | Kirloskar vs Kirloskar property dispute | Judgment | 2.1s | 6703 |
| 40 | PASS | Judgment-Party | State of Maharashtra vs Suresh | SCI_Judgment | 2.1s | 4590 |
| 41 | PASS | Judgment-CaseType | quashing of FIR in cyber crime cases | Scenario, Legislation, Judgment | 2.0s | 10674 |
| 42 | PASS | Judgment-CaseType | writ petition cases on fundamental rights | Constitution, Judgment | 2.1s | 10831 |
| 43 | PASS | Judgment-Provision | cases invoking Section 498A IPC | Newacts, Judgment | 2.7s | 7769 |
| 44 | PASS | Judgment-Provision | judgments under Section 138 NI Act | Legislation, Judgment | 2.7s | 7626 |
| 45 | PASS | Judgment-Court | Bombay High Court cases on rent dispute | Judgment | 2.7s | 16098 |
| 46 | PASS | Judgment-Court | Delhi High Court cybercrime judgments | Judgment | 2.1s | 24616 |
| 47 | PASS | SCI-Topic | Supreme Court cases on right to privacy | SCI_Judgment, Judgment | 101.6s | 7915 |
| 48 | PASS | SCI-Topic | SC judgment on Article 21 right to life | SCI_Judgment | 2.1s | 3131 |
| 49 | PASS | SCI-Topic | Supreme Court ruling on triple talaq | SCI_Judgment | 2.0s | 2604 |
| 50 | PASS | SCI-Topic | SC cases on bail conditions | SCI_Judgment, Judgment | 2.0s | 6667 |
| 51 | PASS | SCI-Topic | Supreme Court judgments on environmental protectio... | SCI_Judgment, Judgment | 68.4s | 9315 |
| 52 | PASS | SCI-Landmark | Supreme Court landmark judgment on Aadhaar privacy | SCI_Judgment | 30.6s | 2056 |
| 53 | PASS | SCI-Landmark | Kesavananda Bharati vs State of Kerala | SCI_Judgment | 2.0s | 1668 |
| 54 | PASS | SCI-Landmark | Maneka Gandhi vs Union of India case | SCI_Judgment | 32.7s | 2898 |
| 55 | PASS | SCI-Topic | SC precedents on freedom of speech Article 19 | SCI_Judgment, Constitution, Judgment | 115.4s | 11245 |
| 56 | PASS | SCI-Topic | Supreme Court on reservation and equality | SCI_Judgment | 44.1s | 5857 |
| 57 | PASS | Constitution | Article 21 of Indian Constitution | Constitution | 33.4s | 10196 |
| 58 | PASS | Constitution | Fundamental rights under Part III of Constitution | Constitution | 49.8s | 21231 |
| 59 | PASS | Constitution | Article 14 right to equality | Constitution | 40.6s | 15527 |
| 60 | PASS | Constitution | What are fundamental duties under Article 51A? | Constitution | 37.2s | 10146 |
| 61 | PASS | Constitution | Directive principles of state policy | Constitution | 43.0s | 14729 |
| 62 | PASS | Constitution | Article 32 writ jurisdiction of Supreme Court | Constitution, SCI_Judgment | 58.7s | 10959 |
| 63 | PASS | Constitution | Article 19(1)(a) freedom of expression | Constitution | 38.9s | 11762 |
| 64 | PASS | Constitution | What is Article 226 and its scope? | Constitution | 35.9s | 10057 |
| 65 | PASS | Maxim | What is audi alteram partem? | Maxim | 33.8s | 11343 |
| 66 | PASS | Maxim | Explain the doctrine of res judicata | Maxim | 33.2s | 11653 |
| 67 | PASS | Maxim | Meaning of caveat emptor in law | Maxim | 37.1s | 10249 |
| 68 | PASS | Maxim | What is estoppel in legal terms? | Maxim | 37.9s | 9366 |
| 69 | PASS | Maxim | Doctrine of ultra vires | Maxim | 37.6s | 10508 |
| 70 | PASS | Maxim | Explain nemo judex in causa sua | Maxim | 30.1s | 8800 |
| 71 | PASS | Maxim | habeas corpus meaning and legal significance | Maxim, Legal_Concepts | 68.0s | 8558 |
| 72 | PASS | Maxim | What is the doctrine of proportionality? | Maxim | 38.9s | 9732 |
| 73 | PASS | Legal_Concepts | define murder | Legal_Concepts | 2.1s | 3029 |
| 74 | PASS | Legal_Concepts | What is the difference between bail and anticipato... | Legal_Concepts | 35.8s | 52151 |
| 75 | PASS | Legal_Concepts | Explain FIR and its importance | Legal_Concepts | 2.0s | 4031 |
| 76 | PASS | Legal_Concepts | theft | Legal_Concepts | 3.6s | 4287 |
| 77 | PASS | Scenario | My landlord is refusing to return my security depo... | Scenario, Legislation | 50.3s | 7649 |
| 78 | PASS | Scenario | My employer terminated me without any notice or se... | Scenario, Legislation | 62.5s | 10541 |
| 79 | PASS | Scenario | I received a legal notice for defamation on social... | Scenario, Legislation, Drafting, Judgment | 93.9s | 18360 |
| 80 | PASS | Scenario | Can police arrest someone without an FIR? What are... | Scenario, Legislation | 41.6s | 12062 |
| 81 | PASS | Scenario | What is the procedure to file a consumer complaint... | Scenario, Legislation | 39.0s | 8009 |
| 82 | PASS | Scenario-Long | My neighbour is encroaching on my land and has con... | Scenario, Legislation, Document | 85.6s | 8862 |
| 83 | PASS | Scenario-News | What are the latest changes in GST laws in India? | Legislation, Scenario | 75.4s | 10808 |
| 84 | PASS | Scenario-News | Is cryptocurrency legal in India? What are the rec... | Scenario, SCI_Judgment | 60.2s | 8618 |
| 85 | PASS | Multi-Constitution+Judgment | Explain Article 21 with landmark Supreme Court jud... | SCI_Judgment | 2.0s | 5412 |
| 86 | PASS | Multi-Constitution+Judgment | Article 14 equality with related case laws | Constitution, Judgment | 104.2s | 11544 |
| 87 | PASS | Multi-Newacts+SCI | Section 438 BNSS with Supreme Court precedents on ... | Newacts, SCI_Judgment, Judgment | 70.7s | 6644 |
| 88 | PASS | Multi-Newacts+SCI | BNS Section 302 murder with relevant SC judgments | SCI_Judgment, Legislation | 68.7s | 4903 |
| 89 | PASS | Multi-Legislation+Judgment | Section 138 NI Act with important case laws | Legislation, Judgment | 65.3s | 7578 |
| 90 | PASS | Multi-Legislation+Judgment | RERA Section 18 with recent court judgments on del... | Legislation, Judgment, Scenario | 47.0s | 10295 |
| 91 | PASS | Multi-Constitution+Maxim | Article 14 and res judicata | Constitution, Maxim | 92.3s | 13701 |
| 92 | PASS | Multi-Scenario+Newacts | I was arrested without a warrant and the police di... | Newacts, Scenario | 48.5s | 6171 |
| 93 | PASS | Multi-Newacts+SCI | Compare old Section 302 IPC with new BNS equivalen... | SCI_Judgment, Newacts | 60.6s | 4930 |
| 94 | PASS | Multi-Mixed | What are the legal provisions for cybercrime in In... | Legislation, Judgment | 61.6s | 9661 |
| 95 | PASS | Edge-Abbreviation | sec 302 ipc | Newacts | 2.1s | 722 |
| 96 | PASS | Edge-Abbreviation | art 21 constitution | Constitution | 44.9s | 10285 |
| 97 | PASS | Edge-Short | Anticipatory bail | Scenario, Newacts | 38.5s | 9569 |
| 98 | PASS | Edge-Complex | What happens if someone commits forgery of a gover... | Scenario, Newacts | 46.2s | 11252 |
| 99 | PASS | Edge-NonLegal | hello | Non_legal | 2.0s | 264 |
| 100 | PASS | Edge-NonLegal | Tell me about the weather today | Non_legal | 2.3s | 320 |

## Multi-Turn Results

### Newacts Follow-up — PASS
Thread: `492589df-b78...`

| Turn | Prompt | Status | Agents | Time | Rewritten? |
|------|--------|--------|--------|------|------------|
| 1 | Section 302 of IPC | PASS | Newacts | 2.1s | No |
| 2 | What is its equivalent in BNS? | PASS | Newacts | 9.0s | Yes |
| 3 | What is the punishment for this section? | PASS | Newacts | 16.1s | Yes |

### Judgment Follow-up — FAIL
Thread: `12b96304-93c...`

| Turn | Prompt | Status | Agents | Time | Rewritten? |
|------|--------|--------|--------|------|------------|
| 1 | cases on anticipatory bail in India | PASS | Judgment | 2.1s | No |
| 2 | What did the Supreme Court say in the most recent ... | ERROR | — | 182.0s | No |

### Legislation Follow-up — PASS
Thread: `989a1828-929...`

| Turn | Prompt | Status | Agents | Time | Rewritten? |
|------|--------|--------|--------|------|------------|
| 1 | Section 138 of Negotiable Instruments Act | PASS | Legislation | 2.0s | No |
| 2 | What are the defences available to the accused und... | PASS | Scenario, Legislation | 43.6s | No |
