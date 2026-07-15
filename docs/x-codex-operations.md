# X運用（@somasaaamon / Chrome優先）

## 投稿時刻

Asia/Tokyoで毎日16回実行する。

`09:05, 09:13, 09:21, 09:47, 10:05, 11:24, 11:45, 12:10, 13:15, 16:12, 16:35, 17:11, 17:31, 21:01, 21:15, 21:30`

時刻ごとに独立したCodex自動化を1件ずつ作る。複数の時・分を1つのRRULEへ入れると直積で不要な時刻が生まれるため、まとめない。

## 固定ルール

- 対象アカウントは必ず `@somasaaamon`。
- Google Chromeを通常経路とし、Edgeは操作しない。
- Chrome接続が複数見える場合は、Google Chromeのプロファイル名 `ユーザー 1` が付いた接続を選ぶ。プロファイル名のない接続は選ばない。
- 投稿前にChromeのプロフィールURLが `/somasaaamon`、表示ハンドルが `@somasaaamon` であることを確認する。
- 投稿元は最初に `https://achanbay.com/` の実食記事を使う。
- 地域は東京を最優先し、候補がない場合だけ埼玉、神奈川、千葉の順に使う。
- サイト候補が半年分のクールダウンですべて使えない場合だけ、食べログ評価3.5以上を東京、埼玉、神奈川、千葉の順で参照する。
- 同じ本文または同じ店舗URLは184日以内に再投稿しない。184日は「半年以上」を早めないための保守的な固定期間。
- 投稿は3行（フック、本文、URL）とし、URLを除く文字は50字以内。
- 1回の実行で投稿は最大1件。排他ロックを取得できない場合は投稿せず終了する。
- 投稿成否が曖昧な場合、まずプロフィールで完全一致本文を確認する。存在が確認できる場合はAPIへ切り替えない。

## 通常のChrome経路

1. `scripts/x_post_lock.py acquire` で排他ロックを取得する。
2. `X_STATE_DIR=%LOCALAPPDATA%\Codex\somasaaamon-x-state` を設定する。
3. GitHubのmainブランチから最新の `data/x-post-history.json` を一時ファイルへ取得し、`scripts/x_auto_post.py --import-history-file <履歴JSON>` を実行する。取得または取込に失敗した場合は投稿しない。
4. `scripts/x_auto_post.py --prepare-file <一時JSON>` で候補を1件だけ準備する。
5. 準備JSONの `username`、`text`、`sourceUrl` を確認する。
6. Google Chromeの `@somasaaamon` で `text` を一字も変えずに投稿する。
7. `https://x.com/somasaaamon/status/<id>` を取得し、投稿本文の完全一致を確認する。
8. `scripts/x_auto_post.py --record-file <一時JSON> --post-url <確認済みURL>` を実行する。
9. 一時JSONを削除し、必ず排他ロックを解放する。

## APIフォールバック

Chromeが未接続、投稿前から利用不能、または投稿していないことをプロフィールで確認できた場合だけ使う。

1. 準備JSONをbase64化する。
2. GitHub Actions `X post fallback and validation` を `dry_run=false`、`sync_history=false`、`prepared_payload_b64=<base64>` で1回だけ実行する。
3. Actionsの成功ログから `https://x.com/somasaaamon/status/<id>` を確認する。
4. ローカルでも `--record-file` を実行し、Chrome/APIで共通の履歴へ記録する。

API側は投稿直前に `/2/users/me` を呼び、認証ユーザー名が `somasaaamon`、保存済みユーザーIDとも一致する場合だけ投稿する。APIは独自に別の文面を選ばず、Chrome用に準備した同じJSONだけを投稿する。

## 初回の履歴同期

自動化を有効にする前に、GitHub Actionsを `sync_history=true`、`dry_run=true` で1回実行する。これによりX APIから少なくとも184日分を逆引きし、mainブランチの履歴種へ `historyWindowDays` と `historyBackfilledAt` を保存する。この信頼済み履歴がない状態では候補準備を失敗させ、重複投稿を防ぐ。

## 検証コマンド

```powershell
python -m unittest discover -s tests -v
python scripts/x_post_queue.py validate
python scripts/x_auto_post.py --prepare-file .codex/prepared-x-post.json
```

旧 `data/x-post-queue.json` は空にしてあり、新しい定期投稿では使用しない。初回だけ `data/x-post-history.json` を種にし、以後は `%LOCALAPPDATA%\Codex\somasaaamon-x-state` の共通履歴を使うため、定期投稿でGit作業ツリーを汚さない。
