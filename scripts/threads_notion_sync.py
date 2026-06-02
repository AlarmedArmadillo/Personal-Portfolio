#!/usr/bin/env python3
"""
Threads <-> Notion sync
- publish_scheduled: finds Scheduled posts due now, publishes to Threads, writes URL back
- sync_metrics: pulls insights for Posted entries, marks them Analyzed
"""

import os
import sys
import time
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


# ---------- Threads helpers ----------

def get_threads_user_id():
    resp = requests.get(
        f"{THREADS_BASE}/me",
        params={"fields": "id", "access_token": THREADS_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def create_threads_container(user_id, text):
    resp = requests.post(
        f"{THREADS_BASE}/{user_id}/threads",
        params={"access_token": THREADS_TOKEN},
        json={"media_type": "TEXT", "text": text},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def publish_threads_container(user_id, container_id):
    resp = requests.post(
        f"{THREADS_BASE}/{user_id}/threads_publish",
        params={"access_token": THREADS_TOKEN},
        json={"creation_id": container_id},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def get_post_permalink(post_id):
    resp = requests.get(
        f"{THREADS_BASE}/{post_id}",
        params={"fields": "permalink", "access_token": THREADS_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()["permalink"]


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


# ---------- Notion helpers ----------

def query_notion(filter_payload):
    resp = requests.post(
        f"{NOTION_BASE}/databases/{NOTION_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={"filter": filter_payload},
    )
    resp.raise_for_status()
    return resp.json().get("results", [])


def update_notion_page(page_id, props):
    resp = requests.patch(
        f"{NOTION_BASE}/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": props},
    )
    resp.raise_for_status()


def normalize_url(url):
    return url.rstrip("/").lower() if url else ""


# ---------- Main tasks ----------

def publish_scheduled():
    print("--- Publishing scheduled posts ---")
    now = datetime.now(timezone.utc).isoformat()

    pages = query_notion({
        "and": [
            {"property": "Status", "select": {"equals": "Scheduled"}},
            {"property": "Date Posted", "date": {"on_or_before": now}},
        ]
    })
    print(f"Posts due to publish: {len(pages)}")

    if not pages:
        return

    user_id = get_threads_user_id()

    for page in pages:
        props = page.get("properties", {})
        title_parts = props.get("Post", {}).get("title", [])
        post_text = "".join(p.get("plain_text", "") for p in title_parts)

        if not post_text:
            print(f"  Skipped (no text): {page['id']}")
            continue

        try:
            container_id = create_threads_container(user_id, post_text)
            time.sleep(5)  # Threads requires a short wait between create and publish
            post_id = publish_threads_container(user_id, container_id)
            permalink = get_post_permalink(post_id)

            update_notion_page(page["id"], {
                "Threads URL": {"url": permalink},
                "Status": {"select": {"name": "Posted"}},
            })
            print(f"  Published: {permalink}")
        except Exception as e:
            print(f"  Error publishing {page['id']}: {e}")


def sync_metrics():
    print("--- Syncing metrics ---")

    threads_posts = get_threads_posts()
    permalink_map = {normalize_url(p.get("permalink", "")): p for p in threads_posts}
    print(f"Threads posts fetched: {len(threads_posts)}")

    notion_pages = query_notion({"property": "Status", "select": {"equals": "Posted"}})
    print(f"Notion 'Posted' entries: {len(notion_pages)}")

    if not notion_pages:
        return

    updated = 0
    skipped = 0
    for page in notion_pages:
        notion_url = normalize_url(
            page["properties"].get("Threads URL", {}).get("url", "")
        )
        if not notion_url:
            skipped += 1
            continue

        post = permalink_map.get(notion_url)
        if not post:
            print(f"  No match: {notion_url}")
            skipped += 1
            continue

        try:
            insights = get_post_insights(post["id"])
            update_notion_page(page["id"], {
                "Views": {"number": insights.get("views", 0)},
                "Likes": {"number": insights.get("likes", 0)},
                "Comments": {"number": insights.get("replies", 0)},
                "Reposts": {"number": insights.get("reposts", 0)},
                "Status": {"select": {"name": "Analyzed"}},
            })
            print(f"  Updated: {notion_url} → views={insights.get('views')} likes={insights.get('likes')}")
            updated += 1
        except Exception as e:
            print(f"  Error on {notion_url}: {e}")
            skipped += 1

    print(f"Metrics done: {updated} updated, {skipped} skipped.")


if __name__ == "__main__":
    print(f"Sync started: {datetime.now(timezone.utc).isoformat()}")
    try:
        publish_scheduled()
        sync_metrics()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
