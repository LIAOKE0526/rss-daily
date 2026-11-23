"""Local RSS -> evidence snapshots -> thematic Chinese daily -> Markdown/HTML/RSS.

No service, database, accounts, telemetry, or scheduler. All dates use UTC+08:00.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
import html
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import feedparser
import requests
import trafilatura
from lxml import etree, html as lxml_html

from news_images import dedupe_images, html_images, normalize_image_url, rss_images

ROOT = Path(__file__).resolve().parent
BJT = timezone(timedelta(hours=8))
HEADERS = {"User-Agent": "RSSDaily/1.0 (+local personal digest)",
           "Accept": "application/rss+xml, application/xml, text/xml, application/atom+xml, text/html, */*;q=0.8"}
PAYWALL = re.compile(r'"isAccessibleForFree"\s*:\s*(?:false|"false")|subscribe to (?:read|continue reading)|订阅后(?:可)?阅读|付费后(?:可)?阅读|购买后(?:可)?阅读', re.I)
STATUS = {"summary_only": "仅摘要", "paywall": "付费内容·仅公开摘要", "rss_excerpt": "RSS 正文片段", "public_extract": "公开网页提取片段"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def http_url(url):
    try:
        p = urlsplit(url)
        return p.scheme in ("http", "https") and bool(p.hostname) and not p.username and not p.password
    except ValueError:
        return False


def canonical_url(url):
    p = urlsplit(url.strip())
    params = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
              if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ocid", "cmpid")]
    return requests.utils.requote_uri(urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(sorted(params)), "")))


def plain(value):
    # feedparser sanitizes HTML; drop all markup before it reaches either model or output.
    value = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", str(value or ""), flags=re.I | re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", value))).strip()


def entry_time(entry, source=None):
    # Use last-update time if provided. Never substitute fetch time for missing dates.
    for field in ("updated_parsed", "published_parsed"):
        raw = entry.get(field.replace("_parsed", ""), "")
        for alias, offset in (source or {}).get("date_timezone_aliases", {}).items():
            if re.search(r"\b" + re.escape(alias) + r"$", raw.strip()):
                fixed = re.sub(r"\b" + re.escape(alias) + r"$", offset, raw.strip())
                return parsedate_to_datetime(fixed).astimezone(BJT), field.replace("_parsed", "") + ":" + alias + "=" + offset
        value = entry.get(field)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc).astimezone(BJT), field.replace("_parsed", "")
    return None, "missing"


def fetch_bytes(url, timeout, *, with_date=False):
    if not http_url(url):
        raise ValueError("invalid_url")
    with request_session(url) as session, session.get(url, headers=HEADERS, timeout=(8, timeout), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 6_000_000:
                raise ValueError("response_too_large")
            chunks.append(chunk)
        result = (b"".join(chunks), response.url)
        return (*result, response.headers.get("Date")) if with_date else result


def request_session(url):
    # Keep Windows/user proxy and CA settings, but never load unrelated .netrc credentials.
    session = requests.Session()
    session.trust_env = False
    session.proxies.update(requests.utils.get_environ_proxies(url))
    session.verify = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("CURL_CA_BUNDLE") or True
    return session


def post_json(url, **kwargs):
    with request_session(url) as session:
        # A configured API endpoint must answer directly; do not forward secrets through redirects.
        return session.post(url, allow_redirects=False, **kwargs)


def error_label(exc):
    # Do not log response bodies, request headers, environment values, or exception URLs.
    if isinstance(exc, requests.HTTPError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "网络超时"
    if isinstance(exc, requests.RequestException):
        return "网络连接失败"
    return type(exc).__name__


def jiemian_list_time(raw, anchor):
    """Official channel cards show Beijing dates to the minute, not fetch times."""
    raw = plain(raw)
    match = re.fullmatch(r"(今天|昨天)\s*(\d{1,2}):(\d{2})", raw)
    if match:
        day = anchor - timedelta(days=1 if match[1] == "昨天" else 0)
        return day.replace(hour=int(match[2]), minute=int(match[3]), second=0, microsecond=0)
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})", raw)
    if match:
        month, day, hour, minute = map(int, match.groups())
        year = anchor.year - int((month, day) > (anchor.month, anchor.day))
        return datetime(year, month, day, hour, minute, tzinfo=BJT)
    return None


def parse_jiemian_list(data, source_url, anchor):
    """Read public first-party business/finance cards; no third-party full-text feed."""
    document = lxml_html.fromstring(data)
    cards = document.xpath("//li[contains(concat(' ', normalize-space(@class), ' '), ' card-list ')]")
    if not cards:
        raise ValueError("jiemian_channel_cards_missing")
    entries = []
    for card in cards:
        links = card.xpath(".//div[contains(@class, 'card-list__content')]/a/@href")
        titles = card.xpath(".//h3[contains(@class, 'card-list__title')]//text()")
        if not links or not titles:
            continue
        raw = plain(" ".join(card.xpath(".//span[contains(@class, 'news-footer__date')]/text()")))
        stamp = jiemian_list_time(raw, anchor)
        entries.append({"title": plain(" ".join(titles)), "link": urljoin(source_url, links[0]),
                        "summary": plain(" ".join(card.xpath(".//div[contains(@class, 'card-list__summary')]//p//text()"))),
                        "published": raw, "published_parsed": stamp.utctimetuple() if stamp else None,
                        "time_basis": "jiemian_channel_display_BJT", "time_precision": "minute"})
    return feedparser.FeedParserDict(version="jiemian_html", bozo=False, entries=entries)


def fetch_feed(source, now, cfg):
    report = {"name": source["name"], "url": source.get("url", ""), "enabled": source.get("enabled", True),
              "note": source.get("note", ""), "status": "disabled", "total": 0, "in_window": 0,
              "missing_time": 0, "outside_window": 0, "invalid_link": 0}
    if not report["enabled"]:
        return [], report
    try:
        if source.get("format") == "jiemian_html":
            data, final_url, response_date = fetch_bytes(source["url"], cfg["request_timeout_seconds"], with_date=True)
            anchor = parsedate_to_datetime(response_date).astimezone(BJT) if response_date else datetime.now(BJT)
            parsed = parse_jiemian_list(data, final_url, anchor)
            # Relative labels can be stale; use the article's explicit epoch, never guess.
            for entry in parsed.entries:
                if not entry["published_parsed"] and re.fullmatch(r"\d+\s*(?:分钟|小时)前", entry["published"]):
                    try:
                        article_data, _ = fetch_bytes(entry["link"], cfg["request_timeout_seconds"])
                        epochs = lxml_html.fromstring(article_data).xpath("//span/@data-article-publish-time")
                        exact = datetime.fromtimestamp(int(epochs[0]), BJT)
                        entry.update(published=exact.isoformat(), published_parsed=exact.utctimetuple(),
                                     time_basis="jiemian_article_publish_epoch", time_precision="second")
                    except (requests.RequestException, ValueError, IndexError, etree.ParserError, OverflowError, OSError):
                        pass  # Still missing a reliable time; normal missing_time accounting applies.
        else:
            data, _ = fetch_bytes(source["url"], cfg["request_timeout_seconds"])
            parsed = feedparser.parse(data)
        if not parsed.version or (parsed.bozo and not parsed.entries):
            raise ValueError("not_an_rss_or_atom_feed")
        report["total"] = len(parsed.entries)
        report["status"] = "ok"
        items = []
        for entry in parsed.entries:
            stamp, date_field = entry_time(entry, source)
            if stamp is None:
                report["missing_time"] += 1
                continue
            if not now - timedelta(hours=cfg["window_hours"]) <= stamp <= now:
                report["outside_window"] += 1
                continue
            url = entry.get("link", "")
            if not http_url(url):
                report["invalid_link"] += 1
                continue
            restricted = source.get("access") == "summary_only"
            only_summary = restricted or source.get("feed_content") == "summary_only"
            summary_raw = (entry.get("summary_detail") or {}).get("value", "") if only_summary else entry.get("summary", "")
            summary = plain(summary_raw)
            content = "" if only_summary else max((plain(c.get("value", "")) for c in entry.get("content", [])), key=len, default="")
            item = {"title": plain(entry.get("title", "无标题")), "url": canonical_url(url),
                          "source": source["name"], "publisher": source.get("publisher", source["name"]),
                          "time": stamp.isoformat(), "time_basis": entry.get("time_basis", date_field),
                          "time_precision": entry.get("time_precision", "second"),
                          "published_raw": entry.get("published", ""), "updated_raw": entry.get("updated", ""),
                          "summary": summary, "text": summary if only_summary else content or summary,
                          "content_status": "paywall" if restricted else "rss_excerpt" if content else "summary_only",
                          "content_note": ("来源配置仅使用公开 RSS 摘要" + ("；" + source["note"] if source.get("note") else "")) if only_summary else "尚未获取公开网页正文",
                          "fetch_article": not restricted and source.get("fetch_article", True),
                          "fetch_image_page": source.get("fetch_image_page", True),
                          "background": False}
            image_entry = {k: v for k, v in entry.items() if k != "content"} if only_summary else entry
            if only_summary:
                image_entry["summary"] = summary_raw
            images = rss_images(image_entry, item["url"])
            if images:
                item["images"] = images
            items.append(item)
        report["in_window"] = len(items)
        report["latest_time"] = max((entry_time(e, source)[0].isoformat() for e in parsed.entries if entry_time(e, source)[0]), default=None)
        return items, report
    except Exception as exc:
        report.update(status="error", error=error_label(exc))
        return [], report


def deduplicate(items):
    unique = {}
    for item in items:
        key = canonical_url(item["url"])
        if key not in unique:
            item["also_in"] = []
            unique[key] = item
        else:
            old = unique[key]
            old["also_in"].append(item["source"])
            if old.get("images") or item.get("images"):
                old["images"] = dedupe_images(old.get("images", []) + item.get("images", []))
            if len(item["text"]) > len(old["text"]):
                for field in ("text", "summary", "content_status", "content_note"):
                    old[field] = item[field]
    result = sorted(unique.values(), key=lambda a: a["time"], reverse=True)
    for i, item in enumerate(result, 1):
        item["id"] = f"A{i:03d}"
    return result


def fetch_article(item, timeout):
    try:
        data, final_url = fetch_bytes(item["url"], timeout)
        # A normal public GET only: no cookies, login, archive, proxy or alternate paywall path.
        page = data.decode("utf-8", errors="replace")
        add_page_images(item, data, final_url, paywall=bool(PAYWALL.search(page)))
        if PAYWALL.search(page):
            item.update(text=item["summary"], content_status="paywall", content_note="网页标记为付费/受限，仅保留 RSS 摘要")
            return item
        text = trafilatura.extract(data, include_comments=False, include_tables=False, url=final_url) or ""
        if len(text) >= 250 and len(text) > len(item["text"]):
            item.update(text=text, content_status="public_extract", content_note="普通公开 GET 的正文提取；不保证全文")
        elif item["content_status"] == "rss_excerpt":
            item["content_note"] = "使用 RSS 正文片段；网页未提取到更完整内容"
        else:
            item["content_note"] = "网页未提取到足够正文，仅使用 RSS 摘要"
    except Exception as exc:
        if isinstance(exc, requests.HTTPError) and exc.response.status_code in (401, 402):
            item.update(text=item["summary"], content_status="paywall", content_note=f"网页要求登录/付款（{error_label(exc)}），仅使用公开 RSS 摘要")
        else:
            item["content_note"] = f"公开网页获取失败（{error_label(exc)}），使用 RSS 已提供内容"
    return item


def add_page_images(item, page, source_page, *, paywall=False):
    candidates = html_images(page, source_page, include_article=not (paywall or item.get("content_status") == "paywall"))
    images = dedupe_images(item.get("images", []) + candidates)
    if images:
        item["images"] = images


def enrich_image_candidates(item, timeout):
    """Only called for explicitly selected articles; never downloads image binaries."""
    if item.get("fetch_image_page") is False:
        item["image_note"] = "来源配置禁止请求原文页，保留公开 RSS 图片候选"
        return item
    try:
        data, final_url = fetch_bytes(item["url"], timeout)
        paywall = bool(PAYWALL.search(data.decode("utf-8", errors="replace")))
        add_page_images(item, data, final_url, paywall=paywall)
        item["image_note"] = "候选未经看图筛选或图片可访问性验证，交由 dot 查看；未下载图片"
    except Exception as exc:
        item["image_note"] = f"入选原文图片候选补取失败（{error_label(exc)}），保留已有候选"
    return item


def collect(cfg, now):
    reports, articles = [], []
    with futures.ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
        jobs = [pool.submit(fetch_feed, source, now, cfg) for source in cfg["feeds"]]
        for job in jobs:
            items, report = job.result()
            reports.append(report)
            articles.extend(items)
            print(f"[{report['status']}] {report['name']}: {report['in_window']} 条 / {report['total']}", flush=True)
    before = len(articles)
    articles = deduplicate(articles)
    eligible = [a for a in articles if a["fetch_article"]]
    selected = select_candidates(eligible, cfg["max_article_fetches"])
    selected_ids = {a["id"] for a in selected}
    for item in eligible:
        if item["id"] in selected_ids:
            continue
        item["content_note"] = "本期公开网页提取额度已用完，保留 RSS 内容（可调 max_article_fetches）"
    print(f"链接去重：{before} -> {len(articles)}；尝试提取 {len(selected)} 篇公开网页", flush=True)
    with futures.ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
        list(pool.map(lambda a: fetch_article(a, cfg["request_timeout_seconds"]), selected))
    return {"schema_version": 1, "data_kind": "live_fetch", "generated_at": now.isoformat(),
            "window_start": (now - timedelta(hours=cfg["window_hours"])).isoformat(), "window_hours": cfg["window_hours"],
            "sources": reports, "articles": articles, "duplicates_removed": before - len(articles)}


def select_candidates(articles, limit):
    # Round-robin by publisher prevents a high-volume feed from hiding smaller sources.
    buckets = {}
    for article in articles:
        if not article.get("background"):
            buckets.setdefault(article["publisher"], []).append(article)
    selected = []
    while len(selected) < limit and any(buckets.values()):
        for bucket in buckets.values():
            if bucket and len(selected) < limit:
                selected.append(bucket.pop(0))
    return selected


def validate_digest(value, articles):
    if not isinstance(value, dict) or not isinstance(value.get("topics"), list) or len(value["topics"]) > 6:
        raise ValueError("digest_schema")
    by_id = {a["id"]: a for a in articles}
    used = set()
    for topic in value["topics"]:
        if not all(isinstance(topic.get(k), str) and topic[k].strip() for k in ("title", "paragraph")):
            raise ValueError("empty_topic")
        ids = topic.get("article_ids")
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in by_id for i in ids):
            raise ValueError("unknown_evidence_id")
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate_evidence_id")
        for article_id in ids:
            reference_url(by_id[article_id])
        current = {i for i in ids if not by_id[i].get("background")}
        if not current or current & used:
            raise ValueError("missing_or_reused_current_evidence")
        used.update(current)
        refs = set(re.findall(r"\[([AB]\d+)\]", topic["paragraph"]))
        if refs != set(ids):
            raise ValueError("invalid_inline_citations")
        if re.search(r"https?://", topic["paragraph"]):
            raise ValueError("use_evidence_ids_not_urls")
        selected_images = topic.get("images", [])
        if not isinstance(selected_images, list):
            raise ValueError("topic_images_must_be_list")
        paragraph_count = len(re.split(r"\n\s*\n", topic["paragraph"].strip()))
        for selected in selected_images:
            if not isinstance(selected, dict) or not http_url(selected.get("source_page", "")):
                raise ValueError("image_source_page_required")
            if not normalize_image_url(selected.get("url", ""), selected["source_page"]):
                raise ValueError("invalid_selected_image_url")
            if not isinstance(selected.get("caption"), str) or not selected["caption"].strip():
                raise ValueError("selected_image_caption_required")
            position = selected.get("after_paragraph", 1)
            if type(position) is not int or not 1 <= position <= paragraph_count:
                raise ValueError("invalid_image_paragraph_position")
    return value


def generate_digest(snapshot, cfg, force_preview=False):
    api = cfg["api"]
    key = os.environ.get(api["key_env"], "").strip()
    if force_preview or not key:
        return None, "尚未生成 AI 摘要：手动预览模式" if force_preview else f"尚未生成 AI 摘要：未设置环境变量 {api['key_env']}"
    selected = select_candidates(snapshot["articles"], cfg["llm_max_articles"])
    background = [a for a in snapshot["articles"] if a.get("background")]
    if not selected:
        return None, "尚未生成 AI 摘要：本期没有可用的最近 48 小时条目，未调用 API"
    fields = ("id", "title", "url", "source", "publisher", "time", "content_status", "content_note")
    def packed(a):
        return {**{k: a[k] for k in fields}, "text": a["text"][:cfg["llm_article_chars"]],
                "text_truncated": len(a["text"]) > cfg["llm_article_chars"],
                **{k: a[k] for k in ("reader_url", "reader_title", "reader_content_kind", "source_language") if a.get(k)}}
    payload = {"beijing_time": snapshot["generated_at"], "window_start": snapshot["window_start"],
               "current_articles": [packed(a) for a in selected], "background_articles": [packed(a) for a in background]}
    base_url = os.environ.get(api.get("base_url_env", ""), "").strip() or api["base_url"]
    model = os.environ.get(api.get("model_env", ""), "").strip() or api["model"]
    if not http_url(base_url):
        return None, "尚未生成 AI 摘要：API 地址配置无效"
    base_url = base_url.rstrip("/")
    endpoint = base_url if base_url.endswith("/chat/completions") else base_url + "/chat/completions"
    try:
        prompt_path = ROOT / cfg["prompt_file"]
        response = post_json(endpoint, headers={"Authorization": "Bearer " + key}, timeout=(10, api["timeout_seconds"]),
                                 json={"model": model, "messages": [{"role": "system", "content": prompt_path.read_text(encoding="utf-8")},
                                        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                                       "temperature": 0.3, "max_tokens": api["max_tokens"], "stream": False,
                                       **api.get("request_options", {})})
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"].strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
        decoded = json.loads(content)
        blocked = decoded.get("blocked") if isinstance(decoded, dict) else None
        if isinstance(blocked, dict) and blocked.get("reason") == "copyright":
            by_id = {a["id"]: a for a in selected + background}
            ids = blocked.get("article_ids", [])
            named = [f"{i}（{by_id[i]['source']}）" for i in ids if isinstance(i, str) and i in by_id] if isinstance(ids, list) else []
            reason = str(blocked.get("message", "模型未提供具体原因")).replace(key, "[密钥已隐藏]")[:500]
            state = f"版权引用限制：尚未生成摘编日报。涉及材料：{'、'.join(named) or '模型未指明'}。{reason}；请向用户报告并等待所需材料，未自动改为同义改写。"
            return None, state.replace(key, "[密钥已隐藏]")
        if isinstance(decoded, dict) and isinstance(decoded.get("topics"), list):
            for topic in decoded["topics"]:
                if isinstance(topic, dict):
                    topic.pop("images", None)  # Text API cannot perform the required dot visual review.
        digest = validate_digest(decoded, selected + background)
        digest["provenance"] = {"method": "compatible_api", "model": model, "candidate_count": len(selected)}
        return digest, f"AI 日报已生成；输入 {len(selected)} / {len(snapshot['articles']) - len(background)} 条近期材料（按媒体均衡取样）"
    except Exception as exc:
        return None, f"尚未生成 AI 摘要：API 或输出校验失败（{error_label(exc)}）；抓取结果已保留"


def preview_groups(articles):
    # Explicitly unreviewed proposals, never promoted to a journalistic conclusion.
    rules = [("AI 模型与产业（候选议题，需编辑确认）", r"\b(?:AI|OpenAI|Anthropic|Gemini|ChatGPT|Claude|Nvidia|DeepSeek)\b|人工智能|大模型|英伟达"),
             ("国际政经（候选议题，需编辑确认）", r"\b(?:Trump|tariff|G20|economy|Ukraine|Russia|China|Iran|Israel)\b|特朗普|关税|峰会|经济|乌克兰"),
             ("产品与游戏（候选议题，需编辑确认）", r"\b(?:game|gaming|Xbox|PlayStation|Nintendo|Steam|iPhone|Apple)\b|游戏|主机|任天堂|苹果|手机")]
    used, groups = set(), []
    for title, pattern in rules:
        matched = [a for a in articles if a["id"] not in used and re.search(pattern, a["title"], re.I)][:8]
        if matched:
            used.update(a["id"] for a in matched)
            groups.append({"title": title, "articles": matched})
    remaining = [a for a in articles if a["id"] not in used][:6]
    if remaining:
        groups.append({"title": "其他近期条目（待选题）", "articles": remaining})
    return groups


def source_line(a):
    shown_time = a["time"].replace("T", " ")
    if a.get("time_precision") == "minute":
        shown_time = a["time"][:16].replace("T", " ") + a["time"][-6:]
    kind = {"chinese_report": "中文报道", "translation": "中文译文", "excerpt": "中文摘编",
            "summary_only": "仅摘要", "english_original": "英文原文"}.get(a.get("reader_content_kind"), "") if a.get("reader_url") else "英文原文" if a.get("source_language") == "en" else ""
    return f"{a['source']} · {shown_time} · {STATUS[a['content_status']]}" + (f" · {kind}" if kind else "") + (" · 较早背景" if a.get("background") else "")


def reference_url(article):
    target = article.get("reader_url") or article["url"]
    if not http_url(target):
        raise ValueError("invalid_reference_url")
    return requests.utils.requote_uri(target)


def markdown_link(title, url):
    return f"[{markdown_text(title)}](<{requests.utils.requote_uri(url)}>)"


def markdown_text(text):
    text = html.escape(str(text).replace("\n", " "), quote=False)
    return re.sub(r"([\\`*_{}\[\]()#+|!~])", r"\\\1", text)


def numbered_paragraph(text, by_id, numbers, *, as_html=False):
    parts = []
    for part in re.split(r"(\[[AB]\d+\])", text):
        if re.fullmatch(r"\[[AB]\d+\]", part):
            article_id = part[1:-1]
            url = reference_url(by_id[article_id])
            label = f"[{numbers[article_id]}]"
            parts.append(f'<a class="cite" href="{html.escape(url, quote=True)}">{label}</a>' if as_html else markdown_link(label, url))
        else:
            parts.append(html.escape(part) if as_html else markdown_text(part))
    return "".join(parts)


CSS = """body{margin:0;background:#f2f1ec;color:#202b2a;font:17px/1.85 system-ui,'Microsoft YaHei',sans-serif}main{max-width:940px;margin:auto;padding:42px 28px}h1{font-size:36px;line-height:1.3}h2{font-size:24px;line-height:1.5;margin-top:0}a{color:#166e67;overflow-wrap:anywhere}header{border-bottom:3px solid #166e67;margin-bottom:30px;padding-bottom:24px}.meta,.note,small{font-size:14px;color:#566461}.banner{background:#e4ece4;border-left:4px solid #166e67;padding:12px 18px}.topic{background:#fff;padding:26px 30px;margin:22px 0;border-radius:8px}.sources{font-size:14px;line-height:1.65;padding-left:22px}.sources li{margin:10px 0}.pill{font-size:13px;background:#edf1ec;border-radius:4px;padding:3px 7px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:9px 7px;border-bottom:1px solid #d4dad4;text-align:left}details{margin:20px 0}p{overflow-wrap:anywhere}.cite{font-size:12px;vertical-align:super}footer{margin-top:34px;font-size:13px;color:#566461}@media(max-width:600px){main{padding:24px 16px}h1{font-size:29px}.topic{padding:22px 18px}table{font-size:12px}}"""
CSS += "figure{margin:22px 0}figure img{display:block;max-width:100%;height:auto;margin:auto}figcaption{font-size:14px;color:#566461;margin-top:8px}"


def reader_figure(selected):
    url = normalize_image_url(selected["url"], selected["source_page"])
    caption = selected["caption"]
    credit = str(selected.get("credit") or urlsplit(selected["source_page"]).hostname)
    alt = str(selected.get("alt") or caption)
    markdown = f"![{markdown_text(alt)}](<{url}>)\n\n{markdown_text(caption)} · 来源：{markdown_link(credit, selected['source_page'])}"
    markup = (f'<figure><a href="{html.escape(url, quote=True)}"><img src="{html.escape(url, quote=True)}" '
              f'alt="{html.escape(alt, quote=True)}" loading="lazy" referrerpolicy="no-referrer"></a>'
              f'<figcaption>{html.escape(caption)} · 来源：<a href="{html.escape(selected["source_page"], quote=True)}">'
              f'{html.escape(credit)}</a></figcaption></figure>')
    return markdown, markup


def render(snapshot, digest, state):
    articles = snapshot["articles"]
    by_id = {a["id"]: a for a in articles}
    date = snapshot["generated_at"][:10]
    title = f"主题日报 · {date}" if digest is not None else f"日报抓取预览 · {date}"
    window = f"北京时间 {snapshot['window_start'].replace('T', ' ')} — {snapshot['generated_at'].replace('T', ' ')}"
    origin = {"live_fetch": "真实网络抓取", "reader_material": "真实 Reader 材料与公开报道", "test_fixture": "测试数据，仅用于验收"}.get(snapshot["data_kind"], "已提供材料")
    md = [f"# {title}", "", window, "", f"> {markdown_text(state)}", f"> 数据：{origin}；链接去重后 {len(articles)} 条。", ""]
    chunks = [f'<header><p class="meta">本地 · 中文 · 主题整合</p><h1>{html.escape(title)}</h1><p class="meta">{html.escape(window)}</p><div class="banner">{html.escape(state)}<br><small>{origin} · 去重后 {len(articles)} 条</small></div></header>']
    if digest is not None:
        reference_ids = list(dict.fromkeys(i for topic in digest["topics"] for i in topic["article_ids"]))
        numbers = {article_id: i for i, article_id in enumerate(reference_ids, 1)}
        for topic in digest["topics"]:
            evidence = [by_id[i] for i in topic["article_ids"]]
            publishers = set(a["publisher"] for a in evidence)
            source_heading = "原文（单篇材料）" if len(evidence) == 1 else "原文（同一媒体）" if len(publishers) == 1 else "原文"
            md += [f"## {markdown_text(topic['title'])}", ""]
            chunks.append(f'<section class="topic"><h2>{html.escape(topic["title"])}</h2>')
            for position, paragraph in enumerate(re.split(r"\n\s*\n", topic["paragraph"].strip()), 1):
                md += [numbered_paragraph(paragraph, by_id, numbers), ""]
                chunks.append(f'<p>{numbered_paragraph(paragraph, by_id, numbers, as_html=True)}</p>')
                for selected in topic.get("images", []):
                    if selected.get("after_paragraph", 1) == position:
                        picture_md, picture_html = reader_figure(selected)
                        md += [picture_md, ""]
                        chunks.append(picture_html)
            md += [f"{source_heading}：", ""]
            chunks.append(f'<p class="note">{source_heading}</p><ul class="sources">')
            for a in evidence:
                target = reference_url(a)
                reference_title = a.get("reader_title") or a["title"]
                md.append(f"- {markdown_link('[' + str(numbers[a['id']]) + '] ' + reference_title, target)} — {source_line(a)}")
                chunks.append(f'<li><a href="{html.escape(target, quote=True)}">[{numbers[a["id"]]}] {html.escape(reference_title)}</a><br><small>{html.escape(source_line(a))}</small></li>')
            md.append("")
            chunks.append("</ul></section>")
        if not digest["topics"]:
            md += ["本期没有足够材料形成主题。", ""]
            chunks.append('<p>本期没有足够材料形成主题。</p>')
    else:
        md += ["以下为关键词候选分组，不是同事件合并，也未生成主题摘要。", ""]
        chunks.append('<p class="note">以下为关键词候选分组，不是同事件合并，也未生成主题摘要。</p>')
        for group in preview_groups(articles):
            md += [f"## {group['title']}", ""]
            chunks.append(f'<section class="topic"><h2>{html.escape(group["title"])}</h2><ul class="sources">')
            for a in group["articles"]:
                md += [f"- {markdown_link(a['title'], a['url'])} — {markdown_text(source_line(a))}", f"  - {markdown_text(a['summary'][:260] or '该源未提供摘要')}"]
                chunks.append(f'<li><a href="{html.escape(a["url"], quote=True)}">{html.escape(a["title"])}</a><br><small>{html.escape(source_line(a))}</small><p>{html.escape(a["summary"][:260] or "该源未提供摘要")}</p></li>')
            md.append("")
            chunks.append('</ul></section>')
    md += ["## 来源状态", "", "| 来源 | 状态 | 48h 条目 | 说明 |", "| --- | --- | ---: | --- |"]
    chunks.append('<details><summary>来源状态与材料限制</summary><table><tr><th>来源</th><th>状态</th><th>48h 条目</th><th>说明</th></tr>')
    for r in snapshot["sources"]:
        detail = r.get("error", "") or r.get("note", "")
        if r.get("missing_time"):
            detail += f"；{r['missing_time']} 条缺失时间已跳过"
        label = {"ok": "成功", "error": "失败·已隔离", "disabled": "未启用"}[r["status"]]
        md.append(f"| {markdown_text(r['name'])} | {label} | {r['in_window']} | {markdown_text(detail)} |")
        chunks.append('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in (r["name"], label, r["in_window"], detail)) + '</tr>')
    chunks.append('</table></details><footer>正文状态为提取片段或摘要；未绕过登录、付费墙或访问限制。feed.xml 仅为本地文件，尚未发布或提供手机访问。</footer>')
    md += ["", "完整条目、正文状态、抓取依据见同目录 articles.json。feed.xml 仅为本地文件，尚未提供手机访问。", ""]
    body = "\n".join(chunks)
    full = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + html.escape(title) + '</title><style>' + CSS + '</style></head><body><main>' + body + '</main></body></html>'
    return title, "\n".join(md), full, body


def publish(output, snapshot, digest, state):
    output = Path(output)
    date = snapshot["generated_at"][:10]
    issue_dir = output / "issues" / date
    output.mkdir(parents=True, exist_ok=True)
    title, md, full, body = render(snapshot, digest, state)
    opened = output / ("preview.html" if digest is None else "latest.html")
    write_json(output / "last-run.json", {"html_path": str(opened.resolve()), "preview": digest is None, "state": state})
    if digest is None:
        (output / "preview.md").write_text(md, encoding="utf-8")
        (output / "preview.html").write_text(full, encoding="utf-8")
        write_json(output / "preview.articles.json", snapshot)
        successful = any(read_json(p).get("digest") is not None for p in (output / "issues").glob("*/digest.json"))
        if successful:
            print(f"{state}\n已有成功日报与 feed.xml 已保留；新抓取见 {output.resolve() / 'preview.html'}")
            return output
    issue_dir.mkdir(parents=True, exist_ok=True)
    write_json(issue_dir / "articles.json", snapshot)
    write_json(issue_dir / "digest.json", {"state": state, "digest": digest})
    (issue_dir / "daily.md").write_text(md, encoding="utf-8")
    (issue_dir / "daily.html").write_text(full, encoding="utf-8")
    write_json(issue_dir / "issue.json", {"date": date, "title": title, "generated_at": snapshot["generated_at"], "body": body})
    for name, content in (("latest.md", md), ("latest.html", full)):
        (output / name).write_text(content, encoding="utf-8")
    write_json(output / "latest.articles.json", snapshot)
    # Regenerate from dated artifacts; rerunning one day replaces that issue instead of duplicating it.
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    for tag, text in (("title", "本地中文主题日报"), ("link", output.resolve().as_uri() + "/latest.html"),
                      ("description", "本地生成的主题日报；未发布，file 链接仅在本机有效"), ("language", "zh-cn")):
        ET.SubElement(channel, tag).text = text
    for path in sorted((output / "issues").glob("*/issue.json"), reverse=True)[:60]:
        issue = read_json(path)
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = issue["title"]
        ET.SubElement(item, "link").text = (path.parent / "daily.html").resolve().as_uri()
        ET.SubElement(item, "guid", isPermaLink="false").text = "local-thematic-daily:" + issue["date"]
        ET.SubElement(item, "pubDate").text = format_datetime(datetime.fromisoformat(issue["generated_at"]))
        ET.SubElement(item, "description").text = issue["body"]
    ET.indent(rss)
    ET.ElementTree(rss).write(output / "feed.xml", encoding="utf-8", xml_declaration=True)
    print(f"HTML: {output.resolve() / 'latest.html'}\nMarkdown: {output.resolve() / 'latest.md'}\nRSS: {output.resolve() / 'feed.xml'}\n{state}")
    return issue_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--preview", action="store_true", help="只抓取和预览，不调用 API")
    parser.add_argument("--feed-url", help="本次临时增加 RSS URL，不改订阅配置")
    parser.add_argument("--feed-name", default="手动订阅")
    parser.add_argument("--snapshot", help="复用本地抓取快照，不重新联网抓取")
    parser.add_argument("--digest", help="渲染已有编辑稿 JSON（必须配合 --snapshot），不调用 API")
    parser.add_argument("--output", help="覆盖输出目录，例如单独存放首期样稿")
    parser.add_argument("--image-articles", nargs="+", metavar="ARTICLE_ID", help="仅补取入选原文的图片候选；必须配合 --snapshot，不调用模型或渲染")
    args = parser.parse_args()
    if args.image_articles and (not args.snapshot or args.digest):
        parser.error("--image-articles 必须配合 --snapshot，且不能与 --digest 同时使用")
    cfg = read_json(ROOT / args.config)
    if args.feed_url:
        cfg["feeds"].append({"name": args.feed_name, "url": args.feed_url, "enabled": True})
    if args.snapshot:
        snapshot = read_json(ROOT / args.snapshot)
    else:
        snapshot = collect(cfg, datetime.now(BJT))
    output = Path(args.output or cfg["output_dir"])
    if not output.is_absolute():
        output = ROOT / output
    if args.image_articles:
        if (ROOT / args.snapshot).resolve() == (output / "image.articles.json").resolve():
            parser.error("图片补取不能覆盖输入快照；请用 --output 指定不同目录")
        by_id = {a["id"]: a for a in snapshot["articles"]}
        unknown = set(args.image_articles) - by_id.keys()
        if unknown:
            parser.error("图片补取存在未知文章 ID：" + "、".join(sorted(unknown)))
        selected = [by_id[i] for i in dict.fromkeys(args.image_articles)]
        with futures.ThreadPoolExecutor(max_workers=cfg["workers"]) as pool:
            list(pool.map(lambda item: enrich_image_candidates(item, cfg["request_timeout_seconds"]), selected))
        write_json(output / "image.articles.json", snapshot)
        print(f"已为 {len(selected)} 篇入选文章补取图片候选；快照：{output.resolve() / 'image.articles.json'}；原始快照未改动，未调用模型或下载图片")
        return
    # Persist before any paid API operation; failure still leaves a useful complete preview.
    if not args.snapshot:
        write_json(output / "fetch.articles.json", snapshot)
    if args.digest:
        if not args.snapshot:
            parser.error("--digest 必须配合 --snapshot，避免编辑稿错配到新条目")
        digest = validate_digest(read_json(ROOT / args.digest), snapshot["articles"])
        state = digest.get("editor_note", "已有编辑稿；未调用配置的 API")
    else:
        digest, state = generate_digest(snapshot, cfg, args.preview)
    publish(output, snapshot, digest, state)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
