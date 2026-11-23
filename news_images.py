"""Extract image candidates from RSS entries and already-fetched article HTML."""
from __future__ import annotations

import re
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from lxml import etree, html


EMPTY_URLS = {"none", "null", "undefined", "about:blank", "placeholder"}
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
IMAGE_PATH = re.compile(r"\.(?:apng|avif|bmp|gif|heic|heif|ico|jpe?g|jxl|png|svg|tiff?|webp)$", re.I)
SRCSET_URL = re.compile(r"[^\s,][^\s]*")
LITERAL_PERCENT = re.compile(r"%(?![a-fA-F0-9]{2})")
ARTICLE_SCOPES = (
    "//article | //*[contains(concat(' ', normalize-space(@class), ' '), ' article-content ')"
    " or contains(concat(' ', normalize-space(@class), ' '), ' post-content ')]"
)
OG_FIELDS = {"og:image:alt": "alt", "og:image:width": "width", "og:image:height": "height"}


def normalize_image_url(value: str, source_page: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.startswith("#") or value.lower() in EMPTY_URLS:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        return None
    try:
        reference = urlsplit(value)
        if (reference.scheme or value.startswith("//")) and not reference.netloc:
            return None
        parsed = urlsplit(urljoin(source_page, value))
        host = parsed.hostname
        if parsed.scheme not in ("http", "https") or not host or parsed.username is not None:
            return None
        if parsed.netloc.startswith("["):
            suffix = parsed.netloc.split("]", 1)[1]
            if suffix and not suffix.startswith(":"):
                return None
            netloc = f"[{host}]"
        else:
            host = host.encode("idna").decode("ascii").lower().removesuffix(".")
            if (host in EMPTY_URLS or host == "invalid" or host.endswith(".invalid")
                    or len(host) > 253 or not all(HOST_LABEL.fullmatch(part) for part in host.split("."))):
                return None
            netloc = host
        if parsed.port not in (None, 443 if parsed.scheme == "https" else 80):
            netloc += f":{parsed.port}"
        path = quote(LITERAL_PERCENT.sub("%25", parsed.path or "/"), safe="/%:@!$&'()*+,;=")
        query = quote(LITERAL_PERCENT.sub("%25", parsed.query), safe="/%?:@!$&'()*+,;=")
        return urlunsplit((parsed.scheme, netloc, path, query, ""))
    except (ValueError, UnicodeError):
        return None


def dedupe_images(candidates: list[dict]) -> list[dict]:
    """Keep first provenance for each URL and fill gaps from repeated observations."""
    by_url = {}
    for candidate in candidates:
        url = normalize_image_url(candidate.get("url"), candidate.get("source_page", ""))
        if not url:
            continue
        if url not in by_url:
            by_url[url] = {**candidate, "url": url}
            continue
        first = by_url[url]
        for field in ("alt", "caption", "width", "height"):
            if not first.get(field) and candidate.get(field):
                first[field] = candidate[field]
    return list(by_url.values())


def _text(value) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _dimension(value) -> int | None:
    try:
        value = int(str(value))
    except ValueError:
        return None
    return value if value > 0 else None


def _candidate(value, source_page, origin, *, alt="", caption="", width=None, height=None):
    url = normalize_image_url(value, source_page)
    width = _dimension(width)
    height = _dimension(height)
    if not url or (width is not None and height is not None and width <= 2 and height <= 2):
        return None
    candidate = {"url": url, "source_page": source_page, "alt": _text(alt),
                 "caption": _text(caption), "origin": origin}
    if width:
        candidate["width"] = width
    if height:
        candidate["height"] = height
    return candidate


def _document(markup):
    if not markup:
        return None
    try:
        return html.document_fromstring(markup, parser=html.HTMLParser(no_network=True))
    except (etree.ParserError, ValueError):
        return None


def _srcset_urls(value):
    """Read URL tokens before descriptors, leaving interior commas in URLs intact."""
    index = 0
    while index < len(value):
        while index < len(value) and (value[index].isspace() or value[index] == ","):
            index += 1
        token = SRCSET_URL.match(value, index)
        if token is None:
            return
        url = token.group()
        index = token.end()
        if url.endswith(","):
            yield 1.0, url.rstrip(",")
            continue
        comma = value.find(",", index)
        end = len(value) if comma == -1 else comma
        descriptor = value[index:end].strip()
        density_or_width = re.fullmatch(r"(\d+(?:\.\d+)?)[wx]", descriptor)
        yield float(density_or_width[1]) if density_or_width else 1.0, url
        index = end + 1


def _image_url(image, source_page):
    for attribute in ("data-srcset", "data-lazy-srcset", "srcset"):
        sizes = [(size, url) for size, value in _srcset_urls(image.get(attribute, ""))
                 if (url := normalize_image_url(value, source_page))]
        if sizes:
            return max(sizes, key=lambda variant: variant[0])[1]
    for attribute in ("data-src", "data-lazy-src", "data-original", "src"):
        if url := normalize_image_url(image.get(attribute), source_page):
            return url
    return None


def _embedded_images(scopes, source_page, origin, *, body_fallback=False):
    images = dict.fromkeys(image for scope in scopes for image in scope.iter("img"))
    results = []
    for image in images:
        parents = list(image.iterancestors())
        if any(node.get("role", "").lower() == "advertisement" or "data-ad-slot" in node.attrib
               for node in [image, *parents]):
            continue
        if any(node.tag == "nav" or node.get("role", "").lower() == "navigation" for node in parents):
            continue
        if body_fallback and any(node.tag in ("header", "footer", "aside")
                                 or node.get("role", "").lower() in ("banner", "contentinfo", "complementary")
                                 for node in parents):
            continue
        figure = next((node for node in parents if node.tag == "figure"), None)
        captions = figure.xpath("./figcaption") if figure is not None else []
        caption = captions[0].text_content() if captions else image.get("data-caption")
        candidate = _candidate(_image_url(image, source_page), source_page, origin, alt=image.get("alt"),
                               caption=caption, width=image.get("width"), height=image.get("height"))
        if candidate:
            results.append(candidate)
    return results


def _og_images(document, source_page, *, head_only=False):
    groups = []
    urls, details = [], {}
    for meta in document.xpath("//head/meta[@content]" if head_only else "//meta[@content]"):
        name = (meta.get("property") or meta.get("name") or "").strip().lower()
        value = meta.get("content")
        if name in ("og:image", "og:image:url"):
            if urls:
                groups.append((urls, details))
                urls, details = [], {}
            urls.append(value)
        elif name == "og:image:secure_url":
            urls.append(value)
        elif name in OG_FIELDS:
            details[OG_FIELDS[name]] = value
    groups.append((urls, details))
    return [candidate for urls, details in groups for url in urls
            if (candidate := _candidate(url, source_page, "og_image", **details))]


def html_images(markup: str | bytes, source_page: str, *, include_article: bool = True) -> list[dict]:
    """Collect sharing metadata and, where accessible, embedded article images."""
    document = _document(markup)
    if document is None:
        return []
    candidates = _og_images(document, source_page, head_only=not include_article)
    if include_article:
        scopes = document.xpath(ARTICLE_SCOPES) or document.xpath("//main | //*[@role='main']")
        candidates.extend(_embedded_images(scopes or document.xpath("//body"), source_page, "article_img",
                                           body_fallback=not scopes))
    return dedupe_images(candidates)


def rss_images(entry: dict, source_page: str) -> list[dict]:
    candidates = []
    for field, origin in (("media_content", "rss_media_content"), ("media_thumbnail", "rss_media_thumbnail"),
                          ("enclosures", "rss_enclosure")):
        for media in entry.get(field, []):
            url = normalize_image_url(media.get("url") or media.get("href"), source_page)
            if not url:
                continue
            is_image = (media.get("medium", "").lower() == "image"
                        or media.get("type", "").lower().startswith("image/")
                        or IMAGE_PATH.search(urlsplit(url).path))
            if field != "media_thumbnail" and not is_image:
                continue
            candidate = _candidate(url, source_page, origin, alt=media.get("title"),
                                   caption=media.get("description"), width=media.get("width"),
                                   height=media.get("height"))
            if candidate:
                candidates.append(candidate)
    fragments = [item.get("value") for item in entry.get("content", [])]
    fragments.append(entry.get("summary"))
    for fragment in fragments:
        document = _document(fragment)
        if document is not None:
            candidates.extend(_embedded_images([document], source_page, "rss_html"))
    return dedupe_images(candidates)
