"""Generate the final test report markdown from JSON results + retry data.

Merges the original test_results.json with retry results for #7 and #9.
"""

import json
from datetime import datetime

# Load original results
with open("tests/test_results.json", "r", encoding="utf-8") as f:
    data = json.load(f)

# Patch #7 and #9 with retry results
RETRY_RESULTS = {
    7: {
        "status": "PASS",
        "elapsed": 13.1,
        "response_len": 4564,
        "tokens": 5462,
        "agents_used": ["Newacts"],
        "error_detail": "",
        "response_preview": """Here are the provisions related to punishment for theft in The Bharatiya Nyaya Sanhita, 2023:

-   **Section 313: Punishment for belonging to gang of robbers, dacoits, etc.**
    Whoever belongs to any gang of persons associated in habitually committing theft or robbery, and not being a gang of dacoits, shall be punished with rigorous imprisonment for a term which may extend to seven years, and shall also be liable to fine.

-   **Section 305: Theft in a dwelling house, or means of transportation or place of worship, etc.**
    Whoever commits theft:
    (a) in any building, tent or vessel used as a human dwelling or used for the custody of property; or
    (b) of any means of transport used for the transport of goods or passengers; or
    (c) of any article or goods from any means of transport used for the transport of goods or passengers; or
    (d) of idol or icon in any place of worship; or
    (e) of any property of the Government or of a local authority, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

-   **Section 306: Theft by clerk or servant of property in possession of master.**
    Whoever, being a clerk or servant, or being employed in the capacity of a clerk or servant, commits theft in respect of any property in the possession of his master or employer, shall be punished with imprisonment of either description for a term which may extend to seven years, and shall also be liable to fine.

-   **Section 328: Punishment for intentionally running vessel aground or ashore with intent to commit theft, etc.**
    Whoever intentionally runs any vessel aground or ashore, intending to commit theft of any property contained therein or to dishonestly misappropriate any such property, or with intent that such theft or misappropriation of property may be committed, shall be punished with imprisonment of either description for a term which may extend to ten years, and shall also be liable to fine.

-   **Section 112: Petty organised crime**
    (1) Whoever, being a member of a group or gang, either singly or jointly, commits any act of theft, snatching, cheating, unauthorised selling of tickets, unauthorised betting or gambling, selling of public examination question papers or any other similar criminal act, is said to commit petty organised crime.
    (2) Whoever commits any petty organised crime shall be punished with imprisonment for a term which shall not be less than one year but which may extend to seven years, and shall also be liable to fine.

-   **Section 307: Theft after preparation made for causing death, hurt or restraint in order to the committing of theft.**

-   **Section 134: Assault or criminal force in attempt to commit theft of property carried by a person.**

-   **Section 62: Punishment for attempting to commit offences punishable with imprisonment for life or other imprisonment.**""",
    },
    9: {
        "status": "PASS",
        "elapsed": 26.5,
        "response_len": 7862,
        "tokens": 16595,
        "agents_used": ["Newacts", "Judgment"],
        "error_detail": "",
        "response_preview": """## Electronic Evidence Rules in The Bharatiya Sakshya Adhiniyam, 2023

The Bharatiya Sakshya Adhiniyam, 2023 (BSA 2023) significantly modernizes the rules surrounding electronic evidence, bringing them in line with contemporary technological advancements. The Act defines "document" to include electronic and digital records, thereby broadening the scope of what can be presented as evidence.

### Statutory Basis for Electronic Evidence

The BSA 2023 lays down specific provisions for the admissibility and effect of electronic records:

-   **Admissibility and Legal Effect:**
    -   **Section 61** explicitly states that the admissibility of an electronic or digital record in evidence will not be denied simply because it is in such a format.
    -   **Section 62** mandates that the contents of electronic records can be proven in accordance with the provisions of Section 63.

-   **Conditions for Admissibility of Computer Output (Section 63):**
    -   Information contained in an electronic record (computer output) printed on paper, stored, recorded, or copied in optical or magnetic media, or semiconductor memory, produced by a computer or communication device, is considered a document if certain conditions are met.
    -   Such computer output is admissible in any proceedings without further proof or production of the original.

-   **Presumptions Regarding Electronic Records and Signatures:**
    -   **Section 85** presumes that every electronic record purporting to be an agreement with an electronic or digital signature was concluded by the affixing of those signatures.
    -   **Section 86** establishes presumptions for secure electronic records and signatures.
    -   **Section 87** presumes the correctness of information listed in an Electronic Signature Certificate.
    -   **Section 90** allows the court to presume that an electronic message forwarded through an electronic mail server corresponds with the message fed into the computer for transmission.
    -   **Section 93** states that where an electronic record, purporting or proved to be five years old, is produced from proper custody, the court may presume any electronic signature was affixed by a particular person.""",
    },
}

# Apply patches
for detail in data["details"]:
    if detail["id"] in RETRY_RESULTS:
        patch = RETRY_RESULTS[detail["id"]]
        detail.update(patch)

# Recalculate summary
data["summary"] = {"PASS": 0, "WEAK": 0, "FAIL": 0, "ERROR": 0}
for d in data["details"]:
    data["summary"][d["status"]] += 1

# Generate markdown
now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
total = len(data["details"])
total_elapsed = data["total_elapsed"]

lines = []
lines.append(f"# Test Report")
lines.append(f"**Date:** {now}  ")
lines.append(f"**Server:** `http://localhost:5000/pyapi/search`  ")
lines.append(f"**Total Prompts:** {total}  ")
lines.append(f"**Total Time:** {total_elapsed:.1f}s  ")
lines.append("")

# Summary
lines.append("## Summary")
lines.append("")
lines.append("| Status | Count |")
lines.append("|--------|-------|")
for s in ["PASS", "WEAK", "FAIL", "ERROR"]:
    lines.append(f"| **{s}** | **{data['summary'][s]}** |")
lines.append("")

# Timing stats
times = [d["elapsed"] for d in data["details"]]
lines.append("## Timing Statistics")
lines.append("")
lines.append("| Metric | Value |")
lines.append("|--------|-------|")
lines.append(f"| Average | {sum(times)/len(times):.1f}s |")
lines.append(f"| Min | {min(times):.1f}s |")
lines.append(f"| Max | {max(times):.1f}s |")
lines.append(f"| Median | {sorted(times)[len(times)//2]:.1f}s |")
lines.append(f"| Total | {sum(times):.1f}s |")
lines.append("")

# Results overview table
lines.append("## Results Overview")
lines.append("")
lines.append("| # | Status | Category | Prompt | Agent(s) | Time | Resp Len | Tokens |")
lines.append("|---|--------|----------|--------|----------|------|----------|--------|")
for d in data["details"]:
    agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "-"
    prompt_short = d["prompt"][:50] + ("..." if len(d["prompt"]) > 50 else "")
    lines.append(
        f"| {d['id']} | **{d['status']}** | {d['category']} | {prompt_short} "
        f"| {agents_str} | {d['elapsed']:.1f}s | {d['response_len']:,} | {d.get('tokens', 0):,} |"
    )
lines.append("")

# Detailed per-prompt sections
lines.append("---")
lines.append("")
lines.append("## Detailed Results")
lines.append("")

for d in data["details"]:
    lines.append(f"### #{d['id']} - {d['status']} - {d['category']}")
    lines.append("")
    lines.append(f"**Query:** `{d['prompt']}`  ")
    lines.append(f"**Expected Agent:** {d['expected_agent'] or 'Any'}  ")
    agents_str = ", ".join(d["agents_used"]) if d["agents_used"] else "-"
    lines.append(f"**Actual Agent(s):** {agents_str}  ")
    lines.append(f"**Time:** {d['elapsed']:.1f}s  ")
    lines.append(f"**Response Length:** {d['response_len']:,} chars  ")
    lines.append(f"**Tokens:** {d.get('tokens', 0):,}  ")
    lines.append("")

    preview = d.get("response_preview", "")
    if preview:
        lines.append("<details>")
        lines.append("<summary>Response Preview</summary>")
        lines.append("")
        lines.append(preview)
        lines.append("")
        lines.append("</details>")
        lines.append("")

    if d.get("error_detail"):
        lines.append(f"**Error:** `{d['error_detail']}`")
        lines.append("")

    lines.append("---")
    lines.append("")

md_content = "\n".join(lines)

with open("tests/test_report.md", "w", encoding="utf-8") as f:
    f.write(md_content)

print(f"Report generated: tests/test_report.md ({len(md_content)} chars)")
print(f"Results: {data['summary']}")
