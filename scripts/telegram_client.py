"""Telegram Bot API client -- direct REST calls, no SDK (2026-09-23, replaces kakao_client.py).

Kakao's "send to me" memo API required a refresh token that expired roughly every 60 days and had
to be renewed by hand, plus a link-domain allowlist that forced every article URL through a
self-hosted redirect gateway (docs/go.html) and a list-template card format capped at 90-100
chars/2-3 items -- the repeated source of "content gets truncated" complaints. A Telegram bot
token from @BotFather never expires, and sendMessage supports 4096 chars with no per-item count
limit, so none of that machinery is needed here.

Deliberately does NOT use parse_mode (HTML/MarkdownV2): the message bodies are built from
LLM-generated free text (fact/insight/market-mood summaries) that can contain "&", "<", ">" or
markdown-special characters, and an unescaped one of those would make Telegram's API reject the
whole send with a 400 -- the same class of "model didn't follow the format" fragility this project
has repeatedly hardened against elsewhere (see notify_kr_watchlist.py's lenient pipe-format
parsing). Plain text sidesteps escaping entirely: Telegram auto-linkifies bare http(s) URLs on
their own line, so article links still land as tappable links without needing HTML anchors.
"""
from __future__ import annotations

import os

import requests

TELEGRAM_API_BASE = "https://api.telegram.org"

MAX_MESSAGE_CHARS = 4096  # Telegram sendMessage's documented "text" length limit.


class TelegramAuthError(RuntimeError):
    pass


class TelegramSendError(RuntimeError):
    pass


def _trim_sentence_safe(text: str, limit: int) -> str:
    """Trim to `limit` chars, preferring a sentence/word boundary over a mid-word hard cut so a
    truncated message doesn't end mid-syllable. Falls back to a hard cut + ellipsis when no good
    boundary exists within the limit. Ported from kakao_client.py -- at 4096 chars this is a rare
    safety net, not something normal digests are expected to hit."""
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    best = -1
    for punct in (".", "!", "?", "다", "요"):
        idx = window.rfind(punct)
        if idx > best:
            best = idx
    if best >= limit * 0.5:  # only trust a boundary that isn't suspiciously early
        return window[: best + 1]
    space_idx = window.rfind(" ")
    if space_idx >= limit * 0.5:
        return window[:space_idx].rstrip() + "…"
    return window[: limit - 1].rstrip() + "…"


def _get_credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise TelegramAuthError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않았습니다.")
    return token, chat_id


def send_text(message: str) -> None:
    """Send one plain-text message to the configured chat. Raises on any failure (caller decides
    whether to skip-and-continue or hard-fail) -- same contract as kakao_client's send helpers."""
    token, chat_id = _get_credentials()
    text = _trim_sentence_safe(message, MAX_MESSAGE_CHARS)

    resp = requests.post(
        f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",  # a digest can carry several article links --
            # link-preview cards for each would bury the actual text under a wall of thumbnails.
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise TelegramSendError(f"텔레그램 메시지 발송 실패 (HTTP {resp.status_code}): {resp.text[:500]}")
