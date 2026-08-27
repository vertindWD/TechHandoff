from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tracker.config import Settings


class SettingsTests(unittest.TestCase):
    def test_model_can_use_provider_specific_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "missing.env"
            with patch.dict(
                "os.environ",
                {
                    "MODEL_NAME": "dashscope/qwen-plus",
                    "DASHSCOPE_API_KEY": "sk-provider-key",
                    "MODEL_THINKING": "on",
                    "MODEL_MAX_OUTPUT_TOKENS": "8192",
                },
                clear=True,
            ):
                settings = Settings.from_env(env_file)

        self.assertTrue(settings.model_enabled)
        self.assertEqual(settings.model_base_url, "")
        self.assertEqual(settings.model_api_key, "")
        self.assertEqual(settings.model_thinking, "on")
        self.assertEqual(settings.model_max_output_tokens, 8192)

    def test_model_is_disabled_without_a_model_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "missing.env"
            with patch.dict(
                "os.environ",
                {"DEEPSEEK_API_KEY": "sk-unused"},
                clear=True,
            ):
                settings = Settings.from_env(env_file)

        self.assertFalse(settings.model_enabled)


if __name__ == "__main__":
    unittest.main()
