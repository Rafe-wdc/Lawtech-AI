"""Request and response schemas for the case workspace (slice 1: cases).

``matter_type``, ``stage`` and ``our_side`` are free text bounded by length and
normalised to lower-case slugs; ``config/case_types.py`` only supplies
suggestions for the UI. Nothing here is a hard-coded enum.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

TITLE_MAX = 200
SHORT_MAX = 200
SLUG_MAX = 64
CASE_NUMBER_MAX = 100
CNR_MAX = 32
OBJECTIVE_MAX = 2000

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CNR_RE = re.compile(r"^[A-Z0-9]{0,32}$")


def _strip(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    return " ".join(str(v).split())


def _slug(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = _SLUG_RE.sub("_", str(v).strip().lower()).strip("_")
    return s[:SLUG_MAX]


def _cnr(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    s = re.sub(r"[\s\-/]", "", str(v)).upper()
    if not _CNR_RE.match(s):
        raise ValueError("cnr must be letters and digits only (up to 32 characters)")
    return s


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=1, max_length=TITLE_MAX)
    client_name: str = Field("", max_length=SHORT_MAX)
    our_side: str = Field("", max_length=SLUG_MAX)
    matter_type: str = Field("", max_length=SLUG_MAX)
    stage: str = Field("", max_length=SLUG_MAX)
    forum: str = Field("", max_length=SHORT_MAX)
    case_number: str = Field("", max_length=CASE_NUMBER_MAX)
    cnr: str = Field("", max_length=CNR_MAX)
    objective: str = Field("", max_length=OBJECTIVE_MAX)

    _v_text = field_validator("title", "client_name", "forum", "case_number", "objective", mode="before")(_strip)
    _v_slug = field_validator("our_side", "matter_type", "stage", mode="before")(_slug)
    _v_cnr = field_validator("cnr", mode="before")(_cnr)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank")
        return v


class CaseUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = Field(None, min_length=1, max_length=TITLE_MAX)
    client_name: Optional[str] = Field(None, max_length=SHORT_MAX)
    our_side: Optional[str] = Field(None, max_length=SLUG_MAX)
    matter_type: Optional[str] = Field(None, max_length=SLUG_MAX)
    stage: Optional[str] = Field(None, max_length=SLUG_MAX)
    forum: Optional[str] = Field(None, max_length=SHORT_MAX)
    case_number: Optional[str] = Field(None, max_length=CASE_NUMBER_MAX)
    cnr: Optional[str] = Field(None, max_length=CNR_MAX)
    objective: Optional[str] = Field(None, max_length=OBJECTIVE_MAX)
    status: Optional[str] = Field(None, pattern=r"^(active|archived)$")

    _v_text = field_validator("title", "client_name", "forum", "case_number", "objective", mode="before")(_strip)
    _v_slug = field_validator("our_side", "matter_type", "stage", mode="before")(_slug)
    _v_cnr = field_validator("cnr", mode="before")(_cnr)

    def changes(self) -> dict:
        """Only the fields the client actually sent."""
        return {k: v for k, v in self.model_dump(exclude_unset=True).items() if v is not None}


class CaseOut(BaseModel):
    case_id: str
    title: str
    client_name: str
    our_side: str
    matter_type: str
    stage: str
    forum: str
    case_number: str
    cnr: str
    objective: str
    status: str
    doc_count: int
    bytes_total: int
    created_at: datetime
    updated_at: datetime


class CaseListOut(BaseModel):
    items: list[CaseOut]
    total: int
    limit: int
    offset: int


class Suggestion(BaseModel):
    value: str
    label: str


class CaseMetaOut(BaseModel):
    matter_types: list[Suggestion]
    stages: list[Suggestion]
    sides: list[Suggestion]
    limits: dict


# --- slice 2: documents, status, soft delete ---------------------------------

FILENAME_MAX = 255
_SHA256_RE = r"^(?:[a-fA-F0-9]{64})?$"


class DocumentPresignIn(BaseModel):
    """What the browser knows before the upload: name, size, optional MIME and
    an optional client-side SHA-256 (a hint for duplicate warnings, never
    trusted; the worker computes the authoritative hash)."""
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., min_length=1, max_length=FILENAME_MAX)
    size_bytes: int = Field(..., ge=1)
    content_type: str = Field("", max_length=128)
    client_sha256: str = Field("", pattern=_SHA256_RE)

    _v_text = field_validator("filename", "content_type", mode="before")(_strip)


class UploadInstructions(BaseModel):
    url: str
    method: str = "PUT"
    headers: dict[str, str]
    expires_at: datetime


class DocumentOut(BaseModel):
    doc_id: str
    case_id: str
    filename: str
    ext: str
    declared_mime: str = ""
    sniffed_mime: str = ""
    size_bytes: int
    sha256: Optional[str] = None
    sse: Optional[str] = None
    status: str
    error_code: Optional[str] = None
    duplicate_of: Optional[str] = None
    kind: str = ""
    kind_confidence: float = 0
    page_count: Optional[int] = None
    chars_total: Optional[int] = None
    ocr_pages: int = 0
    extractor: Optional[str] = None
    stored: bool = False                      # object accepted at complete; counted; downloadable
    created_at: datetime
    updated_at: datetime
    ready_at: Optional[datetime] = None


class DocumentPresignOut(DocumentOut):
    upload: UploadInstructions
    duplicate_hint: Optional[str] = None      # client sha256 matched a sibling (hint only)


class DocumentCompleteOut(DocumentOut):
    """``complete`` answer. ``duplicate_hint`` names a stored sibling with the
    same S3 ETag and size; it is advisory. Only the worker marks a document
    ``duplicate``, after confirming the sha256 of the bytes."""
    duplicate_hint: Optional[str] = None


class DocumentListOut(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int
    counts: dict[str, int]


class DownloadOut(BaseModel):
    url: str
    expires_at: datetime
    filename: str
    size_bytes: int
    content_type: str


class DocumentDeleteOut(BaseModel):
    doc_id: str
    case_id: str
    status: str
    jobs_cancelled: int


class CaseDeleteOut(BaseModel):
    case_id: str
    status: str
    documents: int
    jobs_cancelled: int


class CaseStatusOut(BaseModel):
    case_id: str
    status: str
    doc_count: int
    bytes_total: int
    documents: dict[str, int]
    pending: int
    updated_at: datetime
