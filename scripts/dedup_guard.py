"""Prevents a duplicate KakaoTalk send when both the external trigger (workflow_dispatch, the
primary path -- see README's "외부 트리거 설정") and the `schedule` trigger (kept only as a
safety net for when the external trigger fails) end up firing on the same day.

Only a `schedule`-triggered run needs to check this; the external-trigger path always proceeds
without asking (it IS the primary send, and cron-job.org is configured to fire at most once/day
per workflow).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
GITHUB_API = "https://api.github.com"


def already_dispatched_today(workflow_file: str, now_kst: datetime) -> bool:
    """True if a workflow_dispatch run of `workflow_file` already completed successfully today
    (KST date). Always returns False when not called from a `schedule`-triggered run, or when the
    GitHub API check itself can't be performed -- a missed duplicate-check must never block the
    actual safety-net send."""
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return False

    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print(
            "dedup_guard: GITHUB_TOKEN/GITHUB_REPOSITORY 미설정 -- 중복 방지 확인을 건너뜁니다.",
            file=sys.stderr,
        )
        return False

    today = now_kst.date()
    try:
        resp = requests.get(
            f"{GITHUB_API}/repos/{repo}/actions/workflows/{workflow_file}/runs",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            params={"event": "workflow_dispatch", "status": "success", "per_page": 10},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"dedup_guard: GitHub API 조회 실패({exc}) -- 안전하게 계속 진행합니다.", file=sys.stderr)
        return False

    for run in resp.json().get("workflow_runs", []):
        created_at = datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if created_at.astimezone(KST).date() == today:
            print(
                f"dedup_guard: 오늘 이미 workflow_dispatch 성공 실행이 있습니다(run {run['id']}) "
                "-- schedule 안전망 실행은 건너뜁니다.",
                file=sys.stderr,
            )
            return True
    return False
