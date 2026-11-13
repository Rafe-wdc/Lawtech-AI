"""Reading a High Court index entry in either layout (core/judgment_index.py)."""
from __future__ import annotations

import pytest

from core import judgment_index as jx


def test_the_agents_read_judgements_v2_by_default():
    assert jx.INDEX == "judgements_v2" and not jx.LEGACY
    assert jx.TEXT_FIELD == "full_text" and jx.CASE_NO_FIELD == "case_no"
    assert jx.ODISHA_COURT == "odisha high court"


def test_fields_are_read_from_either_layout():
    v2 = {"full_text": "order", "case_no": "BA/3072/2026", "petitioner_names": "GANESH KATHEWADE",
          "bench_judge_names": ["MILIND N. JADHAV"]}
    old = {"page_content": "order", "case_number": "39182008", "petitioner_names": ["SREEMATHY"],
           "judge_names": ["A", "B"]}
    assert jx.text(v2) == jx.text(old) == "order"
    assert jx.case_number(v2) == "BA/3072/2026" and jx.case_number(old) == "39182008"
    assert jx.names(v2["petitioner_names"]) == ["GANESH KATHEWADE"]     # one string in v2's new entries
    assert jx.names(old["petitioner_names"]) == ["SREEMATHY"]
    assert jx.names(None) == [] and jx.judges(old) == ["A", "B"]


def test_a_link_into_the_public_bucket_is_kept(monkeypatch):
    monkeypatch.setattr("tools.shared.storage_tools._s3_key_exists", lambda b, k: True)
    url = "https://lawttorney.s3.ap-south-1.amazonaws.com/kerala%20high%20court/x.pdf"
    src = {"pdf_links": [{"url": url}], "s3_bucket": "lawttorney", "s3_key": "kerala high court/x.pdf"}
    assert jx.pdf_url(src) == url


def test_a_missing_file_in_the_public_bucket_gets_no_link(monkeypatch):
    monkeypatch.setattr("tools.shared.storage_tools._s3_key_exists", lambda b, k: False)
    src = {"pdf_links": [{"url": "https://lawttorney.s3.ap-south-1.amazonaws.com/x.pdf"}],
           "s3_bucket": "lawttorney", "s3_key": "x.pdf"}
    assert jx.pdf_url(src) is None


def test_a_link_readers_cannot_open_is_left_out(monkeypatch):
    monkeypatch.setattr(jx, "_bucket_is_public", lambda host, url: False)
    src = {"pdf_links": [{"url": "https://bhc-pdfs.s3.ap-south-1.amazonaws.com/bombay/x.pdf"}],
           "s3_bucket": "bhc-pdfs", "s3_key": "bombay/x.pdf"}
    assert jx.pdf_url(src) is None


def test_an_old_index_entry_builds_its_link_from_the_file_name(monkeypatch):
    calls = []

    class _Link:
        @staticmethod
        def invoke(args):
            calls.append(args)
            return "https://lawttorney.s3.ap-south-1.amazonaws.com/bombay%20high%20court/f.pdf"
    monkeypatch.setattr("tools.shared.storage_tools.generate_s3_link", _Link)
    url = jx.pdf_url({"court_name": "bombay high court", "source": "Bombay 1-2\\f.pdf"}, "A vs B")
    assert url.endswith("f.pdf")
    assert calls == [{"court": "bombay high court", "file_name": "Bombay 1-2\\f.pdf", "title": "A vs B"}]


@pytest.mark.parametrize("court", ["Odisha High Court", "orissa high court", "odisa high court"])
def test_every_odisha_spelling_filters_on_the_index_label(court):
    from agents.judgment import _normalize_court_filter
    assert _normalize_court_filter(court) == ("odisha high court", False)
