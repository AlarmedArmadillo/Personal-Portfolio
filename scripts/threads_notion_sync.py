#!/usr/bin/env python3
"""
Threads -> Notion metrics sync
Fetches insights for all 'Posted' entries in the Notion tracker and marks them 'Analyzed'.
"""

import os
import sys
import requests
from datetime import datetime, timezone

THREADS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
NOTION_TOKEN = os.environ["NOTION_API_KEY"]
NOTION_DB_ID = os.environ["NOTION_DATABASE_ID"]

THREADS_BASE = "https://graph.threads.net/v1.0"
NOTION_BASE = "https://api.notion.com/v1"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def get_threads_posts(limit=50):
    resp = requests.get(
        f"{THREADS_BASE}/me/threads",
        params={
            "fields": "id,text,timestamp,permalink",
            "limit": limit,
            "access_token": THREADS_TOKEN,
        },
    )
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_post_insights(post_id):
    resp = requests.get(
        f"{THREADS_BASE}/{post_id}/insights",
        params={
            "metric": "views,likes,replies,reposts,quotes",
            "access_token": THREADS_TOKEN,
        },
    )
    resp.raise_for_status()
    data = resp.json().get("data", [])
    return {item["name"]: item["values"][0]["value"] for item in data}


def get_notion_posted():
    resp = requests.post(
        f"{NOTION_BASE}/databases/{NOTION_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={"filter": {"property": "Status", "select": {"equals": "Posted"}}},
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def update_notion_page(page_id, metrics):
    payload = {
        "properties": {
            "Views": {"number": metrics.get("views", 0)},
            "Likes": {"number": metrics.get("likes", 0)},
            "Comments": {"number": metrics.get("replies", 0)},
            "Reposts": {"number": metrics.get("reposts", 0)},
            "Status": {"select": {"name": "Analyzed"}},
        }
    }
    resp = requests.patch(
        f"{NOTION_BASE}/pages/{page_id}", headers=NOTION_HEADERS, json=payload
    )
    resp.raise_for_status()


def normalize_url(url):
    return url.rstrip("/").lower() if url else ""


def sync():
    print(f"Sync started: {datetime.now(timezone.utc).isoformat()}")

    threads_posts = get_threads_posts()
    permalink_map = {
        normalize_url(p.get("permalink", "")): p for p in threads_posts
    }
    print(f"Threads posts fetched: {len(threads_posts)}")

    notion_pages = get_notion_posted()
    print(f"Notion 'Posted' entries: {len(notion_pages)}")

    if not notion_pages:
        print("Nothing to update.")
        return

    updated = 0
    skipped = 0
    for page in notion_pages:
        notion_url = normalize_url(
            page["properties"].get("Threads URL", {}).get("url", "")
        )
        if not notion_url:
            print(f"  Skipped (no URL): {page['id']}")
            skipped += 1
            continue

        post = permalink_map.get(notion_url)
        if not post:
            print(f"  No Threads match for: {notion_url}")
            skipped += 1
            continue

        try:
            insights = get_post_insights(post["id"])
            update_notion_page(page["id"], insights)
            print(f"  Updated: {notion_url}")
            print(f"    views={insights.get('views')} likes={insights.get('likes')} "
                  f"replies={insights.get('replies')} reposts={insights.get('reposts')}")
            updated += 1
        except Exception as e:
            print(f"  Error on {notion_url}: {e}")
            skipped += 1

    print(f"\nDone. {updated} updated, {skipped} skipped.")


if __name__ == "__main__":
    try:
        sync()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
