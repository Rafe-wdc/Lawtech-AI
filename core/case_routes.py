"""Case workspace HTTP API — slice 1: cases.

Mounted by ``core/gateway.py`` only when ``CASE_WORKSPACE_ENABLED`` is on, so
with the flag off every path under ``/pyapi/cases`` is a plain 404.

Every route requires the service API key (``require_user_key``, like the rest
of the gateway) **and** the lawyer's JWT (``resolve_case_user``). Every store
call is scoped by ``owner_user_id``; a case that belongs to someone else is
indistinguishable from one that does not exist (404).

The router is built by a factory so the gateway can pass its slowapi limiter
(defined in ``core/gateway.py``; importing it here would be circular and would
pull the whole agent graph into tests). Tests build the router without one.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from config.case_types import MATTER_TYPES, SIDES, STAGES, as_suggestions
from core import settings as _settings
from core.auth import require_user_key
from core.case_auth import resolve_case_user
from core.case_models import (
    CASE_NUMBER_MAX, CNR_MAX, OBJECTIVE_MAX, SHORT_MAX, SLUG_MAX, TITLE_MAX,
    CaseCreate, CaseListOut, CaseMetaOut, CaseOut, CaseUpdate,
)
from core.case_store import (
    ACTION_CASE_CREATE, ACTION_CASE_UPDATE, CaseStoreProtocol, get_case_store,
)
from core.logger import get_logger, get_request_id

log = get_logger("CaseRoutes")

LIST_MAX_LIMIT = 200


def _req_id(request: Request) -> str:
    rid = getattr(request.state, "request_id", "") or get_request_id()
    return rid if rid and rid != "-" else ""


def _view_window_sec() -> int:
    try:
        return max(0, int(getattr(_settings, "CASE_AUDIT_VIEW_WINDOW_SEC", 900)))
    except (TypeError, ValueError):
        return 900


def build_case_router(limiter=None, limit_value: Optional[Callable[[], str] | str] = None) -> APIRouter:
    router = APIRouter(prefix="/pyapi/cases", tags=["cases"],
                       dependencies=[Depends(require_user_key)])

    def _rl(fn):
        """Apply the gateway's rate limit when a limiter is provided."""
        if limiter is not None and limit_value is not None:
            return limiter.limit(limit_value)(fn)
        return fn

    # ---- meta (before /{case_id} so it is never captured as an id) --------

    @router.get("/meta", response_model=CaseMetaOut)
    @_rl
    async def case_meta(request: Request, user_id: str = Depends(resolve_case_user)):
        return CaseMetaOut(
            matter_types=as_suggestions(MATTER_TYPES),
            stages=as_suggestions(STAGES),
            sides=as_suggestions(SIDES),
            limits={
                "title_max": TITLE_MAX, "short_max": SHORT_MAX, "slug_max": SLUG_MAX,
                "case_number_max": CASE_NUMBER_MAX, "cnr_max": CNR_MAX,
                "objective_max": OBJECTIVE_MAX, "list_max_limit": LIST_MAX_LIMIT,
            },
        )

    # ---- cases ---------------------------------------------------------------

    @router.post("", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
    @_rl
    async def create_case(
        request: Request,
        body: CaseCreate,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        case = await store.create_case(user_id, body.model_dump())
        await store.record_audit(case["case_id"], user_id, ACTION_CASE_CREATE,
                                 object_id=case["case_id"], request_id=_req_id(request))
        return case

    @router.get("", response_model=CaseListOut)
    @_rl
    async def list_cases(
        request: Request,
        limit: int = Query(50, ge=1, le=LIST_MAX_LIMIT),
        offset: int = Query(0, ge=0),
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        items, total = await store.list_cases(user_id, limit=limit, offset=offset)
        return CaseListOut(items=items, total=total, limit=limit, offset=offset)

    @router.get("/{case_id}", response_model=CaseOut)
    @_rl
    async def get_case(
        request: Request,
        case_id: str,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        case = await store.get_case(case_id, user_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        await store.record_view(case["case_id"], user_id, _req_id(request), _view_window_sec())
        return case

    @router.patch("/{case_id}", response_model=CaseOut)
    @_rl
    async def update_case(
        request: Request,
        case_id: str,
        body: CaseUpdate,
        user_id: str = Depends(resolve_case_user),
        store: CaseStoreProtocol = Depends(get_case_store),
    ):
        changes = body.changes()
        case = await store.update_case(case_id, user_id, changes)
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        if changes:
            await store.record_audit(case["case_id"], user_id, ACTION_CASE_UPDATE,
                                     object_id=",".join(sorted(changes)), request_id=_req_id(request))
        return case

    return router
