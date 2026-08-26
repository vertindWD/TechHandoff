import tempfile
import unittest
from pathlib import Path

from tracker.config import Settings
from tracker.models import Project
from tracker.planning_agent import ReadOnlyPlanningAgent
from tracker.service import TrackerService


def settings_for(root: Path) -> Settings:
    return Settings(
        database_path=root / "data" / "tracker.db",
        output_dir=root / "data" / "proposals",
        projects_file=None,
        allowed_repo_roots=(root,),
        max_file_bytes=524288,
        max_files=100,
        max_evidence=8,
        model_base_url="",
        model_api_key="",
        model_name="",
        github_api_url="https://api.github.com",
        github_api_version="2026-03-10",
        github_token="",
        github_webhook_secret="",
        github_full_sync_threshold=100,
        feishu_base_url="https://open.feishu.cn/open-apis",
        feishu_app_id="",
        feishu_app_secret="",
        feishu_verification_token="",
        feishu_tenant_domain="",
        feishu_allowed_chat_ids=(),
        feishu_allowed_user_ids=(),
        public_base_url="",
    )


class ServiceTests(unittest.TestCase):
    def test_reads_feishu_minute_as_meeting_source(self) -> None:
        class Feishu:
            @staticmethod
            def extract_minute_token(value: str) -> str:
                self_value = value.split("/minutes/", 1)[-1].split("?", 1)[0]
                return self_value

            @staticmethod
            def read_minute_transcript(token: str) -> str:
                return f"王五 00:00:01\n讨论 {token} 的上传流程"

        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrackerService(settings_for(Path(temp_dir)))
            notes, source = service.read_meeting_source(
                "https://example.feishu.cn/minutes/obcnABC_def-12345678?from=meeting",
                Feishu(),  # type: ignore[arg-type]
            )

        self.assertIn("上传流程", notes)
        self.assertEqual(source, "飞书妙记 obcnABC_def-12345678")

    def test_registers_github_repository_from_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = TrackerService(settings_for(Path(temp_dir)))
            service.github.client.get_repository = lambda owner, repo: {  # type: ignore[method-assign]
                "name": "web-app",
                "full_name": "Acme/web-app",
                "default_branch": "develop",
                "private": True,
                "html_url": "https://github.com/Acme/web-app",
            }
            project = service.register_github_repository(
                "https://github.com/Acme/web-app"
            )
            self.assertEqual(project.project_id, "acme-web-app")
            self.assertEqual(project.github_full_name, "Acme/web-app")
            self.assertEqual(project.github_ref, "develop")
            self.assertEqual(
                service.store.find_github_project("acme", "web-app"),
                project,
            )

    def test_generates_evidence_backed_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            target = repo / "frontend" / "OrderDetail.tsx"
            target.parent.mkdir(parents=True)
            target.write_text(
                "export function OrderDetail() { return <button>发送订单通知</button>; }",
                encoding="utf-8",
            )
            service = TrackerService(settings_for(root))
            service.register_project(Project("orders", "订单系统", str(repo), aliases=("订单",)))
            proposal = service.generate_proposal(
                "订单",
                "订单详情页增加重新发送通知按钮。点击后显示成功提示。",
                "测试会议纪要",
            )
            self.assertTrue(Path(proposal.output_path).is_file())
            self.assertTrue(Path(proposal.output_path).name.endswith("-latest.md"))
            self.assertIn("frontend/OrderDetail.tsx", proposal.markdown)
            self.assertIn("代码版本", proposal.markdown)
            self.assertTrue(proposal.evidence)
            for item in proposal.evidence:
                self.assertTrue((repo / item.path).is_file())
            self.assertEqual(service.store.list_memory("orders", include_stale=True), [])
            repeated = service.generate_proposal(
                "订单",
                "订单详情页增加重新发送通知按钮。点击后显示成功提示。",
                "第二次测试会议纪要",
            )
            self.assertEqual(repeated.output_path, proposal.output_path)
            self.assertEqual(len(list(settings_for(root).output_dir.glob("*.md"))), 1)

            target.write_text(target.read_text(encoding="utf-8") + "\n// changed\n", encoding="utf-8")
            refresh = service.refresh_project("订单")
            self.assertNotEqual(refresh["repository_version"], proposal.repository_version)
            self.assertNotIn("stale_proposal_ids", refresh)

            memory = service.remember(
                "订单",
                "confirmed_decision",
                "只有客服角色可以重新发送订单通知。",
                "产品确认",
            )
            context = service.build_context("订单", "客服重新发送通知")
            self.assertEqual(memory["kind"], "confirmed_decision")
            self.assertTrue(
                any("客服角色" in item["content"] for item in context["memory"])
            )

    def test_model_agent_path_generates_short_manager_handoff(self) -> None:
        class Model:
            def __init__(self) -> None:
                self.actions = [
                    {
                        "action": "read_file",
                        "path": "backend/orders.py",
                        "start_line": 1,
                        "end_line": 20,
                    },
                    {
                        "action": "final",
                        "requirement": {
                            "business_goal": "增加订单重发通知接口",
                            "requested_changes": ["新增重发通知入口"],
                            "acceptance_criteria": ["返回发送成功或失败原因"],
                            "unknowns": [],
                        },
                        "changes": [
                            {
                                "path": "backend/orders.py",
                                "line_start": 1,
                                "line_end": 2,
                                "symbol": "resend_notification",
                                "instruction": "在现有订单通知函数附近增加 API 入口并复用发送逻辑。",
                                "confidence": "verified",
                            }
                        ],
                        "tests": ["覆盖成功和失败响应。"],
                        "risks": [],
                        "unknowns": [],
                    },
                ]

            def complete_json(self, messages, temperature=0.1):
                return self.actions.pop(0)

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            target = repo / "backend" / "orders.py"
            target.parent.mkdir(parents=True)
            target.write_text(
                "def resend_notification(order_id):\n    return {'ok': True}\n",
                encoding="utf-8",
            )
            service = TrackerService(settings_for(root))
            service.register_project(Project("orders", "订单系统", str(repo)))
            service.planning_agent = ReadOnlyPlanningAgent(Model(), max_steps=4)  # type: ignore[arg-type]

            proposal = service.generate_proposal("orders", "增加订单重发通知接口")

            self.assertIn("技术改动建议", proposal.markdown)
            self.assertIn("backend/orders.py:1", proposal.markdown)
            self.assertIn("只读调查结果", proposal.markdown)
            self.assertEqual(proposal.recommendations[0].confidence, "verified")
            self.assertTrue(service.store.get_project_map("orders", proposal.repository_version))


if __name__ == "__main__":
    unittest.main()
