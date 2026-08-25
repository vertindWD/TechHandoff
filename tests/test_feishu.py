import unittest

from tracker.feishu import FeishuClient, markdown_to_blocks


class FeishuTests(unittest.TestCase):
    def test_extracts_docx_identifier(self) -> None:
        self.assertEqual(
            FeishuClient.extract_document_id("https://example.feishu.cn/docx/ABC_def-123"),
            "ABC_def-123",
        )

    def test_converts_markdown_to_supported_blocks(self) -> None:
        blocks = markdown_to_blocks("# 标题\n\n## 小节\n- 条目\n1. 步骤\n正文")
        self.assertEqual([item["block_type"] for item in blocks], [3, 4, 12, 13, 2])


if __name__ == "__main__":
    unittest.main()

