# Agent AI Evaluation Report — 2026-02-25 12:41:29

**Total Single-Turn:** 100  
**Total Multi-Turn:** 3  
**Evaluation Time:** 30.9s  
**Model:** GPT-4o-mini (temperature=0.1)  

## Overall Verdicts (Single-Turn)

| Verdict | Count | % |
|---------|-------|---|
| PASS | 94 | 94% |
| PARTIAL | 4 | 4% |
| FAIL | 2 | 2% |
| ERROR | 0 | 0% |

## Average Scores (Single-Turn)

| Dimension | Average |
|-----------|---------|
| Relevance | 9.4/10 |
| Completeness | 9.0/10 |
| Accuracy | 8.9/10 |
| **Overall** | **9.1/10** |

## Per-Agent Breakdown

| Agent | Count | Avg Rel | Avg Comp | Avg Acc | PASS | PARTIAL | FAIL |
|-------|-------|---------|----------|---------|------|---------|------|
| Constitution | 8 | 9.1 | 8.0 | 8.9 | 6 | 2 | 0 |
| Edge | 6 | 9.8 | 9.5 | 9.5 | 6 | 0 | 0 |
| Judgment | 14 | 9.6 | 9.4 | 8.9 | 14 | 0 | 0 |
| Legal_Concepts | 4 | 10.0 | 9.8 | 9.0 | 4 | 0 | 0 |
| Legislation | 14 | 9.1 | 8.6 | 9.0 | 13 | 0 | 1 |
| Maxim | 8 | 9.2 | 8.4 | 8.5 | 6 | 2 | 0 |
| Multi | 10 | 8.4 | 7.9 | 7.6 | 9 | 0 | 1 |
| Newacts | 18 | 9.9 | 9.7 | 9.6 | 18 | 0 | 0 |
| SCI | 10 | 9.7 | 9.3 | 9.3 | 10 | 0 | 0 |
| Scenario | 8 | 9.8 | 9.4 | 8.8 | 8 | 0 | 0 |

## Multi-Turn Evaluation

| Sequence | Coherence | Follow-up | Verdict | Reasoning |
|----------|-----------|-----------|---------|-----------|
| Newacts Follow-up | 9 | 9 | PASS | The conversation maintains strong coherence across all turns, with each response building logically  |
| Judgment Follow-up | 10 | 10 | PASS | The system maintains perfect context across both turns, as the follow-up question directly relates t |
| Legislation Follow-up | 9 | 9 | PASS | The system effectively maintains context between the two turns, with the second turn directly buildi |

## Per-Prompt Results

| # | Verdict | Rel | Comp | Acc | Category | Agent(s) | Prompt |
|---|---------|-----|------|-----|----------|----------|--------|
| 1 | PASS | 10 | 9 | 9 | Newacts-Single | Newacts | Section 302 of IPC |
| 2 | PASS | 10 | 10 | 10 | Newacts-Single | Newacts | Section 35 of BNS |
| 3 | PASS | 10 | 10 | 10 | Newacts-Single | Newacts | What is Section 438 of CrPC? |
| 4 | PASS | 10 | 10 | 10 | Newacts-Single | Newacts | Section 173 of BNSS |
| 5 | PASS | 10 | 10 | 9 | Newacts-Multi | Newacts | Sections 302 and 307 of IPC |
| 6 | PASS | 10 | 9 | 9 | Newacts-Multi | Legislation | Compare Section 154 and Section 161 of C... |
| 7 | PASS | 10 | 10 | 9 | Newacts-Multi | Newacts | Explain Sections 64, 65 and 66 of BSA |
| 8 | PASS | 9 | 9 | 9 | Newacts-Topic | Newacts | punishment for theft in BNS |
| 9 | PASS | 10 | 10 | 9 | Newacts-Topic | Newacts | bail provisions under BNSS |
| 10 | PASS | 10 | 10 | 10 | Newacts-Topic | Newacts | electronic evidence rules in BSA |
| 11 | PASS | 10 | 10 | 10 | Newacts-Mapping | Newacts | What is the equivalent of Section 498a o... |
| 12 | PASS | 10 | 10 | 10 | Newacts-Mapping | Newacts, Legislation | Section 125 CrPC new law equivalent |
| 13 | PASS | 10 | 10 | 10 | Newacts-Subsection | Newacts | Section 3(5) of Bharatiya Nyaya Sanhita |
| 14 | PASS | 10 | 10 | 10 | Newacts-Subsection | Newacts | IEA Section 65b |
| 15 | PASS | 10 | 10 | 10 | Newacts-Nearby | Newacts | What does Section 100 of BNS say? |
| 16 | PASS | 10 | 10 | 10 | Newacts-Nearby | Newacts | What comes after section 35 of BNS? |
| 17 | PASS | 9 | 8 | 8 | Newacts-Range | Newacts | Sections 302, 304, 304a, 307 and 376 of ... |
| 18 | PASS | 10 | 10 | 10 | Newacts-Single | Newacts | Section 420 IPC punishment for cheating |
| 19 | PASS | 10 | 10 | 10 | Legislation-Single | Legislation | Section 138 of Negotiable Instruments Ac... |
| 20 | PASS | 10 | 10 | 10 | Legislation-Single | Legislation | Section 9 of Arbitration Act |
| 21 | PASS | 10 | 9 | 9 | Legislation-Single | Legislation | Section 23 of Indian Contract Act |
| 22 | PASS | 10 | 10 | 10 | Legislation-Single | Legislation | Section 34 of Indian Contract Act |
| 23 | PASS | 10 | 10 | 10 | Legislation-Multi | Legislation | Sections 44 and 45 of Transfer of Proper... |
| 24 | PASS | 9 | 8 | 9 | Legislation-Multi | Legislation | Explain Sections 3, 4 and 5 of Consumer ... |
| 25 | PASS | 9 | 8 | 9 | Legislation-Range | Legislation | Sections 10 to 15 of Companies Act |
| 26 | FAIL | 4 | 4 | 6 | Legislation-Topic | Legislation | director duties under companies act |
| 27 | PASS | 9 | 8 | 8 | Legislation-Topic | Legislation, Judgment | tenant rights in rent control legislatio... |
| 28 | PASS | 9 | 8 | 9 | Legislation-Topic | Legislation | minimum wages provisions in labour law |
| 29 | PASS | 9 | 8 | 8 | Legislation-Subsection | Legislation | Section 138(1) of Negotiable Instruments... |
| 30 | PASS | 10 | 10 | 10 | Legislation-Single | Legislation | Section 18 of RERA Act |
| 31 | PASS | 10 | 10 | 10 | Legislation-Single | Legislation | Section 12 of Domestic Violence Act |
| 32 | PASS | 8 | 7 | 8 | Legislation-NonSection | Legislation | Rule 3 of Maharashtra Rent Control Rules |
| 33 | PASS | 10 | 10 | 9 | Judgment-Topic | Judgment | cases on anticipatory bail |
| 34 | PASS | 10 | 10 | 9 | Judgment-Topic | Judgment | dowry harassment case law |
| 35 | PASS | 10 | 9 | 9 | Judgment-Topic | Legislation, Judgment | cheque bounce cases under Section 138 |
| 36 | PASS | 9 | 9 | 9 | Judgment-Topic | Judgment | property dispute judgments |
| 37 | PASS | 10 | 10 | 9 | Judgment-Topic | Judgment | cases on medical negligence |
| 38 | PASS | 10 | 10 | 9 | Judgment-Topic | Judgment, Legislation | land acquisition compensation judgments |
| 39 | PASS | 9 | 9 | 8 | Judgment-Party | Judgment, Scenario | Kirloskar vs Kirloskar property dispute |
| 40 | PASS | 10 | 10 | 10 | Judgment-Party | Judgment | State of Maharashtra vs Suresh |
| 41 | PASS | 10 | 9 | 9 | Judgment-CaseType | Scenario, Judgment | quashing of FIR in cyber crime cases |
| 42 | PASS | 9 | 9 | 8 | Judgment-CaseType | Constitution, Judgment | writ petition cases on fundamental right... |
| 43 | PASS | 9 | 9 | 9 | Judgment-Provision | Judgment, Newacts | cases invoking Section 498A IPC |
| 44 | PASS | 9 | 8 | 8 | Judgment-Provision | Judgment, Legislation | judgments under Section 138 NI Act |
| 45 | PASS | 10 | 10 | 9 | Judgment-Court | Judgment | Bombay High Court cases on rent dispute |
| 46 | PASS | 10 | 10 | 9 | Judgment-Court | Judgment | Delhi High Court cybercrime judgments |
| 47 | PASS | 10 | 9 | 9 | SCI-Topic | SCI_Judgment, Constitutio | Supreme Court cases on right to privacy |
| 48 | PASS | 10 | 10 | 9 | SCI-Topic | SCI_Judgment, Constitutio | SC judgment on Article 21 right to life |
| 49 | PASS | 10 | 10 | 10 | SCI-Topic | SCI_Judgment, Judgment | Supreme Court ruling on triple talaq |
| 50 | PASS | 10 | 10 | 9 | SCI-Topic | SCI_Judgment | SC cases on bail conditions |
| 51 | PASS | 8 | 7 | 8 | SCI-Topic | SCI_Judgment | Supreme Court judgments on environmental... |
| 52 | PASS | 10 | 10 | 10 | SCI-Landmark | SCI_Judgment, Constitutio | Supreme Court landmark judgment on Aadha... |
| 53 | PASS | 10 | 10 | 10 | SCI-Landmark | SCI_Judgment, Constitutio | Kesavananda Bharati vs State of Kerala |
| 54 | PASS | 10 | 10 | 10 | SCI-Landmark | SCI_Judgment, Constitutio | Maneka Gandhi vs Union of India case |
| 55 | PASS | 10 | 9 | 9 | SCI-Topic | SCI_Judgment, Constitutio | SC precedents on freedom of speech Artic... |
| 56 | PASS | 9 | 8 | 9 | SCI-Topic | SCI_Judgment, Constitutio | Supreme Court on reservation and equalit... |
| 57 | PARTIAL | 8 | 5 | 9 | Constitution | Constitution | Article 21 of Indian Constitution |
| 58 | PASS | 9 | 8 | 9 | Constitution | Constitution | Fundamental rights under Part III of Con... |
| 59 | PARTIAL | 8 | 5 | 9 | Constitution | Constitution | Article 14 right to equality |
| 60 | PASS | 10 | 9 | 9 | Constitution | Constitution | What are fundamental duties under Articl... |
| 61 | PASS | 9 | 9 | 8 | Constitution | Constitution | Directive principles of state policy |
| 62 | PASS | 10 | 10 | 9 | Constitution | Constitution, SCI_Judgmen | Article 32 writ jurisdiction of Supreme ... |
| 63 | PASS | 9 | 8 | 9 | Constitution | Constitution | Article 19(1)(a) freedom of expression |
| 64 | PASS | 10 | 10 | 9 | Constitution | Constitution, Judgment | What is Article 226 and its scope? |
| 65 | PASS | 9 | 8 | 9 | Maxim | Maxim | What is audi alteram partem? |
| 66 | PARTIAL | 8 | 6 | 8 | Maxim | Maxim | Explain the doctrine of res judicata |
| 67 | PASS | 10 | 10 | 9 | Maxim | Maxim, Legal_Concepts | Meaning of caveat emptor in law |
| 68 | PARTIAL | 8 | 6 | 7 | Maxim | Maxim | What is estoppel in legal terms? |
| 69 | PASS | 10 | 10 | 9 | Maxim | Maxim, Constitution | Doctrine of ultra vires |
| 70 | PASS | 9 | 7 | 8 | Maxim | Maxim | Explain nemo judex in causa sua |
| 71 | PASS | 10 | 10 | 9 | Maxim | Legal_Concepts | habeas corpus meaning and legal signific... |
| 72 | PASS | 10 | 10 | 9 | Maxim | Maxim | What is the doctrine of proportionality? |
| 73 | PASS | 10 | 10 | 9 | Legal_Concepts | Newacts | define murder |
| 74 | PASS | 10 | 9 | 9 | Legal_Concepts | Legal_Concepts | What is the difference between bail and ... |
| 75 | PASS | 10 | 10 | 9 | Legal_Concepts | Legal_Concepts | Explain FIR and its importance |
| 76 | PASS | 10 | 10 | 9 | Legal_Concepts | Newacts, Judgment | theft |
| 77 | PASS | 10 | 10 | 9 | Scenario | Scenario, Judgment, Legis | My landlord is refusing to return my sec... |
| 78 | PASS | 10 | 10 | 9 | Scenario | Scenario, Legislation | My employer terminated me without any no... |
| 79 | PASS | 10 | 9 | 9 | Scenario | Scenario, Judgment, Legal | I received a legal notice for defamation... |
| 80 | PASS | 10 | 9 | 9 | Scenario | Scenario, Legislation, Co | Can police arrest someone without an FIR... |
| 81 | PASS | 10 | 9 | 9 | Scenario | Scenario, Legislation | What is the procedure to file a consumer... |
| 82 | PASS | 10 | 10 | 9 | Scenario-Long | Scenario, Judgment, Legis | My neighbour is encroaching on my land a... |
| 83 | PASS | 9 | 9 | 8 | Scenario-News | Legislation | What are the latest changes in GST laws ... |
| 84 | PASS | 9 | 9 | 8 | Scenario-News | Legislation, Judgment | Is cryptocurrency legal in India? What a... |
| 85 | PASS | 10 | 10 | 9 | Multi-Constitution+Judgment | Constitution, SCI_Judgmen | Explain Article 21 with landmark Supreme... |
| 86 | PASS | 9 | 9 | 8 | Multi-Constitution+Judgment | Constitution, Judgment | Article 14 equality with related case la... |
| 87 | FAIL | 0 | 0 | 0 | Multi-Newacts+SCI | — | Section 438 BNSS with Supreme Court prec... |
| 88 | PASS | 9 | 8 | 8 | Multi-Newacts+SCI | Newacts, SCI_Judgment | BNS Section 302 murder with relevant SC ... |
| 89 | PASS | 9 | 8 | 8 | Multi-Legislation+Judgment | Legislation, Judgment | Section 138 NI Act with important case l... |
| 90 | PASS | 10 | 10 | 9 | Multi-Legislation+Judgment | Legislation, Judgment | RERA Section 18 with recent court judgme... |
| 91 | PASS | 9 | 8 | 9 | Multi-Constitution+Maxim | Constitution, Maxim | Article 14 and res judicata |
| 92 | PASS | 10 | 10 | 9 | Multi-Scenario+Newacts | Newacts, Judgment | I was arrested without a warrant and the... |
| 93 | PASS | 9 | 8 | 8 | Multi-Newacts+SCI | Newacts, SCI_Judgment | Compare old Section 302 IPC with new BNS... |
| 94 | PASS | 9 | 8 | 8 | Multi-Mixed | Legislation, Judgment | What are the legal provisions for cyberc... |
| 95 | PASS | 9 | 8 | 9 | Edge-Abbreviation | Newacts | sec 302 ipc |
| 96 | PASS | 10 | 10 | 10 | Edge-Abbreviation | Constitution | art 21 constitution |
| 97 | PASS | 10 | 9 | 9 | Edge-Short | Scenario, Newacts, Judgme | Anticipatory bail |
| 98 | PASS | 10 | 10 | 9 | Edge-Complex | Scenario, Newacts | What happens if someone commits forgery ... |
| 99 | PASS | 10 | 10 | 10 | Edge-NonLegal | — | hello |
| 100 | PASS | 10 | 10 | 10 | Edge-NonLegal | — | Tell me about the weather today |

## Non-PASS Details

### #26 — FAIL — Legislation-Topic

**Query:** `director duties under companies act`  
**Agents:** Legislation  
**Scores:** Relevance=4, Completeness=4, Accuracy=6  
**Reasoning:** The response does not directly address the user's query about director duties under the Companies Act, focusing instead on the Director Identification Number (DIN) provisions. It fails to cover the actual duties of directors as outlined in the Act, such as fiduciary duties, duty of care, and compliance with statutory obligations. Additionally, while the sections mentioned are accurate, they do not pertain to the duties of directors, leading to a lack of relevance and completeness.  

### #57 — PARTIAL — Constitution

**Query:** `Article 21 of Indian Constitution`  
**Agents:** Constitution  
**Scores:** Relevance=8, Completeness=5, Accuracy=9  
**Reasoning:** The response provides a relevant definition of Article 21, addressing the core aspect of the user's query. However, it lacks depth in explaining the scope and application of the provision, such as mentioning relevant case law or interpretations, and does not distinguish between fundamental rights, DPSPs, and duties. The legal information is accurate, but the response is incomplete.  

### #59 — PARTIAL — Constitution

**Query:** `Article 14 right to equality`  
**Agents:** Constitution  
**Scores:** Relevance=8, Completeness=5, Accuracy=9  
**Reasoning:** The response accurately defines Article 14 and its core principle of equality before the law, which is relevant to the user's query. However, it lacks depth in explaining the scope and application of the provision, such as its implications in case law or how it interacts with other constitutional provisions. Additionally, it does not distinguish between fundamental rights, directive principles, and duties, which is essential for a comprehensive understanding.  

### #66 — PARTIAL — Maxim

**Query:** `Explain the doctrine of res judicata`  
**Agents:** Maxim  
**Scores:** Relevance=8, Completeness=6, Accuracy=8  
**Reasoning:** The response is relevant and accurately explains the meaning and legal interpretation of the doctrine of res judicata. However, it lacks depth in covering all aspects, such as providing relevant case law or statutory provisions, and practical examples of its application, which are essential for a comprehensive understanding.  

### #68 — PARTIAL — Maxim

**Query:** `What is estoppel in legal terms?`  
**Agents:** Maxim  
**Scores:** Relevance=8, Completeness=6, Accuracy=7  
**Reasoning:** The response provides a relevant definition of estoppel and explains its meaning, which is good. However, it lacks depth in covering the legal context, application, and relevant case law or statutory provisions, which are essential for a comprehensive understanding of the doctrine. While the information is mostly accurate, the absence of practical examples and citations affects the completeness of the response.  

### #87 — FAIL — Multi-Newacts+SCI

**Query:** `Section 438 BNSS with Supreme Court precedents on anticipatory bail`  
**Agents:** —  
**Scores:** Relevance=0, Completeness=0, Accuracy=0  
**Reasoning:** No response to evaluate (status: ERROR)  


## Lowest Scoring Prompts (Bottom 5)

- **#87** (0/30) [Multi-Newacts+SCI] — Section 438 BNSS with Supreme Court precedents on  — No response to evaluate (status: ERROR)
- **#26** (14/30) [Legislation-Topic] — director duties under companies act — The response does not directly address the user's query about director duties un
- **#68** (21/30) [Maxim] — What is estoppel in legal terms? — The response provides a relevant definition of estoppel and explains its meaning
- **#57** (22/30) [Constitution] — Article 21 of Indian Constitution — The response provides a relevant definition of Article 21, addressing the core a
- **#59** (22/30) [Constitution] — Article 14 right to equality — The response accurately defines Article 14 and its core principle of equality be

## Highest Scoring Prompts (Top 5)

- **#53** (30/30) [SCI-Landmark] — Kesavananda Bharati vs State of Kerala
- **#54** (30/30) [SCI-Landmark] — Maneka Gandhi vs Union of India case
- **#96** (30/30) [Edge-Abbreviation] — art 21 constitution
- **#99** (30/30) [Edge-NonLegal] — hello
- **#100** (30/30) [Edge-NonLegal] — Tell me about the weather today

---
*Generated by evaluate_agents.py on 2026-02-25 12:41:29*
