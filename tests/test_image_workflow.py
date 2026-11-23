"""TEST DATA: image collection, selected-only requests, and reader placement."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import requests

import daily
from tests.test_images import RSS_FIXTURE, HTML_FIXTURE
from tests.test_workflow import fixture_article, fixture_snapshot, fixture_digest, fixture_api_config, TEST_NOW


class ImageWorkflowTests(unittest.TestCase):
    def test_feed_candidates_survive_link_dedup(self):
        xml = RSS_FIXTURE.replace(b'<title>TEST DATA story</title>', b'<title>TEST DATA story</title><pubDate>Mon, 23 Nov 2026 08:30:00 +0800</pubDate>')
        source = {"name": "TEST DATA feed", "url": "https://example.test/rss"}
        with mock.patch.object(daily, "fetch_bytes", return_value=(xml, source["url"])):
            items, report = daily.fetch_feed(source, TEST_NOW, {"request_timeout_seconds": 18, "window_hours": 48})
        self.assertEqual(report["status"], "ok")
        self.assertTrue(items[0]["images"])
        with mock.patch.object(daily, "fetch_bytes", return_value=(xml, source["url"])):
            restricted, _ = daily.fetch_feed(dict(source, access="summary_only"), TEST_NOW, {"request_timeout_seconds": 18, "window_hours": 48})
        self.assertNotIn("https://news.example.test/story/images/detail.png", [i["url"] for i in restricted[0]["images"]])
        other = dict(items[0], source="TEST DATA duplicate", images=[{
            "url": "https://example.test/extra.jpg", "source_page": items[0]["url"], "alt": "", "caption": "", "origin": "rss_html"}])
        result = daily.deduplicate(items + [other])
        self.assertEqual(len(result), 1)
        self.assertIn("https://example.test/extra.jpg", [i["url"] for i in result[0]["images"]])

    def test_existing_body_fetch_reuses_html_and_paywall_only_exposes_og(self):
        item = fixture_article()
        with mock.patch.object(daily, "fetch_bytes", return_value=(HTML_FIXTURE.encode(), item["url"])) as fetch:
            daily.fetch_article(item, 18)
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(item["images"])
        paid = fixture_article()
        markup = b'<meta property="og:image" content="/cover.jpg"><script type="application/ld+json">{"isAccessibleForFree":false}</script><article><figure><img src="/cover.jpg" alt="restricted alt"><figcaption>restricted caption</figcaption></figure><img src="/private.jpg"></article>'
        with mock.patch.object(daily, "fetch_bytes", return_value=(markup, paid["url"])):
            daily.fetch_article(paid, 18)
        self.assertEqual(paid["content_status"], "paywall")
        self.assertEqual(len(paid["images"]), 1)
        self.assertEqual(paid["images"][0]["alt"], "")
        self.assertEqual(paid["images"][0]["caption"], "")

    def test_failed_selected_page_keeps_prior_candidates_and_text(self):
        item = fixture_article()
        item["images"] = [{"url": "https://example.test/cover.jpg", "source_page": item["url"], "alt": "TEST DATA", "caption": "", "origin": "rss_html"}]
        response = requests.Response(); response.status_code = 404
        error = requests.HTTPError(response=response)
        before = item["text"]
        with mock.patch.object(daily, "fetch_bytes", side_effect=error):
            daily.enrich_image_candidates(item, 18)
        self.assertIn("HTTP 404", item["image_note"])
        self.assertEqual(item["text"], before)
        self.assertEqual(len(item["images"]), 1)

    def test_only_selected_reader_images_render_near_paragraph_with_caption(self):
        article = fixture_article()
        article["images"] = [{"url": "https://example.test/unreviewed.jpg", "source_page": article["url"], "origin": "rss_html"}]
        snapshot = fixture_snapshot(articles=[article])
        digest = fixture_digest(["A001"], "TEST DATA first.[A001]\n\nTEST DATA second.")
        _, _, no_image, _ = daily.render(snapshot, digest, "TEST DATA")
        self.assertNotIn("<figure>", no_image)
        selected = {"url": "https://example.test/reviewed.jpg", "source_page": article["url"],
                    "caption": "TEST DATA official rendering <script>", "credit": "TEST DATA maker", "after_paragraph": 1}
        digest["topics"][0]["images"] = [selected]
        daily.validate_digest(digest, [article])
        _, markdown, document, _ = daily.render(snapshot, digest, "TEST DATA")
        self.assertEqual(document.count("<figure>"), 1)
        self.assertLess(document.index("<figure>"), document.index("TEST DATA second."))
        self.assertIn("来源：", markdown)
        self.assertIn("TEST DATA maker", document)
        self.assertNotIn("<script>", document)
        self.assertNotIn("unreviewed.jpg", document)
        selected["source_page"] = "https://example.test/source>page"
        _, markdown, _, _ = daily.render(snapshot, digest, "TEST DATA")
        self.assertIn("source%3Epage", markdown)
        selected["url"] = "javascript:alert(1)"
        with self.assertRaises(ValueError):
            daily.validate_digest(digest, [article])

    def test_selected_only_cli_calls_no_model_and_preserves_original_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="rss-image-test-") as temp:
            root = Path(temp); output = root / "out"; output.mkdir(); source = output / "fetch.articles.json"
            snapshot = fixture_snapshot(articles=[fixture_article("A001"), fixture_article("A002")])
            daily.write_json(source, snapshot); original = source.read_text(encoding="utf-8")
            config = root / "config.json"
            daily.write_json(config, {"workers": 2, "request_timeout_seconds": 18, "output_dir": str(output)})
            (output / "latest.md").write_text("TEST DATA existing digest", encoding="utf-8")
            def enrich(item, timeout):
                item["images"] = [{"url": "https://example.test/selected.jpg", "source_page": item["url"], "alt": "", "caption": "", "origin": "og_image"}]
                return item
            argv = ["daily.py", "--config", str(config), "--snapshot", str(source), "--image-articles", "A001"]
            with mock.patch("sys.argv", argv), mock.patch.object(daily, "enrich_image_candidates", side_effect=enrich) as request, \
                    mock.patch.object(daily, "post_json") as model, mock.patch.object(daily, "publish") as publish, redirect_stdout(io.StringIO()):
                daily.main()
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0]["id"], "A001")
            model.assert_not_called(); publish.assert_not_called()
            saved = json.loads((output / "image.articles.json").read_text(encoding="utf-8"))
            self.assertTrue(saved["articles"][0]["images"])
            self.assertNotIn("images", saved["articles"][1])
            self.assertEqual(source.read_text(encoding="utf-8"), original)
            self.assertEqual((output / "latest.md").read_text(encoding="utf-8"), "TEST DATA existing digest")

    def test_text_api_cannot_publish_unreviewed_pictures(self):
        article = fixture_article()
        value = fixture_digest(["A001"])
        value["topics"][0]["images"] = [{"url": "https://example.test/unreviewed.jpg",
            "source_page": article["url"], "caption": "TEST DATA unreviewed"}]
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(value)}}]}
        with mock.patch.dict("os.environ", {"RSS_DAILY_TEST_API_KEY": "TEST_DATA_KEY"}, clear=True), \
                mock.patch.object(daily, "post_json", return_value=response):
            digest, state = daily.generate_digest(fixture_snapshot(articles=[article]), fixture_api_config())
        self.assertIsNotNone(digest)
        self.assertNotIn("images", digest["topics"][0])

    def test_enrichment_rejects_same_input_output_before_requests(self):
        with tempfile.TemporaryDirectory(prefix="rss-image-same-") as temp:
            root = Path(temp); source = root / "image.articles.json"; cfg = root / "config.json"
            daily.write_json(source, fixture_snapshot(articles=[fixture_article()]))
            daily.write_json(cfg, {"output_dir": str(root), "workers": 1, "request_timeout_seconds": 18})
            argv = ["daily.py", "--config", str(cfg), "--snapshot", str(source), "--image-articles", "A001"]
            with mock.patch("sys.argv", argv), mock.patch.object(daily, "enrich_image_candidates") as request, redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit):
                    daily.main()
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
