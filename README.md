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
  access token → 메시지 발송). MCP를 쓰지 않는다(무인 CI 환경 전제). `build_redirect_link()`가
  기사 링크를 `docs/go.html` 리다이렉트 게이트웨이 경유 URL로 감싸고(아래 "카카오 링크 도메인
  등록" 참고), `_trim_sentence_safe()`가 텍스트/리스트 두 템플릿 모두에서 문장 경계를 존중해
  잘라낸다(2026-09-08 추가 — 이전엔 단순 슬라이스라 단어 중간에서 잘림).
- `scripts/notify_kr_watchlist.py` — `watchlist.json`의 종목마다 Claude(Sonnet, 2026-09-07 Haiku에서
  교체 — WebSearch 그라운딩이 약해 8/8 종목이 근거 확인 없이 스킵된 사고 발생)+웹서치로 지난 24시간
  (월요일 실행분은 주말 공백을 메우기 위해 72시간)이내 뉴스를 한 줄 요약+호재/부정/중립 판정으로
  확인한 뒤, 뉴스가 있는 종목만 모아 **카카오 메시지 1건으로 묶어** 발송(뉴스 없는 종목은 언급 안
  함). 모델의 원문 응답을 항상 stderr(Actions 로그)에 남겨(`_log_raw`) "뉴스가 진짜 없었는지 vs
  형식/그라운딩 실패였는지"를 사후에 구분할 수 있게 한다. `NONE` sentinel 비교를 관대화하고
  형식 위반(파이프 3필드 불일치)은 1회 재프롬프트로 복구를 시도한다(2026-09-08 추가 — 이전엔
  형식을 살짝 벗어나기만 해도 조용히 스킵되어 8종목 중 2종목만 도착하는 문제가 있었음). 05:50 KST
  `schedule`은 안전망이고, 실제 목표(06:30 도착)는 외부 트리거가 주 경로다(아래 "외부 트리거 설정"
  참고).
- `scripts/notify_us_market.py` — 미국 시장 전반 분위기 요약을 카카오 메시지 1건으로 발송. 프롬프트에
  실행 시점의 실제 한국시간·요일을 넣고 "오늘이 미국 증시 휴장일이면 그걸 먼저 밝히고 실제 마감일이
  언제인지 명시하라"고 지시한다(2026-09-07 추가 — 기존 프롬프트는 날짜를 전혀 안 줘서 휴장일을
  놓친 채 옛 기사 날짜를 오늘 마감으로 착각한 사고 발생). 07:35 KST `schedule`은 안전망(위와 동일한
  구조).
- `scripts/dedup_guard.py` — 외부 트리거(주 경로)와 `schedule`(안전망)이 같은 날 둘 다 발동했을 때
  카카오 메시지가 중복 발송되는 것을 막는다. `schedule`로 실행된 경우에만 GitHub API로 오늘 이미
  `workflow_dispatch` 성공 실행이 있었는지 확인 후, 있으면 조용히 종료한다(2026-09-08 추가).
- `docs/go.html` — 카카오 기본 템플릿 링크의 도메인 사전 등록 제약을 우회하기 위한 고정 리다이렉트
  게이트웨이(아래 "카카오 링크 도메인 등록" 참고, 2026-09-08 추가).
- `scripts/kakao_get_refresh_token.py` — 최초 1회, 로컬에서 직접 실행하는 OAuth 인가 코드 교환
  헬퍼(브라우저 로그인 필요). CI에서는 쓰지 않는다.

## 카카오 링크 도메인 등록 (필수 — 안 하면 링크가 눌리지 않음)

카카오 기본 템플릿의 링크(`web_url`/`mobile_web_url`)는 **카카오 개발자 콘솔에 사전 등록한 도메인과
일치할 때만 탭이 활성화된다**(공식 문서: [기본 템플릿](https://developers.kakao.com/docs/latest/ko/message-template/default),
[데브톡 사례](https://devtalk.kakao.com/t/topic/112581)). 종목별 기사 링크는 매일 다른 뉴스 매체
도메인(네이버/조선일보/한경 등)을 가리키므로 그 어떤 뉴스 도메인도 사전 등록이 불가능하다 — 그래서
`kakao_client.build_redirect_link()`가 모든 기사 링크를 이 저장소가 소유한 고정 도메인
(`leemong-cloud.github.io`) 경유로 감싼다. **아래 두 가지를 한 번만 설정하면 된다** (설정 전까지는
링크가 눌리지 않는 것이 정상):

1. **GitHub Pages 활성화**: 이 저장소 Settings → Pages → Source: `Deploy from a branch` →
   Branch: `main` / `/docs` → Save. 몇 분 뒤 `https://leemong-cloud.github.io/stock-watchlist-notifier/go.html`
   이 열리는지 확인.
2. **카카오 개발자 콘솔에 도메인 등록**: [Kakao Developers](https://developers.kakao.com) → 내
   애플리케이션 → 해당 앱 → 앱 설정 → 플랫폼 → Web 플랫폼에 아래 두 도메인을 추가:
   - `https://leemong-cloud.github.io` (기사별 리다이렉트 링크)
   - `https://github.com` (다이제스트 헤더 링크, `DEFAULT_LINK_URL`)

## GitHub Actions 스케줄 지연 → 외부 트리거로 전환

`schedule` 트리거는 GitHub 전체가 공유하는 큐를 타므로 지정 시각에 정확히 시작된다는 보장이 없다.
2026-09-06(95분/109분 지연)에 이어 2026-09-07 크론을 앞당긴(05:50/07:35 KST) 뒤에도 2026-09-08
지연이 오히려 131분/122분으로 더 커져, cron 시각 조정만으로는 06:30 목표 도착을 안정적으로
맞출 수 없음이 2일 연속 데이터로 확인됐다. 따라서 **`schedule`은 안전망으로 남기고, 외부
스케줄러가 GitHub API를 직접 호출해 `workflow_dispatch`로 실행시키는 방식을 주 경로로 쓴다**
(아래 "외부 트리거 설정"). `scripts/dedup_guard.py`가 두 경로가 같은 날 겹쳐도 중복 발송되지
않도록 막는다.

## 외부 트리거 설정 (수동, 브라우저 필요 — 코드로 대신할 수 없음)

1. **GitHub Fine-grained PAT 발급**: GitHub → Settings → Developer settings → Fine-grained
   tokens → Generate new token. Repository access: 이 저장소(`stock-watchlist-notifier`)만 선택,
   Permissions: **Actions = Read and write**만 부여(다른 권한 불필요). 이 토큰은 이 저장소의
   Secret이 아니라 **cron-job.org에만** 입력한다(GitHub API를 이 저장소 바깥에서 호출하기
   위한 용도).
2. **cron-job.org(또는 유사 서비스) 계정 생성 후 job 2개 등록**:
   - Job 1 (KR, 매일 06:15 KST 전후): `POST https://api.github.com/repos/leemong-cloud/stock-watchlist-notifier/actions/workflows/kr-watchlist-news.yml/dispatches`,
     헤더 `Authorization: Bearer <PAT>` + `Accept: application/vnd.github+json`, 바디 `{"ref":"main"}`.
   - Job 2 (US, 매일 07:50 KST 전후): 위와 동일하되 workflow 파일만 `us-market-mood.yml`로 교체.
   - cron-job.org의 스케줄 입력은 UTC 기준이므로 KST-9시간으로 환산해서 입력한다(예: 06:15 KST →
     21:15 UTC 전날).
3. PAT는 만료 시(fine-grained 토큰은 발급 시 만료일을 직접 지정) cron-job.org에서 재발급·교체.

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
