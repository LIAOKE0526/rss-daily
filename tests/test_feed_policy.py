"""TEST DATA: public RSS summaries are distinct from confirmed paywalls."""
from datetime import datetime
import io
from contextlib import redirect_stdout
import unittest
from unittest import mock

import daily
import requests
from tests.test_workflow import fixture_snapshot, fixture_digest


class FeedContentPolicyTests(unittest.TestCase):
    def test_deduplication_carries_or_clears_adopted_body_provenance(self):
        def item(text, **extra):
            return dict(url="https://example.test/article", title="TEST DATA", time="2026-11-23T10:00:00+08:00", source="TEST DATA", publisher="TEST DATA", text=text, summary="", content_status="rss_excerpt", content_note="TEST DATA", **extra)
        original = item("short", fetch_article=True)
        mirror = item("TEST DATA longer mirror body", content_origin="mirror", mirror_url="https://example.test/feed", fetch_article=False, fetch_image_page=False)
        selected = daily.deduplicate([original, mirror])[0]
        self.assertEqual(selected["content_origin"], "mirror")
        self.assertEqual(selected["mirror_url"], mirror["mirror_url"])
        self.assertFalse(selected["fetch_article"])
        adopted = daily.deduplicate([selected, item("TEST DATA original article body longer than the mirror", fetch_article=True)])[0]
        self.assertNotIn("content_origin", adopted)
        self.assertNotIn("mirror_url", adopted)
        restricted = item("", fetch_article=False)
        restricted.update(content_status="paywall", summary="TEST DATA public preview", content_note="TEST DATA source restriction")
        mirror = item("TEST DATA full mirror body", content_origin="mirror", mirror_url="https://example.test/feed")
        known = daily.deduplicate([restricted, mirror])[0]
        self.assertEqual(known["text"], "TEST DATA public preview")
        self.assertNotIn("mirror_url", known)
        self.assertFalse(known["fetch_article"])

    def test_original_restriction_cannot_reuse_promoted_encoded_body_as_summary(self):
        xml = b'''<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>TEST DATA</title><item>
            <title>TEST DATA</title><link>https://example.test/article</link><pubDate>Mon, 23 Nov 2026 10:00:00 +0800</pubDate>
            <content:encoded><![CDATA[<p>TEST DATA full mirror body</p><img src="https://example.test/body.jpg">]]></content:encoded>
            </item></channel></rss>'''
        source = {"name": "TEST DATA", "url": "https://example.test/feed", "content_origin": "mirror", "feed_content": "prefer_fulltext"}
        with mock.patch.object(daily, "fetch_bytes", return_value=(xml, source["url"])):
            items, _ = daily.fetch_feed(source, datetime(2026, 11, 23, 11, tzinfo=daily.BJT), {"window_hours": 48, "request_timeout_seconds": 18})
        self.assertEqual(items[0]["summary"], "")
        self.assertIn("full mirror body", items[0]["text"])
        for status in (200, 401, 402):
            item = dict(items[0], images=list(items[0]["images"]))
            response = requests.Response(); response.status_code = status
            payload = (b'<script>{"isAccessibleForFree":false}</script>', item["url"])
            patch_args = {"return_value": payload} if status == 200 else {"side_effect": requests.HTTPError(response=response)}
            with mock.patch.object(daily, "fetch_bytes", **patch_args):
                daily.fetch_article(item, 18)
            self.assertEqual(item["text"], "")
            self.assertEqual(item["content_status"], "paywall")
            self.assertNotIn("content_origin", item)
            self.assertNotIn("mirror_url", item)
            self.assertFalse(item["images"])

    def test_available_mirror_body_is_kept_whole_with_internal_provenance(self):
        body = "TEST DATA mirror sentence. " * 300 + "TEST_DATA_END_OF_BODY"
        xml = f'''<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>TEST DATA</title><item>
            <title>TEST DATA headline</title><link>https://example.test/article</link><pubDate>Mon, 23 Nov 2026 10:00:00 +0800</pubDate>
            <description>TEST DATA public summary</description><content:encoded><![CDATA[<p>{body}</p><img src="https://example.test/body.jpg">]]></content:encoded>
            </item></channel></rss>'''.encode()
        source = {"name": "TEST DATA feed", "publisher": "TEST DATA publisher", "url": "https://example.test/feed",
                  "feed_content": "prefer_fulltext", "content_origin": "mirror", "fetch_article": False, "fetch_image_page": False}
        cfg = {"window_hours": 48, "request_timeout_seconds": 18}
        with mock.patch.object(daily, "fetch_bytes", return_value=(xml, source["url"])):
            items, report = daily.fetch_feed(source, datetime(2026, 11, 23, 11, tzinfo=daily.BJT), cfg)
        self.assertEqual(report["status"], "ok")
        article = items[0]
        self.assertEqual(article["content_status"], "mirror_text")
        self.assertEqual(article["content_origin"], "mirror")
        self.assertEqual(article["mirror_url"], source["url"])
        self.assertGreater(len(article["text"]), 4500)
        self.assertTrue(article["text"].endswith("TEST_DATA_END_OF_BODY"))
        self.assertIn("未核实", article["content_note"])
        self.assertEqual(len(article["images"]), 1)
        article["id"] = "A001"
        for status in ("mirror_text", "paywall"):
            article["content_status"] = status
            snapshot = fixture_snapshot(articles=[article])
            snapshot["sources"][0]["note"] = "TEST DATA 镜像正文 / 付费墙 / 完整性未核实"
            _, md, html, _ = daily.render(snapshot, fixture_digest(["A001"]), "TEST DATA")
            for label in ("镜像正文", "付费墙", "付费内容", "完整性未核实"):
                self.assertNotIn(label, md)
                self.assertNotIn(label, html)
            self.assertIn(article["url"], html)
        self.assertIn("仅摘要", daily.source_line(article))
        # A known restricted source still uses its public summary even if a mirror body exists.
        with mock.patch.object(daily, "fetch_bytes", return_value=(xml, source["url"])):
            restricted, _ = daily.fetch_feed(dict(source, access="summary_only"), datetime(2026, 11, 23, 11, tzinfo=daily.BJT), cfg)
        self.assertEqual(restricted[0]["content_status"], "paywall")
        self.assertEqual(restricted[0]["text"], "TEST DATA public summary")
        self.assertNotIn("content_origin", restricted[0])
        # If the mirror body is absent, preserve the actual summary rather than inventing text.
        without_body = xml[:xml.index(b'<content:encoded>')] + b'</item></channel></rss>'
        with mock.patch.object(daily, "fetch_bytes", return_value=(without_body, source["url"])):
            summary, _ = daily.fetch_feed(source, datetime(2026, 11, 23, 11, tzinfo=daily.BJT), cfg)
        self.assertEqual(summary[0]["content_status"], "summary_only")
        self.assertEqual(summary[0]["text"], "TEST DATA public summary")

    def test_summary_policy_ignores_encoded_body_without_claiming_paywall(self):
        xml = b'''<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"
            xmlns:media="http://search.yahoo.com/mrss/"><channel><title>TEST DATA</title><item>
            <title>TEST DATA public headline</title><link>https://example.test/article</link>
            <pubDate>Mon, 23 Nov 2026 10:00:00 +0800</pubDate>
            <description>TEST DATA public summary</description>
            <content:encoded><![CDATA[<p>TEST DATA unverified full body</p><img src="https://example.test/unverified.jpg">]]></content:encoded>
            <media:thumbnail url="https://example.test/public-cover.jpg"/>
            </item></channel></rss>'''
        source = {"name": "TEST DATA LatePost", "publisher": "晚点LatePost", "url": "https://example.test/feed.xml",
                  "feed_content": "summary_only", "fetch_article": False, "fetch_image_page": False, "note": "TEST DATA dates from feed; original unverified"}
        cfg = {"feeds": [source], "window_hours": 48, "request_timeout_seconds": 18, "workers": 1, "max_article_fetches": 10}
        with mock.patch.object(daily, "fetch_bytes", return_value=(xml, source["url"])), \
                mock.patch.object(daily, "fetch_article") as article_request, redirect_stdout(io.StringIO()):
            snapshot = daily.collect(cfg, datetime(2026, 11, 23, 11, tzinfo=daily.BJT))
        article_request.assert_not_called()
        self.assertEqual(snapshot["sources"][0]["status"], "ok")
        article = snapshot["articles"][0]
        self.assertEqual(article["content_status"], "summary_only")
        self.assertEqual(article["text"], "TEST DATA public summary")
        self.assertIn(source["note"], article["content_note"])
        self.assertEqual([i["url"] for i in article["images"]], ["https://example.test/public-cover.jpg"])
        with mock.patch.object(daily, "fetch_bytes") as original_request:
            daily.enrich_image_candidates(article, 18)
        original_request.assert_not_called()
        self.assertIn("禁止请求原文页", article["image_note"])
        without_description = xml.replace(b'<description>TEST DATA public summary</description>', b'')
        with mock.patch.object(daily, "fetch_bytes", return_value=(without_description, source["url"])):
            items, report = daily.fetch_feed(source, datetime(2026, 11, 23, 11, tzinfo=daily.BJT), cfg)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(items[0]["text"], "")
        self.assertEqual([i["url"] for i in items[0]["images"]], ["https://example.test/public-cover.jpg"])


if __name__ == "__main__":
    unittest.main()
