"""One-off local helper to discover a Telegram chat_id (2026-09-23, replaces
kakao_get_refresh_token.py's OAuth dance -- Telegram bots don't need an OAuth exchange).

Setup, done once:
    1. Message @BotFather on Telegram, send `/newbot`, follow the prompts -- it gives you a bot
       token (looks like "123456789:AAExampleTokenHere").
    2. Open a chat with your new bot in the Telegram app and send it any message (e.g. "/start")
       -- a bot can't message you first, so this step is required before chat_id can be looked up.
    3. Run this script with that token to print the chat_id:
           TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenHere python scripts/telegram_get_chat_id.py
       Register both TELEGRAM_BOT_TOKEN and the printed chat_id as GitHub Actions secrets
       (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Unlike Kakao's refresh token, the bot token does not
       expire, so this is a true one-time setup step.
"""
from __future__ import annotations

import os
import sys

import requests

TELEGRAM_API_BASE = "https://api.telegram.org"


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token and len(sys.argv) > 1:
        token = sys.argv[1].strip()
    if not token:
        print("TELEGRAM_BOT_TOKEN env var (or a CLI arg) is required.", file=sys.stderr)
        return 1

    resp = requests.get(f"{TELEGRAM_API_BASE}/bot{token}/getUpdates", timeout=30)
    if resp.status_code != 200:
        print(f"getUpdates 호출 실패 (HTTP {resp.status_code}): {resp.text[:500]}", file=sys.stderr)
        return 1

    updates = resp.json().get("result", [])
    if not updates:
        print(
            "업데이트가 없습니다 -- 먼저 텔레그램 앱에서 이 봇에게 메시지를 하나 보낸 뒤 "
            "다시 실행하세요.",
            file=sys.stderr,
        )
        return 1

    latest = updates[-1]
    chat = latest.get("message", {}).get("chat", {})
    chat_id = chat.get("id")
    if chat_id is None:
        print(f"최근 업데이트에서 chat_id를 찾지 못했습니다: {latest}", file=sys.stderr)
        return 1

    print(f"chat_id: {chat_id}")
    print(f"(chat title/name: {chat.get('title') or chat.get('username') or chat.get('first_name')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
