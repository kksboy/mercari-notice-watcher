#!/usr/bin/env python3
"""
Mercari official announcements watcher.

Monitors the "メルカリびより" official announcement list
(https://jp-news.mercari.com/info/) for new articles whose title
contains any of the target keywords ("メルカード" / "入会" / "キャンペーン"),
and notifies a Discord channel via webhook when new matching articles
appear.

Design notes
------------
* Only ONE HTTP request is made to the target page per run (the first
  page of the announcement list, which already covers the ~20 most
  recent articles -- far more than enough for a once-a-day check).
  There is no pagination, no retry loop, and no polling.
* robots.txt is fetched and checked (via urllib.robotparser) before
  that request. This is a compliance check, not a "content" request,
  and is required to responsibly decide whether crawling is allowed
  at all. As of writing, jp-news.mercari.com/robots.txt only disallows
  /wp-admin/, so /info/ is permitted.
* A descriptive User-Agent is sent so the site owner can identify (and,
  if desired, block) this bot.
* Already-notified article URLs are persisted in seen.json so that
  only genuinely new articles trigger a Discord notification. On the
  very first run (seen.json missing or empty) the current matches are
  recorded as a baseline WITHOUT sending notifications, to avoid
  spamming Discord with the entire pre-existing history.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_URL = "https://jp-news.mercari.com/info/"
ROBOTS_URL = "https://jp-news.mercari.com/robots.txt"

KEYWORDS = ["メルカード", "入会", "キャンペーン"]

# Identify this bot clearly and give the site owner a way to reach you.
# Customize the URL below (e.g. to your GitHub repo) before deploying.
USER_AGENT = (
    "MercariNoticeWatcher/1.0 "
    "(+https://github.com/YOUR_GITHUB_USER/YOUR_REPO; "
    "personal daily announcement watcher, low-frequency single request)"
)

REQUEST_TIMEOUT = 15  # seconds

SCRIPT_DIR = Path(__file__).resolve().parent
SEEN_FILE = SCRIPT_DIR / "seen.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")


# ---------------------------------------------------------------------------
# robots.txt compliance
# ---------------------------------------------------------------------------

def is_allowed_by_robots(url: str, user_agent: str) -> bool:
    """Fetch and evaluate robots.txt before touching the target page."""
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(ROBOTS_URL)
    try:
        rp.read()
    except Exception as exc:  # noqa: BLE001 - any failure means "don't crawl"
        print(f"[WARN] Failed to fetch/parse robots.txt: {exc}", file=sys.stderr)
        return False
    return rp.can_fetch(user_agent, url)


# ---------------------------------------------------------------------------
# Fetch & parse
# ---------------------------------------------------------------------------

def fetch_announcements() -> list[dict]:
    """Perform the single allowed GET request and parse the article list."""
    headers = {"User-Agent": USER_AGENT}
    resp = requests.get(TARGET_URL, headers=headers, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or "utf-8"

    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    for item in soup.select("li.p-postList__item"):
        link_tag = item.select_one("a.p-postList__link")
        title_tag = item.select_one("h2.p-postList__title")
        time_tag = item.select_one("time.c-postTimes__posted")

        if not link_tag or not title_tag:
            continue

        href = (link_tag.get("href") or "").strip()
        url = urljoin(TARGET_URL, href)
        title = title_tag.get_text(strip=True)
        date = (time_tag.get("datetime") or "").strip() if time_tag else ""

        if not url or not title:
            continue

        articles.append({"url": url, "title": title, "date": date})

    return articles


def matches_keywords(title: str) -> bool:
    return any(keyword in title for keyword in KEYWORDS)


# ---------------------------------------------------------------------------
# seen.json persistence
# ---------------------------------------------------------------------------

def load_seen() -> dict:
    if not SEEN_FILE.exists():
        return {}
    try:
        with SEEN_FILE.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] Could not read {SEEN_FILE}: {exc}", file=sys.stderr)
        return {}


def save_seen(seen: dict) -> None:
    with SEEN_FILE.open("w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# Discord notification
# ---------------------------------------------------------------------------

def notify_discord(article: dict) -> None:
    if not DISCORD_WEBHOOK_URL:
        print(
            "[WARN] DISCORD_WEBHOOK_URL is not set; skipping notification.",
            file=sys.stderr,
        )
        return

    payload = {
        "username": "メルカリお知らせ Watcher",
        "embeds": [
            {
                "title": article["title"],
                "url": article["url"],
                "description": "新着お知らせを検知しました（メルカード / 入会 / キャンペーン）",
                "color": 0xFF0211,  # Mercari red
                "fields": [
                    {"name": "公開日", "value": article["date"] or "不明", "inline": True},
                ],
                "footer": {"text": "メルカリびより 公式お知らせ監視"},
            }
        ],
    }

    resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 300:
        print(
            f"[ERROR] Discord webhook returned {resp.status_code}: {resp.text}",
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not is_allowed_by_robots(TARGET_URL, USER_AGENT):
        print(
            f"[ERROR] robots.txt disallows fetching {TARGET_URL} for this "
            "User-Agent. Aborting without making the request.",
            file=sys.stderr,
        )
        return 1

    try:
        articles = fetch_announcements()
    except requests.RequestException as exc:
        print(f"[ERROR] Failed to fetch {TARGET_URL}: {exc}", file=sys.stderr)
        return 1

    matched = [a for a in articles if matches_keywords(a["title"])]

    seen = load_seen()
    is_first_run = len(seen) == 0

    new_articles = [a for a in matched if a["url"] not in seen]
    now = datetime.now(timezone.utc).isoformat()

    if is_first_run:
        # Baseline run: record current matches as seen, but don't spam
        # Discord with the entire pre-existing history on first execution.
        print(
            f"[INFO] First run: recording {len(matched)} existing matching "
            "article(s) as baseline (no notifications sent)."
        )
        for a in matched:
            seen[a["url"]] = {"title": a["title"], "date": a["date"], "first_seen": now}
        save_seen(seen)
        return 0

    if not new_articles:
        print("[INFO] No new matching articles found.")
        return 0

    print(f"[INFO] Found {len(new_articles)} new matching article(s). Notifying Discord...")
    for a in new_articles:
        notify_discord(a)
        seen[a["url"]] = {"title": a["title"], "date": a["date"], "first_seen": now}
        print(f"  - {a['date']}  {a['title']}  ({a['url']})")

    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
