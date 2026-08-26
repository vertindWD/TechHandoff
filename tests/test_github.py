import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from test_service import settings_for
from tracker.github import (
    GitHubAPIError,
    GitHubClient,
    GitHubCommit,
    GitHubRepositorySyncer,
    GitHubTreeFile,
    verify_webhook_signature,
)
from tracker.models import Project
from tracker.repository import RepositoryReader
from tracker.service import TrackerService


def make_tarball(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for path, content in files.items():
            info = tarfile.TarInfo(f"owner-repo-abcdef/{path}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return output.getvalue()


class FakeGitHubClient:
    def __init__(self) -> None:
        self.commit = GitHubCommit("commit-1", "tree-1")
        self.tree = (
            GitHubTreeFile("src/order.py", "blob-1", 52),
            GitHubTreeFile("contracts/order.move", "blob-move", 48),
            GitHubTreeFile("README", "blob-readme", 20),
            GitHubTreeFile(".env", "secret-blob", 20),
        )
        self.blobs = {
            "blob-1": b'def resend_notification():\n    return "order notification"\n',
            "blob-2": b'def resend_notification():\n    return "order notification sent"\n',
            "blob-3": b"from src.order import resend_notification\n",
            "blob-move": b"module orders::notification { public fun resend() {} }\n",
            "blob-readme": b"Order notification service\n",
        }
        self.tarball_calls = 0
        self.blob_calls = 0

    def get_commit(self, owner: str, repo: str, ref: str) -> GitHubCommit:
        return self.commit

    def get_tree(self, owner: str, repo: str, tree_sha: str):
        return self.tree

    def get_blob(self, owner: str, repo: str, blob_sha: str) -> bytes:
        self.blob_calls += 1
        return self.blobs[blob_sha]

    def download_tarball(self, owner: str, repo: str, commit_sha: str) -> bytes:
        self.tarball_calls += 1
        return make_tarball(
            {
                "src/order.py": self.blobs["blob-1"],
                "contracts/order.move": self.blobs["blob-move"],
                "README": self.blobs["blob-readme"],
                ".env": b"SECRET=never-index",
            }
        )


class GitHubTests(unittest.TestCase):
    def test_official_webhook_signature_vector(self) -> None:
        secret = "It's a Secret to Everybody"
        payload = b"Hello, World!"
        signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
        self.assertTrue(verify_webhook_signature(payload, signature, secret))
        self.assertFalse(verify_webhook_signature(payload + b"!", signature, secret))

    def test_rejects_truncated_recursive_tree(self) -> None:
        class TruncatedClient(GitHubClient):
            def _request_json(self, path: str):
                return {"truncated": True, "tree": []}

        client = TruncatedClient("https://api.github.com")
        with self.assertRaises(GitHubAPIError):
            client.get_tree("acme", "orders", "tree-sha")

    def test_full_then_incremental_sync_and_cached_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = settings_for(root)
            service = TrackerService(settings)
            project = Project(
                project_id="orders-github",
                name="GitHub订单系统",
                github_owner="acme",
                github_repo="orders",
                github_ref="main",
            )
            service.register_project(project)
            fake = FakeGitHubClient()
            syncer = GitHubRepositorySyncer(
                fake,
                service.store,
                RepositoryReader((root,)),
                full_sync_threshold=100,
            )
            service.github = syncer

            first = service.sync_github_project(project.project_id)
            self.assertEqual(first["mode"], "full")
            self.assertEqual(first["file_count"], 3)
            self.assertEqual(fake.tarball_calls, 1)
            cached = service.build_context(project.project_id, "订单通知")
            self.assertEqual(cached["repository_version"], "commit-1")
            self.assertTrue(cached["evidence"])
            self.assertIn(
                "contracts/order.move",
                {item["path"] for item in service.store.list_repository_files(project.project_id)},
            )
            self.assertEqual(fake.tarball_calls, 1, "对话读取缓存，不应再次下载 GitHub")
            proposal = service.generate_proposal(
                project.project_id,
                "订单通知需要支持重新发送，并显示成功或失败提示。",
                "GitHub memory test",
            )
            remembered = service.build_context(project.project_id, "重新发送订单通知")
            self.assertTrue(remembered["memory"])
            self.assertEqual(fake.tarball_calls, 1, "生成方案和后续对话都应复用缓存")

            fake.commit = GitHubCommit("commit-2", "tree-2")
            fake.tree = (
                GitHubTreeFile("src/order.py", "blob-2", 57),
                GitHubTreeFile("tests/test_order.py", "blob-3", 43),
                GitHubTreeFile("README", "blob-readme", 20),
            )
            second = service.sync_github_project(project.project_id)
            self.assertEqual(second["mode"], "incremental")
            self.assertEqual(second["changed"], 2)
            self.assertEqual(fake.blob_calls, 2)
            self.assertEqual(
                service.store.get_repository_snapshot(project.project_id)["commit_sha"],
                "commit-2",
            )
            self.assertEqual(service.store.get_proposal(proposal.proposal_id)["status"], "stale")
            stale_code_memory = [
                item
                for item in service.store.list_memory(project.project_id, include_stale=True)
                if item["kind"] == "code_fact" and item["stale"] == 1
            ]
            self.assertTrue(stale_code_memory)


if __name__ == "__main__":
    unittest.main()
