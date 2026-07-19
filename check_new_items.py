# FROG Web Shop 新商品チェック(クラウド実行版)
# RSSフィードから /SHOP/ の商品リンクを抽出し、seen_items.json と比較。
# 新商品があれば ntfy.sh にプッシュ通知を送り、seen_items.json を更新する。
#
# 環境変数 NTFY_TOPIC: 通知先の ntfy トピック名(GitHub Secrets で設定)。
# 未設定の場合は通知を送らず内容を表示するだけ(ドライラン)。

import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

RSS_URL = "https://frog-ltd.com/hpgen/HPB/rss.xml"
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_items.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()


def fetch_products():
    """RSSを取得し、商品(/SHOP/を含むリンク)のみをリンク重複なしで返す(3回まで再試行)"""
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": "frog-shop-watch/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                data = res.read()
            break
        except Exception as e:
            if attempt == 2:
                raise
            print(f"fetch retry {attempt + 1}: {e}")
            time.sleep(10)
    root = ET.fromstring(data)
    products = {}
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if "/SHOP/" not in link:
            continue
        if link not in products:
            products[link] = {
                "title": (item.findtext("title") or "").strip(),
                "link": link,
                "pubDate": (item.findtext("pubDate") or "").strip(),
            }
    return list(products.values())


def send_ntfy(title, body, click_url=None):
    """ntfy.sh にプッシュ通知を送る"""
    if not NTFY_TOPIC:
        print(f"[dry-run] {title} / {body} / click={click_url}")
        return
    headers = {
        "Title": title,  # ヘッダはASCIIのみ。日本語は本文に入れる
        "Tags": "fishing_pole_and_fish",
    }
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        print(f"ntfy sent: HTTP {res.status}")


def main():
    current = fetch_products()
    print(f"RSS products: {len(current)}")

    if not os.path.exists(STATE_FILE):
        # 初回: 全商品を既知として保存し、通知しない
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump([p["link"] for p in current], f, ensure_ascii=False, indent=1)
        print(f"initialized with {len(current)} items")
        return

    with open(STATE_FILE, encoding="utf-8") as f:
        seen = set(json.load(f))

    new_items = [p for p in current if p["link"] not in seen]
    print(f"new items: {len(new_items)}")
    if not new_items:
        return

    if len(new_items) <= 5:
        for p in new_items:
            send_ntfy("FROG Web Shop", f"新商品: {p['title']}\n{p['link']}", p["link"])
    else:
        titles = "\n".join(f"・{p['title']}" for p in new_items)
        send_ntfy(
            "FROG Web Shop",
            f"新商品{len(new_items)}件:\n{titles}",
            "https://frog-ltd.com/",
        )

    # 通知が送れてから既知リストを更新(送信失敗時は次回再通知される)
    merged = sorted(seen | {p["link"] for p in new_items})
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    print(f"state updated: {len(merged)} known items")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
