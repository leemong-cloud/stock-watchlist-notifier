"""Daily KR watchlist news digest -> KakaoTalk (single combined message).

Reads watchlist.json (symbol+name only -- committed by the opencode project's
Publish-Watchlist.ps1, which derives it from the user's actual TOSS holdings but deliberately
strips weight/quantity/avgPrice before it ever reaches this public repo).

For each stock, asks Claude (Haiku, via the Claude Code CLI's built-in WebSearch tool -- see
claude_cli.py) for material news from the last 24 hours (anchored to the run's actual KST
clock, not a vague "24~48h" window), reduced to a one-line summary + a 호재/부정/중립 verdict.
No numeric claim is accepted without the model having actually searched for it. A stock with no
material news returns the NONE sentinel and is silently omitted from the digest -- the user
asked not to spell out "no news" per stock.

All per-stock results are collected first, then sent as ONE KakaoTalk message (not one message
per stock) so a quiet morning doesn't mean 8 near-empty notifications. Note: Kakao's default
"text" template caps around ~200 chars (see kakao_client.MAX_MESSAGE_CHARS) -- if most stocks
have material news on the same day the digest can hit that ceiling and get truncated with "...".
Acceptable first-cut behavior; revisit (e.g. splitting into a second message) if it recurs.

Runs on a daily schedule regardless of weekday/holiday (see .github/workflows/kr-watchlist-news.yml,
scheduled ~06:20 KST so the ~7min per-stock WebSearch pass lands before the 06:30 send target) --
a quiet digest on a non-trading day is harmless, and a KR market holiday calendar would be
overengineering for this use case.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_cli import ClaudeCliError, run_claude
from kakao_client import KakaoAuthError, KakaoSendError, send_kakao_message

WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "watchlist.json"
KST = timezone(timedelta(hours=9))
NONE_SENTINEL = "NONE"


def summarize_stock(symbol: str, name: str, now_kst: datetime) -> str:
    prompt = (
        f"지금은 {now_kst.strftime('%Y-%m-%d %H:%M')} (한국시간) 기준이야. "
        f"{name}({symbol}) 관련, 이 시점으로부터 지난 24시간 이내에 나온 국내 뉴스 중 "
        "투자자에게 중요한 것만 웹 검색으로 확인해줘. 중요한 뉴스가 있으면 다른 말 없이 "
        "정확히 이 형식으로만 답해: <한 줄 요약(30자 이내)>|<호재 또는 부정 또는 중립 중 하나>. "
        f"확인된 중요 뉴스가 전혀 없으면 절대 추측하지 말고 정확히 '{NONE_SENTINEL}'이라고만 답해."
    )
    return run_claude(prompt) or NONE_SENTINEL


def parse_verdict_line(raw: str) -> tuple[str, str] | None:
    """Returns (summary, verdict), or None if the stock had no material news."""
    text = raw.strip()
    if text == NONE_SENTINEL or not text:
        return None
    if "|" in text:
        summary, _, verdict = text.partition("|")
        return summary.strip(), verdict.strip()
    return text, "중립"  # model didn't follow the format -- keep the content, guess neutral


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

    test_date = os.environ.get("TEST_DATE_KST", "").strip()
    if test_date:
        now_kst = datetime.strptime(test_date, "%Y-%m-%d").replace(
            hour=23, minute=59, tzinfo=KST
        )
        print(f"TEST_DATE_KST 오버라이드: {test_date} 기준으로 실행합니다 (실 운영 스케줄에는 영향 없음)")
    else:
        now_kst = datetime.now(KST)
    digest_lines = []
    failures = []
    for stock in stocks:
        symbol, name = stock["symbol"], stock["name"]
        try:
            raw = summarize_stock(symbol, name, now_kst)
        except ClaudeCliError as exc:
            print(f"FAIL(claude) {symbol} {name}: {exc}", file=sys.stderr)
            failures.append(symbol)
            continue

        parsed = parse_verdict_line(raw)
        if parsed is None:
            print(f"SKIP {symbol} {name}: 특이 뉴스 없음")
            continue
        summary, verdict = parsed
        digest_lines.append(f"{name}: {summary} ({verdict})")
        print(f"OK  {symbol} {name}: {verdict}")

    body = "\n".join(digest_lines) if digest_lines else "특이 뉴스 없음"
    message = f"[관심종목 뉴스 {now_kst.strftime('%m/%d')}]\n{body}"
    try:
        send_kakao_message(message)
    except (KakaoAuthError, KakaoSendError) as exc:
        print(f"FAIL(kakao) 다이제스트 발송 실패: {exc}", file=sys.stderr)
        return 1

    if failures:
        print(f"{len(failures)}/{len(stocks)}건 조회 실패(다이제스트에서 누락됨): {failures}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
