#!/usr/bin/env python3
"""Archive supplied photos and create optimized web copies."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import date
from pathlib import Path

from PIL import Image, ImageOps


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--category", default="food")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--prefix", default="food")
    parser.add_argument("--max-width", type=int, default=1200)
    parser.add_argument("--max-height", type=int, default=1600)
    parser.add_argument("--quality", type=int, default=84)
    return parser.parse_args()


def relative_to_project(path: Path) -> str:
    return path.relative_to(PROJECT_ROOT).as_posix()


def main() -> int:
    args = parse_args()
    archive_dir = (
        PROJECT_ROOT
        / "photo-library"
        / args.category
        / args.date
        / "originals"
    )
    web_dir = (
        PROJECT_ROOT
        / "assets"
        / "photos"
        / args.category
        / args.date
    )
    archive_dir.mkdir(parents=True, exist_ok=True)
    web_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, object]] = []
    for index, source in enumerate(args.inputs, start=1):
        source = source.resolve(strict=True)
        suffix = source.suffix.lower() or ".jpg"
        stem = f"{args.prefix}-{index:02d}"
        archived = archive_dir / f"{stem}{suffix}"
        web_copy = web_dir / f"{stem}.webp"

        shutil.copy2(source, archived)
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            if image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            image.thumbnail(
                (args.max_width, args.max_height),
                Image.Resampling.LANCZOS,
            )
            image.save(
                web_copy,
                "WEBP",
                quality=args.quality,
                method=6,
            )
            width, height = image.size

        records.append(
            {
                "sourceName": source.name,
                "sha256": hashlib.sha256(archived.read_bytes()).hexdigest(),
                "original": relative_to_project(archived),
                "web": relative_to_project(web_copy),
                "width": width,
                "height": height,
            }
        )

    manifest = archive_dir.parent / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "category": args.category,
                "date": args.date,
                "photos": records,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(records, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
