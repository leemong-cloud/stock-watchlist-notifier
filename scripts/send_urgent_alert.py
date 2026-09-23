"""One-off urgent Telegram alert -- no news search, no LLM, just a plain text message.

Companion to notify_kr_watchlist.py's daily digest but deliberately much lighter: the opencode
project's /watch-portfolio near-real-time monitoring loop (2026-09-22) dispatches this via
`gh workflow run urgent-alert.yml -f message="..."` the moment it detects a volume/price anomaly
on a holding, and needs the Telegram message to land in seconds, not the minutes a per-stock
WebSearch pass would take. Reuses telegram_client.py's existing credentials (TELEGRAM_BOT_TOKEN /
TELEGRAM_CHAT_ID secrets, already verified live by the daily digest job) -- no new credentials.

Message text is passed straight through to telegram_client.send_text, which already truncates to
MAX_MESSAGE_CHARS (4096) with a sentence-boundary-aware trim -- the caller does not need to
pre-truncate.

CLI/env:
    ALERT_MESSAGE env var (set by the GitHub Actions workflow_dispatch input), or a single CLI
    arg as a local-testing fallback.

    python scripts/send_urgent_alert.py "테스트 알림"
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram_client import send_text


def main() -> int:
    message = os.environ.get("ALERT_MESSAGE", "").strip()
    if not message and len(sys.argv) > 1:
        message = sys.argv[1].strip()
    if not message:
        print("ALERT_MESSAGE env var (or a CLI arg) is required and was empty.", file=sys.stderr)
        return 1

    try:
        send_text(message)
    except Exception as e:  # noqa: BLE001 -- surface the failure clearly, this is the only step in the job
        print(f"Telegram send failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print("Telegram urgent alert sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
