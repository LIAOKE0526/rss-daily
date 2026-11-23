"""TEST DATA: prefer verified Reader documents without duplicating source links."""
import unittest

import daily
from tests.test_workflow import fixture_article, fixture_digest, fixture_snapshot


class ReaderReferenceTests(unittest.TestCase):
    def test_reader_translation_and_excerpt_links_replace_original_targets(self):
        for kind, label in [("chinese_report", "中文报道"), ("translation", "中文译文"), ("excerpt", "中文摘编"), ("summary_only", "仅摘要")]:
            with self.subTest(kind=kind):
                article = fixture_article()
                article.update(reader_url="https://read.example.test/read/chinese", reader_title="TEST DATA 中文文档",
                               reader_content_kind=kind, source_language="en")
                digest = daily.validate_digest(fixture_digest(["A001"]), [article])
                _, markdown, document, _ = daily.render(fixture_snapshot(articles=[article]), digest, "TEST DATA")
                self.assertIn(article["reader_url"], markdown)
                self.assertIn(article["reader_url"], document)
                self.assertNotIn(article["url"], markdown)
                self.assertNotIn(article["url"], document)
                self.assertIn(label, document)
                self.assertIn(article["reader_title"], document)

    def test_external_english_reference_is_labelled_and_bad_reader_url_rejected(self):
        article = fixture_article()
        article["source_language"] = "en"
        digest = daily.validate_digest(fixture_digest(["A001"]), [article])
        _, markdown, document, _ = daily.render(fixture_snapshot(articles=[article]), digest, "TEST DATA")
        self.assertIn(article["url"], document)
        self.assertIn("英文原文", markdown)
        article["reader_url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            daily.validate_digest(digest, [article])


if __name__ == "__main__":
    unittest.main()
