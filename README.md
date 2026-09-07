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
- `scripts/notify_kr_watchlist.py` — `watchlist.json`의 종목마다 Claude(Sonnet, 2026-09-07 Haiku에서
  교체 — WebSearch 그라운딩이 약해 8/8 종목이 근거 확인 없이 스킵된 사고 발생)+웹서치로 지난 24시간
  (월요일 실행분은 주말 공백을 메우기 위해 72시간)이내 뉴스를 한 줄 요약+호재/부정/중립 판정으로
  확인한 뒤, 뉴스가 있는 종목만 모아 **카카오 메시지 1건으로 묶어** 발송(뉴스 없는 종목은 언급 안
  함). 모델의 원문 응답을 항상 stderr(Actions 로그)에 남겨(`_log_raw`) "뉴스가 진짜 없었는지 vs
  형식/그라운딩 실패였는지"를 사후에 구분할 수 있게 한다. 05:50 KST 실행(06:30 목표 도착 — 아래
  "GitHub Actions 스케줄 지연" 참고).
- `scripts/notify_us_market.py` — 미국 시장 전반 분위기 요약을 카카오 메시지 1건으로 발송. 프롬프트에
  실행 시점의 실제 한국시간·요일을 넣고 "오늘이 미국 증시 휴장일이면 그걸 먼저 밝히고 실제 마감일이
  언제인지 명시하라"고 지시한다(2026-09-07 추가 — 기존 프롬프트는 날짜를 전혀 안 줘서 휴장일을
  놓친 채 옛 기사 날짜를 오늘 마감으로 착각한 사고 발생). 07:35 KST 실행.
- `scripts/kakao_get_refresh_token.py` — 최초 1회, 로컬에서 직접 실행하는 OAuth 인가 코드 교환
  헬퍼(브라우저 로그인 필요). CI에서는 쓰지 않는다.

## GitHub Actions 스케줄 지연

`schedule` 트리거는 GitHub 전체가 공유하는 큐를 타므로 지정 시각에 정확히 시작된다는 보장이 없다.
2026-09-06 스케줄이 처음 실제로 발동한 날 KR watchlist는 06:20 예약분이 07:55에(95분 지연), 미국장
요약은 08:00 예약분이 09:49에(109분 지연) 시작됐다 — 실측 1회뿐이라 상시 패턴인지는 아직 불명확.
위 cron을 앞당기고(05:50/07:35) 정각이 아닌 분(:50/:35)으로 잡은 것은 완화책이지 보장이 아니다.
지연이 반복되면 `workflow_dispatch`를 외부 스케줄러(cron-job.org 등)가 GitHub API로 직접 트리거하는
방식(스케줄 큐를 우회)을 다음 단계로 고려한다.

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
