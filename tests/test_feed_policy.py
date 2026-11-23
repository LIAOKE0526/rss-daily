"""TEST DATA: public RSS summaries are distinct from confirmed paywalls."""
from datetime import datetime
import io
from contextlib import redirect_stdout
import unittest
from unittest import mock

import daily


class FeedContentPolicyTests(unittest.TestCase):
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
