from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import x_auto_post
from x_post_shared import (
    DuplicatePostError,
    PostValidationError,
    assert_not_posted,
    compose_post,
    enqueue_post,
    load_posted_log,
    load_queue,
    mark_queue_item_posted,
    post_hash,
    validate_all,
    validate_post_text,
    visible_body_length,
)


class PostRulesTests(unittest.TestCase):
    def test_requested_daily_schedule_is_exact(self) -> None:
        self.assertEqual(
            (
                "09:05", "09:13", "09:21", "09:47",
                "10:05", "11:24", "11:45", "12:10",
                "13:15", "16:12", "16:35", "17:11",
                "17:31", "21:01", "21:15", "21:30",
            ),
            x_auto_post.DAILY_POST_TIMES,
        )

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

    def test_site_selection_prefers_tokyo_then_neighboring_prefectures(self) -> None:
        urls = [
            "https://achanbay.com/restaurant-001.html",
            "https://achanbay.com/restaurant-002.html",
            "https://achanbay.com/restaurant-003.html",
        ]
        regions = {
            urls[0]: "chiba",
            urls[1]: "saitama",
            urls[2]: "tokyo",
        }
        with patch.object(x_auto_post, "site_region", side_effect=regions.get):
            self.assertEqual(
                urls[2],
                x_auto_post.select_site_candidate(urls, set(), 0),
            )
            self.assertEqual(
                urls[1],
                x_auto_post.select_site_candidate(urls, {urls[2]}, 0),
            )

    def test_tabelog_fallback_order_and_threshold(self) -> None:
        self.assertEqual(
            ("東京", "埼玉", "神奈川", "千葉"),
            tuple(group[0] for group in x_auto_post.TABELLOG_SEARCH_GROUPS),
        )
        self.assertEqual(3.5, x_auto_post.TABELLOG_MIN_RATING)

    def test_api_account_mismatch_is_rejected(self) -> None:
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        with patch.object(
            x_auto_post,
            "x_request",
            return_value={"data": {"id": "1", "username": "someone_else"}},
        ):
            with self.assertRaisesRegex(RuntimeError, "not @somasaaamon"):
                x_auto_post.recent_x_history(x_auto_post.default_history(), now)

    def test_confirmed_post_url_must_belong_to_target_account(self) -> None:
        self.assertEqual(
            "123",
            x_auto_post.confirmed_post_id(
                "https://x.com/somasaaamon/status/123?ref=test"
            ),
        )
        with self.assertRaises(RuntimeError):
            x_auto_post.confirmed_post_id(
                "https://x.com/gachahiroba/status/123"
            )

    def test_prepared_text_url_must_match_declared_source(self) -> None:
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        payload = x_auto_post.prepared_payload(
            text=compose_post(
                "今日のランチ候補に",
                "東京の実食メモ",
                "https://achanbay.com/restaurant-001.html",
            ),
            source_type="site",
            source_url="https://achanbay.com/restaurant-001.html",
            title="候補店",
            slot="lunch",
            now=now,
        )
        payload["sourceUrl"] = "https://achanbay.com/restaurant-002.html"
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            x_auto_post.validate_prepared_payload(payload)

    def test_old_history_marker_is_not_trusted(self) -> None:
        history = x_auto_post.default_history()
        history["siteHistoryInitialized"] = True
        history["historyWindowDays"] = 90
        self.assertFalse(x_auto_post.history_is_trusted(history))
        history["historyWindowDays"] = 184
        self.assertTrue(x_auto_post.history_is_trusted(history))

    def test_neighboring_prefecture_hook_is_not_labeled_tokyo(self) -> None:
        post = x_auto_post.build_tabelog_post(
            "morning",
            {
                "name": "候補店",
                "areaGenre": "大宮 / 和食",
                "rating": 3.6,
                "prefecture": "埼玉",
                "url": "https://tabelog.com/saitama/A1101/11000000/",
            },
            datetime(2026, 7, 15, tzinfo=timezone.utc),
        )
        self.assertIn("埼玉グルメ", post.splitlines()[0])
        self.assertNotIn("東京グルメ", post)


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

    def test_exact_text_is_reusable_only_after_184_days(self) -> None:
        text = compose_post(
            "今日のランチ候補に",
            "実食メモから一軒紹介",
            "https://achanbay.com/restaurant-001.html",
        )
        now = datetime(2026, 7, 15, tzinfo=timezone.utc)
        self.log_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "updatedAt": None,
                    "posts": [
                        {
                            "textHash": post_hash(text),
                            "postedAt": (now - timedelta(days=183)).isoformat(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(DuplicatePostError):
            assert_not_posted(text, self.log_path, now=now)

        payload = json.loads(self.log_path.read_text(encoding="utf-8"))
        payload["posts"][0]["postedAt"] = (
            now - timedelta(days=184, seconds=1)
        ).isoformat()
        self.log_path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(post_hash(text), assert_not_posted(text, self.log_path, now=now))


if __name__ == "__main__":
    unittest.main()
