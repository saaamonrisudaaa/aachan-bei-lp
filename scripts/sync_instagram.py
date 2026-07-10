#!/usr/bin/env python3
"""Synchronize the latest public Instagram posts for the site homepage."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USERNAME = "ah_bei_gourmet"
OUTPUT = Path("assets/instagram-posts.json")
PROFILE_URL = f"https://www.instagram.com/{USERNAME}/"
API_URL = (
    "https://www.instagram.com/api/v1/users/web_profile_info/"
    f"?username={urllib.parse.quote(USERNAME)}"
)
HEADERS = {
    "Accept": "*/*",
    "Referer": PROFILE_URL,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "X-IG-App-ID": "936619743392459",
}


def request_text(url: str) -> str:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def caption_from_node(node: dict) -> str:
    edges = node.get("edge_media_to_caption", {}).get("edges", [])
    if edges:
        return str(edges[0].get("node", {}).get("text", "")).strip()[:500]
    return str(node.get("caption", {}).get("text", "")).strip()[:500]


def posts_from_api() -> list[dict]:
    payload = json.loads(request_text(API_URL))
    user = payload.get("data", {}).get("user") or {}
    timeline = user.get("edge_owner_to_timeline_media", {})
    edges = timeline.get("edges", [])
    posts: list[dict] = []

    for edge in edges:
        node = edge.get("node") or {}
        shortcode = str(node.get("shortcode") or node.get("code") or "").strip()
        if not shortcode:
            continue
        product_type = str(node.get("product_type") or "").lower()
        typename = str(node.get("__typename") or "").lower()
        is_reel = product_type == "clips" or "video" in typename
        kind = "reel" if is_reel else "post"
        timestamp = int(
            node.get("taken_at_timestamp")
            or node.get("taken_at")
            or 0
        )
        posted_at = (
            datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
            if timestamp
            else None
        )
        posts.append(
            {
                "shortcode": shortcode,
                "type": kind,
                "url": f"https://www.instagram.com/{kind}/{shortcode}/",
                "caption": caption_from_node(node),
                "postedAt": posted_at,
            }
        )

    return posts


def posts_from_profile_html() -> list[dict]:
    html = request_text(PROFILE_URL)
    matches = re.findall(
        r"https?:\\?/\\?/(?:www\\.)?instagram\\?\.com\\?/(p|reel)\\?/([A-Za-z0-9_-]+)",
        html,
    )
    if not matches:
        matches = re.findall(r'"shortcode":"([A-Za-z0-9_-]+)"', html)
        return [
            {
                "shortcode": shortcode,
                "type": "post",
                "url": f"https://www.instagram.com/p/{shortcode}/",
                "caption": "Instagramで最新の店舗投稿を見る",
                "postedAt": None,
            }
            for shortcode in matches
        ]
    return [
        {
            "shortcode": shortcode,
            "type": kind,
            "url": f"https://www.instagram.com/{kind}/{shortcode}/",
            "caption": "Instagramで最新の店舗投稿を見る",
            "postedAt": None,
        }
        for kind, shortcode in matches
    ]


def existing_posts() -> list[dict]:
    if not OUTPUT.exists():
        return []
    try:
        return json.loads(OUTPUT.read_text(encoding="utf-8")).get("posts", [])
    except (OSError, json.JSONDecodeError):
        return []


def merge_posts(fetched: list[dict], existing: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for post in fetched + existing:
        shortcode = str(post.get("shortcode") or "").strip()
        if not shortcode or shortcode in seen:
            continue
        seen.add(shortcode)
        merged.append(post)
    return merged[:9]


def main() -> int:
    fetched: list[dict] = []
    errors: list[str] = []

    for loader in (posts_from_api, posts_from_profile_html):
        try:
            fetched = loader()
            if fetched:
                break
        except (OSError, ValueError, KeyError, urllib.error.URLError) as error:
            errors.append(f"{loader.__name__}: {error}")

    if not fetched:
        print("Instagram could not be refreshed; keeping the existing feed.")
        for error in errors:
            print(error)
        return 0

    posts = merge_posts(fetched, existing_posts())
    timestamps = [
        post["postedAt"]
        for post in posts
        if isinstance(post.get("postedAt"), str) and post["postedAt"]
    ]
    payload = {
        "version": 2,
        "profile": PROFILE_URL,
        "updatedAt": max(timestamps) if timestamps else None,
        "posts": posts,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Instagram feed synchronized: {len(posts)} posts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
