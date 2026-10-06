import os
import json
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app import app
from services.ai.llm_client import (
    LLMAuthenticationError,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    llm,
)


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        self.client = LLMClient()
        self.client.api_key = "test-key"

    @staticmethod
    def response(status_code, body):
        response = Mock(status_code=status_code, ok=200 <= status_code < 300, text="")
        response.json.return_value = body
        return response

    def test_successful_chat_keeps_existing_interface(self):
        response = self.response(
            200,
            {"choices": [{"message": {"content": "translated"}}]},
        )

        with patch("services.ai.llm_client.requests.post", return_value=response):
            result = self.client.chat([{"role": "user", "content": "Hello"}])

        self.assertEqual(result, "translated")

    def test_401_reports_invalid_api_key(self):
        response = self.response(401, {"error": {"message": "invalid key"}})

        with patch("services.ai.llm_client.requests.post", return_value=response):
            with self.assertRaisesRegex(LLMAuthenticationError, "invalid key"):
                self.client.chat([])

    def test_429_reports_quota_or_rate_limit(self):
        response = self.response(429, {"error": {"message": "quota exceeded"}})

        with patch("services.ai.llm_client.requests.post", return_value=response):
            with self.assertRaisesRegex(LLMRateLimitError, "quota exceeded"):
                self.client.chat([])

    def test_other_http_error_includes_provider_detail(self):
        response = self.response(500, {"error": {"message": "provider unavailable"}})

        with patch("services.ai.llm_client.requests.post", return_value=response):
            with self.assertRaisesRegex(LLMError, "provider unavailable"):
                self.client.chat([])

    def test_openrouter_provider_uses_openrouter_endpoint(self):
        with patch.dict(
            os.environ,
            {"AI_PROVIDER": "openrouter", "AI_API_KEY": "test-key"},
        ):
            client = LLMClient()

        self.assertEqual(client.base_url, LLMClient.PROVIDER_URLS["openrouter"])


class LessonRouteTests(unittest.TestCase):
    def test_create_lesson_calls_translator(self):
        analysis = json.dumps(
            [
                {
                    "id": 1,
                    "translation": "你好",
                    "keywords": [],
                    "chunks": [],
                    "grammar": "",
                }
            ]
        )

        with patch.object(llm, "chat", return_value=analysis):
            response = TestClient(app).post(
                "/api/lesson/create",
                json={"title": "Demo", "sentences": ["Hello"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sentences"][0]["text"], "Hello")
        self.assertEqual(response.json()["sentences"][0]["translation"], "你好")


if __name__ == "__main__":
    unittest.main()
