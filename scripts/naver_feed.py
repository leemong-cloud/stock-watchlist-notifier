"""네이버 모바일 증권 종목 뉴스 피드 -> 최신 기사 후보 목록.

2026-10-12: 모델의 WebSearch(US-only)는 당일 새 기사를 잘 못 올려 한국경제 화장품 업종 기사(한국콜마
언급)를 놓쳤다. 이 피드는 종목별로 최신순이고, 제목에 종목명이 없어도 본문에 언급된 기사를 포함한다.
비공식 공개 엔드포인트라 실패(차단·형식 변경)는 빈 목록으로 돌려 기존 WebSearch 방식으로 폴백한다.
"""
from __future__ import annotations

import html
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
FEED_URL = "https://m.stock.naver.com/api/news/stock/{code}"  # pageSize=100은 400 오류, 30은 정상
ARTICLE_URL = "https://n.news.naver.com/article/{office}/{article}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
MAX_CANDIDATES = 15
MAX_BODY_FETCH = 3  # 제목에 종목명이 없는 후보 중 본문 언급 문장을 뽑아 볼 최대 건수


def _get(url: str, **kw) -> requests.Response:
    last: Exception | None = None
    for _ in range(2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            last = exc
    raise last  # type: ignore[misc]


def parse_feed(groups: list, now_kst: datetime, hours: int) -> list[dict]:
    """피드 JSON(그룹 목록) -> 윈도 내 최신순 후보. 네트워크 없이 테스트할 수 있게 분리."""
    cutoff = now_kst - timedelta(hours=hours)
    seen, out = set(), []
    for g in groups:
        for it in g.get("items", []):
            try:
                when = datetime.strptime(it["datetime"], "%Y%m%d%H%M").replace(tzinfo=KST)
                url = ARTICLE_URL.format(office=it["officeId"], article=it["articleId"])
            except (KeyError, ValueError):
                continue
            if when < cutoff or url in seen:
                continue
            seen.add(url)
            out.append({
                "when": when,
                "title": html.unescape(it.get("title", "")).strip(),
                "office": it.get("officeName", ""),
                "snippet": html.unescape(it.get("body", "")).strip(),
                "url": url,
            })
    out.sort(key=lambda c: c["when"], reverse=True)
    return out[:MAX_CANDIDATES]


def mention_sentence(article_html: str, name: str) -> str:
    """기사 본문(#dic_area)에서 종목명이 들어간 첫 문장(최대 140자). 없으면 ''."""
    m = re.search(r'id="dic_area"[^>]*>(.*?)</article>', article_html, re.S)
    if not m:
        return ""
    text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1))))
    for sent in re.split(r"(?<=[.다])\s+", text):
        if name in sent:
            return sent.strip()[:140]
    return ""


def fetch_recent(symbol: str, name: str, now_kst: datetime, hours: int) -> list[dict] | None:
    """종목의 윈도 내 최신 기사 후보. 조회·해석 실패는 None(기사가 정말 없으면 []) — 호출 쪽이 실패를
    알림으로 알릴 수 있게 구분한다. 제목에 종목명이 없는 상위 후보는 본문 언급 문장을 붙인다."""
    try:
        groups = _get(FEED_URL.format(code=symbol), params={"pageSize": 30, "page": 1}).json()
        cands = parse_feed(groups, now_kst, hours)
    except (requests.RequestException, ValueError, AttributeError, TypeError) as exc:
        print(f"NAVER-FEED fail {symbol} {name}: {exc!r}"[:300], file=sys.stderr)
        return None
    print(f"FEED {symbol} {name}: in-window={len(cands)}", file=sys.stderr)

    fetched = 0
    for c in cands:
        if fetched >= MAX_BODY_FETCH:
            break
        if name in c["title"] or name in c["snippet"]:
            continue
        fetched += 1
        try:
            c["mention"] = mention_sentence(_get(c["url"]).text, name)
        except requests.RequestException:
            c["mention"] = ""
    return cands


def feed_warning(failed: list[str], total: int) -> str:
    """피드 조회에 실패한 종목이 있으면 텔레그램 다이제스트에 붙일 경고 한 줄('' = 경고 없음).
    비공식 API라 형식 변경·차단이 조용히 일어날 수 있어, 그때 사용자가 바로 알아야 한다."""
    if not failed:
        return ""
    who = "전체" if len(failed) == total else f"{len(failed)}/{total}종목"
    return f"⚠️ 네이버 뉴스 피드 조회 실패({who}) — 해당 종목은 웹 검색만으로 확인해 최신 기사가 빠졌을 수 있음"


def format_candidates(cands: list[dict]) -> str:
    lines = []
    for c in cands:
        line = f"- [{c['when']:%m-%d %H:%M}] {c['title']} ({c['office']}) {c['url']}"
        if c["snippet"]:
            line += f"\n    요약: {c['snippet']}"
        if c.get("mention"):
            line += f"\n    본문 언급: {c['mention']}"
        lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":  # 수동 확인: python scripts/naver_feed.py 161890 한국콜마
    code, nm = sys.argv[1], sys.argv[2]
    got = fetch_recent(code, nm, datetime.now(KST), 72)
    print("(조회 실패)" if got is None else format_candidates(got) or "(후보 없음)")
