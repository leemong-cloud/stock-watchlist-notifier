"""Daily US market mood summary -> KakaoTalk.

Not tied to any specific holding -- a general "how did US markets close, what should a Korean
investor know before KR market open" summary, sent once per day (see
.github/workflows/us-market-mood.yml, scheduled ~08:00 KST).
"""
from __future__ import annotations

import os
import sys

from anthropic import Anthropic

from kakao_client import KakaoAuthError, KakaoSendError, send_kakao_message

MODEL = "claude-haiku-4-5-20251001"

PROMPT = (
    "오늘 기준 가장 최근 미국 증시 마감 결과(S&P500, 나스닥, 다우 등락률)와 "
    "한국 투자자가 참고할 만한 미국 증시 전반 분위기(주요 이슈, 눈에 띄는 업종/종목 동향)를 "
    "웹 검색으로 확인해서 한국어로 4문장 이내, 180자 이내로 요약해줘"
    "(카카오톡 메시지로 바로 보낼 수 있는 분량). 확인되지 않은 수치는 추측하지 말 것."
)


def main() -> int:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ANTHROPIC_API_KEY가 설정되지 않았습니다.", file=sys.stderr)
        return 1
    client = Anthropic(api_key=api_key)

    response = client.messages.create(
        model=MODEL,
        max_tokens=500,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}],
        messages=[{"role": "user", "content": PROMPT}],
    )
    text_blocks = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    summary = "\n".join(text_blocks).strip() or "요약 생성 실패"

    try:
        send_kakao_message(f"[미국장 분위기]\n{summary}")
    except (KakaoAuthError, KakaoSendError) as exc:
        print(f"FAIL(kakao): {exc}", file=sys.stderr)
        return 1

    print("OK  미국장 분위기 요약 발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
