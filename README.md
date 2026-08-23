# mercari-notice-watcher

メルカリ公式のお知らせサイト「メルカリびより」（`https://jp-news.mercari.com/info/`）を
毎日 JST 9:00 に監視し、タイトルに「メルカード」「入会」「キャンペーン」のいずれかを含む
新着記事が出たら Discord Webhook に通知する GitHub Actions 用スクリプトです。

## 仕組み

- `scrape_mercari_notices.py`
  - `urllib.robotparser` で `robots.txt` を読み込み、対象URLがクロール許可されているか確認してから
    アクセスします（本稿執筆時点で `jp-news.mercari.com/robots.txt` は `/wp-admin/` のみ禁止のため
    `/info/` は許可されています）。
  - 対象ページへのHTTPリクエストは **1回のみ**（ページネーションや再試行は行いません）。
    1ページ目に最新約20件が表示されるため、1日1回のチェックには十分です。
  - 明示的な `User-Agent`（連絡先URL付き）を送信します。デプロイ前に
    `scrape_mercari_notices.py` 内の `USER_AGENT` を自分のリポジトリURLなどに書き換えてください。
  - 記事一覧を BeautifulSoup でパースし（`li.p-postList__item` → `a.p-postList__link` /
    `h2.p-postList__title` / `time[datetime]`）、キーワードにマッチした記事だけを対象にします。
  - 既知のURLは `seen.json` に保存し、差分（未通知の新着のみ）を Discord に通知します。
  - **初回実行時**（`seen.json` が空）は、既存記事をベースラインとして記録するだけで、
    Discord への通知は送りません（過去記事を一気に通知してしまうのを防ぐため）。

- `.github/workflows/mercari-watch.yml`
  - `cron: "0 0 * * *"`（UTC 0:00 = JST 9:00）で毎日実行、`workflow_dispatch` で手動実行も可能。
  - `DISCORD_WEBHOOK_URL` は GitHub Secrets から環境変数として読み込みます。
  - 実行後、`seen.json` に差分があれば `github-actions[bot]` としてコミット・プッシュします
    （そのため `permissions: contents: write` が必要です）。

## セットアップ手順

1. このディレクトリの内容をリポジトリのルートに配置し、GitHub にプッシュします。
   ```bash
   cd mercari-notice-watcher
   git init
   git add .
   git commit -m "Add Mercari notice watcher"
   git branch -M main
   git remote add origin https://github.com/<YOUR_GITHUB_USER>/<YOUR_REPO>.git
   git push -u origin main
   ```

2. Discord で通知先チャンネルの「連携サービス」→「Webhook」から Webhook URL を発行します。

3. GitHub リポジトリの **Settings → Secrets and variables → Actions → New repository secret** で
   `DISCORD_WEBHOOK_URL` という名前でシークレットを登録します。

4. `scrape_mercari_notices.py` 内の `USER_AGENT` にあるプレースホルダー URL を、
   自分のリポジトリURLなど実際に連絡が取れる情報に書き換えてコミットします。

5. **Actions** タブから `Mercari Notice Watcher` ワークフローを手動実行（`workflow_dispatch`）し、
   正常終了することを確認します。初回実行では `seen.json` が更新されるだけで、Discord通知は
   送信されません。以降、対象キーワードを含む新着記事が出た時だけ通知されます。

## ローカルでのテスト

```bash
pip install -r requirements.txt
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/xxxx/yyyy"
python scrape_mercari_notices.py
```

## 注意事項

- GitHub Actions の `schedule` はサーバー負荷により数分〜数十分程度実行が遅延することがあります
  （GitHub の仕様であり、本スクリプト側では制御できません）。
- サイトの HTML 構造が変わった場合、`li.p-postList__item` 等の CSS セレクタが一致しなくなり
  記事が取得できなくなる可能性があります。その場合はセレクタの調整が必要です。
- `robots.txt` やサイトの利用規約は将来変更される可能性があります。定期的に確認してください。
