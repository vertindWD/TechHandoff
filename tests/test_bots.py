import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tracker.bots import load_feishu_bots


class FeishuBotConfigTests(unittest.TestCase):
    def test_multi_project_bot_does_not_require_fixed_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bots.json"
            path.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "project-bot",
                                "transport": "websocket",
                                "app_id_env": "BOT_APP_ID",
                                "app_secret_env": "BOT_APP_SECRET",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"BOT_APP_ID": "cli_project", "BOT_APP_SECRET": "secret"},
                clear=True,
            ):
                bots = load_feishu_bots(path)
            self.assertEqual(bots[0].project_id, "")

    def test_websocket_bot_does_not_require_verification_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bots.json"
            path.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "orders-bot",
                                "project_id": "orders",
                                "transport": "websocket",
                                "app_id_env": "BOT_APP_ID",
                                "app_secret_env": "BOT_APP_SECRET",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"BOT_APP_ID": "cli_orders", "BOT_APP_SECRET": "secret-value"},
                clear=True,
            ):
                bots = load_feishu_bots(path)
            self.assertEqual(bots[0].transport, "websocket")
            self.assertEqual(bots[0].verification_token, "")

    def test_webhook_bot_still_requires_verification_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bots.json"
            path.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "orders-bot",
                                "project_id": "orders",
                                "transport": "webhook",
                                "app_id_env": "BOT_APP_ID",
                                "app_secret_env": "BOT_APP_SECRET",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"BOT_APP_ID": "cli_orders", "BOT_APP_SECRET": "secret-value"},
                clear=True,
            ):
                with self.assertRaisesRegex(ValueError, "verification_token_env"):
                    load_feishu_bots(path)

    def test_loads_secrets_from_environment_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bots.json"
            path.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "orders-bot",
                                "callback_key": "orders",
                                "project_id": "orders",
                                "app_id_env": "BOT_APP_ID",
                                "app_secret_env": "BOT_APP_SECRET",
                                "verification_token_env": "BOT_VERIFY",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "BOT_APP_ID": "cli_orders",
                    "BOT_APP_SECRET": "secret-value",
                    "BOT_VERIFY": "verify-value",
                },
            ):
                bots = load_feishu_bots(path)
            self.assertEqual(bots[0].project_id, "orders")
            self.assertEqual(bots[0].app_secret, "secret-value")
            self.assertNotIn("secret-value", path.read_text(encoding="utf-8"))

    def test_rejects_duplicate_callback_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "bots.json"
            path.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "orders-bot",
                                "callback_key": "shared",
                                "project_id": "orders",
                                "app_id_env": "BOT_APP_ID",
                                "app_secret_env": "BOT_APP_SECRET",
                                "verification_token_env": "BOT_VERIFY",
                            },
                            {
                                "bot_id": "crm-bot",
                                "callback_key": "shared",
                                "project_id": "crm",
                                "app_id_env": "BOT_APP_ID",
                                "app_secret_env": "BOT_APP_SECRET",
                                "verification_token_env": "BOT_VERIFY",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "BOT_APP_ID": "cli_test",
                    "BOT_APP_SECRET": "secret",
                    "BOT_VERIFY": "verify",
                },
            ):
                with self.assertRaisesRegex(ValueError, "callback_key 不能重复"):
                    load_feishu_bots(path)


if __name__ == "__main__":
    unittest.main()
