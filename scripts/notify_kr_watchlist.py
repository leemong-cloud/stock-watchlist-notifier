"""Daily KR watchlist news digest -> KakaoTalk (Kakao "list" template, one item per stock).

Reads watchlist.json (symbol+name only -- committed by the opencode project's
Publish-Watchlist.ps1, which derives it from the user's actual TOSS holdings but deliberately
strips weight/quantity/avgPrice before it ever reaches this public repo).

For each stock, asks Claude (via the Claude Code CLI's built-in WebSearch tool -- see
claude_cli.py) for material news from the last 24 hours (anchored to the run's actual KST
clock, not a vague "24~48h" window -- except Monday runs, which widen to 72h so weekend news
isn't silently missed, see lookback_hours_for) across BOTH domestic and foreign (외신) coverage,
reduced to a one-line summary + a 호재/부정/중립 verdict + the URL of one representative article
actually found via search. No numeric claim is accepted without the model having actually
searched for it, and no item is built without a real URL -- if Claude can't pin down a confirmed
article link, that's treated the same as "no material news" (NONE_SENTINEL) rather than guessing
a link. A stock with no material news is silently omitted from the digest -- the user asked not
to spell out "no news" per stock. The model's raw per-stock response is always printed to stderr
(_log_raw) so a day where everything gets skipped can actually be diagnosed after the fact
(2026-09-06: 8/8 stocks skipped with zero visibility into why -- this was previously unrecoverable).

All per-stock results are collected first. Kakao's "list" default template lets each item carry
its own link (unlike "text", which supports only one link per whole message), so tapping a
stock's row opens that stock's own article -- but Kakao caps a list message at 3 items, so
results are chunked into groups of <=3 and sent as one KakaoTalk message per chunk (worst case,
with all 8 watchlist stocks having news, is 3 messages). A quiet day with zero items still sends
the old plain "text" message ("특이 뉴스 없음") since there's nothing to link.

Runs on a daily schedule regardless of weekday/holiday (see .github/workflows/kr-watchlist-news.yml,
scheduled ~05:50 KST to leave margin for GitHub Actions' own `schedule`-trigger queuing delay
before the 06:30 send target -- see README) -- a quiet digest on a non-trading day is harmless,
and a KR market holiday calendar would be overengineering for this use case.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claude_cli import ClaudeCliError, run_claude
from dedup_guard import already_dispatched_today
from kakao_client import (
    KakaoAuthError,
    KakaoSendError,
    build_redirect_link,
    get_access_token,
    send_kakao_list_message,
    send_kakao_message,
    send_text,
)

WORKFLOW_FILE = "kr-watchlist-news.yml"

WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "watchlist.json"
KST = timezone(timedelta(hours=9))
NONE_SENTINEL = "NONE"
LIST_CHUNK_MAX = 3  # Kakao "list" template supports 2-3 content items per message, never 1
LIST_CHUNK_MIN = 2
BUTTON_TITLE = "알림 봇 정보"  # relabels Kakao's un-removable default button (still opens
# header_link_url, i.e. this repo) so it doesn't read like a link to the article itself


def lookback_hours_for(now_kst: datetime) -> int:
    """Monday runs need to look back across the weekend, not just 24h -- a plain 24h window on a
    Monday morning run only reaches back into Sunday and silently misses Saturday/Friday-evening
    news that accumulated while the market was closed. No KR holiday calendar involved (the
    project deliberately avoids that, see README) -- this is a pure day-of-week fact."""
    return 72 if now_kst.weekday() == 0 else 24


def _build_stock_prompt(symbol: str, name: str, now_kst: datetime) -> str:
    hours = lookback_hours_for(now_kst)
    window_note = (
        f"오늘이 월요일이라 주말 동안 나온 뉴스까지 포함해서 지난 {hours}시간(주말 포함) 이내"
        if hours != 24
        else f"지난 {hours}시간 이내"
    )
    return (
        f"지금은 {now_kst.strftime('%Y-%m-%d %H:%M')} (한국시간, {['월','화','수','목','금','토','일'][now_kst.weekday()]}요일) 기준이야. "
        f"{name}({symbol}) 관련, 이 시점으로부터 {window_note}에 나온 국내 뉴스와 해외(외신) "
        "뉴스를 모두 웹 검색으로 확인해서, 그중 투자자에게 중요한 것만 알려줘. 중요한 뉴스가 있으면 "
        "다른 말 없이 정확히 이 형식으로만 답해: <기사 핵심 내용 요약. 반드시 두 문장으로 써: "
        "①무슨 일이 있었는지 핵심만 한 문장(불필요한 정밀 수치는 생략, 꼭 필요한 숫자 1개 정도만), "
        "②그게 투자자에게 왜 중요한지/어떤 영향인지에 대한 해석을 ①보다 더 비중 있게 한 문장. "
        "전체 45~55자 이내로 짧고 압축해서, 카카오톡 카드에 2줄로 보이는 분량>|"
        "<호재 또는 부정 또는 중립 중 하나>|<그 뉴스를 확인한 대표 기사 1건의 정확한 URL(마크다운 "
        "링크 문법 금지, 순수 URL 문자열만)>. "
        "URL은 웹 검색으로 실제 확인한 기사의 링크여야 하며 절대 추측하거나 지어내지 마라. "
        f"확인된 중요 뉴스가 없거나, 있어도 확실한 원문 URL을 확인 못 했으면 절대 추측하지 말고 "
        f"정확히 '{NONE_SENTINEL}'이라고만 답해."
    )


def summarize_stock(symbol: str, name: str, now_kst: datetime) -> str:
    return run_claude(_build_stock_prompt(symbol, name, now_kst)) or NONE_SENTINEL


def retry_stock_strict(symbol: str, name: str, now_kst: datetime) -> str:
    """Re-asks with an added strict-format reminder -- used only when the first response wasn't a
    clean NONE_SENTINEL match but also didn't parse as 3 valid pipe-separated fields, i.e. the
    model most likely had real news but drifted from the required format (2026-09-08: this was
    silently dropping stocks like a format violation would look identical to genuine "no news")."""
    prompt = _build_stock_prompt(symbol, name, now_kst) + (
        " 방금 전 답변 형식이 올바르지 않았어. 다시 답할 때는 절대 다른 말을 덧붙이지 말고 "
        "정확히 '<요약>|<판정>|<URL>' 한 줄만, 또는 뉴스가 없으면 정확히 "
        f"'{NONE_SENTINEL}' 한 단어만 출력해."
    )
    return run_claude(prompt) or NONE_SENTINEL


def chunk_for_kakao_list(items: list) -> list[list]:
    """Splits into groups of LIST_CHUNK_MIN..LIST_CHUNK_MAX -- Kakao's list template rejects a
    group of 1 (confirmed via official docs: "2개 이상 필수, 최대 3개"), so a naive fixed-size
    chunk (e.g. 4 items -> [3, 1]) would make the last message fail to send. Instead this borrows
    one item back from the last full group whenever the remainder would be exactly 1 (e.g. 4 ->
    [2, 2], 7 -> [3, 2, 2])."""
    n = len(items)
    chunks = []
    i = 0
    while n - i > LIST_CHUNK_MAX:
        size = LIST_CHUNK_MAX - 1 if (n - i - LIST_CHUNK_MAX) == 1 else LIST_CHUNK_MAX
        chunks.append(items[i : i + size])
        i += size
    if n - i > 0:
        chunks.append(items[i:])
    return chunks


def _log_raw(symbol: str, name: str, raw: str) -> None:
    """Print the model's raw response to the workflow log (stderr) so a SKIP can be told apart
    from a real "no news" vs a format/grounding failure -- previously nothing was logged, so a
    day where every stock skipped was completely unverifiable (2026-09-06)."""
    flat = " ".join(raw.split())
    print(f"RAW  {symbol} {name}: {flat[:300]}", file=sys.stderr)


def parse_verdict_line(raw: str) -> tuple[str, str, str] | None:
    """Returns (summary, verdict, url), or None if the stock had no material news or no
    confirmed article URL (an item without a real URL can't go into the Kakao list template, so
    it's dropped rather than sent unlinked or with a guessed link).

    Lenient on two fronts that caused real stocks to be silently dropped (2026-09-08): the model
    sometimes answers a NONE-ish variant ("NONE.", "none") instead of the exact sentinel, and
    sometimes the summary field itself contains a stray "|" pushing the split past 3 parts -- in
    that case the last two parts are still reliably verdict/url, so they're recovered via rsplit
    instead of discarding the whole item."""
    text = raw.strip()
    if not text or text.rstrip(".!").upper() == NONE_SENTINEL:
        return None
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        return None  # model didn't follow the 3-field format -- can't build a linked item
    if len(parts) == 3:
        summary, verdict, url = parts
    else:
        head, verdict, url = text.rsplit("|", 2)
        summary, verdict, url = head.strip(), verdict.strip(), url.strip()
    if not url.startswith("http"):
        return None
    return summary, verdict, url


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

    if already_dispatched_today(WORKFLOW_FILE, now_kst):
        return 0

    items = []
    failures = []
    for stock in stocks:
        symbol, name = stock["symbol"], stock["name"]
        try:
            raw = summarize_stock(symbol, name, now_kst)
        except ClaudeCliError as exc:
            # 2026-09-08~11 실전 로그에서 반복 확인: 8종목을 연속 호출하다 보면 뒤쪽 2~5종목이
            # exit 1(빈 stderr, 즉시 실패) 또는 180초 타임아웃으로 한꺼번에 무너지는 패턴이
            # 거의 매일 나타남 -- 순간적인 레이트리밋/사용량 한도로 추정. 20초 대기 후 1회만
            # 재시도해 짧은 버스트성 제한이면 회복하도록 한다.
            print(f"FAIL(claude) {symbol} {name}: {exc}", file=sys.stderr)
            print(f"RETRY(claude) {symbol} {name}: 20초 대기 후 1회 재시도", file=sys.stderr)
            time.sleep(20)
            try:
                raw = summarize_stock(symbol, name, now_kst)
            except ClaudeCliError as exc2:
                print(f"FAIL(claude-2nd) {symbol} {name}: {exc2}", file=sys.stderr)
                failures.append(symbol)
                continue
        _log_raw(symbol, name, raw)

        parsed = parse_verdict_line(raw)
        is_honest_none = raw.strip().rstrip(".!").upper() == NONE_SENTINEL
        if parsed is None and raw.strip() and not is_honest_none:
            # Didn't parse but also wasn't an honest "no news" -- likely a format slip rather
            # than a real absence of news, so give the model one more chance before giving up.
            try:
                retry_raw = retry_stock_strict(symbol, name, now_kst)
            except ClaudeCliError as exc:
                print(f"FAIL(claude-retry) {symbol} {name}: {exc}", file=sys.stderr)
                retry_raw = ""
            if retry_raw:
                _log_raw(f"{symbol}(retry)", name, retry_raw)
                parsed = parse_verdict_line(retry_raw)
        if parsed is None:
            print(f"SKIP {symbol} {name}: 특이 뉴스 없음 (또는 URL 미확보)")
            continue
        summary, verdict, url = parsed
        items.append(
            {
                # 2026-09-11: verdict를 description 끝이 아니라 title 앞쪽에 붙인다 -- 공식
                # Kakao 문서 확인 결과 list 템플릿은 title+description 합쳐 최대 4줄까지만
                # 보이므로, description 끝에 붙이면 내용이 길 때 (verdict) 태그부터 잘려나가
                # "호재/악재 구분자가 안 보인다"는 문제가 실제로 발생했다. title은 짧아서
                # 잘릴 위험이 거의 없으므로 여기 붙이는 게 훨씬 안전하다.
                "title": f"{name} [{verdict}]",
                "description": summary,
                "link_url": build_redirect_link(url),
            }
        )
        print(f"OK  {symbol} {name}: {verdict} url={url}")

    base_header = f"[관심종목 뉴스 {now_kst.strftime('%m/%d')}]"
    try:
        if not items:
            send_kakao_message(f"{base_header}\n특이 뉴스 없음")
        elif len(items) == 1:
            # Kakao's list template rejects a single content item -- fall back to the text
            # template, which still carries the one article's own link.
            only = items[0]
            send_text(
                get_access_token(),
                f"{base_header}\n{only['title']}: {only['description']}",
                link_url=only["link_url"],
            )
        else:
            chunks = chunk_for_kakao_list(items)
            for idx, chunk in enumerate(chunks, start=1):
                header = base_header if len(chunks) == 1 else f"{base_header} ({idx}/{len(chunks)})"
                send_kakao_list_message(header, chunk, button_title=BUTTON_TITLE)
    except (KakaoAuthError, KakaoSendError) as exc:
        print(f"FAIL(kakao) 다이제스트 발송 실패: {exc}", file=sys.stderr)
        return 1

    if failures:
        print(f"{len(failures)}/{len(stocks)}건 조회 실패(다이제스트에서 누락됨): {failures}", file=sys.stderr)
        if not items:
            # 성공한 종목이 하나도 없다 -- 카카오 발송 자체가 사실상 빈 메시지였을 것이므로
            # 진짜 실패로 취급한다.
            return 1
        # 2026-09-11 사용자 리포트: 일부 종목 조회만 실패해도 매번 워크플로가 "Failed"로 끝나
        # GitHub Actions가 매일 실패 메일을 보냈다 -- 정작 카카오 다이제스트는 성공한 종목만
        # 모아 정상 발송됐는데도 매번 거짓 실패 알림이 온 것. 실패 종목은 위 로그에 이미
        # 남겼으니(진단 가능) 부분 실패로 전체 워크플로를 실패 처리하지 않는다.
    return 0


if __name__ == "__main__":
    sys.exit(main())
