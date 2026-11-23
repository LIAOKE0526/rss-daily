"""Offline TEST DATA for event documents and chat delivery; no Reader writes."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from lxml import html

import daily
from tests.test_workflow import fixture_article, fixture_digest, fixture_snapshot, fixture_api_config


def event(article_id, index=1, *, verified=True):
    topic = fixture_digest([article_id])["topics"][0]
    topic.update(title=f"【科研】TEST DATA 事件{index}",
                 reader_summary_url=f"https://read.readwise.io/read/TEST_DATA_{index}")
    if verified:
        topic["reader_summary_verified"] = True
    return topic


class DeliveryTests(unittest.TestCase):
    def publish(self, output, snapshot, digest):
        if digest is not None:
            daily.validate_digest(digest, snapshot["articles"])
        with redirect_stdout(io.StringIO()):
            daily.publish(output, snapshot, digest, "TEST DATA local validation")
        return daily.read_json(output / "delivery.json")

    def test_zero_events_emit_no_chat_or_issue_and_preserve_prior_success(self):
        snapshot = fixture_snapshot(articles=[fixture_article()])
        with tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
            output = Path(temp)
            manifest = self.publish(output, snapshot, {"topics": []})
            self.assertEqual(manifest["status"], "empty")
            self.assertEqual(manifest["documents"], [])
            self.assertEqual((output / "chat.md").read_bytes(), b"")
            self.assertFalse((output / "issues").exists())
            self.assertFalse((output / "feed.xml").exists())

            self.publish(output, snapshot, {"topics": [event("A001")]})
            previous = {p: (output / p).read_bytes() for p in (
                "latest.md", "latest.html", "feed.xml", "issues/2026-11-23/digest.json")}
            self.assertTrue((output / "chat.md").read_text(encoding="utf-8"))
            self.publish(output, snapshot, {"topics": []})
            self.assertEqual((output / "chat.md").read_bytes(), b"")
            for path, content in previous.items():
                self.assertEqual((output / path).read_bytes(), content)
            self.assertEqual(daily.read_json(output / "last-run.json")["preview"], True)

    def test_single_event_keeps_long_extracts_and_only_title_link_in_chat(self):
        articles = [fixture_article("A001"), fixture_article("A002", publisher="TEST DATA second publisher")]
        topic = event("A001")
        sentences = ["测试材料中的长段原句应保留，编辑只删除重复信息并衔接必要的主语。" * 8,
                     "第二段保留关键数字、时间、条件及争议，不添加编辑自己的评论。" * 7]
        topic.update(paragraph=sentences[0] + "[A001]\n\n" + sentences[1] + "[A002]",
                     article_ids=["A001", "A002"], chat_summary="TEST DATA obsolete intro")
        with tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
            output = Path(temp)
            manifest = self.publish(output, fixture_snapshot(articles=articles), {"topics": [topic]})
            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(manifest["ready_count"], 1)
            chat = (output / "chat.md").read_text(encoding="utf-8")
            self.assertEqual(chat, daily.markdown_link(topic["title"], topic["reader_summary_url"]) + "\n")
            self.assertNotIn("obsolete intro", chat)
            for extension in ("md", "html"):
                text = (output / f"reader/001.{extension}").read_text(encoding="utf-8")
                for sentence in sentences:
                    self.assertIn(sentence, text)
                self.assertNotIn("来源状态", text)
                self.assertNotIn("TEST DATA local validation", text)
                self.assertIn("https://example.test/a001", text)
                self.assertIn("https://example.test/a002", text)
            doc = html.fromstring((output / "reader/001.html").read_text(encoding="utf-8"))
            self.assertEqual(doc.xpath("//h1/text()"), [topic["title"]])

    def test_many_events_each_have_an_independent_document_and_local_citations(self):
        articles = [fixture_article(f"A{i:03d}") for i in range(1, 8)]
        topics = [event(a["id"], i) for i, a in enumerate(articles, 1)]
        with tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
            output = Path(temp)
            manifest = self.publish(output, fixture_snapshot(articles=articles), {"topics": topics})
            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(len(manifest["documents"]), 7)
            self.assertEqual(len((output / "chat.md").read_text(encoding="utf-8").splitlines()), 7)
            for i, document in enumerate(manifest["documents"], 1):
                doc = html.fromstring((output / document["html"]).read_text(encoding="utf-8"))
                self.assertEqual(doc.xpath("//h1/text()"), [topics[i - 1]["title"]])
                self.assertEqual(doc.xpath('//a[@class="cite"]/text()'), ["[1]"])
                source_urls = doc.xpath('//ul[@class="sources"]/li/a/@href')
                self.assertEqual(source_urls, [articles[i - 1]["url"]])

    def test_unverified_or_invalid_reader_links_stay_pending_without_source_fallback(self):
        snapshot = fixture_snapshot(articles=[fixture_article()])
        cases = [{}, {"reader_summary_verified": True},
                 {"reader_summary_url": "https://read.readwise.io/read/TEST_DATA_1"},
                 {"reader_summary_url": "https://example.test/source", "reader_summary_verified": True},
                 {"reader_summary_url": "https://read.readwise.io.evil.test/read/TEST_DATA_1", "reader_summary_verified": True}]
        for fields in cases:
            with self.subTest(fields=fields), tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
                output = Path(temp)
                topic = event("A001")
                topic.pop("reader_summary_url"); topic.pop("reader_summary_verified")
                topic.update(fields)
                manifest = self.publish(output, snapshot, {"topics": [topic]})
                self.assertEqual(manifest["status"], "pending")
                self.assertEqual((output / "chat.md").read_bytes(), b"")
                self.assertTrue((output / "reader/001.html").is_file())

    def test_partial_delivery_and_preview_never_reuse_previous_chat_links(self):
        articles = [fixture_article("A001"), fixture_article("A002")]
        topics = [event("A001", 1), event("A002", 2, verified=False)]
        snapshot = fixture_snapshot(articles=articles)
        with tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
            output = Path(temp)
            manifest = self.publish(output, snapshot, {"topics": topics})
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(manifest["ready_count"], 1)
            self.assertEqual((output / "chat.md").read_text(encoding="utf-8"),
                             daily.markdown_link(topics[0]["title"], topics[0]["reader_summary_url"]) + "\n")
            topics[1].update(reader_summary_url=topics[0]["reader_summary_url"] + "/?source=TEST_DATA#top",
                             reader_summary_verified=True)
            manifest = self.publish(output, snapshot, {"topics": topics})
            self.assertEqual(manifest["status"], "pending")
            self.assertTrue(all(d["reason"] == "reader_summary_shared_by_events" for d in manifest["documents"]))
            self.assertEqual((output / "chat.md").read_bytes(), b"")
            manifest = self.publish(output, snapshot, None)
            self.assertEqual(manifest["status"], "preview")
            self.assertEqual(manifest["documents"], [])
            self.assertEqual((output / "chat.md").read_bytes(), b"")

    def test_text_api_cannot_invent_verified_reader_summary_links(self):
        article = fixture_article()
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": json.dumps({"topics": [event("A001")]})}}]}
        with mock.patch.dict(os.environ, {"RSS_DAILY_TEST_API_KEY": "TEST_DATA_KEY"}, clear=True), \
                mock.patch.object(daily, "post_json", return_value=response):
            digest, state = daily.generate_digest(fixture_snapshot(articles=[article]), fixture_api_config())
        self.assertIsNotNone(digest, state)
        self.assertNotIn("reader_summary_url", digest["topics"][0])
        self.assertNotIn("reader_summary_verified", digest["topics"][0])

    def test_cli_preview_wins_over_digest_and_never_outputs_chat_links(self):
        with tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
            root = Path(temp)
            snapshot = fixture_snapshot(articles=[fixture_article()])
            cfg = fixture_api_config()
            cfg["output_dir"] = str(root / "out")
            daily.write_json(root / "config.json", cfg)
            daily.write_json(root / "snapshot.json", snapshot)
            daily.write_json(root / "draft.json", {"topics": [event("A001")]})
            argv = ["daily.py", "--config", str(root / "config.json"), "--snapshot", str(root / "snapshot.json"),
                    "--digest", str(root / "draft.json"), "--preview"]
            with mock.patch("sys.argv", argv), redirect_stdout(io.StringIO()), mock.patch.object(daily, "post_json") as api:
                daily.main()
            api.assert_not_called()
            self.assertEqual((root / "out/chat.md").read_bytes(), b"")
            self.assertEqual(daily.read_json(root / "out/delivery.json")["status"], "preview")

    def test_cli_invalid_new_draft_clears_previous_ready_delivery(self):
        with tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
            root = Path(temp)
            output = root / "out"
            snapshot = fixture_snapshot(articles=[fixture_article()])
            self.publish(output, snapshot, {"topics": [event("A001")]})
            previous = (output / "latest.html").read_bytes()
            daily.write_json(root / "config.json", {"output_dir": str(output)})
            daily.write_json(root / "snapshot.json", snapshot)
            daily.write_json(root / "draft.json", {"topics": [event("A999")]})
            argv = ["daily.py", "--config", str(root / "config.json"), "--snapshot", str(root / "snapshot.json"),
                    "--digest", str(root / "draft.json")]
            with mock.patch("sys.argv", argv), self.assertRaises(ValueError):
                daily.main()
            self.assertEqual((output / "chat.md").read_bytes(), b"")
            self.assertEqual(daily.read_json(output / "delivery.json")["ready_count"], 0)
            self.assertEqual((output / "latest.html").read_bytes(), previous)

    def test_render_failure_cannot_leave_a_ready_chat_list(self):
        with tempfile.TemporaryDirectory(prefix="rss-delivery-test-") as temp:
            output = Path(temp)
            snapshot = fixture_snapshot(articles=[fixture_article()])
            digest = {"topics": [event("A001")]}
            self.publish(output, snapshot, digest)
            with mock.patch.object(daily, "render", side_effect=ValueError("TEST DATA render failure")), self.assertRaises(ValueError):
                daily.publish(output, snapshot, digest, "TEST DATA failure")
            self.assertEqual((output / "chat.md").read_bytes(), b"")
            self.assertEqual(daily.read_json(output / "delivery.json")["ready_count"], 0)


if __name__ == "__main__":
    unittest.main()
