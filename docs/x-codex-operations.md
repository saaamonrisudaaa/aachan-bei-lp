# X運用（Codex + Chrome）

## 構成

- 通常投稿はCodexの定期タスクがChromeのログイン済みXを操作する。
- GitHub ActionsのX API投稿は定期実行しない。障害時の手動フォールバックだけに使う。
- 投稿候補は `data/x-post-queue.json`、投稿済み本文は `data/x-posted-log.json` で共有する。
- APIフォールバックとChrome投稿は、どちらも `scripts/x_post_shared.py` の同じ検証・重複判定を使う。

## 固定ルール

- 投稿時刻: 毎日 7:30 / 12:30 / 15:30 / 19:30（Asia/Tokyo）。
- メンテナンス: 毎日 10:23（Asia/Tokyo）。
- 投稿は必ず3行（フック、本文、URL）。空行や4行目を作らない。
- URLを除く見える文字は50文字以内。
- 重複判定は先頭2行の本文ハッシュで行う。URLやUTMだけを変えた同文も再投稿しない。
- Xの投稿成功を確認した後だけ、キューから削除して投稿済みログへ記録する。
- 成否が曖昧な場合は、Chromeでプロフィールを確認する。確認できない限りログを更新しない。

## キュー操作

```powershell
python scripts/x_post_queue.py validate
python scripts/x_post_queue.py peek --slot current
python scripts/x_post_queue.py refill-site --target 8
python scripts/x_post_queue.py add --hook "フック" --body "本文" --url "https://achanbay.com/..." --origin trend --reference-url "https://出典URL" --slot auto --priority
python scripts/x_post_queue.py mark-posted --id "キューID" --post-url "https://x.com/.../status/..."
```

`add` と `refill-site` は、50字、3行、既投稿、キュー内重複を保存前に検証する。`mark-posted` は確認済みのXステータスURLを必須とする。

## 日次メンテナンス

1. 当日の日本・東京の話題をWebで確認する。
2. 飲食、季節、天候、街のイベントなど、ガチャひろばの実在ページと自然につながる話題だけを採用する。
3. 事実が確認できない話題や、サイト内容と関係の薄い流行は使わない。
4. キューが8件未満になるよう補充する。適合する話題がなければ `refill-site --target 8` を使う。
5. `validate` 成功後、キューだけをコミットしてmainへpushする。

## Chrome投稿

1. `peek --slot current` で候補を1件取得する。候補がなければサイト実データを補充して再取得する。
2. ChromeでXの投稿画面を開き、`text` を一字も変えずに入力する。
3. 投稿前に3行と50字を再検証する。
4. 投稿し、ステータスURLまたはプロフィール上の完全一致本文で成功を確認する。
5. 成功後にだけ `mark-posted` を実行し、キューと投稿済みログをコミットしてmainへpushする。

Chromeが未ログイン、接続不能、投稿制限中の場合は投稿しない。キューを残したままタスクへエラーを報告する。

## 手動APIフォールバック

GitHub Actionsの `X post fallback and validation` を手動実行する。初回は必ず `dry_run: true` で文面を確認し、Chromeが長時間利用できない場合だけ明示的に本投稿へ切り替える。
