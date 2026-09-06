"""Daily KR watchlist news summary -> KakaoTalk.

Reads watchlist.json (symbol+name only -- committed by the opencode project's
Publish-Watchlist.ps1, which derives it from the user's actual TOSS holdings but deliberately
strips weight/quantity/avgPrice before it ever reaches this public repo).

For each stock, asks Claude (Haiku, via the Claude Code CLI's built-in WebSearch tool -- see
claude_cli.py) for the most important recent news and market reaction, in a
KakaoTalk-message-sized Korean summary. No numeric claim is accepted without the model having
actually searched for it -- the prompt explicitly tells the model to say "특이 뉴스 없음" rather
than guess.

Runs on a daily schedule regardless of weekday/holiday (see .github/workflows/kr-watchlist-news.yml)
-- a quiet "no news" message on a non-trading day is harmless, and a KR market holiday calendar
would be overengineering for this use case.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from claude_cli import ClaudeCliError, run_claude
from kakao_client import KakaoAuthError, KakaoSendError, send_kakao_message

WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "watchlist.json"
SEND_DELAY_SECONDS = 3  # be gentle with Kakao's per-app rate limit


def summarize_stock(symbol: str, name: str) -> str:
    prompt = (
        f"{name}({symbol}) 관련 최근 24~48시간 이내 국내 뉴스 중 투자자에게 중요한 것을 "
        "웹 검색으로 확인해줘. 찾은 뉴스가 있으면 핵심 내용과 시장/주가 반응을 한국어 "
        "3문장 이내, 180자 이내로 요약해줘(카카오톡 메시지로 바로 보낼 수 있는 분량). "
        "확인된 중요 뉴스가 없으면 절대 추측하지 말고 정확히 '특이 뉴스 없음'이라고만 답해."
    )
    return run_claude(prompt) or "특이 뉴스 없음"


def main() -> int:
    if not WATCHLIST_PATH.exists():
        print(f"watchlist.json이 없습니다: {WATCHLIST_PATH}", file=sys.stderr)
        return 1

    watchlist = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    stocks = watchlist.get("stocks", [])
    if not stocks:
        print("watchlist.json에 종목이 없습니다 -- 아무 것도 보내지 않습니다.")
        return 0

    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        print("CLAUDE_CODE_OAUTH_TOKEN이 설정되지 않았습니다.", file=sys.stderr)
        return 1

    failures = []
    for i, stock in enumerate(stocks):
        symbol, name = stock["symbol"], stock["name"]
        try:
            summary = summarize_stock(symbol, name)
            message = f"[관심종목] {name}({symbol})\n{summary}"
            send_kakao_message(message)
            print(f"OK  {symbol} {name}")
        except (KakaoAuthError, KakaoSendError) as exc:
            print(f"FAIL(kakao) {symbol} {name}: {exc}", file=sys.stderr)
            failures.append(symbol)
        except ClaudeCliError as exc:
            print(f"FAIL(claude) {symbol} {name}: {exc}", file=sys.stderr)
            failures.append(symbol)
        if i < len(stocks) - 1:
            time.sleep(SEND_DELAY_SECONDS)

    if failures:
        print(f"{len(failures)}/{len(stocks)}건 실패: {failures}", file=sys.stderr)
        # Exit non-zero so the Action run is visibly flagged, but only after attempting every stock.
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
