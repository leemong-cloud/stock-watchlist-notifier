"""Headless Claude Code CLI wrapper -- subscription auth, no Anthropic API key.

Calls the `claude` CLI (installed via `npm install -g @anthropic-ai/claude-code` in the
workflow) in non-interactive print mode (`-p`), authenticated via the CLAUDE_CODE_OAUTH_TOKEN
env var (a one-year token from `claude setup-token`, tied to the user's Claude Pro/Max
subscription -- not pay-per-token API billing). If ANTHROPIC_API_KEY is ever set in this
process's environment it takes priority over the OAuth token, so the GitHub Actions workflow
must not set that secret.

`--permission-mode bypassPermissions` is required because this runs unattended -- a permission
prompt would hang forever with no TTY to answer it. `--allowedTools WebSearch` scopes the run to
web search only (no Bash/file-write access), since this is a public repo and the task never
needs anything else. The system-prompt addition suppresses Claude Code's default habit of
appending a "Sources:" section and explanatory asides -- confirmed empirically (2026-09-06) that
without it, responses include markdown source links and parenthetical commentary that blow past
Kakao's ~190-char message limit.
"""
from __future__ import annotations

import subprocess

MODEL = "sonnet"  # switched from haiku 2026-09-07: haiku's WebSearch grounding was too weak
# for material-news detection (8/8 watchlist stocks silently skipped as "no news" on 2026-09-06,
# with zero visibility into whether that was true or a format/grounding failure -- see the raw
# per-stock logging added to notify_kr_watchlist.py the same day)
TIMEOUT_SECONDS = 180  # sonnet's WebSearch pass runs longer than haiku's; was 120

_CLEAN_OUTPUT_SYSTEM_PROMPT = (
    "출력은 요청된 요약 문장 그 자체만 반환하라. 부연 설명, 괄호 안 보충 설명, 출처 목록, "
    "Sources 섹션, 마크다운 링크, 서두 인사말을 절대 포함하지 마라. 오직 순수 텍스트 한 덩어리만 "
    "출력하라."
)


class ClaudeCliError(RuntimeError):
    pass


def run_claude(prompt: str) -> str:
    try:
        result = subprocess.run(
            [
                "claude",
                "-p",
                prompt,
                "--model",
                MODEL,
                "--output-format",
                "text",
                "--permission-mode",
                "bypassPermissions",
                "--allowedTools",
                "WebSearch",
                "--append-system-prompt",
                _CLEAN_OUTPUT_SYSTEM_PROMPT,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(f"claude CLI 호출이 {TIMEOUT_SECONDS}초 내에 끝나지 않았습니다") from exc
    except FileNotFoundError as exc:
        raise ClaudeCliError("claude CLI를 찾을 수 없습니다 (npm install -g @anthropic-ai/claude-code 필요)") from exc

    if result.returncode != 0:
        raise ClaudeCliError(f"claude CLI 실패 (exit {result.returncode}): {result.stderr.strip()[:500]}")

    return result.stdout.strip()
