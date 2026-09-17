# Thread `641823a-ea6d-4adc-8210-bc79df3170d7`

Source: prod `api.lawttorney.com` (52.66.246.103), pulled 2026-08-20 09:47 UTC.

## Timeline (all UTC — IST = +5:30)

| # | Time | Endpoint | Query preview | Latency | Tokens | Agents | Outcome |
|---|---|---|---|---|---|---|---|
| 1 | 07:28:56 | `/pyapi/chat` | prepare SLP to be filed in the supreme court | **300 002 ms** | 653 685 | Drafting, Document, SCI_Judgment | **TIMED OUT** at gunicorn worker_timeout ceiling — response never persisted |
| 2 | 07:40:03 | `/pyapi/search/stream` | Please draft a SLP as an complete document to be filed in the Supreme Court of India by M/s Sharma & Associates Contractors Pr. Ltd. | 254 903 ms | 686 944 | Drafting, Document, SCI_Judgment | Succeeded → `messages.turn_number=1`, `ai_response` = **31 995 chars** |
| 3 | 08:35:19 | `/pyapi/search/stream` | Provide specific law points and Supreme Court rulings in favour of the Special Leave Petition… (Article 136, FAO(OS) 96/2025) | 56 479 ms | 688 304 | SCI_Judgment, Constitution, Judgment, Document | Succeeded → `messages.turn_number=2`, `ai_response` = **31 058 chars** |

Note: request 1 timed out at exactly 300 s (the documented gunicorn `worker_timeout` ceiling — same failure class as the reverted Phase-2 mixed-intent smoke on 2026-07-26). The user retried 11 minutes later (request 2) and it landed in 4:15, just under the wire. Fallbacks: 0. Blocks: 0. Errors: none.

## Uploaded files (5)

All stored under `/root/Lawtech-AI/uploads/641823a-ea6d-4adc-8210-bc79df3170d7/` on prod AND in Postgres `thread_files`. Raw copies are in this folder.

| file_id (chromadb suffix) | filename | size | pages | extracted chars |
|---|---|---|---|---|
| `31e2daaa` | 2025-7-5-_FINAL_APPEAL_BY_PCL_BEFORE_DIV_BENCH_Dl._HC.pdf | 48.2 MB | 851 | 1 669 612 |
| `31183edd` | 2026-2-4-_reply_to_Appeal_by_SACPL.pdf | 1.15 MB | 13 | 17 846 |
| `184bcc4a` | 2025-9-26-Reply_to_Appeal_filed_by_PCL_in_Delhi_High_Court_against_HC.docx | 583 KB | — | 140 091 |
| `b7613166` | 2026-2-14-_Rejoinder_by_PCL_to_the_Reply_Condonation.pdf | 2.23 MB | 20 | 32 616 |
| `243ba858` | 2026-5-29-_DB_Judgement_on_delay_for_filing_the_Appeal.pdf | 1.11 MB | 16 | 27 000 |

All ChromaDB collections named `inline_641823a-ea6d-4adc-8210-bc79df3170d7_<suffix>`. `primary` (used by Document agent) = `…_31e2daaa` (the 851-page appeal). No `upload_error`, no `ocr_status` flags.

The extra file `..._Rejoinder..._pike.pdf` (2 055 730 bytes, mode `-rw-r--r--`) sitting next to `..._Rejoinder....pdf` looks like a compressed/OCR'd sibling — same `31183edd` file_id prefix. Only one row for it in `thread_files` (the original), so the `_pike.pdf` is a working-copy left by the compression pass.

## Case details recovered from the drafts

- **Petitioner**: M/s Sharma & Associates, Contractor Pvt. Ltd., through MD Sh. Virender Kumar Sharma, F-133, Ashok Vihar Phase-I, Delhi-110052.
- **Respondent 1**: M/s Progressive Constructions Limited, Flat 203, Plot 29, Sector-6, Dwarka.
- **Proforma Respondent 2**: Sh. B. Majumdar, Sole Arbitrator, 1421 Sector-A Pocket-B&C, Vasant Kunj.
- **Impugned order**: judgment dated 29.05.2026, Hon'ble High Court of Delhi, C.M. APPL. Nos. 53193/2025 & 53197/2025 in FAO(OS) No. 96 of 2025 — HC condoned 26-day filing delay + 155-day re-filing delay.
- **Ground**: Article 136 SLP.

## Files in this folder

- `turn1_user_query.txt` / `turn1_ai_response.md` — the full SLP draft (31 995 chars).
- `turn2_user_query.txt` / `turn2_ai_response.md` — the follow-up "specific law points and SC rulings" turn (31 058 chars).
- The 6 raw uploaded PDFs/DOCX (including the `_pike.pdf` working copy).
