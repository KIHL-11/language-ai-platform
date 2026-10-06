import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")

OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
DEFAULT_REALTIME_MODEL = "gpt-realtime-2.1-mini"
DEFAULT_REALTIME_VOICE = "marin"

router = APIRouter()


def _upstream_detail(response: requests.Response, api_key: str) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    detail: Any = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message")
    if not isinstance(detail, str) or not detail.strip():
        detail = response.reason or "OpenAI Realtime request failed"

    return detail.replace(api_key, "[redacted]")[:500]


@router.post("/realtime/client-secret")
def create_realtime_client_secret():
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="OpenAI Realtime is not configured on the server.",
        )

    model = os.getenv("OPENAI_REALTIME_MODEL", DEFAULT_REALTIME_MODEL).strip()
    voice = os.getenv("OPENAI_REALTIME_VOICE", DEFAULT_REALTIME_VOICE).strip()
    session = {
        "type": "realtime",
        "model": model,
        "audio": {"output": {"voice": voice}},
    }

    try:
        response = requests.post(
            OPENAI_CLIENT_SECRETS_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"session": session},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail="Unable to reach the OpenAI Realtime credential service.",
        ) from exc

    if not response.ok:
        raise HTTPException(
            status_code=502,
            detail=(
                f"OpenAI Realtime credential request failed "
                f"({response.status_code}): {_upstream_detail(response, api_key)}"
            ),
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="OpenAI returned an invalid Realtime credential response.",
        ) from exc

    value = payload.get("value") if isinstance(payload, dict) else None
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=502,
            detail="OpenAI returned a Realtime credential without a client secret.",
        )

    return {
        "value": value,
        "expires_at": payload.get("expires_at"),
        "model": model,
        "voice": voice,
    }
