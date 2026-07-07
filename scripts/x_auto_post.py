#!/usr/bin/env python3
"""Publish one rotating restaurant article to X using OAuth 1.0a."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

SITE_URL = "https://achanbay.com"
SITEMAP_URL = f"{SITE_URL}/sitemap.xml"
X_POST_URL = "https://api.x.com/2/tweets"
JST = timezone(timedelta(hours=9))
EXCLUDED_PAGES = {
    "about.html",
    "area-chiba.html",
    "area-koiwa.html",
    "area-tama.html",
    "area-tokyo.html",
    "genre-cafe.html",
    "genre-chinese.html",
    "genre-izakaya.html",
    "genre-other.html",
    "genre-ramen.html",
    "genre-sushi.html",
    "index.html",
    "privacy.html",
    "shops.html",
}


class PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "meta":
            key = values.get("property") or values.get("name")
            value = values.get("content")
            if key and value:
                self.meta[key.lower()] = value

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Achanbay-X-AutoPost/1.0 (+https://achanbay.com/)"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def article_urls() -> list[str]:
    xml_text = fetch_text(SITEMAP_URL)
    root = ET.fromstring(xml_text)
    urls: list[str] = []

    for loc in root.findall(".//{*}loc"):
        if not loc.text:
            continue
        url = loc.text.strip()
        path = urllib.parse.urlparse(url).path
        filename = path.rsplit("/", 1)[-1]
        if filename.endswith(".html") and filename not in EXCLUDED_PAGES:
            urls.append(url)

    if not urls:
        raise RuntimeError("No restaurant article URLs were found in sitemap.xml.")

    return sorted(set(urls))


def get_page_metadata(url: str) -> tuple[str, str]:
    parser = PageMetadataParser()
    parser.feed(fetch_text(url))

    title = parser.meta.get("og:title") or parser.title
    description = parser.meta.get("og:description") or parser.meta.get("description", "")
    title = html.unescape(re.split(r"\s*[｜|]\s*", title, maxsplit=1)[0]).strip()
    description = re.sub(r"\s+", " ", html.unescape(description)).strip()
    return title, description


def choose_slot(requested: str, now: datetime) -> tuple[str, int]:
    if requested != "auto":
        return requested, {"morning": 0, "afternoon": 1, "evening": 2}[requested]
    if now.hour < 11:
        return "morning", 0
    if now.hour < 18:
        return "afternoon", 1
    return "evening", 2


def first_sentence(text: str, limit: int = 42) -> str:
    sentence = re.split(r"[。！？]", text, maxsplit=1)[0].strip()
    if len(sentence) > limit:
        sentence = sentence[: limit - 1].rstrip() + "…"
    return sentence


def location_tag(title: str, description: str) -> str:
    source = title + description
    for location in (
        "東久留米",
        "錦糸町",
        "吉祥寺",
        "秋葉原",
        "神田",
        "人形町",
        "新小岩",
        "立川",
        "福生",
        "小岩",
        "東京",
        "千葉",
    ):
        if location in source:
            return location
    return "東京"


def genre_tag(description: str) -> str:
    checks = (
        ("ラーメン", "ラーメン"),
        ("中華", "中華"),
        ("カフェ", "カフェ"),
        ("チーズケーキ", "スイーツ"),
        ("とんかつ", "とんかつ"),
        ("メキシコ", "メキシコ料理"),
        ("イタリアン", "イタリアン"),
        ("寿司", "寿司"),
        ("焼肉", "焼肉"),
        ("居酒屋", "居酒屋"),
    )
    for keyword, tag in checks:
        if keyword in description:
            return tag
    return "東京グルメ"


def build_post(slot: str, url: str, title: str, description: str, now: datetime) -> str:
    labels = {
        "morning": ("朝の実食グルメ", "今日のお店選びに"),
        "afternoon": ("午後のお店探し", "次のランチ候補に"),
        "evening": ("夜のグルメ案内", "次の外食候補に"),
    }
    heading, prompt = labels[slot]
    hook = first_sentence(description)
    location = location_tag(title, description)
    genre = genre_tag(description)
    separator = "&" if "?" in url else "?"
    tracked_url = (
        f"{url}{separator}utm_source=x&utm_medium=social"
        f"&utm_campaign=auto_post_{slot}"
    )

    lines = [
        f"{now.month}/{now.day} {heading}",
        f"「{title}」",
        f"{prompt}。{hook}" if hook else f"{prompt}。",
        "訪問メモはこちら",
        tracked_url,
        f"#{location}グルメ #{genre}",
    ]
    return "\n".join(lines)


def percent_encode(value: str) -> str:
    return urllib.parse.quote(str(value), safe="~-._")


def oauth_header(
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    access_token: str,
    access_token_secret: str,
) -> str:
    params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    normalized = "&".join(
        f"{percent_encode(key)}={percent_encode(value)}"
        for key, value in sorted(params.items())
    )
    signature_base = "&".join(
        (method.upper(), percent_encode(url), percent_encode(normalized))
    )
    signing_key = (
        f"{percent_encode(consumer_secret)}&{percent_encode(access_token_secret)}"
    )
    signature = base64.b64encode(
        hmac.new(
            signing_key.encode("utf-8"),
            signature_base.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")
    params["oauth_signature"] = signature

    return "OAuth " + ", ".join(
        f'{percent_encode(key)}="{percent_encode(value)}"'
        for key, value in sorted(params.items())
    )


def publish(text: str) -> dict:
    names = (
        "X_API_KEY",
        "X_API_KEY_SECRET",
        "X_ACCESS_TOKEN",
        "X_ACCESS_TOKEN_SECRET",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise RuntimeError(f"Missing GitHub Actions secrets: {', '.join(missing)}")

    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    authorization = oauth_header(
        "POST",
        X_POST_URL,
        values["X_API_KEY"],
        values["X_API_KEY_SECRET"],
        values["X_ACCESS_TOKEN"],
        values["X_ACCESS_TOKEN_SECRET"],
    )
    request = urllib.request.Request(
        X_POST_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json",
            "User-Agent": "Achanbay-X-AutoPost/1.0",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"X API returned HTTP {error.code}: {details}") from error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slot",
        choices=("auto", "morning", "afternoon", "evening"),
        default="auto",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    now = datetime.now(JST)
    slot, slot_index = choose_slot(args.slot, now)
    urls = article_urls()
    selected_index = (now.date().toordinal() * 3 + slot_index) % len(urls)
    selected_url = urls[selected_index]
    title, description = get_page_metadata(selected_url)
    post_text = build_post(slot, selected_url, title, description, now)

    print("--- X post preview ---")
    print(post_text)
    print(f"Characters: {len(post_text)}")

    if args.dry_run:
        print("Dry run completed. Nothing was posted.")
        return 0

    result = publish(post_text)
    post_id = result.get("data", {}).get("id", "unknown")
    print(f"Published successfully: https://x.com/somasaaamon/status/{post_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
