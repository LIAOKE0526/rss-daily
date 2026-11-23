"""Synthetic TEST DATA for the offline RSS/HTML image candidate extractor."""
import unittest
from unittest import mock

import feedparser

from news_images import dedupe_images, html_images, normalize_image_url, rss_images


SOURCE_PAGE = "https://news.example.test/story/launch.html"
RSS_FIXTURE = b"""<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"
    xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><title>TEST DATA feed</title><item>
    <title>TEST DATA story</title><link>https://news.example.test/story/launch.html</link>
    <media:content url="/images/launch.jpg#feed" width="1200" medium="image" />
    <media:content url="/videos/unrelated.mp4" type="video/mp4" />
    <media:thumbnail url="//assets.example.test/resize?id=42" width="320" />
    <enclosure url="/photo?id=43" type="image/webp" />
    <enclosure url="/images/no-mime.png" type="application/octet-stream" />
    <enclosure url="/podcast.mp3" type="audio/mpeg" />
    <description><![CDATA[<figure><img src="../images/launch.jpg" alt="TEST DATA launch">
      <figcaption>TEST DATA demonstration <i>stage</i></figcaption></figure>]]></description>
    <content:encoded><![CDATA[<img src="images/detail.png" alt="TEST DATA detail">]]></content:encoded>
  </item></channel>
</rss>"""

HTML_FIXTURE = """<!doctype html><html><head>
  <meta property="og:image" content="../images/launch.jpg#sharing">
  <meta property="og:image:alt" content="TEST DATA broadcast">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="800">
  <meta property="og:image:secure_url" content="//manufacturer.example.test/product-banner.webp">
</head><body>
  <header><img src="/site-logo.png" alt="TEST DATA site logo"></header>
  <main><aside><img src="/avatar.png" alt="TEST DATA avatar"></aside>
    <article><nav><img src="/inner-logo.png"></nav>
      <div role="advertisement"><figure><img src="/ad.jpg"></figure></div>
      <div data-ad-slot="123"><img src="/another-ad.jpg"></div>
      <img src="/counter.gif" width="1" height="1">
      <figure class="product-banner"><img src="data:image/gif;base64,AAAA"
          data-src="//manufacturer.example.test/product-banner.webp" alt="TEST DATA product">
        <figcaption>TEST DATA manufacturer rendering</figcaption>
      </figure>
      <figure><img src="plots.png" alt="TEST DATA plot">
        <figcaption>TEST DATA plot caption</figcaption>
      </figure>
    </article>
  </main>
</body></html>"""


class ImageCandidateTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(mock.patch("socket.socket.connect", side_effect=AssertionError("network in TEST DATA")))
        self.enterContext(mock.patch("socket.getaddrinfo", side_effect=AssertionError("DNS in TEST DATA")))

    def test_rss_image_sources_metadata_and_relative_urls(self):
        entry = feedparser.parse(RSS_FIXTURE).entries[0]
        by_url = {candidate["url"]: candidate for candidate in rss_images(entry, SOURCE_PAGE)}
        self.assertEqual({url: candidate["origin"] for url, candidate in by_url.items()}, {
            "https://news.example.test/images/launch.jpg": "rss_media_content",
            "https://assets.example.test/resize?id=42": "rss_media_thumbnail",
            "https://news.example.test/photo?id=43": "rss_enclosure",
            "https://news.example.test/images/no-mime.png": "rss_enclosure",
            "https://news.example.test/story/images/detail.png": "rss_html",
        })
        self.assertTrue(all(candidate["source_page"] == SOURCE_PAGE for candidate in by_url.values()))
        launch = by_url["https://news.example.test/images/launch.jpg"]
        self.assertEqual((launch["alt"], launch["caption"], launch["width"]),
                         ("TEST DATA launch", "TEST DATA demonstration stage", 1200))

    def test_html_retains_product_and_article_images_excludes_explicit_ads(self):
        by_url = {candidate["url"]: candidate for candidate in html_images(HTML_FIXTURE.encode(), SOURCE_PAGE)}
        self.assertEqual(set(by_url), {
            "https://news.example.test/images/launch.jpg",
            "https://manufacturer.example.test/product-banner.webp",
            "https://news.example.test/story/plots.png",
        })
        self.assertTrue(all(candidate["source_page"] == SOURCE_PAGE for candidate in by_url.values()))
        launch = by_url["https://news.example.test/images/launch.jpg"]
        self.assertEqual((launch["origin"], launch["alt"], launch["width"], launch["height"]),
                         ("og_image", "TEST DATA broadcast", 1200, 800))
        product = by_url["https://manufacturer.example.test/product-banner.webp"]
        self.assertEqual(product["caption"], "TEST DATA manufacturer rendering")
        plot = by_url["https://news.example.test/story/plots.png"]
        self.assertEqual((plot["origin"], plot["alt"], plot["caption"]),
                         ("article_img", "TEST DATA plot", "TEST DATA plot caption"))

    def test_deduplication_merges_rss_and_og_metadata(self):
        entry = feedparser.parse(RSS_FIXTURE).entries[0]
        rss = rss_images(entry, SOURCE_PAGE)
        web = html_images(HTML_FIXTURE, SOURCE_PAGE)
        launch_url = "https://news.example.test/images/launch.jpg"
        candidates = [candidate for candidate in dedupe_images(rss + web) if candidate["url"] == launch_url]
        self.assertEqual(candidates, [{
            "url": launch_url, "source_page": SOURCE_PAGE, "origin": "rss_media_content",
            "alt": "TEST DATA launch", "caption": "TEST DATA demonstration stage", "width": 1200, "height": 800,
        }])

    def test_invalid_urls_are_skipped_and_unsafe_characters_encoded(self):
        invalid = ("", " ", "#", "placeholder", "data:image/png;base64,AAAA", "javascript:alert(1)",
                   "blob:https://example.test/abc", "https://", "https://placeholder/p.png",
                   "https://image.invalid/a.jpg", "https://user:pass@example.test/a.jpg",
                   "https://example.test:xyz/a.jpg", "https://bad host/a.jpg", "http://[::1]bad/a.jpg")
        for value in invalid:
            with self.subTest(value=value):
                self.assertIsNone(normalize_image_url(value, SOURCE_PAGE))
        self.assertEqual(normalize_image_url("//EXAMPLE.test:443/测试 image.png?id=a b#x", SOURCE_PAGE),
                         "https://example.test/%E6%B5%8B%E8%AF%95%20image.png?id=a%20b")
        self.assertEqual(normalize_image_url("/50%%20view.png", SOURCE_PAGE),
                         "https://news.example.test/50%25%20view.png")
        self.assertEqual(rss_images({"media_thumbnail": [{"url": value} for value in invalid]}, SOURCE_PAGE), [])
        self.assertEqual(html_images('<article><img src="javascript:alert(1)"></article>', SOURCE_PAGE), [])

    def test_body_fallback_uses_responsive_image_without_site_header(self):
        candidates = html_images("""<header><img src="/logo.png"></header>
            <figure><img src="" srcset="data:image/png;base64,AAAA 10w, small.png 400w, large.png 800w"
                alt="TEST DATA illustration"><figcaption>TEST DATA caption</figcaption></figure>""", SOURCE_PAGE)
        self.assertEqual(candidates, [{
            "url": "https://news.example.test/story/large.png", "source_page": SOURCE_PAGE,
            "alt": "TEST DATA illustration", "caption": "TEST DATA caption", "origin": "article_img",
        }])

    def test_ads_only_or_no_images_are_empty(self):
        markup = """<main><div data-ad-slot="1"><img src="/ad.webp"></div>
            <div role="advertisement"><img src="/partner.jpg"></div><img width="1" height="1" src="/pixel.gif">
            <p>TEST DATA plain article</p></main>"""
        self.assertEqual(html_images(markup, SOURCE_PAGE), [])
        self.assertEqual(html_images("<article>TEST DATA article without images</article>", SOURCE_PAGE), [])
        self.assertEqual(rss_images({}, SOURCE_PAGE), [])

    def test_sharing_only_does_not_merge_restricted_body_caption(self):
        markup = """<meta property="og:image" content="/launch.jpg">
            <article><figure><img src="/launch.jpg" alt="TEST DATA restricted alt">
            <figcaption>TEST DATA restricted caption</figcaption></figure>
            <meta property="og:image" content="/restricted.jpg"><meta property="og:image:alt" content="restricted metadata"></article>"""
        self.assertEqual(html_images(markup, SOURCE_PAGE, include_article=False), [{
            "url": "https://news.example.test/launch.jpg", "source_page": SOURCE_PAGE,
            "alt": "", "caption": "", "origin": "og_image",
        }])
