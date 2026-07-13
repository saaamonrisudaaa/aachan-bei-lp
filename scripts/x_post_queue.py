#!/usr/bin/env python3
"""Manage the Git-backed queue used by Codex/Chrome X posting."""

from __future__ import annotations

import argparse
import html
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from x_post_shared import (
    DuplicatePostError,
    PostValidationError,
    enqueue_post,
    load_queue,
    mark_queue_item_posted,
    peek_queue,
    validate_all,
    validate_post_text,
    visible_body_length,
)

SITE_URL = "https://achanbay.com"
JST = timezone(timedelta(hours=9))
SLOTS = ("morning", "lunch", "afternoon", "evening")
EXCLUDED_PAGES = {
    "about.html",
    "area-chiba.html",
    "area-kinshicho.html",
    "area-koiwa.html",
    "area-other.html",
    "area-shibuya-ebisu.html",
    "area-tama.html",
    "area-tokyo.html",
    "genre-cafe.html",
    "genre-chinese.html",
    "genre-izakaya.html",
    "genre-japanese.html",
    "genre-other.html",
    "genre-ramen.html",
    "index.html",
    "privacy.html",
    "shops.html",
}


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
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
        return html.unescape("".join(self.title_parts)).strip()


def current_slot(now: datetime | None = None) -> str:
    now = now or datetime.now(JST)
    if now.hour < 10:
        return "morning"
    if now.hour < 14:
        return "lunch"
    if now.hour < 18:
        return "afternoon"
    return "evening"


def resolve_slot(value: str) -> str:
    return current_slot() if value == "current" else value


def site_candidates(root: Path = Path(".")) -> list[dict[str, str]]:
    sitemap = root / "sitemap.xml"
    if not sitemap.exists():
        raise PostValidationError("sitemap.xml was not found.")
    try:
        xml_root = ET.fromstring(sitemap.read_text(encoding="utf-8"))
    except (OSError, ET.ParseError) as error:
        raise PostValidationError(f"Cannot read sitemap.xml: {error}") from error

    candidates: list[dict[str, str]] = []
    for node in xml_root.findall(".//{*}loc"):
        url = (node.text or "").strip()
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        if not filename.endswith(".html") or filename in EXCLUDED_PAGES:
            continue
        page_path = root / filename
        if not page_path.exists():
            continue
        parser = MetadataParser()
        parser.feed(page_path.read_text(encoding="utf-8", errors="replace"))
        title = parser.meta.get("og:title") or parser.title
        title = title.split("｜", 1)[0].split("|", 1)[0].strip()
        description = html.unescape(
            parser.meta.get("og:description")
            or parser.meta.get("description")
            or ""
        ).strip()
        if title:
            candidates.append(
                {
                    "title": title,
                    "description": description,
                    "url": f"{SITE_URL}/{filename}",
                }
            )
    if not candidates:
        raise PostValidationError("No restaurant pages were found in sitemap.xml.")
    return sorted(candidates, key=lambda item: item["url"])


def refill_site_queue(target: int) -> list[dict]:
    queue = load_queue()
    missing = max(0, target - len(queue["items"]))
    if missing == 0:
        return []

    now = datetime.now(JST)
    candidates = site_candidates()
    start = now.date().toordinal() % len(candidates)
    slot_start = SLOTS.index(current_slot(now))
    hooks = {
        "morning": "朝の店選び、これどう？",
        "lunch": "今日のランチ候補に",
        "afternoon": "次のお店探しに",
        "evening": "今夜の候補、ここどう？",
    }
    added: list[dict] = []
    attempts = 0
    max_attempts = len(candidates) * len(SLOTS) * 3
    while len(added) < missing and attempts < max_attempts:
        candidate = candidates[(start + attempts) % len(candidates)]
        slot = SLOTS[(slot_start + len(added)) % len(SLOTS)]
        date_label = f"{now.year}/{now.month}/{now.day}"
        body = f"{date_label}は「{candidate['title']}」をチェック"
        try:
            item = enqueue_post(
                hook=hooks[slot],
                body=body,
                url=candidate["url"],
                origin="site",
                source_title=candidate["title"],
                slot=slot,
            )
        except DuplicatePostError:
            attempts += 1
            continue
        added.append(item)
        attempts += 1
    if len(added) < missing:
        raise DuplicatePostError(
            f"Only {len(added)} unique site posts could be added; {missing} were needed."
        )
    return added


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add")
    add.add_argument("--hook", required=True)
    add.add_argument("--body", required=True)
    add.add_argument("--url", required=True)
    add.add_argument("--origin", default="trend")
    add.add_argument("--source-title", default="")
    add.add_argument("--reference-url", default="")
    add.add_argument("--slot", choices=("auto", *SLOTS), default="auto")
    add.add_argument("--priority", action="store_true")

    peek = subparsers.add_parser("peek")
    peek.add_argument(
        "--slot",
        choices=("auto", "current", *SLOTS),
        default="current",
    )

    mark = subparsers.add_parser("mark-posted")
    mark.add_argument("--id", required=True)
    mark.add_argument("--post-url", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--text-file", type=Path)

    refill = subparsers.add_parser("refill-site")
    refill.add_argument("--target", type=int, default=8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "add":
        result = enqueue_post(
            hook=args.hook,
            body=args.body,
            url=args.url,
            origin=args.origin,
            source_title=args.source_title,
            reference_url=args.reference_url,
            slot=args.slot,
            priority=args.priority,
        )
    elif args.command == "peek":
        result = peek_queue(resolve_slot(args.slot))
        if result is None:
            print(json.dumps({"item": None}, ensure_ascii=False))
            return 3
    elif args.command == "mark-posted":
        result = mark_queue_item_posted(
            args.id,
            post_url=args.post_url,
        )
    elif args.command == "validate":
        if args.text_file:
            text = args.text_file.read_text(encoding="utf-8")
            normalized = validate_post_text(text)
            result = {
                "valid": True,
                "bodyCharacters": visible_body_length(normalized),
                "text": normalized,
            }
        else:
            result = {"valid": True, **validate_all()}
    elif args.command == "refill-site":
        if args.target < 1:
            raise PostValidationError("--target must be at least 1.")
        added = refill_site_queue(args.target)
        result = {"added": len(added), "items": added}
    else:  # pragma: no cover
        raise AssertionError(args.command)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PostValidationError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(2)
