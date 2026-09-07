import io
import json
import os
import tempfile
import unittest
import zipfile
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

    def test_local_mode_reads_docx_meeting_notes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            repo.mkdir()
            (repo / "ims.py").write_text("def upload(): pass\n", encoding="utf-8")
            notes = root / "IMS 功能确认.docx"
            document_xml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body><w:p><w:r><w:t>IMS 增加上传功能</w:t></w:r></w:p></w:body>'
                '</w:document>'
            )
            with zipfile.ZipFile(notes, "w") as document:
                document.writestr("word/document.xml", document_xml)
            output = root / "proposal.md"
            environment = {
                "DATABASE_PATH": str(root / "tracker.db"),
                "OUTPUT_DIR": str(root / "proposals"),
                "PROJECTS_FILE": "",
                "FEISHU_BOTS_FILE": "",
                "MODEL_NAME": "",
                "SEMANTIC_ENABLED": "false",
            }
            with patch.dict(os.environ, environment, clear=False), redirect_stdout(io.StringIO()):
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
            self.assertEqual(result, 0)
            self.assertIn("IMS", output.read_text(encoding="utf-8"))
