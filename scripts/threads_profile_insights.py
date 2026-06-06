#!/usr/bin/env python3
"""
One-off exploration: print everything the Threads API returns for profile-level insights.
Run via GitHub Actions workflow_dispatch to see output in the Actions log.
"""

import os
import json
import requests
from datetime import datetime, timedelta, timezone

THREADS_TOKEN = os.environ["THREADS_ACCESS_TOKEN"]
THREADS_BASE = "https://graph.threads.net/v1.0"


def get_user_id():
    resp = requests.get(
        f"{THREADS_BASE}/me",
        params={"fields": "id,username", "access_token": THREADS_TOKEN},
    )
    resp.raise_for_status()
    return resp.json()


def get_follower_count(user_id):
    resp = requests.get(
        f"{THREADS_BASE}/{user_id}",
        params={"fields": "followers_count", "access_token": THREADS_TOKEN},
    )
    return resp.status_code, resp.json()


def get_profile_insights(user_id, metric, period, since=None, until=None):
    params = {
        "metric": metric,
        "period": period,
        "access_token": THREADS_TOKEN,
    }
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    resp = requests.get(
        f"{THREADS_BASE}/{user_id}/threads_insights",
        params=params,
    )
    return resp.status_code, resp.json()


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


if __name__ == "__main__":
    now = datetime.now(timezone.utc)
    week_ago = int((now - timedelta(days=7)).timestamp())
    month_ago = int((now - timedelta(days=30)).timestamp())
    today_start = int(now.replace(hour=0, minute=0, second=0).timestamp())

    section("1. USER ID + USERNAME")
    user = get_user_id()
    print(json.dumps(user, indent=2))
    user_id = user["id"]

    section("2. FOLLOWER COUNT")
    status, data = get_follower_count(user_id)
    print(f"HTTP {status}")
    print(json.dumps(data, indent=2))

    section("3. PROFILE INSIGHTS — period=day (last 7 days)")
    metrics = "views,likes,replies,reposts,quotes,followers_count"
    status, data = get_profile_insights(user_id, metrics, "day", since=week_ago)
    print(f"HTTP {status}")
    print(json.dumps(data, indent=2))

    section("4. PROFILE INSIGHTS — period=week")
    status, data = get_profile_insights(user_id, metrics, "week")
    print(f"HTTP {status}")
    print(json.dumps(data, indent=2))

    section("5. PROFILE INSIGHTS — period=month")
    status, data = get_profile_insights(user_id, metrics, "month")
    print(f"HTTP {status}")
    print(json.dumps(data, indent=2))

    section("6. PROFILE INSIGHTS — period=lifetime")
    status, data = get_profile_insights(user_id, metrics, "lifetime")
    print(f"HTTP {status}")
    print(json.dumps(data, indent=2))

    section("7. FOLLOWER DEMOGRAPHICS — lifetime")
    demo_metrics = "follower_demographics"
    status, data = get_profile_insights(
        user_id, demo_metrics, "lifetime",
        # demographics require a breakdown param
    )
    print(f"HTTP {status}")
    print(json.dumps(data, indent=2))

    section("8. FOLLOWER DEMOGRAPHICS — breakdown=country")
    resp = requests.get(
        f"{THREADS_BASE}/{user_id}/threads_insights",
        params={
            "metric": "follower_demographics",
            "period": "lifetime",
            "breakdown": "country",
            "access_token": THREADS_TOKEN,
        },
    )
    print(f"HTTP {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))

    section("9. FOLLOWER DEMOGRAPHICS — breakdown=age")
    resp = requests.get(
        f"{THREADS_BASE}/{user_id}/threads_insights",
        params={
            "metric": "follower_demographics",
            "period": "lifetime",
            "breakdown": "age",
            "access_token": THREADS_TOKEN,
        },
    )
    print(f"HTTP {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))

    section("10. FOLLOWER DEMOGRAPHICS — breakdown=gender")
    resp = requests.get(
        f"{THREADS_BASE}/{user_id}/threads_insights",
        params={
            "metric": "follower_demographics",
            "period": "lifetime",
            "breakdown": "gender",
            "access_token": THREADS_TOKEN,
        },
    )
    print(f"HTTP {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))

    print(f"\nDone. {now.isoformat()}")
