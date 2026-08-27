import tempfile
import unittest
import hashlib
import hmac
import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from test_service import settings_for
from tracker.models import Job, Project
from tracker.server import TrackerApplication


class ServerBoundaryTests(unittest.TestCase):
    def test_one_bot_routes_two_chats_to_isolated_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            orders_repo = root / "orders-repo"
            crm_repo = root / "crm-repo"
            orders_repo.mkdir()
            crm_repo.mkdir()
            (orders_repo / "orders.py").write_text("pass\n", encoding="utf-8")
            (crm_repo / "crm.py").write_text("pass\n", encoding="utf-8")
            projects_file = root / "projects.json"
            projects_file.write_text(
                json.dumps(
                    {
                        "projects": [
                            {
                                "project_id": "orders",
                                "name": "订单系统",
                                "repo_path": str(orders_repo),
                            },
                            {
                                "project_id": "crm",
                                "name": "CRM",
                                "repo_path": str(crm_repo),
                            },
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            bots_file = root / "bots.json"
            bots_file.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "project-bot",
                                "callback_key": "project",
                                "transport": "websocket",
                                "app_id_env": "TEST_APP_ID",
                                "app_secret_env": "TEST_APP_SECRET",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(
                settings_for(root),
                projects_file=projects_file,
                feishu_bots_file=bots_file,
            )
            with patch.dict(
                os.environ,
                {"TEST_APP_ID": "cli_project", "TEST_APP_SECRET": "secret"},
            ):
                app = TrackerApplication(settings)
            sent: list[tuple[str, str]] = []
            submitted: list[tuple[str, str]] = []
            app.feishu_clients["project-bot"].send_text = (  # type: ignore[method-assign]
                lambda chat_id, text, receive_id_type="chat_id": sent.append((chat_id, text))
            )

            def fake_submit(
                selector: str,
                notes: str,
                source_label: str,
                publish_to_feishu: bool,
                chat_id: str = "",
                feishu_bot_id: str = "",
            ) -> Job:
                submitted.append((chat_id, selector))
                return Job(f"job-{len(submitted)}", "queued", selector, source_label)

            app.submit = fake_submit  # type: ignore[method-assign]
            try:
                app.handle_bound_feishu_message(
                    "project", "evt-help", "chat-orders", "user-1", "/"
                )
                app.handle_bound_feishu_message(
                    "project", "evt-bind-orders", "chat-orders", "user-1", "/bind 订单系统"
                )
                app.handle_bound_feishu_message(
                    "project", "evt-bind-crm", "chat-crm", "user-2", "绑定项目 CRM"
                )
                app.handle_bound_feishu_message(
                    "project", "evt-orders", "chat-orders", "user-1", "/plan 增加按钮"
                )
                app.handle_bound_feishu_message(
                    "project", "evt-crm", "chat-crm", "user-2", "方案 增加客户字段"
                )
                self.assertEqual(
                    submitted,
                    [("chat-orders", "orders"), ("chat-crm", "crm")],
                )
                self.assertEqual(
                    app.service.store.get_chat_project_binding("project-bot", "chat-orders")[
                        "project_id"
                    ],
                    "orders",
                )
                self.assertEqual(
                    app.service.store.get_chat_project_binding("project-bot", "chat-crm")[
                        "project_id"
                    ],
                    "crm",
                )
                app.service.store.add_memory("orders", "decision", "订单记忆", "test", "v1")
                app.service.store.add_memory("crm", "decision", "CRM记忆", "test", "v1")
                self.assertEqual(
                    [item["content"] for item in app.service.store.list_memory("orders")],
                    ["订单记忆"],
                )
                self.assertEqual(
                    [item["content"] for item in app.service.store.list_memory("crm")],
                    ["CRM记忆"],
                )
                self.assertTrue(any("已收到" in text for _, text in sent))
                self.assertTrue(any("/bind" in text and "/plan" in text for _, text in sent))
            finally:
                app.executor.shutdown(wait=True, cancel_futures=True)

    def test_each_feishu_bot_is_permanently_bound_to_one_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            orders_repo = root / "orders-repo"
            crm_repo = root / "crm-repo"
            orders_repo.mkdir()
            crm_repo.mkdir()
            (orders_repo / "orders.py").write_text("def order_detail(): pass\n", encoding="utf-8")
            (crm_repo / "crm.py").write_text("def customer_detail(): pass\n", encoding="utf-8")
            projects_file = root / "projects.json"
            projects_file.write_text(
                json.dumps(
                    {
                        "projects": [
                            {"project_id": "orders", "name": "订单系统", "repo_path": str(orders_repo)},
                            {"project_id": "crm", "name": "CRM", "repo_path": str(crm_repo)},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            bots_file = root / "feishu-bots.json"
            bots_file.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "orders-bot",
                                "callback_key": "orders",
                                "project_id": "orders",
                                "app_id_env": "TEST_ORDERS_APP_ID",
                                "app_secret_env": "TEST_ORDERS_APP_SECRET",
                                "verification_token_env": "TEST_ORDERS_VERIFY",
                            },
                            {
                                "bot_id": "crm-bot",
                                "callback_key": "crm",
                                "project_id": "crm",
                                "app_id_env": "TEST_CRM_APP_ID",
                                "app_secret_env": "TEST_CRM_APP_SECRET",
                                "verification_token_env": "TEST_CRM_VERIFY",
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(
                settings_for(root),
                projects_file=projects_file,
                feishu_bots_file=bots_file,
            )
            submitted: list[tuple[str, str, str]] = []

            with patch.dict(
                os.environ,
                {
                    "TEST_ORDERS_APP_ID": "cli_orders",
                    "TEST_ORDERS_APP_SECRET": "orders-secret",
                    "TEST_ORDERS_VERIFY": "orders-verify",
                    "TEST_CRM_APP_ID": "cli_crm",
                    "TEST_CRM_APP_SECRET": "crm-secret",
                    "TEST_CRM_VERIFY": "crm-verify",
                },
            ):
                app = TrackerApplication(settings)

            def fake_submit(
                selector: str,
                notes: str,
                source_label: str,
                publish_to_feishu: bool,
                chat_id: str = "",
                feishu_bot_id: str = "",
            ) -> Job:
                submitted.append((selector, notes, feishu_bot_id))
                return Job(f"job-{len(submitted)}", "queued", selector, source_label)

            app.submit = fake_submit  # type: ignore[method-assign]

            def payload(token: str) -> dict:
                return {
                    "header": {
                        "event_id": "same-event-id",
                        "event_type": "im.message.receive_v1",
                        "token": token,
                    },
                    "event": {
                        "sender": {"sender_id": {"open_id": "ou_anyone"}},
                        "message": {
                            "chat_id": "oc_any_group",
                            "message_type": "text",
                            "content": json.dumps(
                                {"text": "@_user_1 方案 CRM增加客户按钮"},
                                ensure_ascii=False,
                            ),
                        },
                    },
                }

            try:
                orders_result = app.handle_feishu_event(payload("orders-verify"), "orders")
                crm_result = app.handle_feishu_event(payload("crm-verify"), "crm")
                self.assertEqual(orders_result["project_id"], "orders")
                self.assertEqual(crm_result["project_id"], "crm")
                self.assertEqual(
                    submitted,
                    [
                        ("orders", "CRM增加客户按钮", "orders-bot"),
                        ("crm", "CRM增加客户按钮", "crm-bot"),
                    ],
                )
            finally:
                app.executor.shutdown(wait=True, cancel_futures=True)

    def test_bound_feishu_bot_requires_its_own_verification_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            (repo / "app.py").write_text("pass\n", encoding="utf-8")
            projects_file = root / "projects.json"
            projects_file.write_text(
                json.dumps(
                    {"projects": [{"project_id": "orders", "name": "订单", "repo_path": str(repo)}]}
                ),
                encoding="utf-8",
            )
            bots_file = root / "bots.json"
            bots_file.write_text(
                json.dumps(
                    {
                        "bots": [
                            {
                                "bot_id": "orders-bot",
                                "project_id": "orders",
                                "app_id_env": "TEST_APP_ID",
                                "app_secret_env": "TEST_APP_SECRET",
                                "verification_token_env": "TEST_VERIFY",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            settings = replace(
                settings_for(root),
                projects_file=projects_file,
                feishu_bots_file=bots_file,
            )
            with patch.dict(
                os.environ,
                {
                    "TEST_APP_ID": "cli_orders",
                    "TEST_APP_SECRET": "secret",
                    "TEST_VERIFY": "expected",
                },
            ):
                app = TrackerApplication(settings)
            try:
                with self.assertRaises(PermissionError):
                    app.handle_feishu_event(
                        {"challenge": "abc", "token": "wrong"},
                        "orders-bot",
                    )
                self.assertEqual(
                    app.handle_feishu_event(
                        {"challenge": "abc", "token": "expected"},
                        "orders-bot",
                    ),
                    {"challenge": "abc"},
                )
            finally:
                app.executor.shutdown(wait=True, cancel_futures=True)

    def test_challenge_requires_matching_verification_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                settings_for(Path(temp_dir)),
                feishu_verification_token="expected-token",
                feishu_allowed_chat_ids=("oc_allowed",),
            )
            app = TrackerApplication(settings)
            try:
                with self.assertRaises(PermissionError):
                    app.handle_feishu_event({"challenge": "abc", "token": "wrong"})
                self.assertEqual(
                    app.handle_feishu_event({"challenge": "abc", "token": "expected-token"}),
                    {"challenge": "abc"},
                )
            finally:
                app.executor.shutdown(wait=True, cancel_futures=True)

    def test_rejects_unapproved_feishu_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                settings_for(Path(temp_dir)),
                feishu_allowed_chat_ids=("oc_allowed",),
            )
            app = TrackerApplication(settings)
            payload = {
                "header": {"event_id": "evt-1", "event_type": "im.message.receive_v1"},
                "event": {
                    "sender": {"sender_id": {"open_id": "ou_unknown"}},
                    "message": {"chat_id": "oc_denied", "message_type": "text", "content": "{}"},
                },
            }
            try:
                with self.assertRaises(PermissionError):
                    app.handle_feishu_event(payload)
            finally:
                app.executor.shutdown(wait=True, cancel_futures=True)

    def test_accepts_signed_github_push_for_tracked_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(settings_for(Path(temp_dir)), github_webhook_secret="hook-secret")
            app = TrackerApplication(settings)
            app.service.register_project(
                Project(
                    project_id="orders-gh",
                    name="Orders GitHub",
                    github_owner="acme",
                    github_repo="orders",
                    github_ref="main",
                )
            )
            submitted: list[tuple[str, str]] = []

            def fake_submit(selector: str, target_commit_sha: str = "", force_full: bool = False) -> Job:
                submitted.append((selector, target_commit_sha))
                return Job("job-1", "queued", selector, "GitHub sync")

            app.submit_github_sync = fake_submit  # type: ignore[method-assign]
            payload = {
                "ref": "refs/heads/main",
                "after": "a" * 40,
                "repository": {"name": "orders", "owner": {"login": "acme"}},
            }
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            signature = "sha256=" + hmac.new(b"hook-secret", raw, hashlib.sha256).hexdigest()
            try:
                result = app.handle_github_event(
                    raw,
                    payload,
                    "push",
                    "delivery-1",
                    signature,
                )
                self.assertEqual(result["job_id"], "job-1")
                self.assertEqual(submitted, [("orders-gh", "a" * 40)])
            finally:
                app.executor.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
