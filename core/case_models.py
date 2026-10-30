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
