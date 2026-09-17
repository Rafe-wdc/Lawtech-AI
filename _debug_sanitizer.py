"""Force the sanitizer through the same input the server saw
(the captured `response` event content), verify it produces <br>."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

from tools.inline.markdown import sanitize_markdown

text = open("_ipc_bnss_comparison_run_postfix_md_v2.md", encoding="utf-8").read()
hdr_pos = text.find("## Raw SSE event stream")
start = text.find("```json", hdr_pos)
end = text.find("\n```", start + 10)
events = json.loads(text[start + len("```json"):end].strip())

response_content = next(
    e["content"] for e in events if e.get("type") == "response"
)
print(f"captured `response` event content length = {len(response_content)}")
print()

# The server's guardrail log said input to sanitizer was 3129 chars.
# But `response` event content is what was EMITTED, i.e. post-sanitizer.
# Let's confirm by running sanitize_markdown on it — if it's idempotent
# the length should be ~unchanged (sanitizer's own postcondition).
after = sanitize_markdown(response_content)
print(f"after re-sanitizing:      length = {len(after)}, delta = {len(after)-len(response_content):+d}")
print(f"has <br> in response:      {'<br>' in response_content}")
print(f"has <br> after re-sanitize: {'<br>' in after}")
print(f"identical?                 {response_content == after}")
print()

# Show the Key Conditions row in both
idx1 = response_content.find("| **Key Conditions**")
idx2 = after.find("| **Key Conditions**")
print("--- BEFORE (as emitted by server) ---")
print(repr(response_content[idx1:idx1+250]))
print()
print("--- AFTER re-sanitize ---")
print(repr(after[idx2:idx2+250]))
