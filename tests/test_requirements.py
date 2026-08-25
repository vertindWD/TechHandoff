import unittest

from tracker.requirements import extract_requirement


class RequirementTests(unittest.TestCase):
    def test_extracts_change_and_marks_unconfirmed_boundaries(self) -> None:
        result = extract_requirement("订单详情页增加重新发送通知按钮。点击后显示成功提示。")
        self.assertIn("增加", result.requested_changes[0])
        self.assertTrue(any("角色" in item for item in result.unknowns))
        self.assertTrue(any("渠道" in item for item in result.unknowns))
        self.assertTrue(any("重复" in item for item in result.unknowns))

    def test_rejects_empty_notes(self) -> None:
        with self.assertRaises(ValueError):
            extract_requirement("  \n")


if __name__ == "__main__":
    unittest.main()

