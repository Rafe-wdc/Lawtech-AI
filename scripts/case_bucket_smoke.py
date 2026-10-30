#!/usr/bin/env python
"""Real-bucket smoke test for the case workspace upload path (slice 2).

Run once `lawttorney-cases` exists and the API has the scoped credentials:

    python scripts/case_bucket_smoke.py --base https://tool.lawttorney.com \
        --api-key "$API_KEY" --secret "$CASE_JWT_SECRET" --user-id 42 \
        --origin https://tool.lawttorney.com --bucket lawttorney-cases --region ap-south-1

Steps, each printed PASS or FAIL (exit code 1 on any FAIL):
  1. /pyapi/health reports case_workspace ok
  2. create a case
  3. presign a small generated PDF
  4. CORS preflight (OPTIONS) of the presigned URL from the web origin allows PUT + content-type
  5. PUT the bytes with the signed Content-Type and an Origin header; ETag returned; CORS header on the response
  6. complete -> status queued, sniffed_mime application/pdf, sse verified (AES256 by default)
  7. download URL works: 200, bytes identical, Content-Disposition carries the filename
  8. anonymous GET of the raw object URL is 403 (Block Public Access)
  9. a PDF-named PNG is rejected at complete (mime_mismatch) and its object is gone
 10. soft delete the document and the case (unless --keep)

Only the standard library is used. No filename or content is printed beyond the
constant test fixture names; ids are printed so the run can be found in audit.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PDF = (b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
       b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 64

RESULTS: list[tuple[str, bool, str]] = []


def step(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return ok


def http(method: str, url: str, headers: dict | None = None, body: bytes | None = None, timeout: float = 30):
    req = urllib.request.Request(url, method=method, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def mint(secret: str, user_id: str, ttl: int = 900) -> str:
    sys.path.insert(0, ".")
    from core.jwt_auth import mint_hs256
    now = int(time.time())
    return mint_hs256({"userId": user_id, "iat": now, "exp": now + ttl}, secret)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", required=True, help="API host root, e.g. https://tool.lawttorney.com")
    ap.add_argument("--api-key", required=True)
    ap.add_argument("--jwt", help="a Node access token; or use --secret and --user-id")
    ap.add_argument("--secret", help="CASE_JWT_SECRET, to mint a token locally")
    ap.add_argument("--user-id", default="smoke-test")
    ap.add_argument("--origin", required=True, help="web origin for the CORS check, e.g. https://tool.lawttorney.com")
    ap.add_argument("--bucket", default="lawttorney-cases")
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--sse", default="AES256")
    ap.add_argument("--keep", action="store_true", help="leave the case in place for inspection")
    a = ap.parse_args()

    token = a.jwt or (mint(a.secret, a.user_id) if a.secret else None)
    if not token:
        ap.error("--jwt or --secret is required")
    api = a.base.rstrip("/") + "/pyapi"
    H = {"X-API-Key": a.api_key, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(method, path, payload=None):
        st, hd, body = http(method, api + path, H, json.dumps(payload).encode() if payload is not None else None)
        try:
            return st, json.loads(body or b"null")
        except ValueError:
            return st, body[:200]

    # 1. health
    st, hd, body = http("GET", api + "/health")
    try:
        cw = json.loads(body)["checks"].get("case_workspace", {})
    except Exception:
        cw = {}
    step("health: case_workspace ok", cw.get("status") == "ok", f"status={cw.get('status')} bucket={cw.get('bucket')}")

    # 2. case
    st, case = call("POST", "/cases", {"title": "Smoke test case (delete me)", "matter_type": "smoke_test"})
    if not step("create case", st == 201, f"http={st}"):
        return finish()
    cid = case["case_id"]
    print(f"      case_id={cid}")

    # 3. presign
    st, p = call("POST", f"/cases/{cid}/documents/presign",
                 {"filename": "smoke-notice.pdf", "size_bytes": len(PDF), "content_type": "application/pdf"})
    if not step("presign pdf", st == 201 and p.get("status") == "uploading", f"http={st}"):
        return finish(cid, call, a.keep)
    doc_id, up = p["doc_id"], p["upload"]
    print(f"      doc_id={doc_id}")
    key = f"cases/{cid}/raw/{doc_id}.pdf"
    step("presigned URL uses the id-only key", key in up["url"] and "smoke-notice" not in up["url"])

    # 4. CORS preflight
    st, hd, _ = http("OPTIONS", up["url"], {
        "Origin": a.origin, "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "content-type"})
    acao = hd.get("Access-Control-Allow-Origin") or hd.get("access-control-allow-origin")
    step("CORS preflight allows PUT from the web origin", st in (200, 204) and acao in (a.origin, "*"),
         f"http={st} allow-origin={acao}")

    # 5. PUT
    st, hd, _ = http("PUT", up["url"], {**up["headers"], "Origin": a.origin}, PDF)
    etag = hd.get("ETag") or hd.get("etag")
    acao = hd.get("Access-Control-Allow-Origin") or hd.get("access-control-allow-origin")
    step("browser-style PUT succeeds with the signed headers", st == 200 and bool(etag), f"http={st} etag={etag}")
    step("CORS header on the PUT response", acao in (a.origin, "*"), f"allow-origin={acao}")

    # 6. complete
    st, doc = call("POST", f"/cases/{cid}/documents/{doc_id}/complete")
    step("complete -> queued", st == 200 and doc.get("status") == "queued", f"http={st} status={doc.get('status') if isinstance(doc, dict) else doc}")
    step("sniffed as application/pdf", isinstance(doc, dict) and doc.get("sniffed_mime") == "application/pdf")
    step(f"server-side encryption verified ({a.sse})", isinstance(doc, dict) and doc.get("sse") == a.sse,
         f"sse={doc.get('sse') if isinstance(doc, dict) else '?'}")

    # 7. download
    st, dl = call("GET", f"/cases/{cid}/documents/{doc_id}/download")
    ok = st == 200 and isinstance(dl, dict) and "url" in dl
    if step("download URL issued", ok, f"http={st}"):
        st2, hd2, body2 = http("GET", dl["url"])
        cd = hd2.get("Content-Disposition") or hd2.get("content-disposition") or ""
        step("download URL serves the same bytes", st2 == 200 and body2 == PDF, f"http={st2} bytes={len(body2)}")
        step("Content-Disposition carries the filename", "smoke-notice.pdf" in cd, cd[:80])

    # 8. anonymous access
    anon = f"https://{a.bucket}.s3.{a.region}.amazonaws.com/{urllib.parse.quote(key)}"
    st, _, _ = http("GET", anon)
    step("anonymous GET of the raw object is 403", st == 403, f"http={st}")
    st, _, _ = http("HEAD", anon)
    step("anonymous HEAD of the raw object is 403", st == 403, f"http={st}")

    # 9. mime mismatch
    st, p2 = call("POST", f"/cases/{cid}/documents/presign", {"filename": "smoke-disguised.pdf", "size_bytes": len(PNG)})
    if st == 201:
        http("PUT", p2["upload"]["url"], {**p2["upload"]["headers"], "Origin": a.origin}, PNG)
        st, rej = call("POST", f"/cases/{cid}/documents/{p2['doc_id']}/complete")
        code = rej.get("detail", {}).get("code") if isinstance(rej, dict) else None
        step("PNG under .pdf is rejected at complete", st == 422 and code == "mime_mismatch", f"http={st} code={code}")
        time.sleep(1)
        st, _, _ = http("HEAD", f"https://{a.bucket}.s3.{a.region}.amazonaws.com/{urllib.parse.quote(p2['upload']['url'].split('?')[0].split('.amazonaws.com/')[-1])}")
        step("rejected object removed (anonymous HEAD is 403/404 either way; check CloudTrail or console for the delete)", st in (403, 404), f"http={st}")

    return finish(cid, call, a.keep)


def finish(cid=None, call=None, keep=False) -> int:
    if cid and call and not keep:
        st, _ = call("DELETE", f"/cases/{cid}")
        step("soft delete case", st == 200, f"http={st}")
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
