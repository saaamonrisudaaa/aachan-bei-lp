#!/usr/bin/env python3
"""Publish a restaurant post to X while enforcing a 90-day cooldown."""

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
from pathlib import Path

SITE_URL = "https://achanbay.com"
SITEMAP_URL = f"{SITE_URL}/sitemap.xml"
X_POST_URL = "https://api.x.com/2/tweets"
X_ME_URL = "https://api.x.com/2/users/me"
HISTORY_PATH = Path("data/x-post-history.json")
COOLDOWN_DAYS = 90
HISTORY_RETENTION_DAYS = 400
TABELLOG_MIN_RATING = 3.5
JST = timezone(timedelta(hours=9))
UTC = timezone.utc

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

TABELLOG_SEARCH_URLS = tuple(
    (
        f"https://tabelog.com/tokyo/{area}/rstLst/"
        "?SrtT=rt&Srt=D&sort_mode=1"
    )
    for area in (
        "A1301",
        "A1302",
        "A1303",
        "A1304",
        "A1305",
        "A1306",
        "A1307",
        "A1308",
        "A1309",
        "A1310",
        "A1311",
        "A1312",
        "A1313",
        "A1314",
        "A1315",
        "A1316",
        "A1317",
        "A1318",
        "A1319",
        "A1320",
        "A1321",
        "A1322",
        "A1323",
        "A1324",
        "A1325",
        "A1326",
    )
)


class PageMetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
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


class TabelogSearchParser(HTMLParser):
    """Extract restaurant metadata from a Tabelog search result page."""

    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict] = []
        self.current: dict | None = None
        self.depth = 0
        self.capture_field: str | None = None
        self.capture_depth = 0

    @staticmethod
    def classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        value = dict(attrs).get("class") or ""
        return set(value.split())

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        classes = self.classes(attrs)
        if self.current is None:
            if tag.lower() in {"li", "div"} and "list-rst" in classes:
                self.current = {
                    "name": [],
                    "rating": [],
                    "areaGenre": [],
                    "url": "",
                }
                self.depth = 1
            return

        self.depth += 1
        values = dict(attrs)
        if tag.lower() == "a" and classes.intersection(
            {"list-rst__rst-name-target", "cpy-rst-name"}
        ):
            self.current["url"] = values.get("href") or self.current["url"]
            self.capture_field = "name"
            self.capture_depth = self.depth
        elif classes.intersection(
            {"list-rst__rating-val", "c-rating__val--strong"}
        ):
            self.capture_field = "rating"
            self.capture_depth = self.depth
        elif classes.intersection(
            {"list-rst__area-genre", "cpy-area-genre"}
        ):
            self.capture_field = "areaGenre"
            self.capture_depth = self.depth

    def handle_data(self, data: str) -> None:
        if self.current is not None and self.capture_field:
            self.current[self.capture_field].append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.current is None:
            return
        if self.capture_field and self.depth == self.capture_depth:
            self.capture_field = None
            self.capture_depth = 0
        self.depth -= 1
        if self.depth == 0:
            self.finish_current()

    def finish_current(self) -> None:
        if self.current is None:
            return
        name = normalize_text("".join(self.current["name"]))
        rating_text = normalize_text("".join(self.current["rating"]))
        area_genre = normalize_text("".join(self.current["areaGenre"]))
        url = urllib.parse.urljoin(
            "https://tabelog.com",
            str(self.current.get("url") or ""),
        )
        rating_match = re.search(r"[0-5](?:\.\d{1,2})?", rating_text)
        parsed = urllib.parse.urlsplit(url)
        if (
            name
            and rating_match
            and parsed.hostname
            and parsed.hostname.endswith("tabelog.com")
            and parsed.path.startswith("/tokyo/")
        ):
            self.results.append(
                {
                    "name": name,
                    "rating": float(rating_match.group(0)),
                    "areaGenre": area_genre,
                    "url": canonical_source_url(url),
                }
            )
        self.current = None
        self.capture_field = None
        self.capture_depth = 0


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def fetch_text(url: str) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    if host.endswith("tabelog.com"):
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
            "Accept-Language": "ja-JP,ja;q=0.9",
        }
    else:
        headers = {
            "User-Agent": "Achanbay-X-AutoPost/2.0 (+https://achanbay.com/)"
        }
    request = urllib.request.Request(url, headers=headers)
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
            urls.append(canonical_source_url(url))
    if not urls:
        raise RuntimeError("No restaurant article URLs were found in sitemap.xml.")
    return sorted(set(urls))


def get_page_metadata(url: str) -> tuple[str, str]:
    parser = PageMetadataParser()
    parser.feed(fetch_text(url))
    title = parser.meta.get("og:title") or parser.title
    description = parser.meta.get("og:description") or parser.meta.get(
        "description", ""
    )
    title = html.unescape(
        re.split(r"\s*[｜|]\s*", title, maxsplit=1)[0]
    ).strip()
    description = normalize_text(description)
    return title, description


def choose_slot(requested: str, now: datetime) -> tuple[str, int]:
    if requested != "auto":
        return requested, {
            "morning": 0,
            "afternoon": 1,
            "evening": 2,
        }[requested]
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
        ("フレンチ", "フレンチ"),
        ("寿司", "寿司"),
        ("焼肉", "焼肉"),
        ("居酒屋", "居酒屋"),
    )
    for keyword, tag in checks:
        if keyword in description:
            return tag
    return "東京グルメ"


def build_site_post(
    slot: str,
    url: str,
    title: str,
    description: str,
    now: datetime,
) -> str:
    labels = {
        "morning": ("朝の実食グルメ", "今日のお店選びに"),
        "afternoon": ("午後のお店探し", "次のランチ候補に"),
        "evening": ("夜のグルメ案内", "次の外食候補に"),
    }
    heading, prompt = labels[slot]
    hook = first_sentence(description)
    location = location_tag(title, description)
    genre = genre_tag(description)
    tracked_url = (
        f"{url}?utm_source=x&utm_medium=social"
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


def split_area_genre(value: str) -> tuple[str, str]:
    parts = [
        normalize_text(part)
        for part in value.split("/")
        if part.strip()
    ]
    area = parts[0] if parts else "東京"
    genre = parts[1].split("、", 1)[0] if len(parts) > 1 else "グルメ"
    return area[:28], genre[:18]


def build_tabelog_post(slot: str, candidate: dict, now: datetime) -> str:
    labels = {
        "morning": "朝の気になる東京グルメ",
        "afternoon": "次のランチ候補",
        "evening": "夜の気になる東京グルメ",
    }
    area, genre = split_area_genre(candidate.get("areaGenre", ""))
    name = str(candidate["name"])[:38]
    rating = float(candidate["rating"])
    tag = genre_tag(genre)
    hashtags = "#東京グルメ" if tag == "東京グルメ" else f"#東京グルメ #{tag}"
    return "\n".join(
        [
            f"{now.month}/{now.day} {labels[slot]}",
            f"「{name}」",
            f"{area} / {genre}",
            f"食べログ評価 {rating:.2f}（取得時点）",
            "未訪問の候補店です。最新の営業情報・評価は店舗ページで確認してください。",
            str(candidate["url"]),
            hashtags,
        ]
    )


def canonical_source_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if host.endswith(".tabelog.com"):
        host = "tabelog.com"
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit(("https", host, path, "", ""))


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def default_history() -> dict:
    return {
        "version": 1,
        "cooldownDays": COOLDOWN_DAYS,
        "siteHistoryInitialized": False,
        "xUserId": None,
        "updatedAt": None,
        "posts": [],
    }


def load_history(path: Path = HISTORY_PATH) -> dict:
    if not path.exists():
        return default_history()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_history()
    history = default_history()
    if isinstance(payload, dict):
        history.update(payload)
    if not isinstance(history.get("posts"), list):
        history["posts"] = []
    return history


def recent_source_urls(history: dict, now: datetime) -> set[str]:
    cutoff = now.astimezone(UTC) - timedelta(days=COOLDOWN_DAYS)
    recent: set[str] = set()
    for record in history.get("posts", []):
        posted_at = parse_timestamp(record.get("postedAt"))
        source_url = canonical_source_url(
            str(record.get("sourceUrl") or "")
        )
        if posted_at and posted_at >= cutoff and source_url:
            recent.add(source_url)
    return recent


def merge_history_records(
    history: dict,
    records: list[dict],
    now: datetime,
) -> None:
    cutoff = now.astimezone(UTC) - timedelta(
        days=HISTORY_RETENTION_DAYS
    )
    combined = list(history.get("posts", [])) + records
    unique: dict[tuple[str, str], dict] = {}
    for record in combined:
        posted_at = parse_timestamp(record.get("postedAt"))
        source_url = canonical_source_url(
            str(record.get("sourceUrl") or "")
        )
        if not posted_at or posted_at < cutoff or not source_url:
            continue
        normalized = dict(record)
        normalized["sourceUrl"] = source_url
        normalized["postedAt"] = posted_at.isoformat()
        key = (str(normalized.get("postId") or ""), source_url)
        if not key[0]:
            key = (normalized["postedAt"], source_url)
        unique[key] = normalized
    history["posts"] = sorted(
        unique.values(),
        key=lambda item: str(item.get("postedAt") or ""),
        reverse=True,
    )[:1000]


def save_history(
    history: dict,
    path: Path = HISTORY_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def percent_encode(value: str) -> str:
    return urllib.parse.quote(str(value), safe="~-._")


def x_credentials() -> dict[str, str]:
    names = (
        "X_API_KEY",
        "X_API_KEY_SECRET",
        "X_ACCESS_TOKEN",
        "X_ACCESS_TOKEN_SECRET",
    )
    values = {name: os.environ.get(name, "") for name in names}
    missing = [
        name for name, value in values.items() if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing GitHub Actions secrets: {', '.join(missing)}"
        )
    return values


def oauth_header(
    method: str,
    url: str,
    credentials: dict[str, str],
    query_params: dict[str, str] | None = None,
) -> str:
    oauth_params = {
        "oauth_consumer_key": credentials["X_API_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": credentials["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    signature_params = list(oauth_params.items())
    signature_params.extend((query_params or {}).items())
    encoded = sorted(
        (percent_encode(key), percent_encode(value))
        for key, value in signature_params
    )
    normalized = "&".join(
        f"{key}={value}" for key, value in encoded
    )
    signature_base = "&".join(
        (
            method.upper(),
            percent_encode(url),
            percent_encode(normalized),
        )
    )
    signing_key = (
        f"{percent_encode(credentials['X_API_KEY_SECRET'])}&"
        f"{percent_encode(credentials['X_ACCESS_TOKEN_SECRET'])}"
    )
    signature = base64.b64encode(
        hmac.new(
            signing_key.encode("utf-8"),
            signature_base.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")
    oauth_params["oauth_signature"] = signature
    return "OAuth " + ", ".join(
        f'{percent_encode(key)}="{percent_encode(value)}"'
        for key, value in sorted(oauth_params.items())
    )


def x_request(
    method: str,
    url: str,
    query_params: dict[str, str] | None = None,
    json_payload: dict | None = None,
) -> dict:
    credentials = x_credentials()
    query_params = query_params or {}
    request_url = url
    if query_params:
        request_url += "?" + urllib.parse.urlencode(query_params)
    body = None
    headers = {
        "Authorization": oauth_header(
            method,
            url,
            credentials,
            query_params=query_params,
        ),
        "User-Agent": "Achanbay-X-AutoPost/2.0",
    }
    if json_payload is not None:
        body = json.dumps(
            json_payload,
            ensure_ascii=False,
        ).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        request_url,
        data=body,
        method=method.upper(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(
            request, timeout=30
        ) as response:
            return json.loads(
                response.read().decode("utf-8")
            )
    except urllib.error.HTTPError as error:
        details = error.read().decode(
            "utf-8", errors="replace"
        )
        raise RuntimeError(
            f"X API returned HTTP {error.code}: {details}"
        ) from error


def source_record_from_x_url(
    url: str,
    tweet: dict,
) -> dict | None:
    canonical = canonical_source_url(url)
    host = urllib.parse.urlsplit(canonical).hostname or ""
    if host == "achanbay.com":
        source_type = "site"
    elif host == "tabelog.com":
        source_type = "tabelog"
    else:
        return None
    return {
        "sourceType": source_type,
        "sourceUrl": canonical,
        "title": "",
        "postedAt": tweet.get("created_at"),
        "postId": tweet.get("id"),
    }


def recent_x_history(
    history: dict,
    now: datetime,
) -> tuple[str, list[dict]]:
    user_id = str(history.get("xUserId") or "").strip()
    if not user_id:
        me = x_request("GET", X_ME_URL)
        user_id = str(
            me.get("data", {}).get("id") or ""
        ).strip()
    if not user_id:
        raise RuntimeError(
            "X API did not return the authenticated user ID."
        )

    page_limit = (
        10 if not history.get("siteHistoryInitialized") else 1
    )
    cutoff = now.astimezone(UTC) - timedelta(
        days=COOLDOWN_DAYS
    )
    records: list[dict] = []
    pagination_token = ""

    for _ in range(page_limit):
        params = {
            "max_results": "100",
            "exclude": "retweets,replies",
            "tweet.fields": "created_at,entities",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        payload = x_request(
            "GET",
            f"https://api.x.com/2/users/{user_id}/tweets",
            query_params=params,
        )
        oldest: datetime | None = None
        for tweet in payload.get("data", []):
            created_at = parse_timestamp(
                tweet.get("created_at")
            )
            if not created_at:
                continue
            oldest = (
                created_at
                if oldest is None
                else min(oldest, created_at)
            )
            if created_at < cutoff:
                continue
            for entity in (
                tweet.get("entities", {}).get("urls", [])
            ):
                expanded_url = (
                    entity.get("unwound_url")
                    or entity.get("expanded_url")
                    or ""
                )
                record = source_record_from_x_url(
                    expanded_url, tweet
                )
                if record:
                    records.append(record)

        pagination_token = str(
            payload.get("meta", {}).get("next_token") or ""
        )
        if (
            not pagination_token
            or (oldest and oldest < cutoff)
        ):
            break

    return user_id, records


def select_site_candidate(
    urls: list[str],
    recent_urls: set[str],
    seed: int,
) -> str | None:
    eligible = [
        url
        for url in urls
        if canonical_source_url(url) not in recent_urls
    ]
    if not eligible:
        return None
    return eligible[seed % len(eligible)]


def discover_tabelog_candidate(
    now: datetime,
    slot_index: int,
    recent_urls: set[str],
) -> dict | None:
    start = (
        now.date().toordinal() * 3 + slot_index
    ) % len(TABELLOG_SEARCH_URLS)
    candidates: dict[str, dict] = {}

    for offset in range(
        min(6, len(TABELLOG_SEARCH_URLS))
    ):
        search_url = TABELLOG_SEARCH_URLS[
            (start + offset) % len(TABELLOG_SEARCH_URLS)
        ]
        try:
            parser = TabelogSearchParser()
            parser.feed(fetch_text(search_url))
        except (OSError, urllib.error.URLError) as error:
            print(
                f"Tabelog search skipped: {error}",
                file=sys.stderr,
            )
            continue
        for candidate in parser.results:
            url = canonical_source_url(
                str(candidate["url"])
            )
            if (
                float(candidate["rating"])
                >= TABELLOG_MIN_RATING
                and url not in recent_urls
            ):
                candidates[url] = candidate
        if len(candidates) >= 12:
            break

    if not candidates:
        return None
    ordered = sorted(
        candidates.values(),
        key=lambda item: (
            -float(item["rating"]),
            str(item["name"]),
        ),
    )
    seed = now.date().toordinal() * 3 + slot_index
    return ordered[seed % len(ordered)]


def publish(text: str) -> dict:
    return x_request(
        "POST",
        X_POST_URL,
        json_payload={"text": text},
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slot",
        choices=(
            "auto",
            "morning",
            "afternoon",
            "evening",
        ),
        default="auto",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--force-fallback",
        action="store_true",
    )
    args = parser.parse_args()

    now = datetime.now(JST)
    slot, slot_index = choose_slot(args.slot, now)
    seed = now.date().toordinal() * 3 + slot_index
    history = load_history()

    x_history_synced = False
    try:
        user_id, x_records = recent_x_history(
            history, now
        )
        history["xUserId"] = user_id
        history["siteHistoryInitialized"] = True
        merge_history_records(history, x_records, now)
        x_history_synced = True
        print(
            "Recent X history synchronized: "
            f"{len(x_records)} linked posts"
        )
    except Exception as error:
        print(
            "WARNING: Recent X history could not be read: "
            f"{error}",
            file=sys.stderr,
        )

    recent_urls = recent_source_urls(history, now)
    source_type = ""
    source_url = ""
    title = ""
    post_text = ""

    site_history_ready = bool(
        history.get("siteHistoryInitialized")
    )
    if (
        not args.force_fallback
        and site_history_ready
    ):
        selected_url = select_site_candidate(
            article_urls(),
            recent_urls,
            seed,
        )
        if selected_url:
            source_type = "site"
            source_url = selected_url
            title, description = get_page_metadata(
                selected_url
            )
            post_text = build_site_post(
                slot,
                selected_url,
                title,
                description,
                now,
            )

    if not post_text:
        candidate = discover_tabelog_candidate(
            now,
            slot_index,
            recent_urls,
        )
        if not candidate:
            reason = (
                "recent X history was unavailable"
                if (
                    not site_history_ready
                    and not x_history_synced
                )
                else (
                    "all candidates are inside "
                    "the 90-day cooldown"
                )
            )
            raise RuntimeError(
                "No safe X post candidate was found: "
                f"{reason}."
            )
        source_type = "tabelog"
        source_url = canonical_source_url(
            str(candidate["url"])
        )
        title = str(candidate["name"])
        post_text = build_tabelog_post(
            slot, candidate, now
        )

    print("--- X post preview ---")
    print(post_text)
    print(f"Source: {source_type} {source_url}")
    print(f"Characters: {len(post_text)}")

    if args.dry_run:
        print(
            "Dry run completed. "
            "Nothing was posted or saved."
        )
        return 0

    result = publish(post_text)
    post_id = str(
        result.get("data", {}).get("id") or "unknown"
    )
    new_record = {
        "sourceType": source_type,
        "sourceUrl": source_url,
        "title": title,
        "postedAt": now.astimezone(UTC).isoformat(),
        "postId": post_id,
    }
    merge_history_records(
        history, [new_record], now
    )
    history["version"] = 1
    history["cooldownDays"] = COOLDOWN_DAYS
    history["updatedAt"] = now.astimezone(
        UTC
    ).isoformat()
    save_history(history)
    print(
        "Published successfully: "
        f"https://x.com/somasaaamon/status/{post_id}"
    )
    print(f"History saved to {HISTORY_PATH}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
