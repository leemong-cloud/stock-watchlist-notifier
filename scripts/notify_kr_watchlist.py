"""Daily KR watchlist news digest -> Telegram.

Reads watchlist.json (symbol+name only -- committed by the opencode project's
Publish-Watchlist.ps1, which derives it from the user's actual TOSS holdings but deliberately
strips weight/quantity/avgPrice before it ever reaches this public repo).

For each stock, asks Claude (via the Claude Code CLI's built-in WebSearch tool -- see
claude_cli.py) for material news from the last 24 hours (anchored to the run's actual KST
clock, not a vague "24~48h" window -- except Monday runs, which widen to 72h so weekend news
isn't silently missed, see lookback_hours_for) across BOTH domestic and foreign (외신) coverage.
The model is told to run multiple distinct searches (not stop at the first hit), gather several
candidate articles, tally how many are 호재/부정/중립, and pick the single most CRITICAL one as
the representative article -- ranked by real-world price impact (regulatory/legal action,
recalls, protests at a business site, earnings surprises, etc.) over indirect analyst-note
chatter, not by whichever article the search happened to surface first or was published earliest
(2026-09-23: user reported the digest kept surfacing the oldest/first-found article for a stock
even when a same-day, more impactful event existed -- e.g. a residents' protest outranking a
sell-side target-price note from the day before). Reduced to a one-line summary + verdict + the
URL of that representative article. No numeric claim is accepted without the model having
actually searched for it, and no item is built without a real URL -- if Claude can't pin down a
confirmed article link, that's treated the same as "no material news" (NONE_SENTINEL) rather than
guessing a link. A stock with no material news is silently omitted from the digest -- the user
asked not to spell out "no news" per stock. The model's raw per-stock response is always printed
to stderr (_log_raw) so a day where everything gets skipped can actually be diagnosed after the
fact (2026-09-06: 8/8 stocks skipped with zero visibility into why -- this was previously
unrecoverable).

All per-stock results are collected first, then rendered as one Telegram message with each
stock's title/fact/insight and its article URL on its own line (Telegram auto-linkifies a bare
URL, so no card/link-domain machinery is needed -- see telegram_client.py). Unlike Kakao's list
template, Telegram has no min/max item-count constraint per message, only a 4096-char budget
(telegram_client.MAX_MESSAGE_CHARS); `_pack_items_by_budget` only splits into multiple messages
if the combined digest would actually exceed that (not expected at this watchlist's size). A
quiet day with zero items still sends a plain "특이 뉴스 없음" message.

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
from telegram_client import MAX_MESSAGE_CHARS, TelegramAuthError, TelegramSendError, send_text

WORKFLOW_FILE = "kr-watchlist-news.yml"

WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "watchlist.json"
KST = timezone(timedelta(hours=9))
NONE_SENTINEL = "NONE"


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
        "뉴스를 웹 검색으로 확인해줘. 검색어를 바꿔가며 최소 2회 이상 검색해서 후보 기사를 "
        "여러 건 모아봐 -- 처음 걸린 결과 하나만 보고 멈추지 마라. 모은 기사들을 각각 "
        "호재/부정/중립으로 분류하고 각 개수를 세어라. "
        "그중에서 대표로 삼을 기사 1건은 '가장 critical한' 것으로 골라라: 주가에 직접적·구체적 "
        "영향을 줄 수 있는 사건(규제·제재, 소송, 리콜, 대규모 계약 체결/파기, 사업장 관련 "
        "시위·갈등, 실적 서프라이즈, 경영진 이슈 등)을 증권사 리포트·목표주가 조정처럼 "
        "간접적이고 해석 여지가 있는 뉴스보다 우선해라. 단순히 먼저 검색된 기사나 가장 이른 "
        "시각에 발행된 기사를 그대로 대표로 고르지 마라 -- 반드시 투자자 임팩트 기준으로 "
        "재평가해라. 임팩트가 비슷하면 더 최근 기사를 우선해라. "
        "투자자에게 중요한 뉴스가 하나라도 있으면 다른 말 없이 정확히 이 형식으로만 답해: "
        "<호재로 분류한 기사 개수(숫자만)>|<부정으로 분류한 기사 개수(숫자만)>|<중립으로 분류한 "
        "기사 개수(숫자만)>|<사실: 대표 기사에서 무슨 일이 있었는지 완결된 한 문장, 25자 이내, "
        "꼭 필요한 숫자 1개 정도만>|<해석: 그게 투자자에게 왜 중요한지/어떤 영향인지 완결된 한 문장, "
        "45자 이내>|<대표 기사의 호재 또는 부정 또는 중립 중 하나>|<대표 기사의 정확한 URL(마크다운 "
        "링크 문법 금지, 순수 URL 문자열만)>. "
        "사실·해석은 반드시 한국어로 써라. 외신·영문 기사도 한국어로 번역해서 요약하고, 영어 문장이나 "
        "영어 기사 제목을 그대로 쓰지 마라(회사명·티커 제외). 각 문장은 글자 수 안에서 끝까지 완결해야 한다. "
        "URL은 웹 검색으로 실제 확인한 대표 기사의 링크여야 하며 절대 추측하거나 지어내지 마라. "
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
        "정확히 '<호재수>|<부정수>|<중립수>|<사실>|<해석>|<판정>|<URL>' 한 줄만(한국어), "
        f"또는 뉴스가 없으면 정확히 '{NONE_SENTINEL}' 한 단어만 출력해."
    )
    return run_claude(prompt) or NONE_SENTINEL


def _digest_block(item: dict) -> str:
    lines = [item["title"], item["fact"]]
    if item["insight"]:
        lines.append(f"↳ {item['insight']}")
    lines.append(item["link_url"])
    return "\n".join(lines)


def _pack_items_by_budget(items: list, header: str) -> list[list]:
    """Groups stock items into as few messages as possible while keeping each rendered message
    under MAX_MESSAGE_CHARS. Unlike Kakao's list template (2-3 items, never 1), Telegram has no
    item-count constraint -- this is a plain cumulative-length pack, not expected to ever split
    at this watchlist's size (8 stocks x ~150 chars is well under the 4096 budget)."""
    groups: list[list] = []
    current: list = []
    current_len = len(header)
    for item in items:
        block_len = len(_digest_block(item)) + 2  # +2 for the blank-line separator
        if current and current_len + block_len > MAX_MESSAGE_CHARS:
            groups.append(current)
            current = []
            current_len = len(header)
        current.append(item)
        current_len += block_len
    if current:
        groups.append(current)
    return groups


def _log_raw(symbol: str, name: str, raw: str) -> None:
    """Print the model's raw response to the workflow log (stderr) so a SKIP can be told apart
    from a real "no news" vs a format/grounding failure -- previously nothing was logged, so a
    day where every stock skipped was completely unverifiable (2026-09-06)."""
    flat = " ".join(raw.split())
    print(f"RAW  {symbol} {name}: {flat[:300]}", file=sys.stderr)


VERDICTS = ("호재", "부정", "중립")
VERDICT_EMOJI = {"호재": "🟢", "부정": "🔴", "중립": "⚪"}


def _has_hangul(text: str) -> bool:
    return any("가" <= ch <= "힣" for ch in text)


def parse_verdict_line(raw: str) -> tuple[int | None, int | None, int | None, str, str, str, str] | None:
    """Returns (good_count, bad_count, neutral_count, fact, insight, verdict, url), or None if the
    stock had no material news, no confirmed article URL (an item without a real URL is dropped
    rather than sent unlinked or with a guessed link), or the summary came back without any
    Korean (English-only -> None so the caller's strict retry kicks in).

    Format is '<good>|<bad>|<neutral>|<fact>|<insight>|<verdict>|<url>' (2026-09-23: added the
    3 leading counts so the digest can show how many 호재/부정/중립 articles were actually found,
    not just the one picked as representative -- see _build_stock_prompt). The 3 count fields are
    peeled off the front first since they're pure digits, which is unambiguous even if the
    free-text fact/insight later contain a stray "|". If the leading fields aren't all digits
    (model replied in the older 4-field '<fact>|<insight>|<verdict>|<url>' format, or drifted),
    falls back to the legacy parse with counts as None rather than dropping the item entirely --
    same lenient philosophy as before (2026-09-08 lessons: NONE-ish variants like "NONE.",
    "none", a stray "|" inside the text recovered via rsplit, and the legacy 3-field
    '<summary>|<verdict>|<url>' treated as fact with empty insight)."""
    text = raw.strip()
    if not text or text.rstrip(".!").upper() == NONE_SENTINEL:
        return None

    good = bad = neutral = None
    parts = text.split("|", 3)
    if len(parts) == 4 and all(p.strip().isdigit() for p in parts[:3]):
        good, bad, neutral = (int(p.strip()) for p in parts[:3])
        text = parts[3]

    if text.count("|") < 2:
        return None  # model didn't follow the field format -- can't build a linked item
    head, verdict, url = (p.strip() for p in text.rsplit("|", 2))
    if not url.startswith("http"):
        return None
    if "|" in head:
        fact, insight = (p.strip() for p in head.split("|", 1))
    else:
        fact, insight = head, ""
    if not _has_hangul(fact + insight):
        return None
    if verdict not in VERDICTS:
        verdict = "중립"
    return good, bad, neutral, fact, insight, verdict, url


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
        good, bad, neutral, fact, insight, verdict, url = parsed
        # 2026-09-23: 개수(good/bad/neutral)를 구했으면 제목에 이모지로 압축해 호재/부정/중립
        # 검색 건수를 함께 보여준다. 구 포맷 응답(개수 없음)은 기존 [verdict] 태그로 자연 폴백.
        if good is not None:
            title = (
                f"{VERDICT_EMOJI[verdict]} {name} "
                f"{VERDICT_EMOJI['호재']}{good}{VERDICT_EMOJI['부정']}{bad}{VERDICT_EMOJI['중립']}{neutral}"
            )
        else:
            title = f"{VERDICT_EMOJI[verdict]} {name} [{verdict}]"
        items.append(
            {
                "title": title,
                "fact": fact,
                "insight": insight,
                "verdict": verdict,
                "link_url": url,
            }
        )
        print(f"OK  {symbol} {name}: {verdict} (호재{good} 부정{bad} 중립{neutral}) url={url}")

    base_header = f"[관심종목 뉴스 {now_kst.strftime('%m/%d')}]"
    try:
        if not items:
            send_text(f"{base_header}\n특이 뉴스 없음")
        else:
            groups = _pack_items_by_budget(items, base_header)
            for idx, group in enumerate(groups, start=1):
                header = base_header if len(groups) == 1 else f"{base_header} ({idx}/{len(groups)})"
                send_text("\n\n".join([header] + [_digest_block(item) for item in group]))
    except (TelegramAuthError, TelegramSendError) as exc:
        print(f"FAIL(telegram) 다이제스트 발송 실패: {exc}", file=sys.stderr)
        return 1

    if failures:
        print(f"{len(failures)}/{len(stocks)}건 조회 실패(다이제스트에서 누락됨): {failures}", file=sys.stderr)
        if not items:
            # 성공한 종목이 하나도 없다 -- 발송 자체가 사실상 빈 메시지였을 것이므로 진짜
            # 실패로 취급한다.
            return 1
        # 2026-09-11 사용자 리포트: 일부 종목 조회만 실패해도 매번 워크플로가 "Failed"로 끝나
        # GitHub Actions가 매일 실패 메일을 보냈다 -- 정작 다이제스트는 성공한 종목만 모아
        # 정상 발송됐는데도 매번 거짓 실패 알림이 온 것. 실패 종목은 위 로그에 이미 남겼으니
        # (진단 가능) 부분 실패로 전체 워크플로를 실패 처리하지 않는다.
    return 0


if __name__ == "__main__":
    sys.exit(main())
