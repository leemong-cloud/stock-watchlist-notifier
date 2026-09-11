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
    "Sources 섹션, 마크다운 링크, 서두 인사말을 절대 포함하지 마라. 특히 스스로 글자 수나 문장 "
    "수를 세어 보고하거나('~자, ~문장으로 작성 완료', '아래가 최종 답변이다' 같은) 작업 완료를 "
    "알리는 메타 발언을 절대 출력하지 마라. 헤더나 대괄호 제목(예: '[제목]')도 스스로 붙이지 "
    "마라 -- 그건 호출하는 쪽에서 별도로 붙인다. 오직 순수한 본문 텍스트 한 덩어리만 출력하라."
    # 2026-09-11: 미국장 분위기 요약에서 모델이 "153자, 170자 이내로 4문장 구성 완료. 아래가
    # 최종 답변이다. [미국장 분위기] ..." 처럼 글자수 검증 메타발언 + 자체 헤더를 실제 발송
    # 메시지에 그대로 남기는 것이 실측 확인돼 이 지시를 추가함(기존 문구로는 커버 안 됐음).
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
        # 2026-09-11: 실전 실패의 상당수가 stderr는 비어있고 exit 1만 뜨는 패턴으로 확인됨
        # (GitHub Actions 로그 실측) -- stdout도 함께 봐야 실제 원인(레이트리밋/사용량 한도 등)을
        # 진단할 수 있어 둘 다 담는다.
        detail = result.stderr.strip() or result.stdout.strip() or "(stdout/stderr 모두 비어있음)"
        raise ClaudeCliError(f"claude CLI 실패 (exit {result.returncode}): {detail[:500]}")

    return result.stdout.strip()
