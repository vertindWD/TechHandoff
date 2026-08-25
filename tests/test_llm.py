from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from tracker.llm import OpenAICompatibleModel


class FakeResponse:
    def __init__(self, body: dict) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.body


class ModelTests(unittest.TestCase):
    def test_parses_fenced_and_prefixed_json_objects(self) -> None:
        self.assertEqual(
            OpenAICompatibleModel._parse_json('```json\n{"action":"final"}\n```'),
            {"action": "final"},
        )
        self.assertEqual(
            OpenAICompatibleModel._parse_json('结果如下：\n{"action":"project_understanding"}'),
            {"action": "project_understanding"},
        )

    def test_retries_empty_content_and_requests_json_mode(self) -> None:
        responses = [
            FakeResponse(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "", "reasoning_content": "thought"},
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": '{"action":"project_understanding"}'},
                        }
                    ]
                }
            ),
        ]
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            return responses.pop(0)

        model = OpenAICompatibleModel(
            "https://api.example.com",
            "secret",
            "model",
            json_retries=2,
        )
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = model.complete_json(
                [{"role": "system", "content": "Return JSON."}]
            )

        self.assertEqual(result, {"action": "project_understanding"})
        self.assertEqual(len(requests), 2)
        sent = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertFalse(sent["stream"])


if __name__ == "__main__":
    unittest.main()
