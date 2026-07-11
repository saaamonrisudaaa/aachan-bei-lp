#!/usr/bin/env python3
"""Synchronize the latest Instagram posts for the site homepage."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USERNAME = "ah_bei_gourmet"
OUTPUT = Path("assets/instagram-posts.json")
PROFILE_URL = f"https://www.instagram.com/{USERNAME}/"
GRAPH_API_VERSION = "v21.0"
GRAPH_ACCESS_TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
PUBLIC_API_URL = (
    "https://www.instagram.com/api/v1/users/web_profile_info/"
    f"?username={urllib.parse.quote(USERNAME)}"
)
PUBLIC_HEADERS = {
    "Accept": "*/*",
    "Referer": PROFILE_URL,
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0 Safari/537.36"
    ),
    "X-IG-App-ID": "936619743392459",
}
GRAPH_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "aachan-bei-instagram-sync/1.0",
}


def request_text(url: str, headers: dict[str, str] | None = None) -> str:
    request = urllib.request.Request(url, headers=headers or PUBLIC_HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def request_json(url: str, headers: dict[str, str] | None = None) -> dict:
    return json.loads(request_text(url, headers=headers))


def graph_request(path: str, **params: str | int) -> dict:
    if not GRAPH_ACCESS_TOKEN:
        return {}
    query = urllib.parse.urlencode(
        {**params, "access_token": GRAPH_ACCESS_TOKEN}
    )
    url = (
        f"https://graph.instagram.com/{GRAPH_API_VERSION}/"
        f"{path.lstrip('/')}?{query}"
    )
    return request_json(url, headers=GRAPH_HEADERS)


def first_graph_record(payload: dict) -> dict:
    data = payload.get("data")
    if isinstance(data, list):
        return data[0] if data else {}
    if isinstance(data, dict):
        return data
    return payload


def shortcode_from_permalink(permalink: str) -> str:
    match = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", permalink)
    return match.group(1) if match else ""


def posts_from_graph_api() -> list[dict]:
    if not GRAPH_ACCESS_TOKEN:
        return []

    account = first_graph_record(
        graph_request("me", fields="user_id,username")
    )
    username = str(account.get("username") or "").strip()
    if username and username.lower() != USERNAME.lower():
        raise ValueError(
            f"Token is linked to @{username}, expected @{USERNAME}."
        )

    user_id = str(account.get("user_id") or account.get("id") or "").strip()
    if not user_id:
        raise ValueError("Instagram professional account ID was not returned.")

    payload = graph_request(
        f"{user_id}/media",
        fields=(
            "id,caption,media_type,media_product_type,permalink,"
            "timestamp,thumbnail_url,media_url"
        ),
        limit=12,
    )
    posts: list[dict] = []

    for media in payload.get("data", []):
        permalink = str(media.get("permalink") or "").strip()
        shortcode = shortcode_from_permalink(permalink)
        if not permalink or not shortcode:
            continue

        media_product_type = str(
            media.get("media_product_type") or ""
        ).upper()
        kind = "reel" if media_product_type == "REELS" or "/reel/" in permalink else "post"
        timestamp = str(media.get("timestamp") or "").strip() or None
        posts.append(
            {
                "shortcode": shortcode,
                "type": kind,
                "url": permalink,
                "caption": str(media.get("caption") or "").strip()[:500],
                "postedAt": timestamp,
                "imageUrl": str(
                    media.get("thumbnail_url")
                    or media.get("media_url")
                    or ""
                ).strip()
                or None,
            }
        )

    return posts


def caption_from_node(node: dict) -> str:
    edges = node.get("edge_media_to_caption", {}).get("edges", [])
    if edges:
        return str(edges[0].get("node", {}).get("text", "")).strip()[:500]
    return str(node.get("caption", {}).get("text", "")).strip()[:500]


def posts_from_public_api() -> list[dict]:
    payload = request_json(PUBLIC_API_URL)
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
        r"(?:https?:)?\\?/\\?/(?:www\\.)?instagram\\?\.com"
        r"\\?/(p|reel)\\?/([A-Za-z0-9_-]+)",
        html,
    )
    if not matches:
        matches = [
            ("post", shortcode)
            for shortcode in re.findall(
                r'"shortcode"\s*:\s*"([A-Za-z0-9_-]+)"',
                html,
            )
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
        return json.loads(
            OUTPUT.read_text(encoding="utf-8")
        ).get("posts", [])
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
    source = "existing"

    loaders = [
        ("meta-graph-api", posts_from_graph_api),
        ("public-profile-api", posts_from_public_api),
        ("profile-html", posts_from_profile_html),
    ]
    for loader_name, loader in loaders:
        try:
            fetched = loader()
            if fetched:
                source = loader_name
                break
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            urllib.error.URLError,
        ) as error:
            errors.append(f"{loader.__name__}: {error}")

    if not fetched:
        print("Instagram could not be refreshed; keeping the existing feed.")
        if not GRAPH_ACCESS_TOKEN:
            print(
                "INSTAGRAM_ACCESS_TOKEN is not configured; "
                "the age-restricted profile cannot be read anonymously."
            )
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
        "version": 3,
        "profile": PROFILE_URL,
        "source": source,
        "updatedAt": max(timestamps) if timestamps else None,
        "posts": posts,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Instagram feed synchronized from {source}: {len(posts)} posts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
