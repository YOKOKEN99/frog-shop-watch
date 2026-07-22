# 釣具ショップ 新商品チェック(クラウド実行版・複数サイト対応)
#
# 各サイトのRSSフィードを取得し、「まだ見ていない」かつ「掲載日が監視開始(cutoff)より後」の
# 商品だけを新商品とみなして ntfy.sh にプッシュ通知する。監視先は SITES に足すだけで増やせる。
#
# pubDate/掲載日を見る理由: この手のRSSは最新数件だけを載せる「回転ドア」式で、過去の商品が
# 後から枠に戻ってくることがある。リンクの有無だけで判定すると出戻り商品を新商品と誤検知する。
# 掲載日が監視開始より後のものだけ通知して防ぐ。
#
# 状態ファイル seen_items.json 形式(サイトごとに分離):
#   {"<site>": {"cutoff": "<ISO8601>", "seen": ["<link>", ...]}, ...}
#   旧FROG単体形式 {"cutoff":..., "seen":[...]} も読めるようにし、次回実行で "frog" 配下へ移行する。
#
# 環境変数 NTFY_TOPIC: 通知先の ntfy トピック名(GitHub Secrets)。未設定ならドライラン(表示のみ)。

import json
import os
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_items.json")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()

# 監視先サイト定義。増やすときはここに1件足すだけ。
#   name         : 状態ファイル内のキー(半角英数)
#   label        : 通知タイトル(ASCIIのみ。日本語は本文へ)
#   rss_url      : RSSフィードのURL
#   format       : "rss2"(RFC822 pubDate) / "rss1"(RDF・dc:date)
#   product_match: 商品ページのリンクに必ず含まれる文字列(告知等を除外)
SITES = [
    {
        "name": "frog",
        "label": "FROG Web Shop",
        "rss_url": "https://frog-ltd.com/hpgen/HPB/rss.xml",
        "format": "rss2",
        "product_match": "/SHOP/",
    },
    {
        "name": "fishmagnet",
        "label": "FISH MAGNET",
        "rss_url": "https://fishmagnet.shop-pro.jp/?mode=rss",
        "format": "rss1",
        "product_match": "?pid=",
    },
]

RSS1_NS = {"rss": "http://purl.org/rss/1.0/", "dc": "http://purl.org/dc/elements/1.1/"}


def _to_aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _fetch_bytes(url):
    """URLを取得(3回まで再試行)。ブラウザ相当のUAで弾かれ対策"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (frog-shop-watch)"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return res.read()
        except Exception as e:
            if attempt == 2:
                raise
            print(f"  fetch retry {attempt + 1}: {e}")
            time.sleep(10)


def fetch_products(site):
    """サイトのRSSから商品[{title,link,dt}]をリンク重複なしで返す"""
    data = _fetch_bytes(site["rss_url"])
    root = ET.fromstring(data)
    match = site["product_match"]
    products = {}

    if site["format"] == "rss1":
        items = root.findall("rss:item", RSS1_NS)
        for it in items:
            link = (it.findtext("rss:link", namespaces=RSS1_NS) or "").strip()
            if match not in link or link in products:
                continue
            title = (it.findtext("rss:title", namespaces=RSS1_NS) or "").strip()
            raw = (it.findtext("dc:date", namespaces=RSS1_NS) or "").strip()
            try:
                dt = _to_aware(datetime.fromisoformat(raw))
            except Exception:
                dt = None
            products[link] = {"title": title, "link": link, "dt": dt}
    else:  # rss2
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            if match not in link or link in products:
                continue
            title = (it.findtext("title") or "").strip()
            raw = (it.findtext("pubDate") or "").strip()
            try:
                dt = _to_aware(parsedate_to_datetime(raw))
            except Exception:
                dt = None
            products[link] = {"title": title, "link": link, "dt": dt}

    return list(products.values())


def load_all_state():
    """状態を {site: {cutoff, seen}} に正規化して返す。無ければ空dict"""
    if not os.path.exists(STATE_FILE):
        return {}
    with open(STATE_FILE, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):  # 最初期のリスト形式(FROGのみ)
        return {"frog": {"cutoff": None, "seen": data}}
    if isinstance(data, dict) and "seen" in data and "cutoff" in data:  # FROG単体形式
        return {"frog": data}
    return data  # すでに {site: {...}} 形式


def save_all_state(all_state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(all_state, f, ensure_ascii=False, indent=1)


def send_ntfy(label, body, click_url=None):
    if not NTFY_TOPIC:
        print(f"  [dry-run] {label} / {body} / click={click_url}")
        return
    headers = {"Title": label, "Tags": "fishing_pole_and_fish"}  # ヘッダはASCIIのみ
    if click_url:
        headers["Click"] = click_url
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}", data=body.encode("utf-8"), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        print(f"  ntfy sent: HTTP {res.status}")


def process_site(site, all_state, now):
    """1サイトを処理。状態を更新したら True を返す"""
    name = site["name"]
    try:
        products = fetch_products(site)
    except Exception as e:
        print(f"[{name}] fetch failed, skip this run: {e}")
        return False
    print(f"[{name}] RSS products: {len(products)}")

    st = all_state.get(name)
    if st is None:
        # 初回: 現在の全商品を既知として保存し、今を監視開始基準に(過去商品は通知しない)
        all_state[name] = {"cutoff": now.isoformat(), "seen": sorted(p["link"] for p in products)}
        print(f"[{name}] initialized with {len(products)} items, cutoff={now.isoformat()}")
        return True

    seen = set(st["seen"])
    cutoff = datetime.fromisoformat(st["cutoff"]) if st.get("cutoff") else now

    new_links = [p for p in products if p["link"] not in seen]
    to_notify = []
    for p in new_links:
        if p["dt"] is not None and p["dt"] > cutoff:
            to_notify.append(p)
        else:
            print(f"[{name}] skip (old/undated): {p['link']}")
    print(f"[{name}] new links: {len(new_links)}, to notify: {len(to_notify)}")

    if to_notify:
        if len(to_notify) <= 5:
            for p in to_notify:
                send_ntfy(site["label"], f"新商品: {p['title']}\n{p['link']}", p["link"])
        else:
            titles = "\n".join(f"・{p['title']}" for p in to_notify)
            send_ntfy(site["label"], f"新商品{len(to_notify)}件:\n{titles}", site["rss_url"])

    if new_links:
        all_state[name] = {"cutoff": cutoff.isoformat(), "seen": sorted(seen | {p["link"] for p in new_links})}
        return True
    if not st.get("cutoff"):  # 新規は無いが旧形式だったので新形式へ移行
        all_state[name] = {"cutoff": cutoff.isoformat(), "seen": sorted(seen)}
        return True
    return False


def main():
    all_state = load_all_state()
    now = datetime.now(timezone.utc)
    changed = False
    for site in SITES:
        if process_site(site, all_state, now):
            changed = True
    if changed:
        save_all_state(all_state)
        print("state saved")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
