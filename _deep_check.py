"""Look at the FULL response to see if sanitizer folded ANYTHING."""
import sys, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

text = open("_ipc_bnss_comparison_run_postfix_md_v2.md", encoding="utf-8").read()
hdr_pos = text.find("## Raw SSE event stream")
start = text.find("```json", hdr_pos)
end = text.find("\n```", start + 10)
events = json.loads(text[start + len("```json"):end].strip())
resp = next(e["content"] for e in events if e.get("type") == "response")

# Print the full response with markers for `\n`
print(f"total len = {len(resp)}")
print(f"count of \\n = {resp.count(chr(10))}")
print(f"count of `| ` (row starts) = {resp.count('| ')}")
print(f"count of ` |` (row ends) = {resp.count(' |')}")
print()

# Show around each newline that is between rows
print("--- structure walk ---")
for i, line in enumerate(resp.split("\n")):
    tag = ""
    if line.strip().startswith("|"):
        tag = "PIPE-START"
        if line.rstrip().endswith("|"):
            tag += " CLOSED"
        else:
            tag += " OPEN"
    elif line.strip() == "":
        tag = "BLANK"
    elif line.strip() == "---":
        tag = "BARE-HR"
    else:
        tag = "PROSE/CONTINUATION"
    preview = line[:80].replace("\n", "\\n")
    print(f"{i:3d} [{tag:20s}] {preview}")
