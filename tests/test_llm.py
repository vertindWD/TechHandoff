from __future__ import annotations

import unittest
from unittest.mock import patch

from tracker.llm import OpenAICompatibleModel


class ModelTests(unittest.TestCase):
    def test_prefixes_bare_model_for_custom_compatible_endpoint(self) -> None:
        model = OpenAICompatibleModel(
            "https://example.com/v1",
            "custom-key",
            "deepseek-v4-pro",
        )

        self.assertEqual(model.model_name, "openai/deepseek-v4-pro")
        self.assertEqual(model._api_key_for(model.model_name), "custom-key")

    def test_infers_common_provider_without_custom_endpoint(self) -> None:
        self.assertEqual(
            OpenAICompatibleModel("", "", "qwen-plus").model_name,
            "dashscope/qwen-plus",
        )
        self.assertEqual(
            OpenAICompatibleModel("", "", "deepseek-chat").model_name,
            "deepseek/deepseek-chat",
        )

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
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "", "reasoning_content": "thought"},
                    }
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"action":"project_understanding"}'},
                    }
                ]
            },
        ]

        model = OpenAICompatibleModel(
            "",
            "",
            "deepseek/deepseek-chat",
            json_retries=2,
        )
        with patch.object(model, "_call_litellm", side_effect=responses) as call:
            result = model.complete_json(
                [{"role": "system", "content": "Return JSON."}]
            )

        self.assertEqual(result, {"action": "project_understanding"})
        self.assertEqual(call.call_count, 2)

    def test_redacts_generic_api_key_from_errors(self) -> None:
        model = OpenAICompatibleModel(
            "https://example.com/v1",
            "super-secret",
            "openai/custom-model",
            json_retries=0,
        )
        with patch.object(
            model,
            "_call_litellm",
            side_effect=RuntimeError("request rejected for super-secret"),
        ):
            with self.assertRaisesRegex(Exception, r"\[redacted\]"):
                model.complete_json(
                    [{"role": "system", "content": "Return JSON."}]
                )

    def test_uses_the_selected_provider_key(self) -> None:
        model = OpenAICompatibleModel(
            "",
            "",
            "dashscope/qwen-plus",
        )
        with patch.dict(
            "os.environ",
            {
                "DASHSCOPE_API_KEY": "qwen-key",
            },
            clear=True,
        ):
            self.assertEqual(
                model._api_key_for("dashscope/qwen-plus"),
                "qwen-key",
            )

    def test_calls_litellm_with_qwen_compatible_options(self) -> None:
        model = OpenAICompatibleModel(
            "",
            "",
            "dashscope/qwen-plus",
        )
        response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": '{"action":"final"}'},
                }
            ]
        }
        with patch.dict(
            "os.environ",
            {"DASHSCOPE_API_KEY": "qwen-key"},
            clear=True,
        ), patch("litellm.completion", return_value=response) as completion:
            result = model._call_litellm(
                "dashscope/qwen-plus",
                [{"role": "system", "content": "Return JSON."}],
                0.1,
            )

        self.assertEqual(result, response)
        sent = completion.call_args.kwargs
        self.assertEqual(sent["model"], "dashscope/qwen-plus")
        self.assertEqual(sent["api_key"], "qwen-key")
        self.assertFalse(sent["enable_thinking"])
        self.assertEqual(sent["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
