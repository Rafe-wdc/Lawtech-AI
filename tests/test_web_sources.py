"""Web-grounded sources reach the sources panel, resolved to the real page.

Lawyer report 2026-09-23: an answer built from web search (a case not in
the index) shipped with no source at all — Google's grounding redirects had
been dropped from the payload since 2026-08-22. Now each redirect is
followed to its page, titled from the page, and kept; the answer text stays
URL-free.
"""

import asyncio
import os

os.environ.setdefault("EMBEDDING_SERVICE_URL", "http://127.0.0.1:9")

import httpx

from core.url_filter import sanitize_source_records
from core.web_sources import resolve_web_source_records

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AUZIYQ"


def _web(i, title="casemine.com"):
    return {"source_type": "judgment", "title": title, "web_url": f"{REDIRECT}{i}",
            "web_title": title, "agent_name": "Judgment"}


def _pdf():
    return {"source_type": "sci_judgment", "title": "A v. B",
            "doc_link": "https://api.sci.gov.in/supremecourt/x.pdf", "agent_name": "SCI_Judgment"}


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("AUZIYQ1"):
        return httpx.Response(302, headers={"location": "https://www.the-laws.com/case?caseId=1"})
    if url.endswith("AUZIYQ2"):
        return httpx.Response(302, headers={"location": "https://www.casemine.com/judgement/in/abc"})
    if url.endswith("AUZIYQ3"):
        raise httpx.ConnectTimeout("slow")
    if url.endswith("AUZIYQ4"):
        return httpx.Response(302, headers={"location": "https://elegalix.allahabadhighcourt.in/dl.do?id=9"})
    if url.endswith("AUZIYQ5"):                       # a second link to the same page
        return httpx.Response(302, headers={"location": "https://www.the-laws.com/case?caseId=1#para"})
    if "the-laws.com" in url:
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"},
                              content=b"<html><head><title>  Himanchal Singh vs Ram Autar Singh &amp; Ors. | The Laws </title></head><body>" + b"x" * 200_000)
    if "casemine.com" in url:
        return httpx.Response(403, headers={"content-type": "text/html"},
                              content=b"<html><head><title>403 Forbidden</title></head></html>")
    if "allahabadhighcourt.in" in url:
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4 ...")
    return httpx.Response(404)


def _resolve(records):
    return asyncio.run(resolve_web_source_records(records, transport=httpx.MockTransport(_handler)))


def test_redirects_become_real_pages_with_page_titles():
    out = _resolve([_web(1, "the-laws.com")])
    assert out[0]["web_url"] == "https://www.the-laws.com/case?caseId=1"
    assert out[0]["web_title"] == "Himanchal Singh vs Ram Autar Singh & Ors. | The Laws"
    assert out[0]["title"] == out[0]["web_title"] and out[0]["resolved"] is True


def test_blocked_page_keeps_its_real_url_and_shows_the_domain():
    out = _resolve([_web(2)])
    assert out[0]["web_url"] == "https://www.casemine.com/judgement/in/abc"
    assert out[0]["web_title"] == "casemine.com"          # not "403 Forbidden"


def test_bot_challenge_titles_fall_back_to_the_domain():
    from core.web_sources import _title_from
    assert _title_from("Client Challenge", "https://www.scribd.com/document/1") == "scribd.com"
    assert _title_from("Just a moment...", "https://www.casemine.com/x") == "casemine.com"
    assert _title_from("Section 16 in The Maharashtra Rent Control Act, 1999", "https://indiankanoon.org/doc/1/") ==         "Section 16 in The Maharashtra Rent Control Act, 1999"


def test_unreachable_page_keeps_the_redirect_and_the_domain():
    out = _resolve([_web(3, "supremetoday.ai")])
    assert out[0]["web_url"] == f"{REDIRECT}3"
    assert out[0]["web_title"] == "supremetoday.ai" and out[0]["resolved"] is False


def test_pdf_page_is_titled_by_its_domain():
    out = _resolve([_web(4, "allahabadhighcourt.in")])
    assert out[0]["web_url"].startswith("https://elegalix.allahabadhighcourt.in/")
    assert out[0]["web_title"] == "elegalix.allahabadhighcourt.in"


def test_two_links_to_the_same_page_collapse_and_pdf_records_pass_through():
    out = _resolve([_pdf(), _web(1), _web(5)])
    assert [r.get("web_url") for r in out] == [None, "https://www.the-laws.com/case?caseId=1"]
    assert out[0] == _pdf()


def test_at_most_ten_web_sources_are_kept():
    out = _resolve([_web(1)] + [_web(2)] * 12)
    assert sum(1 for r in out if r.get("web_url")) <= 10


def test_resolution_then_sanitiser_keeps_web_and_pdf_but_not_no_url_records():
    records = [_pdf(), _web(1), {"source_type": "legal_concepts", "title": "AI-Generated Analysis"}]
    out = sanitize_source_records(_resolve(records))
    assert [r["title"] for r in out] == ["A v. B", "Himanchal Singh vs Ram Autar Singh & Ors. | The Laws"]


def test_empty_and_non_web_input_is_returned_unchanged():
    assert _resolve([]) == []
    assert _resolve([_pdf()]) == [_pdf()]
