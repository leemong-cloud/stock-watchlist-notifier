# stock-watchlist-notifier

로컬 PC가 꺼져 있어도 동작하는, 관심종목 뉴스 요약 + 미국 시장 분위기 요약을 카카오톡으로 보내는
독립 알림 파이프라인 (GitHub Actions). 별도 프로젝트(`opencode`, 비공개)의 실제 포트폴리오 판단
파이프라인과는 완전히 분리되어 있으며, 여기에는 **종목코드+이름**만 담긴 `watchlist.json`과
알림 스크립트만 둔다 — 비중·수량·평단·손익 등 민감 수치는 절대 커밋하지 않는다.

## 구성

- `watchlist.json` — 관심종목(코드+이름만). `opencode` 프로젝트의
  `scripts/Publish-Watchlist.ps1 -Push`가 실제 보유 스냅샷에서 상위 N종목을 뽑아 이 파일만
  덮어써서 커밋한다. 이 저장소 자체는 어떻게 종목이 선정됐는지 알지 못한다.
- `scripts/claude_cli.py` — 헤드리스 `claude` CLI(Claude Code) 호출 래퍼. `ANTHROPIC_API_KEY`
  기반 API 과금이 아니라 `CLAUDE_CODE_OAUTH_TOKEN`(사용자의 Claude Pro/Max 구독 인증)으로 동작해
  이 자동화 자체는 별도 API 사용료가 들지 않는다.
- `scripts/kakao_client.py` — 카카오 "나에게 보내기" REST API 직접 호출(리프레시 토큰 →
  access token → 메시지 발송). MCP를 쓰지 않는다(무인 CI 환경 전제).
- `scripts/notify_kr_watchlist.py` — `watchlist.json`의 종목마다 Claude(Haiku)+웹서치로 지난
  24시간 이내 뉴스를 한 줄 요약+호재/부정/중립 판정으로 확인한 뒤, 뉴스가 있는 종목만 모아
  **카카오 메시지 1건으로 묶어** 발송(뉴스 없는 종목은 언급 안 함). 06:20 KST 실행(트리거~발송까지
  약 7분 걸려 06:30 목표 시각 전 도착).
- `scripts/notify_us_market.py` — 미국 시장 전반 분위기 요약을 카카오 메시지 1건으로 발송.
  08:00 KST 실행.
- `scripts/kakao_get_refresh_token.py` — 최초 1회, 로컬에서 직접 실행하는 OAuth 인가 코드 교환
  헬퍼(브라우저 로그인 필요). CI에서는 쓰지 않는다.

## 필요한 GitHub Actions Secrets

| Secret | 설명 |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Claude Code CLI 구독 인증 토큰. 로컬에서 `claude setup-token` 실행(브라우저/기존 로그인 세션 필요) → 출력된 토큰을 이 Secret에 등록. **약 1년 후 만료** — 워크플로가 인증 오류로 실패하면 `claude setup-token`을 다시 실행해 갱신한다. `ANTHROPIC_API_KEY`는 절대 이 저장소의 Secret으로 등록하지 말 것(설정돼 있으면 이 토큰보다 우선 적용되어 다시 API 과금 경로로 빠진다). |
| `KAKAO_REST_API_KEY` | 카카오 Developers 앱의 REST API 키 |
| `KAKAO_REFRESH_TOKEN` | `kakao_get_refresh_token.py`로 최초 발급받은 리프레시 토큰 |

카카오 리프레시 토큰은 약 60일, Claude 구독 토큰은 약 1년 후 만료될 수 있다 — 둘 다 **수동 갱신**
전략(자동 로테이션 없음). 발송이 인증 오류로 실패하면 해당 발급 스크립트/커맨드를 로컬에서 다시
실행해 위 Secret을 갱신한다.

## 수동 실행/테스트

두 워크플로우 모두 `workflow_dispatch`로 GitHub Actions 탭에서 수동 트리거 가능.
