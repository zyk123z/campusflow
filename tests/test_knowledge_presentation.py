import unittest

from app.services.knowledge import SearchResult, format_knowledge_context, public_source_title


class KnowledgePresentationTests(unittest.TestCase):
    def test_known_source_uses_student_facing_title(self):
        self.assertEqual(public_source_title("scholarship-financial-aid.md"), "奖助学金申请指南")

    def test_unknown_source_does_not_expose_filename(self):
        title = public_source_title("uploads/internal-policy-v2.pdf")
        self.assertEqual(title, "校内知识库补充资料")
        self.assertNotIn(".pdf", title)

    def test_prompt_context_does_not_include_internal_source(self):
        context = format_knowledge_context([
            SearchResult(1, "scholarship-financial-aid.md", "申请条件以当年通知为准。", 1.0),
        ])
        self.assertIn("资料标题：奖助学金申请指南", context)
        self.assertNotIn("scholarship-financial-aid.md", context)


if __name__ == "__main__":
    unittest.main()
