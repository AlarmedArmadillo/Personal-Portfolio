#!/usr/bin/env python3
"""
Fetch all replies (comments) for a specific Threads post by permalink shortcode.
Run via GitHub Actions workflow_dispatch to see output in the Actions log.
"""

import os
import json
import requests

THREADS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
THREADS_BASE = "https://graph.threads.net/v1.0"
TARGET_SHORTCODE = os.environ.get("POST_SHORTCODE", "DZN_FJXjkXQ")


def get_user_id():
    resp = requests.get(
        f"{THREADS_BASE}/me",
        params={"fields": "id,username", "access_token": THREADS_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()


def find_post_by_shortcode(shortcode, limit=100):
    """Page through /me/threads to find the post matching the shortcode."""
    params = {
        "fields": "id,text,permalink,timestamp",
        "limit": limit,
        "access_token": THREADS_TOKEN,
    }
    while True:
        resp = requests.get(f"{THREADS_BASE}/me/threads", params=params)
        resp.raise_for_status()
        data = resp.json()
        for post in data.get("data", []):
            if shortcode in post.get("permalink", ""):
                return post
        cursor = data.get("paging", {}).get("cursors", {}).get("after")
        if not cursor:
            break
        params["after"] = cursor
    return None


def get_replies(post_id):
    """Fetch ALL top-level replies for a post, paginating through all pages."""
    all_replies = []
    params = {
        "fields": "id,text,username,timestamp,has_replies",
        "limit": 100,
        "access_token": THREADS_TOKEN,
    }
    while True:
        resp = requests.get(f"{THREADS_BASE}/{post_id}/replies", params=params)
        resp.raise_for_status()
        data = resp.json()
        all_replies.extend(data.get("data", []))
        cursor = data.get("paging", {}).get("cursors", {}).get("after")
        if not cursor:
            break
        params["after"] = cursor
    return all_replies


def get_nested_replies(reply_id):
    """Fetch all replies to a reply, paginating if needed."""
    all_nested = []
    params = {
        "fields": "id,text,username,timestamp",
        "limit": 100,
        "access_token": THREADS_TOKEN,
    }
    while True:
        resp = requests.get(f"{THREADS_BASE}/{reply_id}/replies", params=params)
        if resp.status_code != 200:
            break
        data = resp.json()
        all_nested.extend(data.get("data", []))
        cursor = data.get("paging", {}).get("cursors", {}).get("after")
        if not cursor:
            break
        params["after"] = cursor
    return all_nested


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


if __name__ == "__main__":
    section("USER")
    user = get_user_id()
    print(json.dumps(user, indent=2))

    section(f"FINDING POST — shortcode: {TARGET_SHORTCODE}")
    post = find_post_by_shortcode(TARGET_SHORTCODE)
    if not post:
        print(f"Post not found for shortcode: {TARGET_SHORTCODE}")
        raise SystemExit(1)
    print(json.dumps(post, indent=2))
    post_id = post["id"]

    section("TOP-LEVEL REPLIES")
    replies = get_replies(post_id)
    print(f"Total top-level replies fetched: {len(replies)}\n")

    for i, reply in enumerate(replies, 1):
        username = reply.get("username", "unknown")
        text = reply.get("text", "")
        timestamp = reply.get("timestamp", "")
        has_nested = reply.get("has_replies", False)
        print(f"[{i}] @{username} ({timestamp})")
        print(f"     {text}")
        if has_nested:
            nested = get_nested_replies(reply["id"])
            for nr in nested:
                print(f"       └─ @{nr.get('username','?')}: {nr.get('text','')}")
        print()
