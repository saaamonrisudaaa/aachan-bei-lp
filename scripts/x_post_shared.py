#!/usr/bin/env python3
"""Shared queue, validation, and posted-log rules for X publishing."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

MAX_BODY_CHARS = 50
QUEUE_PATH = Path("data/x-post-queue.json")
POSTED_LOG_PATH = Path("data/x-posted-log.json")
URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)


class PostValidationError(ValueError):
    """Raised when a post violates a publishing rule."""


class DuplicatePostError(PostValidationError):
    """Raised when the exact post text has already been used or queued."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def one_line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_post_text(text: str) -> str:
    lines = str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def visible_body_length(text: str) -> int:
    """Count visible non-URL characters; formatting newlines are not counted."""

    without_urls = URL_RE.sub("", normalize_post_text(text))
    return sum(len(line.strip()) for line in without_urls.split("\n"))


def _valid_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def validate_post_text(text: str) -> str:
    """Return normalized text when it follows the three-line/50-char rules."""

    normalized = normalize_post_text(text)
    lines = normalized.split("\n")
    if len(lines) != 3 or any(not line.strip() for line in lines):
        raise PostValidationError(
            "Post must have exactly three non-empty lines: hook, body, URL."
        )
    if URL_RE.search(lines[0]) or URL_RE.search(lines[1]):
        raise PostValidationError("The hook and body lines must not contain a URL.")
    if lines[2].strip() != lines[2] or not _valid_url(lines[2]):
        raise PostValidationError("The third line must contain only one HTTP(S) URL.")
    if URL_RE.fullmatch(lines[2]) is None:
        raise PostValidationError("The third line must contain only one URL.")
    body_length = visible_body_length(normalized)
    if body_length > MAX_BODY_CHARS:
        raise PostValidationError(
            f"Non-URL text is {body_length} characters; maximum is {MAX_BODY_CHARS}."
        )
    return normalized


def truncate(value: str, limit: int) -> str:
    value = one_line(value)
    if limit < 1:
        return ""
    if len(value) <= limit:
        return value
    if limit == 1:
        return value[:1]
    return value[: limit - 1].rstrip() + "…"


def compose_post(hook: str, body: str, url: str) -> str:
    """Build a validated hook/body/URL post and shorten it when necessary."""

    hook = one_line(hook)
    body = one_line(body)
    url = one_line(url)
    if not hook or not body:
        raise PostValidationError("Hook and body must not be empty.")
    if not _valid_url(url) or URL_RE.fullmatch(url) is None:
        raise PostValidationError("A single absolute HTTP(S) URL is required.")

    # Keep both paragraphs useful even when a restaurant name is long.
    hook = truncate(hook, min(24, MAX_BODY_CHARS - 1))
    body = truncate(body, MAX_BODY_CHARS - len(hook))
    if not body:
        raise PostValidationError("The 50-character budget left no room for body text.")
    return validate_post_text(f"{hook}\n{body}\n{url}")


def post_hash(text: str) -> str:
    normalized = validate_post_text(text)
    body_text = "\n".join(normalized.splitlines()[:2])
    return hashlib.sha256(body_text.encode("utf-8")).hexdigest()


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PostValidationError(f"Cannot safely read {path}: {error}") from error
    if not isinstance(payload, dict):
        raise PostValidationError(f"{path} must contain a JSON object.")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_posted_log(path: Path = POSTED_LOG_PATH) -> dict[str, Any]:
    payload = _load_json(
        path,
        {"version": 1, "updatedAt": None, "posts": []},
    )
    posts = payload.get("posts")
    if not isinstance(posts, list):
        raise PostValidationError(f"{path}: posts must be a JSON array.")
    for record in posts:
        if not isinstance(record, dict) or not isinstance(record.get("textHash"), str):
            raise PostValidationError(f"{path}: every post needs a textHash.")
    return payload


def posted_hashes(log: dict[str, Any]) -> set[str]:
    return {str(record["textHash"]) for record in log.get("posts", [])}


def assert_not_posted(
    text: str,
    log_path: Path = POSTED_LOG_PATH,
) -> str:
    digest = post_hash(text)
    if digest in posted_hashes(load_posted_log(log_path)):
        raise DuplicatePostError("This exact post text has already been published.")
    return digest


def record_posted_text(
    text: str,
    *,
    post_url: str,
    source_url: str,
    origin: str,
    queue_item_id: str | None = None,
    path: Path = POSTED_LOG_PATH,
    posted_at: str | None = None,
) -> dict[str, Any]:
    normalized = validate_post_text(text)
    digest = post_hash(normalized)
    log = load_posted_log(path)
    for record in log["posts"]:
        if record.get("textHash") == digest:
            return record
    record = {
        "textHash": digest,
        "text": normalized,
        "sourceUrl": source_url,
        "origin": origin,
        "queueItemId": queue_item_id,
        "postedAt": posted_at or utc_now_iso(),
        "postUrl": post_url,
    }
    log["posts"].append(record)
    log["updatedAt"] = record["postedAt"]
    _write_json(path, log)
    return record


def load_queue(path: Path = QUEUE_PATH) -> dict[str, Any]:
    payload = _load_json(
        path,
        {"version": 1, "updatedAt": None, "items": []},
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise PostValidationError(f"{path}: items must be a JSON array.")
    for item in items:
        if not isinstance(item, dict):
            raise PostValidationError(f"{path}: every queue item must be an object.")
        validate_post_text(str(item.get("text") or ""))
        expected = post_hash(str(item["text"]))
        if item.get("textHash") != expected:
            raise PostValidationError(f"{path}: queue item has an invalid textHash.")
    return payload


def enqueue_post(
    *,
    hook: str,
    body: str,
    url: str,
    origin: str,
    source_title: str = "",
    reference_url: str = "",
    slot: str = "auto",
    priority: bool = False,
    queue_path: Path = QUEUE_PATH,
    log_path: Path = POSTED_LOG_PATH,
) -> dict[str, Any]:
    text = compose_post(hook, body, url)
    digest = post_hash(text)
    if digest in posted_hashes(load_posted_log(log_path)):
        raise DuplicatePostError("This exact post text has already been published.")

    queue = load_queue(queue_path)
    if any(item.get("textHash") == digest for item in queue["items"]):
        raise DuplicatePostError("This exact post text is already queued.")
    created_at = utc_now_iso()
    item = {
        "id": digest[:16],
        "textHash": digest,
        "text": text,
        "sourceUrl": url,
        "sourceTitle": one_line(source_title),
        "referenceUrl": one_line(reference_url),
        "origin": one_line(origin) or "site",
        "slot": one_line(slot) or "auto",
        "createdAt": created_at,
    }
    if priority:
        queue["items"].insert(0, item)
    else:
        queue["items"].append(item)
    queue["updatedAt"] = created_at
    _write_json(queue_path, queue)
    return item


def peek_queue(
    slot: str = "auto",
    path: Path = QUEUE_PATH,
) -> dict[str, Any] | None:
    queue = load_queue(path)
    for item in queue["items"]:
        item_slot = str(item.get("slot") or "auto")
        if slot == "auto" or item_slot in {"auto", slot}:
            return item
    return None


def mark_queue_item_posted(
    item_id: str,
    *,
    post_url: str,
    queue_path: Path = QUEUE_PATH,
    log_path: Path = POSTED_LOG_PATH,
) -> dict[str, Any]:
    queue = load_queue(queue_path)
    item = next(
        (candidate for candidate in queue["items"] if candidate.get("id") == item_id),
        None,
    )
    if item is None:
        raise PostValidationError(f"Queue item was not found: {item_id}")
    if not _valid_url(post_url):
        raise PostValidationError("A confirmed X status URL is required.")
    record = record_posted_text(
        str(item["text"]),
        post_url=post_url,
        source_url=str(item.get("sourceUrl") or ""),
        origin=str(item.get("origin") or "queue"),
        queue_item_id=str(item["id"]),
        path=log_path,
    )
    queue["items"] = [
        candidate for candidate in queue["items"] if candidate.get("id") != item_id
    ]
    queue["updatedAt"] = utc_now_iso()
    _write_json(queue_path, queue)
    return record


def validate_all(
    queue_path: Path = QUEUE_PATH,
    log_path: Path = POSTED_LOG_PATH,
) -> dict[str, int]:
    queue = load_queue(queue_path)
    log = load_posted_log(log_path)
    queued_hashes = [str(item["textHash"]) for item in queue["items"]]
    if len(queued_hashes) != len(set(queued_hashes)):
        raise DuplicatePostError("The queue contains duplicate post text.")
    historical = posted_hashes(log)
    overlap = historical.intersection(queued_hashes)
    if overlap:
        raise DuplicatePostError("A queued post is already present in the posted log.")
    return {"queued": len(queued_hashes), "posted": len(historical)}
