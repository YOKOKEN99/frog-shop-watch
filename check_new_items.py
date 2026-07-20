# FROG Web Shop 新商品チェック(クラウド実行版)
# RSSフィードから /SHOP/ の商品リンクを抽出し、状態ファイル seen_items.json と比較。
# 「まだ見ていない」かつ「掲載日(pubDate)が監視開始より後」の商品だけを新商品とみなし、
# ntfy.sh にプッシュ通知して状態を更新する。
#
# pubDate を見る理由: FROGのRSSは最新数件だけを載せる「回転ドア」式で、過去の商品が
# 後から枠に戻ってくることがある。リンクの有無だけで判定すると、その出戻り商品を
# 新商品と誤検知してしまう。掲載日が監視開始(cutoff)より後のものだけ通知して防ぐ。
#
# 状態ファイル形式: {"cutoff": "<ISO8601>", "seen": ["<link>", ...]}
#   cutoff = 監視開始時刻(この時刻より後に掲載された商品だけを通知対象にする基準)
#   古いリスト形式 ["<link>", ...] も読めるようにし、その場合は次回実行で新形式へ移行する。
#
# 環境変数 NTFY_TOPIC: 通知先の ntfy トピック名(GitHub Secrets で設定)。
# 未設定の場合は通知を送らず内容を表示するだけ(ドライラン)。

import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

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
        if link in products:
            continue
        pub_raw = (item.findtext("pubDate") or "").strip()
        try:
            pub_dt = parsedate_to_datetime(pub_raw)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except Exception:
            pub_dt = None  # 掲載日が読めない商品は安全側で通知しない
        products[link] = {
            "title": (item.findtext("title") or "").strip(),
            "link": link,
            "pubDate": pub_raw,
            "pub_dt": pub_dt,
        }
    return list(products.values())


def load_state():
    """状態ファイルを読み、{"cutoff":..., "seen":[...]} に正規化して返す。無ければ None"""
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        # 旧リスト形式: cutoff はまだ無い(次回 main で今時刻を設定して移行)
        return {"cutoff": None, "seen": data}
    return {"cutoff": data.get("cutoff"), "seen": data.get("seen", [])}


def save_state(cutoff_iso, seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"cutoff": cutoff_iso, "seen": sorted(seen)}, f, ensure_ascii=False, indent=1)


def send_ntfy(title, body, click_url=None):
    """ntfy.sh にプッシュ通知を送る"""
    if not NTFY_TOPIC:
        print(f"[dry-run] {title} / {body} / click={click_url}")
        return
    headers = {"Title": title, "Tags": "fishing_pole_and_fish"}  # ヘッダはASCIIのみ。日本語は本文へ
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
    now = datetime.now(timezone.utc)

    state = load_state()
    if state is None:
        # 初回: 全商品を既知として保存し、今を監視開始基準にする(過去商品は通知しない)
        save_state(now.isoformat(), [p["link"] for p in current])
        print(f"initialized with {len(current)} items, cutoff={now.isoformat()}")
        return

    seen = set(state["seen"])
    if state["cutoff"]:
        cutoff = datetime.fromisoformat(state["cutoff"])
    else:
        # 旧形式からの移行: 今を基準にする(以降に掲載された商品だけを新商品扱い)
        cutoff = now
        print(f"migrated old state: cutoff set to {cutoff.isoformat()}")

    new_links = [p for p in current if p["link"] not in seen]
    to_notify = []
    for p in new_links:
        if p["pub_dt"] is not None and p["pub_dt"] > cutoff:
            to_notify.append(p)
        else:
            # 監視開始より前に掲載された「出戻り商品」や掲載日不明: 通知せず既知リストに黙って追加
            print(f"skip (old/undated): {p['link']} pub={p['pubDate']}")

    print(f"new links: {len(new_links)}, to notify: {len(to_notify)}")

    if to_notify:
        if len(to_notify) <= 5:
            for p in to_notify:
                send_ntfy("FROG Web Shop", f"新商品: {p['title']}\n{p['link']}", p["link"])
        else:
            titles = "\n".join(f"・{p['title']}" for p in to_notify)
            send_ntfy("FROG Web Shop", f"新商品{len(to_notify)}件:\n{titles}", "https://frog-ltd.com/")

    # 新規リンクは通知の有無に関わらず全て既知リストへ(出戻りが再度出ても二度と誤検知しない)
    if new_links:
        save_state(cutoff.isoformat(), seen | {p["link"] for p in new_links})
        print(f"state updated: cutoff={cutoff.isoformat()}, known={len(seen) + len(new_links)}")
    elif not state["cutoff"]:
        # 新規は無いが旧形式だったので、cutoffだけ書き込んで新形式へ移行しておく
        save_state(cutoff.isoformat(), seen)
        print("state migrated to new format (no new links)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
