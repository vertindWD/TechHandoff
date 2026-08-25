import tempfile
import unittest
from pathlib import Path

from tracker.models import Project
from tracker.repository import RepositoryAccessError, RepositoryReader


class RepositoryReaderTests(unittest.TestCase):
    def test_finds_real_evidence_and_skips_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir()
            (root / "src" / "OrderDetail.tsx").write_text(
                "export function OrderDetail() { return <button>重新发送通知</button>; }",
                encoding="utf-8",
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_order_detail.ts").write_text(
                'import { OrderDetail } from "../src/OrderDetail";\nconst subject = OrderDetail;',
                encoding="utf-8",
            )
            (root / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
            project = Project("orders", "订单", str(root))
            reader = RepositoryReader((root,))
            snapshot, files = reader.scan(project)
            evidence = reader.find_evidence(files, "订单详情增加重新发送通知按钮")
            self.assertEqual(snapshot.file_count, 2)
            self.assertEqual(evidence[0].path, "src/OrderDetail.tsx")
            self.assertTrue(any(item.path == "tests/test_order_detail.ts" for item in evidence))
            self.assertNotIn("do-not-read", evidence[0].excerpt)

    def test_rejects_repo_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as allowed, tempfile.TemporaryDirectory() as outside:
            reader = RepositoryReader((Path(allowed),))
            with self.assertRaises(RepositoryAccessError):
                reader.validate_root(outside)

    def test_keeps_language_build_manifests_for_semantic_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "go.mod").write_text("module example\n\ngo 1.25.0\n", encoding="utf-8")
            (root / "go.sum").write_text("example dependency checksum\n", encoding="utf-8")
            (root / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")

            _, files = RepositoryReader((root,)).scan(Project("go", "Go", str(root)))

            self.assertEqual({item.path for item in files}, {"go.mod", "go.sum", "main.go"})


if __name__ == "__main__":
    unittest.main()
