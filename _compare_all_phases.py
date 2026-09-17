"""Full 4-way comparison across pre-fix, Phase 1 shipped, Phase 2 local, Phase 2 prod."""
import json, re

FILES = [
    ('PROD PRE-fix       (buggy) ', '_verify_image_feedback_response.md', '_verify_image_feedback_response.json'),
    ('PROD Phase-1 shipped       ', '_verify_prod_after_fix_response.md', '_verify_prod_after_fix_response.json'),
    ('LOCAL Phase-2              ', '_verify_local_phase2_response.md',    '_verify_local_phase2_response.json'),
    ('PROD  Phase-2 (THIS RUN)   ', '_verify_prod_phase2_response.md',     '_verify_prod_phase2_response.json'),
]

def stats(md_path: str, json_path: str) -> dict:
    r = open(md_path, encoding='utf-8').read()
    d = json.load(open(json_path, encoding='utf-8-sig'))
    sections = sorted(set(re.compile(r'\bSection\s+(\d+[A-Z]?)').findall(r)))
    return {
        'chars':      len(r),
        'h2':         r.count('\n## '),
        'h3':         r.count('\n### '),
        'draft_hdrs': sum(1 for w in ['FIR','First Information','Seizure','Arrest','Charge']
                          if any(f'{p}{w}' in r for p in ['## Draft ', '### Draft '])),
        'sections':   sections[:24],
        'scc':        r.count('SCC'),
        'sci_pdf':    r.count('api.sci.gov.in'),
        'starts_fail':r[:60].lower().startswith(('i am unable','i could not','unfortunately')),
        'agents':     d.get('agents_used', []),
        'sources':    len(d.get('source', [])),
    }

for label, md, jf in FILES:
    try:
        s = stats(md, jf)
    except FileNotFoundError:
        print(f'--- {label} --- (file missing)\n')
        continue
    print(f'--- {label} ---')
    print(f'  chars           : {s["chars"]:>6}')
    print(f'  agents          : {s["agents"]}')
    print(f'  sources         : {s["sources"]}')
    print(f'  ## H2 headings  : {s["h2"]}')
    print(f'  ### H3 headings : {s["h3"]}')
    print(f'  draft headings  : {s["draft_hdrs"]} of 4')
    print(f'  starts w/ fail? : {s["starts_fail"]}')
    print(f'  SCC citations   : {s["scc"]}')
    print(f'  SCI PDF links   : {s["sci_pdf"]}')
    print(f'  Section refs    : {s["sections"]}')
    print()
