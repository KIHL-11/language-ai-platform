import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BACKEND_DIR / ".env")


class LLMError(RuntimeError):
    """Base error raised by the AI provider client."""


class LLMAuthenticationError(LLMError):
    """The configured API key was rejected by the provider."""


class LLMRateLimitError(LLMError):
    """The provider rejected the request because of quota or rate limits."""


class LLMClient:
    PROVIDER_URLS = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    }

    def __init__(self):
        self.provider = os.getenv("AI_PROVIDER", "openai").strip().lower()
        if self.provider not in self.PROVIDER_URLS:
            supported = ", ".join(sorted(self.PROVIDER_URLS))
            raise ValueError(
                f"Unsupported AI_PROVIDER '{self.provider}'. Supported values: {supported}"
            )

        self.api_key = os.getenv("AI_API_KEY", "").strip()
        self.base_url = self.PROVIDER_URLS[self.provider]

    @staticmethod
    def _response_detail(response: requests.Response) -> str:
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text.strip()

        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                detail = error.get("message") or error.get("detail") or str(error)
            else:
                detail = str(error)
        else:
            detail = str(body)

        return detail[:1000] if detail else "No response details provided"

    def chat(self, messages, model="gpt-4.1-mini", temperature=0.3):
        if not self.api_key:
            raise LLMAuthenticationError("Missing AI_API_KEY in backend/.env")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }

        try:
            response = requests.post(
                self.base_url,
                headers=headers,
                json=payload,
                timeout=60,
            )
        except requests.Timeout as exc:
            raise LLMError(f"{self.provider} request timed out after 60 seconds") from exc
        except requests.RequestException as exc:
            raise LLMError(f"Failed to call {self.provider}: {exc}") from exc

        detail = self._response_detail(response)
        if response.status_code == 401:
            raise LLMAuthenticationError(
                f"{self.provider} rejected AI_API_KEY (HTTP 401): {detail}"
            )
        if response.status_code == 429:
            raise LLMRateLimitError(
                f"{self.provider} quota or rate limit exceeded (HTTP 429): {detail}"
            )
        if not response.ok:
            raise LLMError(
                f"{self.provider} request failed (HTTP {response.status_code}): {detail}"
            )

        try:
            data = response.json()
            return data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"{self.provider} returned an invalid chat response: {detail}"
            ) from exc


llm = LLMClient()
