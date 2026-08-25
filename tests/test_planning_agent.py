from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracker.code_tools import ReadOnlyRepositoryTools, ReadOnlyToolError
from tracker.models import Project, RepositorySnapshot, Requirement
from tracker.planning_agent import ReadOnlyPlanningAgent
from tracker.repository import SourceFile
from tracker.store import Store


class ScriptedModel:
    def __init__(self, actions: list[dict]) -> None:
        self.actions = list(actions)
        self.messages: list[list[dict[str, str]]] = []

    def complete_json(self, messages: list[dict[str, str]], temperature: float = 0.1) -> dict:
        self.messages.append(list(messages))
        return self.actions.pop(0)


class PlanningAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.files = (
            SourceFile(
                "backend/api/orders.py",
                "from services.notification import resend_notification\n\n"
                "def resend_order_notification(order_id: str):\n"
                "    return resend_notification(order_id)\n",
            ),
            SourceFile(
                "backend/tests/test_orders.py",
                "def test_resend_order_notification():\n    assert True\n",
            ),
        )
        self.snapshot = RepositorySnapshot("github:acme/orders", "abc123", 2, 0)
        self.project = Project(
            project_id="orders",
            name="订单系统",
            github_owner="acme",
            github_repo="orders",
        )
        self.requirement = Requirement(
            "允许客服重新发送订单通知",
            ("增加重新发送通知接口",),
            ("接口成功返回发送结果",),
            (),
        )

    def test_agent_investigates_then_verifies_locations(self) -> None:
        model = ScriptedModel(
            [
                {"action": "project_map"},
                {
                    "action": "read_file",
                    "path": "backend/api/orders.py",
                    "start_line": 1,
                    "end_line": 20,
                },
                {"action": "find_symbol", "name": "resend_order_notification"},
                {
                    "action": "final",
                    "requirement": {
                        "business_goal": "允许客服重新发送订单通知",
                        "requested_changes": ["增加重新发送通知接口"],
                        "acceptance_criteria": ["接口返回发送结果"],
                        "unknowns": ["哪些角色有权限"],
                    },
                    "changes": [
                        {
                            "path": "backend/api/orders.py",
                            "line_start": 3,
                            "line_end": 4,
                            "symbol": "resend_order_notification",
                            "instruction": "在现有订单通知入口附近增加新接口，并复用通知服务。",
                            "confidence": "verified",
                        },
                        {
                            "path": "backend/api/missing.py",
                            "line_start": 1,
                            "line_end": 1,
                            "symbol": "missing",
                            "instruction": "修改不存在的文件。",
                            "confidence": "verified",
                        },
                    ],
                    "tests": ["在订单 API 测试中覆盖成功和失败返回。"],
                    "risks": [],
                    "unknowns": [],
                },
            ]
        )
        tools = ReadOnlyRepositoryTools(self.files, "# repository map")
        result = ReadOnlyPlanningAgent(model, max_steps=6).run(
            self.project,
            self.snapshot,
            "增加重新发送通知接口",
            self.requirement,
            (),
            tools,
        )

        self.assertEqual(len(result.recommendations), 1)
        self.assertEqual(result.recommendations[0].confidence, "verified")
        self.assertEqual(result.recommendations[0].line_start, 3)
        self.assertTrue(any("不存在" in item for item in result.risks))
        self.assertEqual(
            result.analysis_steps,
            (
                "读取版本化项目理解索引",
                "读取 backend/api/orders.py:1-4",
                "查找符号 resend_order_notification",
            ),
        )

    def test_tool_surface_rejects_writes_and_path_traversal(self) -> None:
        tools = ReadOnlyRepositoryTools(self.files, "map")
        with self.assertRaises(ReadOnlyToolError):
            tools.execute({"action": "write_file", "path": "backend/api/orders.py"})
        with self.assertRaises(ReadOnlyToolError):
            tools.execute({"action": "read_file", "path": "../.env"})

    def test_serena_tools_record_semantically_inspected_paths(self) -> None:
        class Semantic:
            def symbols_overview(self, path, depth=0):
                return '{"Function":["resend_order_notification"]}'

            def find_symbol(self, name, relative_path="", include_body=False, depth=0):
                return (
                    '[{"name_path":"resend_order_notification",'
                    '"relative_path":"backend/api/orders.py",'
                    '"body_location":{"start_line":2,"end_line":3}}]'
                )

            def find_references(self, name, path):
                return '{"backend/tests/test_orders.py":{"Function":["test_resend_order_notification"]}}'

            def search_pattern(self, pattern, relative_path="", code_only=True):
                return '{}'

        tools = ReadOnlyRepositoryTools(
            self.files,
            "semantic index",
            semantic=Semantic(),  # type: ignore[arg-type]
        )
        overview = tools.execute(
            {"action": "symbols_overview", "path": "backend/api/orders.py"}
        )
        definition = tools.execute(
            {"action": "find_symbol", "name": "resend_order_notification"}
        )
        references = tools.execute(
            {
                "action": "find_references",
                "name_path": "resend_order_notification",
                "path": "backend/api/orders.py",
            }
        )

        self.assertIn("resend_order_notification", overview.content)
        self.assertIn("body_location", definition.content)
        self.assertIn("backend/tests/test_orders.py", references.content)
        self.assertEqual(
            tools.inspected_paths,
            {"backend/api/orders.py", "backend/tests/test_orders.py"},
        )

    def test_project_map_is_scoped_by_project_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "tracker.db")
            store.save_project_map("orders", "abc123", "orders map")
            store.save_project_map("crm", "abc123", "crm map")
            self.assertEqual(store.get_project_map("orders", "abc123"), "orders map")
            self.assertEqual(store.get_project_map("crm", "abc123"), "crm map")
            self.assertEqual(store.get_project_map("orders", "def456"), "")


if __name__ == "__main__":
    unittest.main()
