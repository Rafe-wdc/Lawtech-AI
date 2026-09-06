"""Extract the actual `response` event from the SSE dump and check
whether the sanitizer's <br> folding made it into the payload."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

text = open("_ipc_bnss_comparison_run_postfix_md_v2.md", encoding="utf-8").read()
raw_sse_hdr = "## Raw SSE event stream"
hdr_pos = text.find(raw_sse_hdr)
start = text.find("```json", hdr_pos)
end = text.find("\n```", start + 10)
events_json = text[start + len("```json"):end].strip()
events = json.loads(events_json)

for e in events:
    if e.get("type") == "response":
        content = e.get("content", "")
        idx = content.find("| **Key Conditions**")
        print("=== response event content — Key Conditions slice ===")
        print(repr(content[idx:idx+400]))
        print()
        print("has <br>:", "<br>" in content)
        print("has raw \\n inside Key Conditions cell:",
              "\n" in content[idx+30:idx+400])
        break
