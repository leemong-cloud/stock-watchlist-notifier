"""Daily US market mood summary -> KakaoTalk.

Not tied to any specific holding -- a general "how did US markets close, what should a Korean
investor know before KR market open" summary, sent once per day (see
.github/workflows/us-market-mood.yml, scheduled ~08:00 KST).

Uses the Claude Code CLI (subscription auth, see claude_cli.py) rather than the Anthropic API
directly -- no per-token billing.
"""
from __future__ import annotations

import os
import sys

from claude_cli import ClaudeCliError, run_claude
from kakao_client import KakaoAuthError, KakaoSendError, send_kakao_message

PROMPT = (
    "오늘 기준 가장 최근 미국 증시 마감 결과(S&P500, 나스닥, 다우 등락률)와 "
    "한국 투자자가 참고할 만한 미국 증시 전반 분위기(주요 이슈, 눈에 띄는 업종/종목 동향)를 "
    "웹 검색으로 확인해서 한국어로 4문장 이내, 180자 이내로 요약해줘"
    "(카카오톡 메시지로 바로 보낼 수 있는 분량). 확인되지 않은 수치는 추측하지 말고, "
    "확인 가능한 정보가 없으면 정확히 '최신 지수 정보 확인 실패'라고만 답해."
)

FAILURE_SENTINEL = "최신 지수 정보 확인 실패"


def main() -> int:
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("CLAUDE_CODE_OAUTH_TOKEN이 설정되지 않았습니다.", file=sys.stderr)
        return 1

    try:
        summary = run_claude(PROMPT) or "요약 생성 실패"
        if summary == FAILURE_SENTINEL:
            summary = run_claude(PROMPT) or "요약 생성 실패"  # WebSearch가 그날따라 못 찾은 경우 1회만 재시도
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
