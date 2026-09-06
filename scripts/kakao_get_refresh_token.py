"""One-time local helper: obtain the initial Kakao access_token + refresh_token pair via the
OAuth authorization-code flow. Run this on your own machine (needs a browser login) -- never in
CI. The resulting refresh_token goes into this repo's GitHub Actions secret KAKAO_REFRESH_TOKEN
(manual paste; this script does not touch GitHub in any way).

Prerequisites (one-time, in the Kakao Developers console -- https://developers.kakao.com):
  1. Create an app (or reuse an existing one).
  2. App > 카카오 로그인 (Kakao Login): activate it, add a Redirect URI (any reachable URL is
     fine for this flow -- e.g. https://localhost/oauth, since you'll copy the "code" param by
     hand rather than running a local server).
  3. App > 카카오 로그인 > 동의항목 (Consent items): enable "talk_message" (카카오톡 메시지 전송),
     which may require Kakao's business-app review depending on your account type -- for sending
     messages to yourself only ("나에게 보내기"), the default/basic consent is normally enough,
     but check the console if step 4 below fails with a scope error.
  4. Note the app's REST API key (앱 키 > REST API 키) -- this is KAKAO_REST_API_KEY.

Usage:
    python scripts/kakao_get_refresh_token.py --rest-api-key <KEY> --redirect-uri <URI>
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse

import requests

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rest-api-key", required=True, help="Kakao app REST API key")
    parser.add_argument("--redirect-uri", required=True, help="Redirect URI registered in the Kakao app")
    args = parser.parse_args()

    auth_url = (
        f"{AUTHORIZE_URL}?"
        + urllib.parse.urlencode(
            {
                "client_id": args.rest_api_key,
                "redirect_uri": args.redirect_uri,
                "response_type": "code",
                "scope": "talk_message",
            }
        )
    )
    print("1) 아래 URL을 브라우저에서 열어 카카오 계정으로 로그인/동의하세요:")
    print(f"   {auth_url}")
    print()
    print("2) 로그인 후 리다이렉트된 URL의 'code=' 뒤에 오는 값을 복사해 붙여넣으세요.")
    code = input("code: ").strip()
    if not code:
        print("code가 비어 있습니다.", file=sys.stderr)
        return 1

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "client_id": args.rest_api_key,
            "redirect_uri": args.redirect_uri,
            "code": code,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"토큰 교환 실패 (HTTP {resp.status_code}): {resp.text}", file=sys.stderr)
        return 1

    payload = resp.json()
    print()
    print("성공. 아래 값을 GitHub 저장소 Settings > Secrets and variables > Actions 에 등록하세요:")
    print(f"  KAKAO_REST_API_KEY   = {args.rest_api_key}")
    print(f"  KAKAO_REFRESH_TOKEN  = {payload.get('refresh_token')}")
    print()
    print(f"(access_token은 6시간 후 만료되어 이 스크립트에서는 저장하지 않습니다: {payload.get('access_token')[:20]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
