import unittest
from unittest.mock import patch

from tracker.feishu import FeishuAPIError, FeishuClient, markdown_to_blocks


class FeishuTests(unittest.TestCase):
    def test_extracts_docx_identifier(self) -> None:
        self.assertEqual(
            FeishuClient.extract_document_id("https://example.feishu.cn/docx/ABC_def-123"),
            "ABC_def-123",
        )

    def test_extracts_minute_token_from_url_or_raw_value(self) -> None:
        token = "obcnABC_def-12345678"
        self.assertEqual(
            FeishuClient.extract_minute_token(
                f"https://example.feishu.cn/minutes/{token}?from=meeting"
            ),
            token,
        )
        self.assertEqual(FeishuClient.extract_minute_token(token), token)
        with self.assertRaises(ValueError):
            FeishuClient.extract_minute_token("普通会议纪要")

    def test_reads_plain_text_minute_transcript(self) -> None:
        client = FeishuClient("https://open.feishu.cn/open-apis", "id", "secret")
        with patch.object(
            client,
            "_request_bytes",
            return_value="张三 00:00:03\n我们先做登录页。".encode(),
        ) as request:
            transcript = client.read_minute_transcript("obcnABC_def-12345678")

        self.assertIn("我们先做登录页", transcript)
        method, path = request.call_args.args[:2]
        self.assertEqual(method, "GET")
        self.assertIn("/minutes/v1/minutes/obcnABC_def-12345678/transcript?", path)
        self.assertIn("need_speaker=true", path)
        self.assertIn("need_timestamp=true", path)
        self.assertIn("file_format=txt", path)

    def test_reports_json_error_from_minute_export(self) -> None:
        client = FeishuClient("https://open.feishu.cn/open-apis", "id", "secret")
        payload = b'{"code": 99991672, "msg": "permission denied"}'
        with patch.object(client, "_request_bytes", return_value=payload):
            with self.assertRaisesRegex(FeishuAPIError, "99991672"):
                client.read_minute_transcript("obcnABC_def-12345678")

    def test_converts_markdown_to_supported_blocks(self) -> None:
        blocks = markdown_to_blocks("# 标题\n\n## 小节\n- 条目\n1. 步骤\n正文")
        self.assertEqual([item["block_type"] for item in blocks], [3, 4, 12, 13, 2])


if __name__ == "__main__":
    unittest.main()
