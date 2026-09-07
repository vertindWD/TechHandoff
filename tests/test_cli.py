import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tracker.cli import main


class LocalCLITests(unittest.TestCase):
    def test_local_mode_reads_repository_and_notes_and_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            (repo / "orders.py").write_text(
                "def create_order():\n    return 'created'\n",
                encoding="utf-8",
            )
            notes = root / "meeting.txt"
            notes.write_text("订单创建后增加通知。", encoding="utf-8")
            output = root / "proposal.md"
            environment = {
                "DATABASE_PATH": str(root / "tracker.db"),
                "OUTPUT_DIR": str(root / "proposals"),
                "PROJECTS_FILE": "",
                "FEISHU_BOTS_FILE": "",
                "MODEL_NAME": "",
                "SEMANTIC_ENABLED": "false",
            }
            stdout = io.StringIO()
            with patch.dict(os.environ, environment, clear=False), redirect_stdout(stdout):
                result = main(
                    [
                        "local",
                        "--repo",
                        str(repo),
                        "--notes-file",
                        str(notes),
                        "--output",
                        str(output),
                    ]
                )
            payload = json.loads(stdout.getvalue())
            self.assertEqual(result, 0)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["output_path"], str(output))
            self.assertTrue(output.is_file())
            self.assertIn("订单", output.read_text(encoding="utf-8"))
