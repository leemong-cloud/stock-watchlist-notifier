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

2026-09-12 재작성 (사용자 실사용 리포트 대응, 2가지 구조적 결함을 고침):

1. ClaudeCliError 재시도가 없어 KR 워크플로우(notify_kr_watchlist.py)는 이제 살아남는 세션
   한도(429류) 순간 장애에 US만 그대로 실패 메일을 발생시키던 비대칭을 KR과 동일한 20초 대기
   후 1회 재시도 패턴으로 맞춤.
2. 모델의 메타 발언("153자, 4문장 구성 완료..." 같은 자기보고성 서술)이 문구를 매번 바꿔가며
   실제 발송 메시지에 섞여 나오던 문제 -- 2026-09-11 수정은 자연어 지시 + 고정 문자열
   strip뿐이라 표현이 달라지면 못 잡았음(2026-09-12 00:30 UTC 실사용 로그로 재확인:
   "154자, 3문장으로 조건 충족..."). KR의 `summary|verdict|url` pipe 계약과 같은 원리로,
   여기도 <<<PART1>>>/<<<PART2>>>/<<<END>>> 델리미터 계약을 걸어 마커 밖의 텍스트는 파싱
   단계에서 구조적으로 버려지게 했다 -- 표현이 뭐든 상관없이 마커 밖이면 사라진다.

동시에 사용자 요청으로 콘텐츠 방향도 바꿈: 숫자를 욱여넣기보다 분위기/해석 중심으로, 카카오
"기본 텍스트" 템플릿의 공식 200자 상한(developers.kakao.com/docs/latest/ko/message-template/
default 확인) 안에서 한 메시지로는 여유가 부족해 분위기+해석(PART1)과 세부 지수 흐름(PART2)을
카카오톡 2개 메시지로 나눠 보낸다.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

from claude_cli import ClaudeCliError, run_claude
from dedup_guard import already_dispatched_today
from kakao_client import KakaoAuthError, KakaoSendError, send_kakao_message

KST = timezone(timedelta(hours=9))
_WEEKDAY_KR = ["월", "화", "수", "목", "금", "토", "일"]

FAILURE_SENTINEL = "최신 지수 정보 확인 실패"
WORKFLOW_FILE = "us-market-mood.yml"
HEADER_TAG = "[미국장 분위기]"
HEADER_TAG2 = "[미국장 세부흐름]"

PART1_MARKER = "<<<PART1>>>"
PART2_MARKER = "<<<PART2>>>"
END_MARKER = "<<<END>>>"

MAX_ATTEMPTS = 2  # KR(notify_kr_watchlist.py)과 동일하게 "1회만 재시도" 원칙 -- 세션 한도
# 리스크를 키우지 않기 위해 실패 사유(CLI 에러/정보 없음/형식 위반)와 무관하게 총 호출 2회 상한.
RETRY_BACKOFF_SECONDS = 20


def _strip_model_artifacts(text: str) -> str:
    """모델이 지시를 어기고 자체 헤더를 본문에 남겼을 때를 대비한 잔여 방어선. 주 방어선은
    아래 <<<PART1>>>/<<<PART2>>>/<<<END>>> 마커 계약(마커 밖 텍스트는 파싱 단계에서 이미
    버려짐)이며, 이건 마커 안쪽에서도 헤더 문자열이 중복될 경우를 대비한 2차 안전장치일 뿐."""
    return text.replace(HEADER_TAG, "").replace(HEADER_TAG2, "").strip()


def _parse_two_part(raw: str) -> tuple[str, str] | None:
    """<<<PART1>>>...<<<PART2>>>...<<<END>>> 세 마커가 순서대로 모두 있어야 성공. 마커가
    하나라도 없거나 파트가 비면 None -- KR의 parse_verdict_line과 동일한 fail-closed 원칙
    (형식을 못 지키면 통째로 버리고 재시도하지, 부분적으로 봐주지 않는다)."""
    if PART1_MARKER not in raw or PART2_MARKER not in raw or END_MARKER not in raw:
        return None
    try:
        after_p1 = raw.split(PART1_MARKER, 1)[1]
        part1, rest = after_p1.split(PART2_MARKER, 1)
        part2 = rest.split(END_MARKER, 1)[0]
    except ValueError:
        return None
    part1, part2 = _strip_model_artifacts(part1), _strip_model_artifacts(part2)
    if not part1 or not part2:
        return None
    return part1, part2


# 실사용 로그에서 확인된 메타 발언은 항상 2문장짜리 콤보로 나타났다(09-10: "153자, 170자
# 이내로 4문장 구성 완료. 아래가 최종 답변이다. [헤더] 본문..." / 09-12: "154자, 3문장으로 조건
# 충족. 이 문장을 최종 답변으로 출력. 본문...") -- 첫 문장만 지우면 두 번째 메타 문장이 그대로
# 남으므로, 앞에서부터 최대 2문장까지 반복 검사해 지운다. "자,"/"자." 는 일반 문장에도 흔히
# 나오는 조합(예: "~하자, ~")이라 숫자와 같이 나올 때만 신뢰하고, 나머지는 실제 사고 문구에서만
# 보이는 구체적인 구절이라 숫자 없이도 신뢰한다.
_META_DIGIT_HINTS = ("자,", "자.")
_META_PHRASE_HINTS = ("구성 완료", "작성 완료", "조건 충족", "최종 답변", "답변으로 출력")


def _is_meta_sentence(sentence: str) -> bool:
    if any(hint in sentence for hint in _META_PHRASE_HINTS):
        return True
    return any(hint in sentence for hint in _META_DIGIT_HINTS) and any(ch.isdigit() for ch in sentence)


def _salvage_meta_narration(raw: str) -> str | None:
    """마커 계약이 끝까지 깨졌을 때의 최후 수단. 앞에서부터 최대 2문장까지 메타 발언으로
    의심되는 문장을 제거하고 나머지를 보낸다 -- 모델이 매번 표현을 바꾸므로 완벽하지 않은
    휴리스틱이며, 호출부가 WARN 태그로 로그를 남겨 다음 세션에서 재점검할 수 있게 한다."""
    text = _strip_model_artifacts(raw)
    if not text:
        return None
    sentences = text.split(". ")
    stripped = 0
    while len(sentences) > 1 and stripped < 2 and _is_meta_sentence(sentences[0]):
        sentences.pop(0)
        stripped += 1
    return ". ".join(sentences).strip() or None


def build_prompt(now_kst: datetime) -> str:
    weekday = _WEEKDAY_KR[now_kst.weekday()]
    return (
        f"지금은 {now_kst.strftime('%Y-%m-%d %H:%M')} (한국시간, {weekday}요일) 기준이야. "
        "이 시점을 기준으로 가장 최근에 실제로 마감한 미국 증시 세션(S&P500, 나스닥, 다우, "
        "필라델피아 반도체지수(SOX))을 웹 검색으로 확인해줘. 먼저 오늘(미국 동부시간 기준으로 "
        "환산한 날짜)이 미국 증시 휴장일(주말 또는 공휴일)인지 확인하고, 휴장일이면 그 사실과 "
        "함께 실제로 마감이 있었던 가장 최근 날짜를 명시한 뒤 그날의 결과를 요약해 -- 오래된 "
        "뉴스 기사의 날짜를 오늘 마감으로 착각하지 말고 반드시 검색 결과에 실제로 찍힌 날짜를 "
        "확인해서 말해.\n\n"
        "답변은 반드시 아래 형식으로만, 한국어로 작성해(다른 말은 절대 덧붙이지 마):\n"
        f"{PART1_MARKER}\n"
        "전반적인 시장 분위기와 핵심 동인을 서술 중심으로 2~3문장. 숫자를 욱여넣지 말고 꼭 "
        "필요한 것 1~2개만 골라 쓰고, 그 숫자가 한국 투자자에게 어떤 의미인지 해석에 더 비중을 "
        "둬. 190자 이내.\n"
        f"{PART2_MARKER}\n"
        "S&P500/나스닥/다우/필라델피아 반도체지수(SOX) 등락률을 각각 밝히되, 단순 수치 나열이 "
        "아니라 각 수치 옆에 그게 무슨 의미인지 짧은 해석을 붙여서 2~3문장. 190자 이내.\n"
        f"{END_MARKER}\n\n"
        f"{PART1_MARKER}, {PART2_MARKER}, {END_MARKER} 마커 앞뒤로 헤더, 글자수/문장수 확인 "
        "발언, 인사말, 설명 등 그 어떤 다른 텍스트도 넣지 마 -- 마커와 순수 본문 텍스트만 "
        "출력해(호출하는 쪽에서 카카오톡 헤더는 별도로 붙인다). 확인되지 않은 수치는 추측하지 "
        f"말고, 확인 가능한 정보가 없으면 마커 형식 없이 정확히 '{FAILURE_SENTINEL}'라고만 답해."
    )


def _retry_prompt(base_prompt: str) -> str:
    return base_prompt + (
        f"\n\n방금 답변이 형식을 지키지 않았어. 다시 답할 때는 반드시 {PART1_MARKER} 뒤에 "
        f"분위기+해석, {PART2_MARKER} 뒤에 지수별 수치+해석, {END_MARKER}로 끝맺는 형식만 "
        "지켜. 마커 앞뒤로 그 어떤 다른 텍스트도 넣지 마."
    )


def _fetch_two_part_summary(base_prompt: str) -> tuple[str, str] | None:
    """claude CLI 호출 + 파싱. 실패 사유(ClaudeCliError/정보 없음/형식 위반)와 무관하게 총
    호출은 MAX_ATTEMPTS(2)회 상한. 반환값 None은 '오늘은 보낼 내용 없음'(에러 아님).
    ClaudeCliError가 마지막 시도까지 발생하면 그대로 올려보내 main()이 최종 실패로 처리한다."""
    prompt = base_prompt
    raw = ""

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = run_claude(prompt) or ""
        except ClaudeCliError:
            if attempt < MAX_ATTEMPTS:
                print(
                    f"FAIL(claude) {attempt}차 시도, {RETRY_BACKOFF_SECONDS}초 대기 후 재시도",
                    file=sys.stderr,
                )
                time.sleep(RETRY_BACKOFF_SECONDS)
                continue
            raise

        print(f"RAW  {' '.join(raw.split())[:400]}", file=sys.stderr)

        if raw.strip() == FAILURE_SENTINEL:
            if attempt < MAX_ATTEMPTS:
                print("RETRY(info) 확인된 정보 없음 -- 동일 프롬프트로 재시도", file=sys.stderr)
                continue
            return None

        parsed = _parse_two_part(raw)
        if parsed:
            return parsed

        if attempt < MAX_ATTEMPTS:
            print("RETRY(format) 마커 형식 위반 -- 강한 재알림으로 재시도", file=sys.stderr)
            prompt = _retry_prompt(base_prompt)
            continue

    if not raw.strip() or raw.strip() == FAILURE_SENTINEL:
        return None

    salvaged = _salvage_meta_narration(raw)
    if salvaged:
        print(
            "WARN 마커 형식 없이 휴리스틱 정제로 단일 메시지 발송(다음 진단 필요)",
            file=sys.stderr,
        )
        return salvaged, ""
    return None


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
        parsed = _fetch_two_part_summary(prompt)
    except ClaudeCliError as exc:
        print(f"FAIL(claude): {exc}", file=sys.stderr)
        return 1

    if parsed is None:
        print("SKIP 오늘은 보낼 시황 정보가 없습니다 (정상)")
        return 0

    part1, part2 = parsed
    try:
        send_kakao_message(f"{HEADER_TAG}\n{part1}")
        if part2:
            send_kakao_message(f"{HEADER_TAG2}\n{part2}")
    except (KakaoAuthError, KakaoSendError) as exc:
        print(f"FAIL(kakao): {exc}", file=sys.stderr)
        return 1

    if part2:
        print("OK  미국장 분위기 요약 발송 완료 (2건)")
    else:
        print("OK  미국장 분위기 요약 발송 완료 (휴리스틱 정제, 1건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
