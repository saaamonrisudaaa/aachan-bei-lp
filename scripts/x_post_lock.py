#!/usr/bin/env python3
"""Small cross-run lock for the scheduled @somasaaamon posting workflow."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


LOCK_PATH = Path(
    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
) / "Codex" / "somasaaamon-x-post.lock.json"
GUARD_PATH = LOCK_PATH.with_suffix(".guard")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_lock() -> dict | None:
    try:
        payload = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def parse_time(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@contextmanager
def state_guard():
    """Serialize lock-file inspection and stale replacement across processes."""

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(GUARD_PATH, os.O_CREAT | os.O_RDWR)
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
    os.lseek(descriptor, 0, os.SEEK_SET)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX)
    try:
        yield
    finally:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def acquire(ttl_minutes: int) -> int:
    with state_guard():
        now = utc_now()
        existing = read_lock()
        created = parse_time((existing or {}).get("createdAt"))
        if existing and created and created > now - timedelta(minutes=ttl_minutes):
            print(
                json.dumps({"acquired": False, "lock": existing}, ensure_ascii=False)
            )
            return 3
        if LOCK_PATH.exists():
            LOCK_PATH.unlink(missing_ok=True)

        token = secrets.token_urlsafe(18)
        payload = {"token": token, "createdAt": now.isoformat()}
        with LOCK_PATH.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        print(json.dumps({"acquired": True, **payload}, ensure_ascii=False))
        return 0


def release(token: str) -> int:
    with state_guard():
        existing = read_lock()
        if not existing:
            print(json.dumps({"released": True, "alreadyAbsent": True}))
            return 0
        if str(existing.get("token") or "") != token:
            print(json.dumps({"released": False, "reason": "token-mismatch"}))
            return 4
        LOCK_PATH.unlink(missing_ok=True)
        print(json.dumps({"released": True}))
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    acquire_parser = subparsers.add_parser("acquire")
    acquire_parser.add_argument("--ttl-minutes", type=int, default=25)
    release_parser = subparsers.add_parser("release")
    release_parser.add_argument("--token", required=True)
    subparsers.add_parser("status")
    args = parser.parse_args()

    if args.command == "acquire":
        if args.ttl_minutes < 1:
            parser.error("--ttl-minutes must be at least 1")
        return acquire(args.ttl_minutes)
    if args.command == "release":
        return release(args.token)
    print(json.dumps({"lock": read_lock()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
