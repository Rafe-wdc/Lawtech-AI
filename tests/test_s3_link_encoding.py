"""S3 judgment links must survive as clickable markdown links.

Lawyer-reported answer (2026-09-17): under "Supporting Case Authority" the
entry for SHAJIL C.V. vs STATE OF KERALA showed a raw URL instead of a link,
because the key `kerala high court/<hash>.pdf` was inserted unencoded and the
first space ended the markdown link. Supreme Court titles such as
`STATE (NCT OF DELHI)` break it the same way with parentheses.
"""

from __future__ import annotations

import re

import tools.shared.storage_tools as st

_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _link(monkeypatch, court, file_name, title=""):
    checked = []
    monkeypatch.setattr(st, "_s3_key_exists", lambda bucket, key: checked.append(key) or True)
    url = st.generate_s3_link.invoke({"court": court, "file_name": file_name, "title": title})
    return url, checked


def test_high_court_folder_with_spaces_is_encoded(monkeypatch):
    url, checked = _link(monkeypatch, "kerala high court",
                         "/content/kerala high court/d47de6e2.pdf")
    assert url.endswith("/kerala%20high%20court/d47de6e2.pdf")
    assert " " not in url
    # the existence check still uses the raw key boto3 expects
    assert checked == ["kerala high court/d47de6e2.pdf"]


def test_supreme_court_title_with_parentheses_is_encoded(monkeypatch):
    url, _ = _link(monkeypatch, "supreme", "x.json",
                   title="SUSHILA AGGARWAL AND OTHERS vs. STATE (NCT OF DELHI) AND ANOTHER")
    assert "(" not in url and ")" not in url and " " not in url
    assert "%28NCT%20OF%20DELHI%29" in url


def test_encoded_link_parses_as_one_markdown_link(monkeypatch):
    url, _ = _link(monkeypatch, "bombay high court", "bombay high court/76f1bb37.pdf")
    line = f"- Dr. Bhagirath Bansilal Jaju vs The State of Maharashtra, bombay high court, 2021 ([Judgment PDF]({url}))"
    m = _MD_LINK.search(line)
    assert m and m.group(2) == url


def test_encoded_url_is_still_whitelisted(monkeypatch):
    from core.url_filter import is_whitelisted_url
    url, _ = _link(monkeypatch, "kerala high court", "kerala high court/a.pdf")
    assert is_whitelisted_url(url)


def test_missing_object_still_returns_none(monkeypatch):
    monkeypatch.setattr(st, "_s3_key_exists", lambda bucket, key: False)
    assert st.generate_s3_link.invoke(
        {"court": "kerala high court", "file_name": "a b.pdf", "title": ""}) is None
