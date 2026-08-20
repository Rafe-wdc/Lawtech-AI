# Prod vs local — same thread, same prompts, same order

- **Prod:** `https://api.lawttorney.com` · thread `1e37e0a0-6c97-4d0d-a18f-2a0aaff0e4d9`
- **Local:** `http://127.0.0.1:5000` · thread `e608305f-c169-4b09-86ad-75aa894b704c`

Both runs sent byte-identical prompts in the same order inside a single
thread each. Divergences below are therefore attributable to environment
and to model non-determinism, not to differing inputs.

## Routing agreement

| # | Turn | Prod agents | Local agents | Same route? |
|---|------|-------------|--------------|-------------|
| 1 | Partition suit + temporary injunction | Drafting | Drafting | yes |
| 2 | Medical negligence damages suit | Drafting | Drafting | yes |
| 3 | Case laws for the Rupa partition scenari | Scenario, Judgment, SCI_Judgment | Scenario, Judgment, SCI_Judgment | yes |
| 4 | Section 65B electronic evidence case law | Newacts, Judgment, SCI_Judgment | Newacts, Judgment, SCI_Judgment | yes |
| 5 | Anticipatory bail case laws | Judgment, SCI_Judgment | Judgment, SCI_Judgment | yes |
| 6 | Medical negligence — plaintiff + defenda | Scenario, Judgment, SCI_Judgment | Scenario, Judgment, SCI_Judgment | yes |
| 7 | Same medical-negligence argument prompt  | Scenario | Scenario | yes |
| 8 | S.420 IPC — civil-not-criminal defence a | Newacts, Scenario, Judgment, SCI_Judgment | Newacts, Scenario, Judgment, SCI_Judgment | yes |
| 9 | Defective product liability under CPA 20 | Newacts, Scenario | Legislation, Scenario | **no** |
| 10 | Review the attached draft (PDF built fro | Document | Document | yes |

Routing agreed on **9/10** comparable turns.

## Cost, latency and output size

| # | Prod time | Local time | Prod tokens | Local tokens | Prod $ | Local $ | Prod chars | Local chars |
|---|-----------|------------|-------------|--------------|--------|---------|------------|-------------|
| 1 | 137.9s | 184.6s | 119,850 | 145,284 | $0.3334 | $0.4183 | 10,273 | 9,668 |
| 2 | 118.5s | 144.4s | 119,145 | 119,866 | $0.2758 | $0.3263 | 13,242 | 10,245 |
| 3 | 31.2s | 34.1s | 98,039 | 100,107 | $0.0341 | $0.0337 | 35,842 | 30,514 |
| 4 | 28.7s | 59.0s | 61,341 | 1,208,994 | $0.0210 | $0.3659 | 54,886 | 44,037 |
| 5 | 22.8s | 23.6s | 65,142 | 66,064 | $0.0218 | $0.0222 | 48,155 | 46,919 |
| 6 | 55.5s | 66.7s | 486,569 | 485,041 | $0.1478 | $0.1500 | 45,051 | 47,153 |
| 7 | 25.4s | 17.3s | 9,107 | 9,176 | $0.0010 | $0.0011 | 45,460 | 47,762 |
| 8 | 58.0s | 28.0s | 71,771 | 145,735 | $0.0218 | $0.0455 | 60,486 | 34,016 |
| 9 | 26.4s | 21.8s | 13,785 | 14,902 | $0.0016 | $0.0017 | 68,893 | 35,299 |
| 10 | 20.1s | 12.7s | 12,418 | 11,808 | $0.0055 | $0.0033 | 68,893 | 35,299 |
| **Total** | **524s** | **592s** | **1,057,167** | **2,306,977** | **$0.86** | **$1.37** | | |
