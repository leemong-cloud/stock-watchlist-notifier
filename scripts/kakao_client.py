"""Kakao Talk "send to me" (memo) client -- direct REST calls, no MCP.

This repo runs unattended in GitHub Actions, where an interactive MCP host isn't available.
The Kakao MCP servers people use locally wrap the same two REST calls this module makes:
refresh the access token, then post a memo to the default template endpoint.

Token strategy: MANUAL refresh only (deliberate choice, see project plan). If Kakao returns a
new refresh_token in the token response, we do NOT try to persist it back to GitHub Secrets --
that would need a GitHub PAT with secret-write scope, which is more standing privilege than this
lightweight notifier needs. If the refresh token has actually expired, send_kakao_message will
fail with a clear error; re-run scripts/kakao_get_refresh_token.py locally and update the
KAKAO_REFRESH_TOKEN secret by hand.
"""
from __future__ import annotations

import os
import sys

import requests

KAUTH_TOKEN_URL = "https://kauth.kakao.com/oauth/token"
KAPI_MEMO_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"

MAX_MESSAGE_CHARS = 190  # stays safely under the default "text" template's ~200-char limit


class KakaoAuthError(RuntimeError):
    pass


class KakaoSendError(RuntimeError):
    pass


def get_access_token() -> str:
    rest_api_key = os.environ.get("KAKAO_REST_API_KEY")
    refresh_token = os.environ.get("KAKAO_REFRESH_TOKEN")
    if not rest_api_key or not refresh_token:
        raise KakaoAuthError("KAKAO_REST_API_KEY / KAKAO_REFRESH_TOKEN 환경변수가 설정되지 않았습니다.")

    resp = requests.post(
        KAUTH_TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "client_id": rest_api_key,
            "refresh_token": refresh_token,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise KakaoAuthError(
            f"카카오 access token 갱신 실패 (HTTP {resp.status_code}): {resp.text[:500]} "
            "-- 리프레시 토큰이 만료됐을 수 있습니다. scripts/kakao_get_refresh_token.py를 "
            "로컬에서 다시 실행해 KAKAO_REFRESH_TOKEN 시크릿을 수동으로 갱신하세요."
        )

    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise KakaoAuthError(f"토큰 응답에 access_token이 없습니다: {payload}")

    if payload.get("refresh_token"):
        print(
            "[kakao_client] 참고: 카카오가 새 refresh_token을 함께 발급했지만 이 스크립트는 "
            "저장하지 않습니다(수동 갱신 전략). 기존 리프레시 토큰은 원래 만료일까지 계속 "
            "유효한 것이 일반적이나, 인증 실패가 반복되면 수동으로 재발급하세요.",
            file=sys.stderr,
        )

    return access_token


def send_text(access_token: str, message: str, link_url: str = "https://github.com/leemong-cloud/stock-watchlist-notifier") -> None:
    text = message.strip()
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[: MAX_MESSAGE_CHARS - 1].rstrip() + "…"

    template_object = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": link_url, "mobile_web_url": link_url},
    }

    resp = requests.post(
        KAPI_MEMO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": __import__("json").dumps(template_object, ensure_ascii=False)},
        timeout=30,
    )
    if resp.status_code != 200:
        raise KakaoSendError(f"카카오 메시지 발송 실패 (HTTP {resp.status_code}): {resp.text[:500]}")


def send_kakao_message(message: str) -> None:
    """Convenience wrapper: refresh token -> send. Raises on any failure (caller decides whether
    to skip-and-continue or hard-fail)."""
    token = get_access_token()
    send_text(token, message)
