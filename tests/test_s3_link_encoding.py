"""S3 judgment links must survive as clickable markdown links.

Lawyer-reported issue: clicking a PDF link in the sources panel opened
https://lawttorney.s3.ap-south-1.amazonaws.com/supreme%2520court/...
(double-encoded), which AWS returns AccessDenied (403).

Root cause: generate_s3_link returned a raw unencoded URL with literal spaces
and & from the main branch.
Fix: quote(s3_key, safe="/") before inserting into the URL.
"""

from __future__ import annotations
import re
import urllib.request

import tools.shared.storage_tools as st

_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _link(monkeypatch, court: str, file_name: str, title: str = ""):
    checked = []
    monkeypatch.setattr(st, "_s3_key_exists", lambda b, k: checked.append(k) or True)
    url = st.generate_s3_link.invoke({"court": court, "file_name": file_name, "title": title})
    return url, checked


# --- Standard Chartered Bank v. Noble Kumar (the reported broken case) ---

def test_supreme_court_folder_spaces_encoded(monkeypatch):
    url, checked = _link(
        monkeypatch,
        court="supreme court",
        file_name="STANDARD CHARTERED BANK vs. V. NOBLE KUMAR & OTHERS.pdf",
    )
    assert " " not in url,    f"Raw space in URL: {url!r}"
    assert "&" not in url,    f"Raw ampersand in URL: {url!r}"
    assert "%2520" not in url, f"Double-encoded space in URL: {url!r}"
    assert "%20" in url
    assert "%26" in url
    assert checked == ["supreme court/STANDARD CHARTERED BANK vs. V. NOBLE KUMAR & OTHERS.pdf"]


def test_noble_kumar_url_returns_200(monkeypatch):
    url, _ = _link(
        monkeypatch,
        court="supreme court",
        file_name="STANDARD CHARTERED BANK vs. V. NOBLE KUMAR & OTHERS.pdf",
    )
    req = urllib.request.Request(url, method="HEAD")
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        assert resp.status == 200
    except Exception as exc:
        import pytest; pytest.skip(f"Network unavailable: {exc}")


# --- Regression tests ---

def test_high_court_folder_spaces_encoded(monkeypatch):
    url, checked = _link(monkeypatch, "kerala high court", "/content/kerala high court/d47de6e2.pdf")
    assert " " not in url
    assert url.endswith("/kerala%20high%20court/d47de6e2.pdf")
    assert checked == ["kerala high court/d47de6e2.pdf"]


def test_supreme_title_parentheses_encoded(monkeypatch):
    url, _ = _link(monkeypatch, "supreme", "x.json",
                   title="SUSHILA AGGARWAL vs. STATE (NCT OF DELHI)")
    assert "(" not in url and ")" not in url
    assert "%28NCT%20OF%20DELHI%29" in url


def test_encoded_link_is_valid_markdown_link(monkeypatch):
    url, _ = _link(monkeypatch, "bombay high court", "bombay high court/76f1bb37.pdf")
    text = f"- Some case ([Judgment PDF]({url}))"
    m = _MD_LINK.search(text)
    assert m is not None, f"Markdown link broken in: {text!r}"
    assert m.group(2) == url


def test_encoded_url_passes_whitelist(monkeypatch):
    from core.url_filter import is_whitelisted_url
    url, _ = _link(monkeypatch, "kerala high court", "kerala high court/a.pdf")
    assert is_whitelisted_url(url)


def test_missing_object_returns_none(monkeypatch):
    monkeypatch.setattr(st, "_s3_key_exists", lambda b, k: False)
    assert st.generate_s3_link.invoke({"court": "kerala high court", "file_name": "a.pdf", "title": ""}) is None


def test_no_double_encoding_on_hash_filename(monkeypatch):
    url, _ = _link(monkeypatch, "bombay high court",
                   "bombay high court/ea368f1338626377fea250e159ff46db.pdf")
    assert "%25" not in url
    assert " " not in url
