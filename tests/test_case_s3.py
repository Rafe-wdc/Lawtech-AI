"""core/case_s3.py — key rules and boto3 parameter construction (botocore.stub, no network)."""
from __future__ import annotations

import io
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

boto3 = pytest.importorskip("boto3")
from botocore.config import Config  # noqa: E402
from botocore.response import StreamingBody  # noqa: E402
from botocore.stub import Stubber  # noqa: E402

from core import case_s3  # noqa: E402
from core.case_s3 import CaseS3, HeadResult, content_disposition, meta_key, raw_key, text_key  # noqa: E402

CASE = "11111111-2222-3333-4444-555555555555"
DOC = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _client():
    return boto3.client("s3", region_name="ap-south-1", aws_access_key_id="AKIATESTTESTTESTTEST",
                        aws_secret_access_key="test-secret", config=Config(signature_version="s3v4"))


@pytest.fixture
def s3():
    return CaseS3("lawttorney-cases", "ap-south-1", client=_client())


def test_keys_are_built_from_ids_only():
    assert raw_key(CASE, DOC, "pdf") == f"cases/{CASE}/raw/{DOC}.pdf"
    assert raw_key(CASE, DOC, "") == f"cases/{CASE}/raw/{DOC}"
    assert text_key(CASE, DOC) == f"cases/{CASE}/extracted/{DOC}/text.jsonl"
    assert meta_key(CASE, DOC) == f"cases/{CASE}/extracted/{DOC}/meta.json"


def test_content_disposition_has_ascii_fallback_and_utf8_form():
    cd = content_disposition('Legal "Notice"; नोटिस.pdf')
    assert cd.startswith('attachment; filename="Legal Notice .pdf"; filename*=UTF-8\'\'')
    assert "%E0%A4%A8%E0%A5%8B" in cd                      # UTF-8 percent-encoded Devanagari
    assert content_disposition("नोटिस") .startswith('attachment; filename="document";')


def test_presign_put_signs_content_type_and_expiry(s3):
    out = s3.presign_put(raw_key(CASE, DOC, "pdf"), "application/pdf", 900)
    url = out["url"]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert "lawttorney-cases" in url and f"cases/{CASE}/raw/{DOC}.pdf" in url
    assert q["X-Amz-Expires"] == ["900"]
    assert "content-type" in q["X-Amz-SignedHeaders"][0].split(";")
    assert out["method"] == "PUT" and out["headers"] == {"Content-Type": "application/pdf"}
    assert timedelta(seconds=880) < out["expires_at"] - datetime.now(timezone.utc) <= timedelta(seconds=900)


def test_presign_get_carries_disposition_type_and_version(s3):
    url = s3.presign_get(raw_key(CASE, DOC, "pdf"), 300, "Legal Notice.pdf", "application/pdf", version_id="v7")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["response-content-disposition"] == [content_disposition("Legal Notice.pdf")]
    assert q["response-content-type"] == ["application/pdf"]
    assert q["versionId"] == ["v7"] and q["X-Amz-Expires"] == ["300"]
    assert "Legal" not in urllib.parse.urlparse(url).path          # filename never in the key


def test_head_maps_response_and_404(s3):
    key = raw_key(CASE, DOC, "pdf")
    with Stubber(s3.client()) as st:
        st.add_response("head_object", {"ContentLength": 1234, "ServerSideEncryption": "AES256",
                                        "ETag": '"abc123"', "VersionId": "v1", "ContentType": "application/pdf"},
                        {"Bucket": "lawttorney-cases", "Key": key})
        st.add_client_error("head_object", service_error_code="404", service_message="Not Found",
                            http_status_code=404, expected_params={"Bucket": "lawttorney-cases", "Key": key})
        st.add_client_error("head_object", service_error_code="403", service_message="Forbidden",
                            http_status_code=403, expected_params={"Bucket": "lawttorney-cases", "Key": key})
        assert s3.head(key) == HeadResult(True, 1234, "AES256", "abc123", "v1", "application/pdf")
        assert s3.head(key) == HeadResult(False)
        with pytest.raises(Exception):
            s3.head(key)                                           # permission problems surface, not swallowed


def test_read_range_requests_exactly_the_window(s3):
    key = raw_key(CASE, DOC, "pdf")
    data = b"%PDF-1.4 hello"
    with Stubber(s3.client()) as st:
        st.add_response("get_object", {"Body": StreamingBody(io.BytesIO(data), len(data))},
                        {"Bucket": "lawttorney-cases", "Key": key, "Range": "bytes=0-8191", "VersionId": "v1"})
        assert s3.read_range(key, 0, 8191, version_id="v1") == data


def test_delete_targets_the_version(s3):
    key = raw_key(CASE, DOC, "pdf")
    with Stubber(s3.client()) as st:
        st.add_response("delete_object", {}, {"Bucket": "lawttorney-cases", "Key": key, "VersionId": "v1"})
        s3.delete(key, version_id="v1")


def test_put_bytes_sets_sse(s3):
    key = text_key(CASE, DOC)
    with Stubber(s3.client()) as st:
        st.add_response("put_object", {"VersionId": "v9"},
                        {"Bucket": "lawttorney-cases", "Key": key, "Body": b"{}", "ContentType": "application/x-ndjson",
                         "ServerSideEncryption": "AES256"})
        assert s3.put_bytes(key, b"{}", "application/x-ndjson") == "v9"


def test_singleton_refuses_an_empty_bucket(monkeypatch):
    monkeypatch.setattr(case_s3, "_s3", None)
    monkeypatch.setattr(case_s3._settings, "CASE_S3_BUCKET", "", raising=False)
    with pytest.raises(RuntimeError):
        case_s3.get_case_s3()
    monkeypatch.setattr(case_s3._settings, "CASE_S3_BUCKET", "lawttorney-cases", raising=False)
    monkeypatch.setattr(case_s3._settings, "CASE_S3_REGION", "ap-south-1", raising=False)
    inst = case_s3.get_case_s3()
    assert inst.bucket == "lawttorney-cases" and inst.region == "ap-south-1" and inst.sse == "AES256"
    monkeypatch.setattr(case_s3, "_s3", None)


# --- slice 3: worker-side download and purge -------------------------------------

def test_download_to_file_streams_and_hashes(s3, tmp_path):
    import hashlib
    import io
    from botocore.response import StreamingBody
    data = b"%PDF-1.4 hello " * 1000
    with Stubber(s3.client()) as st:
        st.add_response("get_object", {"Body": StreamingBody(io.BytesIO(data), len(data)), "ContentLength": len(data)},
                        {"Bucket": s3.bucket, "Key": "cases/x/raw/y.pdf", "VersionId": "v9"})
        n, sha = s3.download_to_file("cases/x/raw/y.pdf", str(tmp_path / "y.pdf"), "v9", chunk_size=1024)
    assert n == len(data) and sha == hashlib.sha256(data).hexdigest()
    assert (tmp_path / "y.pdf").read_bytes() == data


def test_delete_all_versions_removes_versions_and_markers_and_refuses_bad_prefixes(s3):
    import uuid
    cid, did = str(uuid.uuid4()), str(uuid.uuid4())
    prefix = f"cases/{cid}/"
    raw, txt = f"{prefix}raw/{did}.pdf", f"{prefix}extracted/{did}/text.jsonl"
    with Stubber(s3.client()) as st:
        st.add_response("list_object_versions",
                        {"Versions": [{"Key": raw, "VersionId": "v1"}, {"Key": txt, "VersionId": "v2"}],
                         "DeleteMarkers": [{"Key": raw, "VersionId": "m1"}]},
                        {"Bucket": s3.bucket, "Prefix": prefix})
        st.add_response("delete_objects", {},
                        {"Bucket": s3.bucket,
                         "Delete": {"Objects": [{"Key": raw, "VersionId": "v1"}, {"Key": txt, "VersionId": "v2"},
                                                {"Key": raw, "VersionId": "m1"}], "Quiet": True}})
        assert s3.delete_all_versions(prefix) == 3
    for bad in ("", "cases/", f"cases/{cid}", f"cases/{cid}/raw/", "other/x/", f"cases/{cid}/raw/{did}.pdf/evil"):
        with pytest.raises(ValueError):
            s3.delete_all_versions(bad)
    with Stubber(s3.client()) as st:
        st.add_response("list_object_versions", {}, {"Bucket": s3.bucket, "Prefix": prefix})
        assert s3.delete_all_versions(prefix) == 0                             # nothing listed, nothing called
