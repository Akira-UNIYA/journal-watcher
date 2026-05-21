#!/usr/bin/env python3
"""
SMJ, Technovation, JPIM の新着論文を取得。
- RSSフィードで新着メタデータを検出
- Playwright (システムChrome) で全文テキストを取得
- 結果をJSONに保存

要約はスケジュールタスクのClaudeが行う。
"""

import requests
import feedparser
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(SCRIPT_DIR, "seen_papers.json")
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "new_papers.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

JOURNALS = [
    {
        "name": "Strategic Management Journal (SMJ)",
        "short": "SMJ",
        "rss_url": "https://onlinelibrary.wiley.com/feed/10970266/most-recent",
        "publisher": "wiley",
    },
    {
        "name": "Journal of Product Innovation Management (JPIM)",
        "short": "JPIM",
        "rss_url": "https://onlinelibrary.wiley.com/feed/15405885/most-recent",
        "publisher": "wiley",
    },
    {
        "name": "Technovation",
        "short": "Technovation",
        "rss_url": "https://rss.sciencedirect.com/publication/science/01664972",
        "publisher": "elsevier",
    },
]

MAX_PAPERS = 10  # 1回の実行で処理する最大論文数

# ---------------------------------------------------------------------------
# seen管理
# ---------------------------------------------------------------------------

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# RSS取得
# ---------------------------------------------------------------------------

def fetch_rss(url):
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def extract_doi(entry):
    for field in ["prism_doi", "dc_identifier"]:
        val = entry.get(field, "")
        if val and val.startswith("10."):
            return val
    link = entry.get("link", "")
    if "doi.org/" in link:
        return link.split("doi.org/")[-1]
    if "/doi/" in link:
        parts = link.split("/doi/")
        if len(parts) > 1:
            return parts[-1].split("?")[0]
    return ""


def extract_wiley_abstract(entry):
    content_list = entry.get("content", [])
    if not content_list:
        return None
    raw = content_list[0].get("value", "")
    if not raw:
        return None
    raw = raw.replace("Abstract\n\n", "").replace("Abstract\n", "")
    raw = re.sub(r"Research Summary\s*\n?", "", raw)
    raw = re.sub(r"Managerial Summary\s*\n?", "", raw)
    return raw.strip() if raw.strip() else None


def parse_wiley_entries(feed, journal_info):
    papers = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "")
        doi = extract_doi(entry)
        authors = []
        if "authors" in entry:
            authors = [a.get("name", "").strip() for a in entry.authors]
        elif "author" in entry:
            authors = [entry.author.strip()]
        pub_date_str = entry.get("published", entry.get("updated", ""))
        if not title or title.lower().startswith("issue information"):
            continue
        abstract = extract_wiley_abstract(entry)
        papers.append({
            "journal": journal_info["name"],
            "journal_short": journal_info["short"],
            "publisher": journal_info["publisher"],
            "title": title,
            "authors": authors,
            "link": link,
            "doi": doi,
            "pub_date": pub_date_str,
            "abstract": abstract,
        })
    return papers


def parse_elsevier_entries(feed, journal_info):
    papers = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "")
        if not title or title.lower() == "editorial board":
            continue
        summary = entry.get("summary", "")
        authors = []
        author_match = re.search(r"Author\(s\):\s*(.+?)(?:</p>|$)", summary)
        if author_match:
            authors = [a.strip() for a in author_match.group(1).split(",")]
        pub_date_str = ""
        date_match = re.search(r"Publication date:\s*(.+?)(?:</p>|$)", summary)
        if date_match:
            pub_date_str = date_match.group(1).strip()
        papers.append({
            "journal": journal_info["name"],
            "journal_short": journal_info["short"],
            "publisher": journal_info["publisher"],
            "title": title,
            "authors": authors,
            "link": link,
            "doi": "",
            "pub_date": pub_date_str,
            "abstract": None,
        })
    return papers


# ---------------------------------------------------------------------------
# Playwright で全文取得
# ---------------------------------------------------------------------------

def fetch_fulltext_batch(papers):
    """システムChrome (非ヘッドレス) で論文全文を取得"""
    from playwright.sync_api import sync_playwright

    print(f"[INFO] Playwright: {len(papers)}本の全文を取得開始", file=sys.stderr)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """)

        for i, paper in enumerate(papers):
            url = paper["link"]
            print(f"  [{i+1}/{len(papers)}] {paper['title'][:60]}...", file=sys.stderr)

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                time.sleep(5)

                title = page.title()
                if "moment" in title.lower() or "challenge" in title.lower():
                    # Cloudflare challenge — wait longer
                    print(f"    Cloudflare検出、追加待機...", file=sys.stderr)
                    time.sleep(10)
                    title = page.title()

                if "moment" in title.lower():
                    print(f"    [WARN] Cloudflare突破できず、スキップ", file=sys.stderr)
                    paper["fulltext"] = None
                    continue

                text = page.inner_text("body")

                # Wiley: 本文部分を抽出
                if paper["publisher"] == "wiley":
                    text = _extract_wiley_body(text)
                elif paper["publisher"] == "elsevier":
                    text = _extract_elsevier_body(text)

                paper["fulltext"] = text if len(text) > 500 else None
                if paper["fulltext"]:
                    print(f"    OK ({len(text):,} chars)", file=sys.stderr)
                else:
                    print(f"    [WARN] テキスト短すぎ ({len(text)} chars)", file=sys.stderr)

            except Exception as e:
                print(f"    [ERROR] {e}", file=sys.stderr)
                paper["fulltext"] = None

            time.sleep(2)  # 礼儀正しく

        browser.close()


def _extract_wiley_body(text):
    """Wileyページの本文セクションを抽出（ナビやフッターを除去）"""
    # "Abstract" から始まる本文を抽出
    markers_start = ["Abstract\n", "ABSTRACT\n", "Research Summary\n"]
    for marker in markers_start:
        idx = text.find(marker)
        if idx >= 0:
            text = text[idx:]
            break

    # References以降を除去
    markers_end = ["REFERENCES\n", "References\n", "\nREFERENCES", "\nReferences"]
    for marker in markers_end:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
            break

    return text.strip()


def _extract_elsevier_body(text):
    """ScienceDirectページの本文セクションを抽出"""
    markers_start = ["Abstract\n", "Highlights\n", "Introduction\n"]
    for marker in markers_start:
        idx = text.find(marker)
        if idx >= 0:
            text = text[idx:]
            break

    markers_end = ["References\n", "\nReferences", "Cited by", "Recommended articles"]
    for marker in markers_end:
        idx = text.find(marker)
        if idx > 0:
            text = text[:idx]
            break

    return text.strip()


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    all_new_papers = []

    # 1. RSS取得
    for journal in JOURNALS:
        name = journal["short"]
        publisher = journal["publisher"]
        print(f"[INFO] Fetching {name}...", file=sys.stderr)

        try:
            feed = fetch_rss(journal["rss_url"])
        except Exception as e:
            print(f"  [ERROR] RSS fetch failed: {e}", file=sys.stderr)
            continue

        if publisher == "wiley":
            papers = parse_wiley_entries(feed, journal)
        else:
            papers = parse_elsevier_entries(feed, journal)

        print(f"  [INFO] {len(papers)} entries in RSS", file=sys.stderr)

        new_papers = []
        for p in papers:
            key = p["doi"] if p["doi"] else p["link"]
            if key not in seen:
                new_papers.append(p)
                seen.add(key)

        print(f"  [INFO] {len(new_papers)} new", file=sys.stderr)
        all_new_papers.extend(new_papers)

    if not all_new_papers:
        print("\n[RESULT] No new papers found.", file=sys.stderr)
        save_seen(seen)
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)
        print(json.dumps({"count": 0}))
        return

    # 最大件数に絞る
    if len(all_new_papers) > MAX_PAPERS:
        print(f"[INFO] {len(all_new_papers)}本中、最新{MAX_PAPERS}本に絞ります", file=sys.stderr)
        all_new_papers = all_new_papers[:MAX_PAPERS]

    # 2. Playwright で全文取得
    fetch_fulltext_batch(all_new_papers)

    # 3. 保存
    save_seen(seen)

    # fulltextが大きすぎる場合は切り詰め（JSONファイルサイズ制御）
    for p in all_new_papers:
        if p.get("fulltext") and len(p["fulltext"]) > 80000:
            p["fulltext"] = p["fulltext"][:80000] + "\n\n[...テキスト切り詰め...]"

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_new_papers, f, ensure_ascii=False, indent=2)

    with_fulltext = sum(1 for p in all_new_papers if p.get("fulltext"))
    print(f"\n[RESULT] {len(all_new_papers)} papers saved ({with_fulltext} with fulltext)",
          file=sys.stderr)
    print(json.dumps({
        "count": len(all_new_papers),
        "with_fulltext": with_fulltext,
        "file": OUTPUT_FILE,
    }))


if __name__ == "__main__":
    main()
