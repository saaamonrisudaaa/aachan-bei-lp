#!/usr/bin/env python3
"""Prepare, publish, and record restaurant posts for @somasaaamon."""

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

from x_post_shared import (
    assert_not_posted,
    compose_post,
    post_hash,
    record_posted_text,
    validate_post_text,
    visible_body_length,
)

SITE_URL = "https://achanbay.com"
SITEMAP_URL = f"{SITE_URL}/sitemap.xml"
X_POST_URL = "https://api.x.com/2/tweets"
X_ME_URL = "https://api.x.com/2/users/me"
STATE_DIR = Path(os.environ.get("X_STATE_DIR") or "data")
HISTORY_PATH = STATE_DIR / "x-post-history.json"
EXPECTED_X_USERNAME = "somasaaamon"
COOLDOWN_DAYS = 184
HISTORY_RETENTION_DAYS = 400
MAX_HISTORY_RECORDS = 7000
TABELLOG_MIN_RATING = 3.5
JST = timezone(timedelta(hours=9))
UTC = timezone.utc
REGION_PRIORITY = ("tokyo", "saitama", "kanagawa", "chiba")
DAILY_POST_TIMES = (
    "09:05",
    "09:13",
    "09:21",
    "09:47",
    "10:05",
    "11:24",
    "11:45",
    "12:10",
    "13:15",
    "16:12",
    "16:35",
    "17:11",
    "17:31",
    "21:01",
    "21:15",
    "21:30",
)

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

TOKYO_TABELLOG_SEARCH_URLS = tuple(
    f"https://tabelog.com/tokyo/{area}/rstLst/?SrtT=rt&Srt=D&sort_mode=1"
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
TABELLOG_SEARCH_GROUPS = (
    ("東京", TOKYO_TABELLOG_SEARCH_URLS),
    (
        "埼玉",
        ("https://tabelog.com/saitama/rstLst/?SrtT=rt&Srt=D&sort_mode=1",),
    ),
    (
        "神奈川",
        ("https://tabelog.com/kanagawa/rstLst/?SrtT=rt&Srt=D&sort_mode=1",),
    ),
    (
        "千葉",
        ("https://tabelog.com/chiba/rstLst/?SrtT=rt&Srt=D&sort_mode=1",),
    ),
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
            and parsed.path.startswith(
                ("/tokyo/", "/saitama/", "/kanagawa/", "/chiba/")
            )
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
    for page in Path(".").glob("restaurant-*.html"):
        urls.append(canonical_source_url(f"{SITE_URL}/{page.name}"))
    if not urls:
        raise RuntimeError("No restaurant article URLs were found in sitemap.xml.")
    return sorted(set(urls))


def site_region(url: str) -> str | None:
    filename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    page = Path(filename)
    if page.exists():
        content = page.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"https://tabelog\.com/(tokyo|saitama|kanagawa|chiba)/",
            content,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1).lower()
    if filename in {
        "pot-higashikurume.html",
        "yayaya-kinshicho.html",
        "sta-kanda.html",
        "sinensis-kichijoji.html",
    }:
        return "tokyo"
    return None


def get_page_metadata(url: str) -> tuple[str, str]:
    parser = PageMetadataParser()
    filename = urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
    local_page = Path(filename)
    if local_page.exists():
        page_text = local_page.read_text(encoding="utf-8", errors="replace")
    else:
        page_text = fetch_text(url)
    parser.feed(page_text)
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
            "lunch": 1,
            "afternoon": 2,
            "evening": 3,
        }[requested]
    if now.hour < 10:
        return "morning", 0
    if now.hour < 14:
        return "lunch", 1
    if now.hour < 18:
        return "afternoon", 2
    return "evening", 3


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
    hooks = {
        "morning": "朝の店選び、これどう？",
        "lunch": "今日のランチ候補に",
        "afternoon": "次のお店探しに",
        "evening": "今夜の候補、ここどう？",
    }
    detail = first_sentence(description, limit=34)
    body = f"「{title}」{detail}" if detail else f"「{title}」をチェック"
    tracked_url = (
        f"{url}?utm_source=x&utm_medium=social"
        f"&utm_campaign=auto_post_{slot}"
    )
    return compose_post(hooks[slot], body, tracked_url)


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
    prefecture = normalize_text(str(candidate.get("prefecture") or "東京"))
    hooks = {
        "morning": f"朝の気になる{prefecture}グルメ",
        "lunch": "今日のランチ候補に",
        "afternoon": "次のお店探しに",
        "evening": f"夜の気になる{prefecture}グルメ",
    }
    area, genre = split_area_genre(candidate.get("areaGenre", ""))
    name = str(candidate["name"])[:38]
    rating = float(candidate["rating"])
    body = f"「{name}」{area}・{genre} 評価{rating:.2f}（取得時）"
    return compose_post(
        hooks[slot],
        body,
        str(candidate["url"]),
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
        "historyWindowDays": 0,
        "historyBackfilledAt": None,
        "siteHistoryInitialized": False,
        "xUserId": None,
        "updatedAt": None,
        "posts": [],
    }


def load_history(path: Path = HISTORY_PATH) -> dict:
    if not path.exists():
        seed_path = Path("data/x-post-history.json")
        if path != seed_path and seed_path.exists():
            path = seed_path
        else:
            return default_history()
    return read_history_file(path)


def read_history_file(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot safely read X history {path}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"X history {path} must contain a JSON object.")
    history = default_history()
    history.update(payload)
    if not isinstance(history.get("posts"), list):
        raise RuntimeError(f"X history {path} must contain a posts array.")
    return history


def import_history_file(path: Path, now: datetime) -> dict:
    incoming = read_history_file(path)
    if not history_is_trusted(incoming):
        raise RuntimeError("Imported X history is not backfilled for 184 days.")
    history = load_history()
    current_user_id = str(history.get("xUserId") or "").strip()
    incoming_user_id = str(incoming.get("xUserId") or "").strip()
    if current_user_id and incoming_user_id and current_user_id != incoming_user_id:
        raise RuntimeError("Imported X history belongs to a different user ID.")
    merge_history_records(history, list(incoming.get("posts", [])), now)
    history["xUserId"] = incoming_user_id or current_user_id or None
    history["siteHistoryInitialized"] = True
    history["historyWindowDays"] = max(
        int(history.get("historyWindowDays") or 0),
        int(incoming.get("historyWindowDays") or 0),
    )
    history["historyBackfilledAt"] = incoming.get("historyBackfilledAt")
    history["cooldownDays"] = COOLDOWN_DAYS
    history["updatedAt"] = now.astimezone(UTC).isoformat()
    save_history(history)
    return history


def history_is_trusted(history: dict) -> bool:
    return bool(history.get("siteHistoryInitialized")) and int(
        history.get("historyWindowDays") or 0
    ) >= COOLDOWN_DAYS


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


def recent_text_hashes(history: dict, now: datetime) -> set[str]:
    cutoff = now.astimezone(UTC) - timedelta(days=COOLDOWN_DAYS)
    recent: set[str] = set()
    for record in history.get("posts", []):
        posted_at = parse_timestamp(record.get("postedAt"))
        digest = str(record.get("textHash") or "").strip()
        if posted_at and posted_at >= cutoff and digest:
            recent.add(digest)
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
    )[:MAX_HISTORY_RECORDS]


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
    text_hash = ""
    try:
        text_hash = post_hash(str(tweet.get("text") or ""))
    except Exception:
        pass
    return {
        "sourceType": source_type,
        "sourceUrl": canonical,
        "title": "",
        "postedAt": tweet.get("created_at"),
        "postId": tweet.get("id"),
        "textHash": text_hash,
    }


def recent_x_history(
    history: dict,
    now: datetime,
    *,
    force_full: bool = False,
) -> tuple[str, list[dict]]:
    me = x_request("GET", X_ME_URL)
    authenticated = me.get("data", {})
    username = str(authenticated.get("username") or "").strip().lstrip("@")
    if username.casefold() != EXPECTED_X_USERNAME.casefold():
        raise RuntimeError(
            "X API credentials belong to @"
            f"{username or 'unknown'}, not @{EXPECTED_X_USERNAME}."
        )
    user_id = str(authenticated.get("id") or "").strip()
    if not user_id:
        raise RuntimeError(
            "X API did not return the authenticated user ID."
        )

    cached_user_id = str(history.get("xUserId") or "").strip()
    if cached_user_id and cached_user_id != user_id:
        raise RuntimeError(
            "The authenticated X user ID does not match the stored account history."
        )

    page_limit = (
        40
        if force_full or not history.get("siteHistoryInitialized")
        else 1
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
    for region in REGION_PRIORITY:
        eligible = [
            url
            for url in urls
            if site_region(url) == region
            and canonical_source_url(url) not in recent_urls
        ]
        if eligible:
            return eligible[seed % len(eligible)]
    return None


def discover_tabelog_candidate(
    now: datetime,
    slot_index: int,
    recent_urls: set[str],
) -> dict | None:
    seed = now.date().toordinal() * 3 + slot_index
    for prefecture, search_urls in TABELLOG_SEARCH_GROUPS:
        start = seed % len(search_urls)
        candidates: dict[str, dict] = {}
        for offset in range(len(search_urls)):
            search_url = search_urls[(start + offset) % len(search_urls)]
            try:
                parser = TabelogSearchParser()
                parser.feed(fetch_text(search_url))
            except (OSError, urllib.error.URLError) as error:
                print(
                    f"Tabelog search skipped ({prefecture}): {error}",
                    file=sys.stderr,
                )
                continue
            for candidate in parser.results:
                url = canonical_source_url(str(candidate["url"]))
                if (
                    float(candidate["rating"]) >= TABELLOG_MIN_RATING
                    and url not in recent_urls
                ):
                    normalized = dict(candidate)
                    normalized["prefecture"] = prefecture
                    candidates[url] = normalized
            if len(candidates) >= 12:
                break
        if candidates:
            ordered = sorted(
                candidates.values(),
                key=lambda item: (
                    -float(item["rating"]),
                    str(item["name"]),
                ),
            )
            return ordered[seed % len(ordered)]
    return None


def publish(text: str) -> dict:
    return x_request(
        "POST",
        X_POST_URL,
        json_payload={"text": text},
    )


def prepared_payload(
    *,
    text: str,
    source_type: str,
    source_url: str,
    title: str,
    slot: str,
    now: datetime,
) -> dict:
    return validate_prepared_payload(
        {
            "version": 1,
            "username": EXPECTED_X_USERNAME,
            "text": text,
            "sourceType": source_type,
            "sourceUrl": source_url,
            "title": title,
            "slot": slot,
            "preparedAt": now.astimezone(UTC).isoformat(),
        }
    )


def validate_prepared_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeError("Prepared post must be a JSON object.")
    username = str(payload.get("username") or "").strip().lstrip("@")
    if username.casefold() != EXPECTED_X_USERNAME.casefold():
        raise RuntimeError(
            f"Prepared post targets @{username or 'unknown'}, not @{EXPECTED_X_USERNAME}."
        )
    text = validate_post_text(str(payload.get("text") or ""))
    source_type = str(payload.get("sourceType") or "").strip().lower()
    if source_type not in {"site", "tabelog"}:
        raise RuntimeError("Prepared post sourceType must be site or tabelog.")
    source_url = canonical_source_url(str(payload.get("sourceUrl") or ""))
    host = urllib.parse.urlsplit(source_url).hostname or ""
    expected_host = "achanbay.com" if source_type == "site" else "tabelog.com"
    if host != expected_host:
        raise RuntimeError(
            f"Prepared {source_type} post has an invalid source URL: {source_url}"
        )
    posted_source_url = canonical_source_url(text.splitlines()[2])
    if posted_source_url != source_url:
        raise RuntimeError(
            "The prepared post URL does not match its declared sourceUrl."
        )
    slot = str(payload.get("slot") or "").strip()
    if slot not in {"morning", "lunch", "afternoon", "evening"}:
        raise RuntimeError("Prepared post has an invalid slot.")
    prepared_at = str(payload.get("preparedAt") or "").strip()
    if parse_timestamp(prepared_at) is None:
        raise RuntimeError("Prepared post needs a valid preparedAt timestamp.")
    return {
        "version": 1,
        "username": EXPECTED_X_USERNAME,
        "text": text,
        "sourceType": source_type,
        "sourceUrl": source_url,
        "title": normalize_text(str(payload.get("title") or "")),
        "slot": slot,
        "preparedAt": prepared_at,
    }


def load_prepared_file(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read prepared post {path}: {error}") from error
    return validate_prepared_payload(payload)


def save_prepared_file(path: Path, payload: dict) -> None:
    normalized = validate_prepared_payload(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def confirmed_post_id(post_url: str) -> str:
    parsed = urllib.parse.urlsplit(str(post_url).strip())
    if (parsed.hostname or "").lower() not in {"x.com", "www.x.com"}:
        raise RuntimeError("The confirmed post URL must be on x.com.")
    match = re.fullmatch(
        rf"/{re.escape(EXPECTED_X_USERNAME)}/status/(\d+)",
        parsed.path.rstrip("/"),
        flags=re.IGNORECASE,
    )
    if not match:
        raise RuntimeError(
            f"The confirmed post URL must belong to @{EXPECTED_X_USERNAME}."
        )
    return match.group(1)


def record_prepared_post(
    payload: dict,
    *,
    post_url: str,
    origin: str,
    now: datetime,
    history: dict | None = None,
) -> dict:
    normalized = validate_prepared_payload(payload)
    post_id = confirmed_post_id(post_url)
    posted_at = now.astimezone(UTC).isoformat()
    record = record_posted_text(
        normalized["text"],
        post_url=post_url,
        source_url=normalized["sourceUrl"],
        origin=origin,
        posted_at=posted_at,
    )
    history = history or load_history()
    merge_history_records(
        history,
        [
            {
                "sourceType": normalized["sourceType"],
                "sourceUrl": normalized["sourceUrl"],
                "title": normalized["title"],
                "postedAt": posted_at,
                "postId": post_id,
                "textHash": post_hash(normalized["text"]),
            }
        ],
        now,
    )
    history["version"] = 1
    history["cooldownDays"] = COOLDOWN_DAYS
    history["updatedAt"] = posted_at
    save_history(history)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--slot",
        choices=(
            "auto",
            "morning",
            "lunch",
            "afternoon",
            "evening",
        ),
        default="auto",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prepare-file", type=Path)
    parser.add_argument("--publish-file", type=Path)
    parser.add_argument("--record-file", type=Path)
    parser.add_argument("--import-history-file", type=Path)
    parser.add_argument("--sync-history", action="store_true")
    parser.add_argument("--post-url")
    parser.add_argument(
        "--force-fallback",
        action="store_true",
    )
    args = parser.parse_args()

    now = datetime.now(JST)

    file_modes = [
        bool(args.prepare_file),
        bool(args.publish_file),
        bool(args.record_file),
        bool(args.import_history_file),
        bool(args.sync_history),
    ]
    if sum(file_modes) > 1:
        parser.error(
            "Prepared-file, history-import, and history-sync modes are mutually exclusive."
        )
    if args.post_url and not args.record_file:
        parser.error("--post-url can only be used with --record-file.")
    if args.import_history_file:
        imported = import_history_file(args.import_history_file, now)
        print(
            "Imported trusted X history: "
            f"{len(imported.get('posts', []))} linked posts"
        )
        return 0
    if args.sync_history:
        history = load_history()
        user_id, x_records = recent_x_history(history, now, force_full=True)
        history["xUserId"] = user_id
        history["siteHistoryInitialized"] = True
        history["historyWindowDays"] = COOLDOWN_DAYS
        history["historyBackfilledAt"] = now.astimezone(UTC).isoformat()
        merge_history_records(history, x_records, now)
        history["cooldownDays"] = COOLDOWN_DAYS
        history["updatedAt"] = now.astimezone(UTC).isoformat()
        save_history(history)
        print(f"Backfilled {len(x_records)} linked X posts for {COOLDOWN_DAYS} days.")
        return 0
    if args.record_file:
        if not args.post_url:
            parser.error("--record-file requires --post-url.")
        payload = load_prepared_file(args.record_file)
        record_prepared_post(
            payload,
            post_url=args.post_url,
            origin="chrome",
            now=now,
        )
        print(f"Recorded Chrome post: {args.post_url}")
        return 0
    if args.publish_file:
        payload = load_prepared_file(args.publish_file)
        history = load_history()
        user_id, x_records = recent_x_history(
            history,
            now,
            force_full=True,
        )
        history["xUserId"] = user_id
        history["siteHistoryInitialized"] = True
        history["historyWindowDays"] = COOLDOWN_DAYS
        history["historyBackfilledAt"] = now.astimezone(UTC).isoformat()
        merge_history_records(history, x_records, now)
        if payload["sourceUrl"] in recent_source_urls(history, now):
            raise RuntimeError(
                f"Prepared source is inside the {COOLDOWN_DAYS}-day cooldown."
            )
        if post_hash(payload["text"]) in recent_text_hashes(history, now):
            raise RuntimeError(
                f"Prepared text is inside the {COOLDOWN_DAYS}-day cooldown."
            )
        assert_not_posted(payload["text"], now=now.astimezone(UTC))
        result = publish(payload["text"])
        post_id = str(result.get("data", {}).get("id") or "").strip()
        if not post_id.isdigit():
            raise RuntimeError("X API did not return a valid post ID.")
        post_url = f"https://x.com/{EXPECTED_X_USERNAME}/status/{post_id}"
        record_prepared_post(
            payload,
            post_url=post_url,
            origin="api",
            now=now,
            history=history,
        )
        print(f"Published prepared post successfully: {post_url}")
        return 0

    slot, slot_index = choose_slot(args.slot, now)
    seed = now.date().toordinal() * 4 + slot_index
    history = load_history()

    needs_full_history = not history_is_trusted(history)
    try:
        user_id, x_records = recent_x_history(
            history,
            now,
            force_full=needs_full_history,
        )
        history["xUserId"] = user_id
        history["siteHistoryInitialized"] = True
        if needs_full_history:
            history["historyWindowDays"] = COOLDOWN_DAYS
            history["historyBackfilledAt"] = now.astimezone(UTC).isoformat()
        merge_history_records(history, x_records, now)
        history["cooldownDays"] = COOLDOWN_DAYS
        history["updatedAt"] = now.astimezone(UTC).isoformat()
        save_history(history)
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

    if not history_is_trusted(history):
        raise RuntimeError(
            "A verified 184-day X history backfill is required before preparing a post."
        )

    recent_urls = recent_source_urls(history, now)
    source_type = ""
    source_url = ""
    title = ""
    post_text = ""

    site_history_ready = history_is_trusted(history)
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
                "all candidates are inside "
                f"the {COOLDOWN_DAYS}-day cooldown"
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

    post_text = validate_post_text(post_text)
    if post_hash(post_text) in recent_text_hashes(history, now):
        raise RuntimeError(
            f"Selected text is inside the {COOLDOWN_DAYS}-day cooldown."
        )
    assert_not_posted(post_text, now=now.astimezone(UTC))

    print("--- X post preview ---")
    print(post_text)
    print(f"Source: {source_type} {source_url}")
    print(f"Characters: {len(post_text)}")
    print(f"Non-URL characters: {visible_body_length(post_text)}")

    payload = prepared_payload(
        text=post_text,
        source_type=source_type,
        source_url=source_url,
        title=title,
        slot=slot,
        now=now,
    )

    if args.prepare_file:
        save_prepared_file(args.prepare_file, payload)
        print(f"Prepared post saved to {args.prepare_file}")
        return 0

    if args.dry_run:
        print(
            "Dry run completed. "
            "Nothing was posted or saved."
        )
        return 0
    raise RuntimeError(
        "Direct API publishing is disabled. Use --publish-file with a prepared payload."
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
