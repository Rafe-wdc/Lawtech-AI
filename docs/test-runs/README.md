# Single-thread prompt-suite run — 2026-08-17

Every prompt in [`docs/test prompt.txt`](../test%20prompt.txt) sent **in order inside one
thread**, against prod and against this working tree, with the full pipeline traced.

**Start here → [`FINDINGS.md`](FINDINGS.md)** (7 findings, 2 critical).

| File | What's in it |
|---|---|
| [`FINDINGS.md`](FINDINGS.md) | The analysis: what broke, the evidence, the code that causes it, suggested order of work |
| [`API_REQUESTS_AND_RESPONSES_PROD.md`](API_REQUESTS_AND_RESPONSES_PROD.md) | Per turn: user prompt verbatim, exact HTTP request (+ curl), what the server made of it, full response |
| [`API_REQUESTS_AND_RESPONSES_LOCAL.md`](API_REQUESTS_AND_RESPONSES_LOCAL.md) | Same, for the local run |
| [`LOCAL_FLOW_ANALYSIS.md`](LOCAL_FLOW_ANALYSIS.md) | Step-by-step behaviour: node timeline, every LLM call with tokens, and the server-log decision trace per turn |
| [`PROD_VS_LOCAL.md`](PROD_VS_LOCAL.md) | Routing agreement (9/10), cost, latency, output size |
| `prod_raw.json` / `local_raw.json` | Every SSE event with timings — the source data for all of the above |
| `control_freshthread_raw.json` | Control: turn 10's prompt + full PDF in a **fresh** thread (works correctly) |
| `control_samethread_fullpdf_raw.json` | Control: same prompt + same PDF in the **original** thread (returns turn 9 verbatim) |
| `local_log/turnNN.log` | Server log slice per turn, filtered to this run's request id |
| `*_turn1_draft.pdf`, `control_draft.pdf` | The PDF fixtures attached on turn 10 |

## Reproducing

```bash
python tests/run_thread_prompt_suite.py --base-url https://api.lawttorney.com --label prod
python tests/run_thread_prompt_suite.py --base-url http://127.0.0.1:5000 --label local \
    --capture-log logs/agent.log
python tests/gen_thread_report.py --raw docs/test-runs/local_raw.json \
    --api-report docs/test-runs/API_REQUESTS_AND_RESPONSES_LOCAL.md \
    --flow-report docs/test-runs/LOCAL_FLOW_ANALYSIS.md --log-dir docs/test-runs/local_log
```

`--only 3,5` runs a subset; `--thread-id <uuid>` continues an existing thread.
The API key is read from `API_KEYS` in `.env` and is never written to any output file.
