#!/usr/bin/env python3
"""
Threads <-> Notion sync
- publish_scheduled: finds Scheduled posts due now, publishes to Threads, writes URL back
- sync_metrics: pulls insights for Posted entries, marks them Analyzed
- sync_metrics_14d: daily deep refresh — all posts in the last 14 days, all statuses
"""

import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone

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


def get_threads_posts(limit=50, since=None):
    params = {
        "fields": "id,text,timestamp,permalink",
        "limit": limit,
        "access_token": THREADS_TOKEN,
    }
    if since:
        params["since"] = since
    resp = requests.get(f"{THREADS_BASE}/me/threads", params=params)
    resp.raise_for_status()
    return resp.json().get("data", [])


def get_threads_posts_since(since_ts, limit=100):
    """Fetch ALL posts since a Unix timestamp, paginating through all pages."""
    all_posts = []
    params = {
        "fields": "id,text,timestamp,permalink,is_reply",
        "limit": limit,
        "since": since_ts,
        "access_token": THREADS_TOKEN,
    }
    while True:
        resp = requests.get(f"{THREADS_BASE}/me/threads", params=params)
        resp.raise_for_status()
        data = resp.json()
        all_posts.extend(data.get("data", []))
        cursor = data.get("paging", {}).get("cursors", {}).get("after")
        if not cursor:
            break
        params["after"] = cursor
    return all_posts


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


def update_notion_page(page_id, props, retries=3):
    for attempt in range(retries):
        resp = requests.patch(
            f"{NOTION_BASE}/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"properties": props},
        )
        if resp.status_code == 502 and attempt < retries - 1:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return


def normalize_url(url):
    return url.rstrip("/").lower() if url else ""


def get_all_notion_urls():
    """Return a set of all Threads URLs already tracked in Notion."""
    resp = requests.post(
        f"{NOTION_BASE}/databases/{NOTION_DB_ID}/query",
        headers=NOTION_HEADERS,
        json={},
    )
    resp.raise_for_status()
    urls = set()
    for page in resp.json().get("results", []):
        url = page["properties"].get("Threads URL", {}).get("url", "")
        if url:
            urls.add(normalize_url(url))
    return urls


def get_all_notion_pages_with_urls():
    """Return {normalized_url: page_id} for every Notion page that has a Threads URL."""
    url_map = {}
    payload = {}
    while True:
        resp = requests.post(
            f"{NOTION_BASE}/databases/{NOTION_DB_ID}/query",
            headers=NOTION_HEADERS,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        for page in data.get("results", []):
            url = page["properties"].get("Threads URL", {}).get("url", "")
            if url:
                url_map[normalize_url(url)] = page["id"]
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data["next_cursor"]
    return url_map


def create_notion_page(post):
    """Create a new Notion entry for a post detected directly on Threads."""
    timestamp = post.get("timestamp", "")
    date_posted = timestamp if timestamp else datetime.now(timezone.utc).isoformat()

    resp = requests.post(
        f"{NOTION_BASE}/pages",
        headers=NOTION_HEADERS,
        json={
            "parent": {"database_id": NOTION_DB_ID},
            "properties": {
                "Post": {"title": [{"text": {"content": post.get("text", "")[:2000]}}]},
                "Threads URL": {"url": post.get("permalink", "")},
                "Status": {"select": {"name": "Posted"}},
                "Date Posted": {"date": {"start": date_posted}},
            },
        },
    )
    resp.raise_for_status()
    return resp.json()["id"]


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


def auto_post_drafted(min_gap_hours=2, post_hour_start=8, post_hour_end=22):
    """Post the oldest Drafted entry if no post has gone out in the last min_gap_hours."""
    print("--- Auto-posting from Drafted queue ---")
    now = datetime.now(timezone.utc)

    if not (post_hour_start <= now.hour < post_hour_end):
        print(f"  Outside posting window ({post_hour_start}:00–{post_hour_end}:00 UTC).")
        return

    # Check last Threads post — skip if posted too recently
    recent = get_threads_posts(limit=1)
    if recent:
        last_ts = datetime.fromisoformat(recent[0]["timestamp"].replace("Z", "+00:00"))
        gap_hours = (now - last_ts).total_seconds() / 3600
        if gap_hours < min_gap_hours:
            print(f"  Last post was {gap_hours:.1f}h ago (min gap {min_gap_hours}h), skipping.")
            return

    drafted = query_notion({"property": "Status", "select": {"equals": "Drafted"}})
    if not drafted:
        print("  No Drafted posts available.")
        return

    def sort_key(page):
        date_start = (page["properties"].get("Date Posted", {}).get("date") or {}).get("start", "")
        return date_start or page.get("created_time", "")

    drafted.sort(key=sort_key)
    page = drafted[0]

    props = page.get("properties", {})
    title_parts = props.get("Post", {}).get("title", [])
    post_text = "".join(p.get("plain_text", "") for p in title_parts)

    if not post_text:
        print(f"  Skipped (no text): {page['id']}")
        return

    try:
        user_id = get_threads_user_id()
        container_id = create_threads_container(user_id, post_text)
        time.sleep(5)
        post_id = publish_threads_container(user_id, container_id)
        permalink = get_post_permalink(post_id)
        update_notion_page(page["id"], {
            "Threads URL": {"url": permalink},
            "Status": {"select": {"name": "Posted"}},
            "Date Posted": {"date": {"start": now.isoformat()}},
        })
        print(f"  Auto-posted: {permalink}")
    except Exception as e:
        print(f"  Error auto-posting {page['id']}: {e}")


def detect_new_posts():
    """Fetch posts from the last hour and add any not already in Notion."""
    print("--- Detecting new posts ---")

    since = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    recent_posts = get_threads_posts(limit=10, since=since)

    # Filter client-side to posts within the last hour (API since param may vary)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_posts = [
        p for p in recent_posts
        if datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")) >= cutoff
    ]
    print(f"Posts in last hour: {len(recent_posts)}")

    if not recent_posts:
        return

    existing_urls = get_all_notion_urls()
    added = 0

    for post in recent_posts:
        url = normalize_url(post.get("permalink", ""))
        if not url or url in existing_urls:
            continue
        try:
            create_notion_page(post)
            print(f"  Added to Notion: {post.get('permalink')}")
            added += 1
        except Exception as e:
            print(f"  Error adding {post.get('permalink')}: {e}")

    print(f"New posts added: {added}")


def sync_metrics_14d(days=14):
    """
    Daily deep refresh: fetch all posts from the last N days, pull fresh insights,
    update every matching Notion page regardless of status. Creates pages for any
    posts not yet tracked.
    """
    print(f"--- 14-day metrics sync (last {days} days) ---")
    since_ts = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())

    posts = get_threads_posts_since(since_ts)
    print(f"Threads posts in window: {len(posts)}")
    if not posts:
        print("  Nothing to sync.")
        return

    # Client-side filter — API `since` param can be fuzzy
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    posts = [
        p for p in posts
        if datetime.fromisoformat(p["timestamp"].replace("Z", "+00:00")) >= cutoff
    ]
    print(f"Posts within {days}-day window after filter: {len(posts)}")

    # Exclude replies to other posts — insights API returns empty data for them
    posts = [p for p in posts if not p.get("is_reply", False)]
    print(f"Original posts (replies excluded): {len(posts)}")

    notion_url_map = get_all_notion_pages_with_urls()

    updated = 0
    created = 0
    errors = 0

    for post in posts:
        norm_url = normalize_url(post.get("permalink", ""))
        if not norm_url:
            continue
        try:
            insights = get_post_insights(post["id"])
            metrics = {
                "Views": {"number": insights.get("views", 0)},
                "Likes": {"number": insights.get("likes", 0)},
                "Comments": {"number": insights.get("replies", 0)},
                "Reposts": {"number": insights.get("reposts", 0)},
            }

            if norm_url in notion_url_map:
                update_notion_page(notion_url_map[norm_url], metrics)
                print(f"  Updated: {norm_url} — views={insights.get('views')} likes={insights.get('likes')} comments={insights.get('replies')}")
                updated += 1
            else:
                page_id = create_notion_page(post)
                update_notion_page(page_id, metrics)
                print(f"  Created + synced: {post.get('permalink')}")
                created += 1

        except Exception as e:
            print(f"  Error on {norm_url}: {e}")
            errors += 1

    print(f"14-day sync done: {updated} updated, {created} created, {errors} errors.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    print(f"Sync started [{mode}]: {datetime.now(timezone.utc).isoformat()}")
    try:
        if mode == "analyze":
            sync_metrics_14d()
        else:
            publish_scheduled()
            auto_post_drafted()
            detect_new_posts()
            sync_metrics()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)
