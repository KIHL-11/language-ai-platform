import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services.api.realtime_api import DEFAULT_REALTIME_MODEL
from services.auth.identity import (
    IdentityDependency,
    ProviderAccessDependency,
    owner_user_id,
    require_personal_storage,
)
from services.library.store import LearningLibraryStore, LibraryStoreError
from services.schemas.speaking import (
    SpeakingContext, SpeakingContextRequest, SpeakingEventRequest,
    SpeakingFinishRequest, SpeakingSession, SpeakingSessionCreate,
    SpeakingSessionCreateResponse, SpeakingSessionList,
)
from services.speaking.context import SpeakingContextBuilder
from services.speaking.store import SpeakingConflictError, SpeakingSessionStore, SpeakingStoreError
from services.speaking.tools import ToolDispatcher, ToolError
from services.study_bank.store import StudyBankStore, StudyBankStoreError


GEMINI_TOKEN_URL = "https://generativelanguage.googleapis.com/v1beta/auth_tokens"
DEFAULT_GEMINI_MODEL = "gemini-3.8-live"
router = APIRouter()
study_bank = StudyBankStore()
library_store = LearningLibraryStore()
speaking_store = SpeakingSessionStore()


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(min_length=1, max_length=128)
    expected_event_count: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


def _model(provider: str) -> str:
    if provider == "gemini-live":
        return os.getenv("GEMINI_LIVE_MODEL", DEFAULT_GEMINI_MODEL).strip() or DEFAULT_GEMINI_MODEL
    return os.getenv("OPENAI_REALTIME_MODEL", DEFAULT_REALTIME_MODEL).strip() or DEFAULT_REALTIME_MODEL


def _context(request: SpeakingContextRequest) -> SpeakingContext:
    try:
        return SpeakingContextBuilder(study_bank, library_store).build(request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Library item not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (StudyBankStoreError, LibraryStoreError) as exc:
        raise HTTPException(status_code=500, detail="Could not build speaking context") from exc


@router.get("/speaking/providers")
def provider_status():
    return {"providers": [
        {"id": "gemini-live", "configured": bool(os.getenv("GEMINI_API_KEY", "").strip()), "model": _model("gemini-live"), "capabilities": {"voice": True, "text_input": True, "barge_in": True, "input_transcript": True, "output_transcript": True, "tools": True}},
        {"id": "openai-realtime", "configured": bool(os.getenv("OPENAI_API_KEY", "").strip()), "model": _model("openai-realtime"), "capabilities": {"voice": True, "text_input": False, "barge_in": True, "input_transcript": True, "output_transcript": True, "tools": False}},
    ]}


@router.post("/speaking/context", response_model=SpeakingContext)
def speaking_context(
    request: SpeakingContextRequest, identity: IdentityDependency
):
    user_id = owner_user_id(identity, request.user_id)
    require_personal_storage(identity)
    return _context(request.model_copy(update={"user_id": user_id}))


@router.post("/speaking/sessions", response_model=SpeakingSessionCreateResponse)
def create_session(request: SpeakingSessionCreate, identity: IdentityDependency):
    user_id = owner_user_id(identity, request.user_id)
    require_personal_storage(identity)
    scoped_request = request.model_copy(update={"user_id": user_id})
    context = _context(SpeakingContextRequest(**scoped_request.model_dump(exclude={"provider", "duration_seconds"})))
    try:
        session = speaking_store.create(
            user_id=user_id, provider=request.provider, model=_model(request.provider),
            duration=request.duration_seconds, context=context, goal=request.goal,
        )
    except SpeakingStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not create speaking session") from exc
    return SpeakingSessionCreateResponse(session=session, context=context)


@router.get("/speaking/sessions", response_model=SpeakingSessionList)
def recent_sessions(
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: IdentityDependency,
    limit: Annotated[int, Query(ge=1, le=10)] = 10,
):
    user_id = owner_user_id(identity, user_id)
    require_personal_storage(identity)
    try:
        return SpeakingSessionList(sessions=speaking_store.list_recent(user_id, limit))
    except SpeakingStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read speaking sessions") from exc


@router.get("/speaking/sessions/{session_id}", response_model=SpeakingSession)
def get_session(
    session_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    identity: IdentityDependency,
):
    user_id = owner_user_id(identity, user_id)
    require_personal_storage(identity)
    try:
        return speaking_store.get(user_id, session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Speaking session not found") from exc
    except SpeakingStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not read speaking session") from exc


@router.post("/speaking/sessions/{session_id}/events", response_model=SpeakingSession)
def append_event(
    session_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    request: SpeakingEventRequest,
    identity: IdentityDependency,
):
    user_id = owner_user_id(identity, user_id)
    require_personal_storage(identity)
    try:
        return speaking_store.append_event(user_id, session_id, request)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Speaking session not found") from exc
    except SpeakingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SpeakingStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not save speaking event") from exc


@router.post("/speaking/sessions/{session_id}/finish", response_model=SpeakingSession)
def finish_session(
    session_id: str,
    user_id: Annotated[str, Query(min_length=1, max_length=128)],
    request: SpeakingFinishRequest,
    identity: IdentityDependency,
):
    user_id = owner_user_id(identity, user_id)
    require_personal_storage(identity)
    try:
        return speaking_store.finish(user_id, session_id, request.expected_event_count, request.status, request.end_reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Speaking session not found") from exc
    except SpeakingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SpeakingStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not finish speaking session") from exc


@router.post("/speaking/sessions/{session_id}/tools")
def execute_tool(
    session_id: str, request: ToolRequest, identity: IdentityDependency
):
    user_id = owner_user_id(identity, request.user_id)
    require_personal_storage(identity)
    try:
        session = speaking_store.get(user_id, session_id)
        if session.event_count != request.expected_event_count or session.status != "active":
            raise SpeakingConflictError("Speaking session state is stale")
        result = ToolDispatcher(study_bank, library_store).execute(
            request.name, request.arguments, user_id=user_id,
            language=session.language, library_item_id=session.library_item_id,
        )
        updated = None
        if request.name == "record_correction":
            updated = speaking_store.append_event(user_id, session_id, SpeakingEventRequest(
                expected_event_count=request.expected_event_count, type="correction", correction=result,
            ))
        elif request.name == "suggest_expression":
            updated = speaking_store.append_event(user_id, session_id, SpeakingEventRequest(
                expected_event_count=request.expected_event_count, type="suggestion", suggestion=result,
            ))
        return {"name": request.name, "result": result, "session": updated}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Speaking session not found") from exc
    except SpeakingConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ToolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SpeakingStoreError as exc:
        raise HTTPException(status_code=500, detail="Could not update speaking session") from exc


@router.post("/speaking/gemini/client-token")
def gemini_client_token(_access: ProviderAccessDependency):
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Gemini Live is not configured on the server.")
    model = _model("gemini-live")
    now = datetime.now(timezone.utc)
    payload = {
        "uses": 1,
        "expireTime": (now + timedelta(minutes=12)).isoformat().replace("+00:00", "Z"),
        "newSessionExpireTime": (now + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "fieldMask": "model",
        "bidiGenerateContentSetup": {"model": f"models/{model}"},
    }
    try:
        response = requests.post(
            GEMINI_TOKEN_URL,
            headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail="Unable to reach the Gemini credential service.") from exc
    if not response.ok:
        detail = (response.text or response.reason or "Gemini credential request failed").replace(api_key, "[redacted]")[:500]
        if response.status_code == 429:
            raise HTTPException(status_code=429, detail="Gemini provider quota or rate limit was reached.")
        raise HTTPException(status_code=502, detail=f"Gemini credential request failed ({response.status_code}): {detail}")
    try:
        value = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Gemini returned an invalid credential response.") from exc
    token = value.get("name") if isinstance(value, dict) else None
    if not isinstance(token, str) or not token:
        raise HTTPException(status_code=502, detail="Gemini returned an empty ephemeral credential.")
    return {"value": token, "model": model, "expires_at": payload["expireTime"]}
