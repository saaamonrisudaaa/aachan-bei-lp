from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import x_auto_post
from x_post_shared import (
    DuplicatePostError,
    PostValidationError,
    compose_post,
    enqueue_post,
    load_posted_log,
    load_queue,
    mark_queue_item_posted,
    validate_all,
    validate_post_text,
    visible_body_length,
)


class PostRulesTests(unittest.TestCase):
    def test_compose_enforces_three_lines_and_50_visible_characters(self) -> None:
        post = compose_post(
            "ランチ候補をチェック",
            "とても長い紹介文" * 20,
            "https://achanbay.com/restaurant-001.html",
        )
        self.assertEqual(3, len(post.splitlines()))
        self.assertLessEqual(visible_body_length(post), 50)
        self.assertEqual(
            "https://achanbay.com/restaurant-001.html",
            post.splitlines()[2],
        )

    def test_rejects_more_than_50_non_url_characters(self) -> None:
        post = f"{'あ' * 25}\n{'い' * 26}\nhttps://example.com"
        with self.assertRaises(PostValidationError):
            validate_post_text(post)

    def test_rejects_wrong_paragraph_layout(self) -> None:
        with self.assertRaises(PostValidationError):
            validate_post_text("1行だけ https://example.com")

    def test_all_generated_templates_follow_rules(self) -> None:
        now = datetime(2026, 7, 13, tzinfo=timezone(timedelta(hours=9)))
        for slot in ("morning", "lunch", "afternoon", "evening"):
            site_post = x_auto_post.build_site_post(
                slot,
                "https://achanbay.com/restaurant-001.html",
                "とても長い店名のテストレストラン東京本店",
                "実際に訪問した料理の説明です。季節のおすすめも紹介します。",
                now,
            )
            validate_post_text(site_post)
            self.assertLessEqual(visible_body_length(site_post), 50)

            tabelog_post = x_auto_post.build_tabelog_post(
                slot,
                {
                    "name": "気になる候補店",
                    "areaGenre": "錦糸町 / ラーメン",
                    "rating": 3.75,
                    "url": "https://tabelog.com/tokyo/A1312/A131201/00000000/",
                },
                now,
            )
            validate_post_text(tabelog_post)
            self.assertLessEqual(visible_body_length(tabelog_post), 50)


class QueueAndLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.queue_path = root / "queue.json"
        self.log_path = root / "posted.json"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_success_moves_item_from_queue_to_permanent_log(self) -> None:
        item = enqueue_post(
            hook="今日のランチ候補に",
            body="実食メモから一軒紹介",
            url="https://achanbay.com/restaurant-001.html",
            origin="site",
            queue_path=self.queue_path,
            log_path=self.log_path,
        )
        record = mark_queue_item_posted(
            item["id"],
            post_url="https://x.com/example/status/1",
            queue_path=self.queue_path,
            log_path=self.log_path,
        )
        self.assertEqual(item["textHash"], record["textHash"])
        self.assertEqual([], load_queue(self.queue_path)["items"])
        self.assertEqual(1, len(load_posted_log(self.log_path)["posts"]))

        with self.assertRaises(DuplicatePostError):
            enqueue_post(
                hook="今日のランチ候補に",
                body="実食メモから一軒紹介",
                url="https://achanbay.com/restaurant-999.html",
                origin="site",
                queue_path=self.queue_path,
                log_path=self.log_path,
            )

    def test_queue_validator_detects_log_overlap(self) -> None:
        item = enqueue_post(
            hook="今夜の候補、ここどう？",
            body="サイトの訪問メモを紹介",
            url="https://achanbay.com/restaurant-002.html",
            origin="site",
            queue_path=self.queue_path,
            log_path=self.log_path,
        )
        self.log_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updatedAt": None,
                    "posts": [{"textHash": item["textHash"]}],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(DuplicatePostError):
            validate_all(self.queue_path, self.log_path)


if __name__ == "__main__":
    unittest.main()
