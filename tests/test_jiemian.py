"""TEST DATA: first-party channel cards, no external requests."""
from datetime import datetime
import unittest
from unittest import mock

import daily


ANCHOR = datetime(2026, 11, 23, 0, 6, tzinfo=daily.BJT)


def cards():
    rows = [("one", "今天 00:04"), ("two", "昨天23:00"),
            ("old", "11/20 20:49"), ("missing", "暂无时间"), ("future", "今天 00:07")]
    return ('<html><head><meta charset="utf-8"></head><body><ul>' + ''.join(
        f'<li class="card-list"><div class="card-list__content"><a href="/article/{key}.html">'
        f'<h3 class="card-list__title">TEST DATA {key}</h3></a></div>'
        f'<div class="card-list__summary"><p>TEST DATA public summary</p></div>'
        f'<span class="news-footer__date">{stamp}</span></li>' for key, stamp in rows
    ) + '</ul></body></html>').encode('utf-8')


class JiemianChannelTests(unittest.TestCase):
    def test_display_dates_and_year_boundary(self):
        self.assertEqual(daily.jiemian_list_time("今天 00:04", ANCHOR).isoformat(), "2026-11-23T00:04:00+08:00")
        self.assertEqual(daily.jiemian_list_time("昨天23:00", ANCHOR).isoformat(), "2026-11-22T23:00:00+08:00")
        self.assertEqual(daily.jiemian_list_time("11/21 10:35", ANCHOR).day, 21)
        january = datetime(2027, 1, 1, 1, tzinfo=daily.BJT)
        self.assertEqual(daily.jiemian_list_time("12/31 23:00", january).year, 2026)
        self.assertIsNone(daily.jiemian_list_time("暂无时间", ANCHOR))

    def test_first_party_cards_use_response_date_and_filter_window(self):
        cfg = {"request_timeout_seconds": 18, "window_hours": 48}
        source = {"name": "TEST DATA 商业", "publisher": "界面新闻", "format": "jiemian_html",
                  "url": "https://example.test/lists/2.html", "enabled": True}
        response = (cards(), source["url"], "Sun, 22 Nov 2026 16:05:00 GMT")
        with mock.patch.object(daily, "fetch_bytes", return_value=response) as fetch:
            items, report = daily.fetch_feed(source, ANCHOR, cfg)
        fetch.assert_called_once_with(source["url"], 18, with_date=True)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["total"], 5)
        self.assertEqual(report["in_window"], 2)
        self.assertEqual(report["missing_time"], 1)
        self.assertEqual(report["outside_window"], 2)
        self.assertEqual(items[0]["url"], "https://example.test/article/one.html")
        self.assertEqual(items[0]["published_raw"], "今天 00:04")
        self.assertEqual(items[0]["time_precision"], "minute")
        self.assertEqual(items[0]["time_basis"], "jiemian_channel_display_BJT")
        self.assertEqual(items[0]["content_status"], "summary_only")
        self.assertIn("00:04+08:00", daily.source_line(items[0]))
        self.assertNotIn("00:04:00", daily.source_line(items[0]))
        # Two channels remain one publisher, and identical article links merge.
        other = [dict(a, source="TEST DATA 财经") for a in items]
        merged = daily.deduplicate(items + other)
        self.assertEqual(len(merged), 2)
        self.assertEqual({a["publisher"] for a in merged}, {"界面新闻"})

    def test_non_article_page_is_an_isolated_source_error(self):
        source = {"name": "TEST DATA blocked", "format": "jiemian_html", "url": "https://example.test/list"}
        with mock.patch.object(daily, "fetch_bytes", return_value=(b"<html>Access denied</html>", source["url"], None)):
            items, report = daily.fetch_feed(source, ANCHOR, {"request_timeout_seconds": 18, "window_hours": 48})
        self.assertEqual(items, [])
        self.assertEqual(report["status"], "error")

    def test_stale_relative_label_uses_article_epoch_instead_of_subtraction(self):
        source = {"name": "TEST DATA 科技", "format": "jiemian_html", "url": "https://example.test/list"}
        listing = cards().decode('utf-8').replace('今天 00:04', '6分钟前').encode('utf-8')
        exact = datetime(2026, 11, 23, 0, 1, 15, tzinfo=daily.BJT)
        article = f'<span data-article-publish-time="{int(exact.timestamp())}"></span>'.encode()
        responses = [(listing, source["url"], "Sun, 22 Nov 2026 16:05:00 GMT"),
                     (article, "https://example.test/article/one.html")]
        with mock.patch.object(daily, "fetch_bytes", side_effect=responses) as fetch:
            items, report = daily.fetch_feed(source, ANCHOR, {"request_timeout_seconds": 18, "window_hours": 48})
        self.assertEqual(report["status"], "ok")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(items[0]["time"], exact.isoformat())
        self.assertEqual(items[0]["time_basis"], "jiemian_article_publish_epoch")

    def test_empty_relative_article_does_not_drop_other_channel_items(self):
        source = {"name": "TEST DATA 科技", "format": "jiemian_html", "url": "https://example.test/list"}
        listing = cards().decode('utf-8').replace('今天 00:04', '6分钟前').encode('utf-8')
        responses = [(listing, source["url"], "Sun, 22 Nov 2026 16:05:00 GMT"),
                     (b"", "https://example.test/article/one.html")]
        with mock.patch.object(daily, "fetch_bytes", side_effect=responses):
            items, report = daily.fetch_feed(source, ANCHOR, {"request_timeout_seconds": 18, "window_hours": 48})
        self.assertEqual(report["status"], "ok")
        self.assertEqual(len(items), 1)
        self.assertEqual(report["missing_time"], 2)


if __name__ == "__main__":
    unittest.main()
