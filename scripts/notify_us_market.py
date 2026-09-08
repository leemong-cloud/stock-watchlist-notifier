"""Daily US market mood summary -> KakaoTalk.

Not tied to any specific holding -- a general "how did US markets close, what should a Korean
investor know before KR market open" summary, sent once per day (see
.github/workflows/us-market-mood.yml).

Uses the Claude Code CLI (subscription auth, see claude_cli.py) rather than the Anthropic API
directly -- no per-token billing.

The prompt anchors on the run's actual KST clock (like notify_kr_watchlist.py does) and
explicitly asks the model to state whether today is a US market holiday/weekend and which date
the reported close actually belongs to -- previously the prompt said only "오늘 기준" with no real
date given to the model, so it had no grounding to notice a closed session (confirmed missed on
2026-09-07, a US holiday Monday).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from claude_cli import ClaudeCliError, run_claude
from dedup_guard import already_dispatched_today
from kakao_client import KakaoAuthError, KakaoSendError, send_kakao_message

KST = timezone(timedelta(hours=9))
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

FAILURE_SENTINEL = "최신 지수 정보 확인 실패"
WORKFLOW_FILE = "us-market-mood.yml"


def build_prompt(now_kst: datetime) -> str:
    weekday = _WEEKDAY_KR[now_kst.weekday()]
    return (
        f"지금은 {now_kst.strftime('%Y-%m-%d %H:%M')} (한국시간, {weekday}요일) 기준이야. "
        "이 시점을 기준으로 가장 최근에 실제로 마감한 미국 증시 세션(S&P500, 나스닥, 다우 등락률에 "
        "더해 반도체 업종 체감을 위한 필라델피아 반도체지수(SOX) 등락률까지 반드시 포함)을 "
        "웹 검색으로 확인해줘. 먼저 오늘(미국 동부시간 기준으로 환산한 날짜)이 미국 증시 휴장일"
        "(주말 또는 공휴일)인지 확인하고, 휴장일이면 그 사실과 함께 실제로 마감이 있었던 가장 최근 "
        "날짜를 명시한 뒤 그날의 결과를 요약해 -- 오래된 뉴스 기사의 날짜를 오늘 마감으로 착각하지 "
        "말고 반드시 검색 결과에 실제로 찍힌 날짜를 확인해서 말해. 한국 투자자가 참고할 만한 미국 "
        "증시 전반 분위기(주요 이슈, 눈에 띄는 업종/종목 동향)도 포함해서 한국어로 4문장 이내, 170자 "
        "이내로 답해(맨 앞에 붙는 '[미국장 분위기]' 헤더를 포함해 카카오톡 메시지 한 건에 바로 들어갈 "
        "분량이니 여유 있게 짧게 -- 위 4개 지수(S&P500/나스닥/다우/필라델피아 반도체지수) 수치를 "
        "다 넣으면서도 이 글자수를 넘기지 않도록 문장을 압축해). 확인되지 않은 수치는 추측하지 말고, "
        f"확인 가능한 정보가 없으면 정확히 '{FAILURE_SENTINEL}'라고만 답해."
    )


def main() -> int:
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("CLAUDE_CODE_OAUTH_TOKEN이 설정되지 않았습니다.", file=sys.stderr)
        return 1

    test_date = os.environ.get("TEST_DATE_KST", "").strip()
    if test_date:
        now_kst = datetime.strptime(test_date, "%Y-%m-%d").replace(hour=23, minute=59, tzinfo=KST)
        print(f"TEST_DATE_KST 오버라이드: {test_date} 기준으로 실행합니다 (실 운영 스케줄에는 영향 없음)")
    else:
        now_kst = datetime.now(KST)

    if already_dispatched_today(WORKFLOW_FILE, now_kst):
        return 0

    prompt = build_prompt(now_kst)

    try:
        summary = run_claude(prompt) or "요약 생성 실패"
        print(f"RAW  {' '.join(summary.split())[:300]}", file=sys.stderr)
        if summary == FAILURE_SENTINEL:
            summary = run_claude(prompt) or "요약 생성 실패"  # WebSearch가 그날따라 못 찾은 경우 1회만 재시도
    except ClaudeCliError as exc:
        print(f"FAIL(claude): {exc}", file=sys.stderr)
        return 1

    try:
        send_kakao_message(f"[미국장 분위기]\n{summary}")
    except (KakaoAuthError, KakaoSendError) as exc:
        print(f"FAIL(kakao): {exc}", file=sys.stderr)
        return 1

    print("OK  미국장 분위기 요약 발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
