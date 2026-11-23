"""Offline acceptance checks using TEST DATA fixtures only."""

from contextlib import redirect_stdout
from datetime import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import requests

import daily


TEST_NOW = datetime(2026, 11, 23, 10, 45, tzinfo=daily.BJT)
TEST_ARTICLE_TIME = "2026-11-23T08:30:00+08:00"

# TEST DATA only. All links use the reserved example.test domain; no fixture is
# fetched from the network.
FIXTURE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<!-- TEST DATA: synthetic RSS items for offline acceptance checks. -->
<rss version="2.0"><channel>
  <title>TEST DATA Example Feed</title>
  <link>https://example.test/</link>
  <description>TEST DATA only; no real reporting.</description>
  <item>
    <title>[TEST DATA] CST alias article</title>
    <link>https://example.test/cst-article</link>
    <guid>https://example.test/cst-article</guid>
    <pubDate>Sun, 22 Nov 2026 23:15:00 CST</pubDate>
    <description>TEST DATA summary for the CST alias article.</description>
  </item>
  <item>
    <title>[TEST DATA] Ordinary in-window article</title>
    <link>https://example.test/in-window-article</link>
    <guid>https://example.test/in-window-article</guid>
    <pubDate>Mon, 23 Nov 2026 08:30:00 +0800</pubDate>
    <description>TEST DATA summary for the ordinary article.</description>
  </item>
  <item>
    <title>[TEST DATA] Old article</title>
    <link>https://example.test/old-article</link>
    <guid>https://example.test/old-article</guid>
    <pubDate>Fri, 20 Nov 2026 10:00:00 +0800</pubDate>
    <description>TEST DATA old summary.</description>
  </item>
  <item>
    <title>[TEST DATA] Missing-time article</title>
    <link>https://example.test/missing-time-article</link>
    <guid>https://example.test/missing-time-article</guid>
    <description>TEST DATA summary without a publication date.</description>
  </item>
  <item>
    <title>[TEST DATA] Future article</title>
    <link>https://example.test/future-article</link>
    <guid>https://example.test/future-article</guid>
    <pubDate>Mon, 23 Nov 2026 13:15:00 +0800</pubDate>
    <description>TEST DATA future summary.</description>
  </item>
</channel></rss>
"""


def fixture_source(name="TEST DATA feed", url="https://example.test/rss"):
    return {
        "name": name,
        "url": url,
        "enabled": True,
        "publisher": "TEST DATA publisher",
        "date_timezone_aliases": {"CST": "+0800"},
    }


def fixture_article(article_id="A001", *, title="[TEST DATA] article", url=None,
                    content_status="summary_only", publisher="TEST DATA publisher",
                    text="TEST DATA article text", background=False):
    return {
        "id": article_id,
        "title": title,
        "url": url or f"https://example.test/{article_id.lower()}",
        "source": "TEST DATA source",
        "publisher": publisher,
        "time": TEST_ARTICLE_TIME,
        "time_basis": "published",
        "published_raw": "TEST DATA date",
        "updated_raw": "",
        "summary": "TEST DATA summary",
        "text": text,
        "content_status": content_status,
        "content_note": "TEST DATA fixture content",
        "fetch_article": False,
        "background": background,
    }


def fixture_snapshot(date="2026-11-23", articles=None):
    return {
        "schema_version": 1,
        "data_kind": "test_fixture",
        "generated_at": f"{date}T10:45:00+08:00",
        "window_start": f"{date}T10:45:00+08:00",
        "window_hours": 48,
        "sources": [{
            "name": "TEST DATA source",
            "status": "ok",
            "in_window": len(articles or []),
            "note": "TEST DATA only",
        }],
        "articles": articles or [],
        "duplicates_removed": 0,
    }


def fixture_digest(article_ids, paragraph=None):
    refs = "".join(f"[{article_id}]" for article_id in article_ids)
    return {
        "topics": [{
            "title": "[TEST DATA] Fixture topic",
            "paragraph": paragraph if paragraph is not None else refs + " TEST DATA summary.",
            "article_ids": article_ids,
        }]
    }


def fixture_api_config():
    return {
        "llm_max_articles": 2,
        "llm_article_chars": 500,
        "prompt_file": "prompts/daily.txt",
        "api": {
            "key_env": "RSS_DAILY_TEST_API_KEY",
            "base_url_env": "RSS_API_BASE_URL",
            "model_env": "RSS_API_MODEL",
            "base_url": "https://api.example.test/v1",
            "model": "TEST DATA model",
            "timeout_seconds": 10,
            "max_tokens": 500,
        },
    }


class WorkflowAcceptanceTests(unittest.TestCase):
    def test_fetch_feed_filters_fixture_and_interprets_cst_alias(self):
        source = fixture_source()
        cfg = {"request_timeout_seconds": 5, "window_hours": 48}
        with mock.patch.object(daily, "fetch_bytes", return_value=(FIXTURE_XML, source["url"])):
            items, report = daily.fetch_feed(source, TEST_NOW, cfg)

        self.assertEqual([item["title"] for item in items], [
            "[TEST DATA] CST alias article",
            "[TEST DATA] Ordinary in-window article",
        ])
        self.assertEqual(report["total"], 5)
        self.assertEqual(report["in_window"], 2)
        self.assertEqual(report["outside_window"], 2)
        self.assertEqual(report["missing_time"], 1)
        self.assertEqual(report["invalid_link"], 0)

        alias_item = next(item for item in items if "CST alias" in item["title"])
        self.assertEqual(alias_item["time"], "2026-11-22T23:15:00+08:00")
        parsed = daily.feedparser.parse(FIXTURE_XML)
        default_time, _ = daily.entry_time(parsed.entries[0])
        self.assertEqual(default_time.isoformat(), "2026-11-23T13:15:00+08:00")

    def test_deduplicate_merges_tracking_url_and_fragment_only(self):
        items = [
            {
                "url": "https://example.test/story?utm_source=TEST_DATA#top",
                "source": "TEST DATA first source",
                "time": "2026-11-23T08:00:00+08:00",
                "text": "TEST DATA short",
                "summary": "TEST DATA short",
                "content_status": "summary_only",
                "content_note": "TEST DATA only",
            },
            {
                "url": "https://EXAMPLE.test/story?fbclid=TEST_DATA#comments",
                "source": "TEST DATA second source",
                "time": "2026-11-23T09:00:00+08:00",
                "text": "TEST DATA longer duplicate text",
                "summary": "TEST DATA longer duplicate text",
                "content_status": "rss_excerpt",
                "content_note": "TEST DATA duplicate excerpt",
            },
            {
                "url": "https://example.test/another-story?utm_campaign=TEST_DATA",
                "source": "TEST DATA third source",
                "time": "2026-11-23T07:00:00+08:00",
                "text": "TEST DATA distinct article",
                "summary": "TEST DATA distinct article",
                "content_status": "summary_only",
                "content_note": "TEST DATA only",
            },
        ]

        result = daily.deduplicate(items)

        self.assertEqual(len(result), 2)
        duplicate = next(article for article in result if article["also_in"])
        self.assertEqual(duplicate["also_in"], ["TEST DATA second source"])
        self.assertEqual(duplicate["text"], "TEST DATA longer duplicate text")
        self.assertTrue(any(article["url"].startswith("https://example.test/another-story") for article in result))

    def test_collect_keeps_good_fixture_when_another_source_returns_404(self):
        good_url = "https://example.test/good-rss"
        bad_url = "https://example.test/missing-rss"
        sources = [fixture_source("TEST DATA good source", good_url), fixture_source("TEST DATA bad source", bad_url)]
        cfg = {
            "feeds": sources,
            "request_timeout_seconds": 5,
            "window_hours": 48,
            "workers": 2,
            "max_article_fetches": 5,
        }
        missing_response = requests.Response()
        missing_response.status_code = 404

        def fetch_fixture(url, timeout):
            if url == good_url:
                return FIXTURE_XML, url
            self.assertEqual(url, bad_url)
            raise requests.HTTPError("TEST DATA synthetic HTTP failure", response=missing_response)

        output = io.StringIO()
        with mock.patch.object(daily, "fetch_bytes", side_effect=fetch_fixture), \
                mock.patch.object(daily, "fetch_article", side_effect=lambda item, timeout: item) as fetch_article, \
                redirect_stdout(output):
            snapshot = daily.collect(cfg, TEST_NOW)

        self.assertEqual(len(snapshot["articles"]), 2)
        self.assertCountEqual(
            [article["title"] for article in snapshot["articles"]],
            ["[TEST DATA] CST alias article", "[TEST DATA] Ordinary in-window article"],
        )
        self.assertEqual(snapshot["sources"][0]["status"], "ok")
        self.assertEqual(snapshot["sources"][1]["status"], "error")
        self.assertEqual(snapshot["sources"][1]["error"], "HTTP 404")
        self.assertEqual(fetch_article.call_count, 2)

    def test_generate_digest_without_api_environment_is_preview_without_post(self):
        cfg = fixture_api_config()
        snapshot = fixture_snapshot(articles=[fixture_article()])

        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(daily, "post_json") as post, \
                mock.patch.object(daily.requests, "post") as requests_post:
            digest, state = daily.generate_digest(snapshot, cfg)

        self.assertIsNone(digest)
        self.assertIn("尚未生成", state)
        post.assert_not_called()
        requests_post.assert_not_called()

    def test_api_failures_and_bad_citations_return_preview_without_key(self):
        fake_key = "TEST_DATA_FAKE_API_KEY_7b1f"
        cfg = fixture_api_config()
        snapshot = fixture_snapshot(articles=[fixture_article()])

        unauthorized = requests.Response()
        unauthorized.status_code = 401
        unauthorized_error = requests.HTTPError("TEST DATA unauthorized", response=unauthorized)

        def response_with_content(content):
            response = mock.Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {"choices": [{"message": {"content": json.dumps(content)}}]}
            return response

        unknown_evidence = fixture_digest(["A999"], paragraph="[A999] TEST DATA unknown reference.")
        invalid_inline_reference = fixture_digest(["A001"], paragraph="[A999] TEST DATA invalid reference.")
        responses = [
            unauthorized_error,
            response_with_content(unknown_evidence),
            response_with_content(invalid_inline_reference),
        ]
        with mock.patch.dict(os.environ, {"RSS_DAILY_TEST_API_KEY": fake_key}, clear=True), \
                mock.patch.object(daily, "post_json", side_effect=responses) as post, \
                mock.patch.object(daily.requests, "post") as requests_post:
            outcomes = [daily.generate_digest(snapshot, cfg) for _ in responses]

        self.assertEqual(post.call_count, 3)
        self.assertTrue(all(digest is None for digest, _state in outcomes))
        self.assertTrue(all("尚未生成" in state for _digest, state in outcomes))
        self.assertIn("HTTP 401", outcomes[0][1])
        self.assertTrue(all(fake_key not in state for _digest, state in outcomes))
        requests_post.assert_not_called()

    def test_copyright_block_reports_materials_without_rewriting_or_exposing_key(self):
        cfg = fixture_api_config()
        article = fixture_article("A001")
        snapshot = fixture_snapshot(articles=[article])
        fake_key = "TEST_DATA_BLOCKED_FAKE_KEY"
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({
            "blocked": {"reason": "copyright", "article_ids": ["A001"],
                        "message": "TEST DATA 需提供本地材料；<script>".ljust(490, "X") + fake_key}
        }, ensure_ascii=False)}}]}
        with mock.patch.dict(os.environ, {"RSS_DAILY_TEST_API_KEY": fake_key}, clear=True), \
                mock.patch.object(daily, "post_json", return_value=response):
            digest, state = daily.generate_digest(snapshot, cfg)
        self.assertIsNone(digest)
        self.assertIn("版权引用限制", state)
        self.assertIn("A001", state)
        self.assertIn(article["source"], state)
        self.assertIn("需提供本地材料", state)
        self.assertIn("未自动改为同义改写", state)
        self.assertNotIn(fake_key, state)
        self.assertNotIn(fake_key[:10], state)
        _title, markdown, document, _body = daily.render(snapshot, None, state)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("<script>", document)

    def test_mocked_valid_api_response_renders_citations_without_persisting_key(self):
        fake_key = "TEST_DATA_SUCCESS_FAKE_KEY_a924"
        articles = [
            fixture_article("A001", url="https://example.test/api-a001", content_status="summary_only"),
            fixture_article("A002", url="https://example.test/api-a002", content_status="public_extract"),
        ]
        snapshot = fixture_snapshot(articles=articles)
        cfg = fixture_api_config()
        api_content = fixture_digest(
            ["A001", "A002"],
            paragraph="[A001][A002] TEST DATA mocked summary.",
        )
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "choices": [{"message": {"content": json.dumps(api_content, ensure_ascii=False)}}]
        }
        env = {
            "RSS_DAILY_TEST_API_KEY": fake_key,
            "RSS_API_BASE_URL": "https://api.example.test/v1",
            "RSS_API_MODEL": "test-model",
        }

        with tempfile.TemporaryDirectory(prefix="rss-daily-test-") as temp_dir:
            output_dir = Path(temp_dir)
            log = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(daily, "post_json", return_value=response) as post, \
                    mock.patch.object(daily.requests, "post") as requests_post:
                digest, state = daily.generate_digest(snapshot, cfg)
                self.assertIsNotNone(digest)
                self.assertEqual(digest["provenance"], {
                    "method": "compatible_api",
                    "model": "test-model",
                    "candidate_count": 2,
                })
                self.assertIn("AI 日报已生成", state)

                post.assert_called_once()
                self.assertEqual(post.call_args.args[0], "https://api.example.test/v1/chat/completions")
                request_body = post.call_args.kwargs["json"]
                self.assertEqual(request_body["model"], "test-model")
                request_payload = json.loads(request_body["messages"][1]["content"])
                self.assertEqual(
                    [article["id"] for article in request_payload["current_articles"]],
                    ["A001", "A002"],
                )
                requests_post.assert_not_called()

                with redirect_stdout(log):
                    daily.publish(output_dir, snapshot, digest, state)

            _title, markdown, document, _body = daily.render(snapshot, digest, state)
            self.assertIn("<https://example.test/api-a001>", markdown)
            self.assertIn("<https://example.test/api-a002>", markdown)
            self.assertIn('href="https://example.test/api-a001">', document)
            self.assertIn('href="https://example.test/api-a002">', document)
            self.assertNotIn("[A001]", markdown)
            self.assertIn('class="cite"', document)
            self.assertIn('href="https://example.test/api-a001">[1]</a>', document)
            self.assertIn('href="https://example.test/api-a002">[2]</a>', document)
            persisted_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output_dir.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(fake_key, state)
            self.assertNotIn(fake_key, log.getvalue())
            self.assertNotIn(fake_key, markdown)
            self.assertNotIn(fake_key, document)
            self.assertNotIn(fake_key, persisted_text)

    def test_render_valid_digest_escapes_title_and_labels_shared_publisher(self):
        articles = [
            fixture_article(
                "A001",
                title="[TEST DATA] <script>alert(1)</script> article",
                url="https://example.test/a001",
                content_status="summary_only",
            ),
            fixture_article(
                "A002",
                title="[TEST DATA] public extract article",
                url="https://example.test/a002",
                content_status="public_extract",
            ),
        ]
        digest = daily.validate_digest(fixture_digest(["A001", "A002"]), articles)
        snapshot = fixture_snapshot(articles=articles)

        _title, markdown, document, _body = daily.render(snapshot, digest, "TEST DATA preview")

        self.assertNotIn("[A001]", markdown)
        self.assertNotIn("[A002]", markdown)
        self.assertIn("仅摘要", markdown)
        self.assertIn("公开网页提取片段", markdown)
        self.assertIn("原文（同一媒体）", markdown)
        self.assertIn("原文（同一媒体）", document)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", document)
        self.assertNotIn("<script>", document)
        self.assertIn('href="https://example.test/a001">', document)
        self.assertIn('href="https://example.test/a002">', document)
        self.assertIn('href="https://example.test/a001">[1]</a>', document)
        self.assertIn('href="https://example.test/a002">[2]</a>', document)

    def test_publish_reruns_keep_two_rss_issues_and_escape_description_html(self):
        article = fixture_article(
            "A001",
            title="[TEST DATA] <script>alert(1)</script> RSS article",
            url="https://example.test/rss-article",
            content_status="summary_only",
        )
        snapshot_day_1 = fixture_snapshot("2026-11-22", [article])
        snapshot_day_2 = fixture_snapshot("2026-11-23", [article])
        digest = daily.validate_digest(fixture_digest(["A001"]), [article])

        with tempfile.TemporaryDirectory(prefix="rss-daily-test-") as temp_dir:
            output_dir = Path(temp_dir)
            with redirect_stdout(io.StringIO()):
                daily.publish(output_dir, snapshot_day_1, digest, "TEST DATA successful issue")
                daily.publish(output_dir, snapshot_day_1, digest, "TEST DATA successful issue rerun")
                daily.publish(output_dir, snapshot_day_2, digest, "TEST DATA next issue")
                daily.publish(output_dir, snapshot_day_1, digest, "TEST DATA successful issue rerun again")

            feed_path = output_dir / "feed.xml"
            feed_text = feed_path.read_text(encoding="utf-8")
            feed_root = ET.fromstring(feed_text)
            items = feed_root.findall("./channel/item")
            guids = [item.findtext("guid") for item in items]
            descriptions = [item.findtext("description", default="") for item in items]

        self.assertEqual(len(items), 2)
        self.assertEqual(set(guids), {
            "local-thematic-daily:2026-11-22",
            "local-thematic-daily:2026-11-23",
        })
        self.assertIn('&lt;a href="https://example.test/rss-article"&gt;', feed_text)
        self.assertTrue(any('href="https://example.test/rss-article"' in desc for desc in descriptions))
        self.assertTrue(all("<script>" not in desc for desc in descriptions))

    def test_preview_publish_preserves_latest_success_and_writes_preview_file(self):
        article = fixture_article(
            "A001",
            title="[TEST DATA] successful article",
            url="https://example.test/successful-article",
            content_status="summary_only",
        )
        snapshot_day_1 = fixture_snapshot("2026-11-22", [article])
        snapshot_day_2 = fixture_snapshot("2026-11-23", [article])
        digest = daily.validate_digest(fixture_digest(["A001"]), [article])
        preview_state = "尚未生成 AI 摘要：TEST DATA preview"

        with tempfile.TemporaryDirectory(prefix="rss-daily-test-") as temp_dir:
            output_dir = Path(temp_dir)
            with redirect_stdout(io.StringIO()):
                daily.publish(output_dir, snapshot_day_1, digest, "TEST DATA valid issue")
            latest_md_before = (output_dir / "latest.md").read_text(encoding="utf-8")
            latest_html_before = (output_dir / "latest.html").read_text(encoding="utf-8")
            feed_before = (output_dir / "feed.xml").read_text(encoding="utf-8")
            feed_before_guids = [
                item.findtext("guid")
                for item in ET.parse(output_dir / "feed.xml").getroot().findall("./channel/item")
            ]

            with redirect_stdout(io.StringIO()):
                daily.publish(output_dir, snapshot_day_1, None, preview_state)
                daily.publish(output_dir, snapshot_day_2, None, preview_state)

            self.assertEqual((output_dir / "latest.md").read_text(encoding="utf-8"), latest_md_before)
            self.assertEqual((output_dir / "latest.html").read_text(encoding="utf-8"), latest_html_before)
            self.assertEqual((output_dir / "feed.xml").read_text(encoding="utf-8"), feed_before)
            feed_after_guids = [
                item.findtext("guid")
                for item in ET.parse(output_dir / "feed.xml").getroot().findall("./channel/item")
            ]
            self.assertEqual(feed_after_guids, feed_before_guids)
            self.assertEqual(len(feed_after_guids), 1)
            preview_html = output_dir / "preview.html"
            self.assertTrue(preview_html.is_file())
            self.assertIn("尚未生成", preview_html.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
