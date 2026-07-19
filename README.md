# frog-shop-watch

FROG Web Shop (https://frog-ltd.com/) のRSSフィードを30分おきにチェックし、
新商品が掲載されたら ntfy.sh 経由でスマホにプッシュ通知する。

## 仕組み

- `check_new_items.py` — RSSから商品リンク(`/SHOP/`)を抽出し、`seen_items.json`(既知リスト)と比較。新商品があれば ntfy に通知して既知リストを更新
- `.github/workflows/watch.yml` — GitHub Actions で30分おきに上記を自動実行し、更新された `seen_items.json` をコミットで保存
- 通知先トピック名はリポジトリの Secret `NTFY_TOPIC` に設定(公開しないこと)

## メンテナンス

- 通知が来なくなったら: リポジトリの Actions タブでワークフローが失敗・停止していないか確認。
  「Workflows disabled due to inactivity」と出ていたら再有効化ボタンを押す(50日ごとの keepalive コミットで通常は防止される)
- 通知テスト: Actions タブ → frog-shop-watch → Run workflow で手動実行できる
