# 새 저장소에 The Last Human 도입하기

한 번만 하는 일(A: GitHub App과 서버)과 저장소마다 하는 일(B: 설치·파일·보호 규칙), 작성자가 하는 일(C)로 나뉨. 값·권한의 상세는 [App 실행 가이드](github-app.md)를 따르고, 이 문서는 순서와 판단 기준만 적음.

## 전체 그림

```
A. 운영자 (한 번)          B. 저장소 관리자 (저장소마다)             C. 작성자 (PR마다)
┌──────────────────┐      ┌──────────────────────────────┐      ┌────────────────────┐
│ 1 App 등록        │      │ 4 App 설치 → 서버에 바인딩      │      │ 10 카드의 링크로     │
│ 2 서버 배포        │ ───► │ 5 파일 3종 PR (relay·정책·     │ ───► │    로그인(최초 1회)   │
│ 3 헬스·모델 확인    │      │   CODEOWNERS)                 │      │ 11 두 문항에 답      │
└──────────────────┘      │ 6 저장소 변수                   │      │    → Human-verified │
                          │ 7 첫 PR로 status 발행 확인      │      │ 12 사람이 머지       │
                          │ 8 필수 상태 체크 지정            │      └────────────────────┘
                          │ 9 한 바퀴 확인                  │
                          └──────────────────────────────┘
```

## A. 한 번만 — 운영자

| # | 할 일 | 판단 기준 |
| --- | --- | --- |
| 1 | **GitHub App 등록** — 조직 Settings → Developer settings → GitHub Apps → New. Callback URL은 서버 주소 + `/auth/github/callback`. 권한: Contents *Read* · Pull requests *Read and write* · Commit statuses *Read and write* · Actions *Read and write* · Metadata *Read* · (선택) Checks *Read and write*. 개인키·client secret 발급 | 같은 App을 API 호출과 작성자 OAuth 로그인 둘 다에 씀. 설치 가능 범위는 조직 정책에 맞춤 |
| 2 | **서버 배포** — `gunicorn 'lasthuman.server.app:create_app()'` 한 프로세스. 영구 디스크의 SQLite. `runtime.env`: `TLH_APP_ID` · `TLH_CLIENT_ID` · `TLH_CLIENT_SECRET` · `TLH_PRIVATE_KEY_FILE` · `TLH_BASE_URL` · `TLH_SECRET_KEY` · `TLH_MODE=live` · `TLH_WORKFLOW=lasthuman-app.yml` · `TLH_WORKFLOW_REF=refs/heads/main` · `TLH_STATUS_CONTEXT=last-human/human-verified` · 모델 설정(`LASTHUMAN_PROVIDER` · `LASTHUMAN_ENDPOINT` · `LASTHUMAN_MODEL` · Azure 인증) | 서버는 GitHub Actions 러너와 작성자 브라우저가 닿을 수 있어야 함. 공개 HTTPS origin이거나, 사내망이면 self-hosted 러너 + 사내 접속. GitHub 웹훅 수신자일 필요는 없음 |
| 3 | **확인** — `/healthz` 200, 모델 호출 1회 성공 | 여기까지는 어떤 저장소에도 영향 없음 |

## B. 저장소마다 — 저장소 관리자 (+ 운영자)

| # | 할 일 | 판단 기준 |
| --- | --- | --- |
| 4 | **App 설치** — 대상 저장소를 선택해 설치하고 권한 승인. 운영자가 `TLH_REPOSITORY` · `TLH_REPOSITORY_ID` · `TLH_OWNER_ID` · `TLH_INSTALLATION_ID`를 서버 env에 넣고 재시작 | 서버 하나는 저장소 하나에 바인딩됨. 저장소가 늘면 서버 인스턴스(또는 설정)도 늘림 |
| 5 | **파일 3종을 PR 하나로 추가** — 아래 "파일 3종" 참조 | 정책 파일은 사람이 검토·승인하는 영역. 위험 규칙과 프롬프트 버전은 모든 스냅샷에 해시로 묶임 |
| 6 | **저장소 변수** — `LASTHUMAN_RUNTIME=app`, `TLH_BOT_URL=<서버 주소>`. audience를 저장소명과 다르게 쓸 때만 `TLH_OIDC_AUDIENCE` | 변수 변경은 이미 열린 PR에 자동 적용되지 않음. 새 이벤트를 만들거나 다시 실행 |
| 7 | **첫 이벤트** — PR을 하나 열어 `Last Human · relay / Relay PR #n`이 성공하고, PR에 status `last-human/human-verified`와 카드가 한 번 발행되는지 확인 | **이 단계 전에는 필수 체크를 걸지 않음.** 발급자가 없는 필수 체크는 머지를 영원히 막음 |
| 8 | **브랜치 보호** — Settings → Rules(또는 Branches): 필수 상태 체크 `last-human/human-verified`, 기대 발급자 = 설치한 App | 워크플로 잡 이름이나 보조 Check 이름 "The Last Human"을 필수로 걸지 않음. 기존 CI·리뷰·up-to-date 조건은 유지 |
| 9 | **한 바퀴 확인** — 위험 PR: 카드 "Awaiting author explanation" → 작성자 로그인·설명 → "Human-verified · sha" → `Verify receipt` 실행 로그 → 사람 머지 → 대시보드 증분. 저위험 PR: "Not required — below risk threshold"로 통과 | 둘 다 확인돼야 도입 완료 |

### 파일 3종

| 파일 | 역할 | 내용 |
| --- | --- | --- |
| `.github/workflows/lasthuman-app.yml` | Relay — 이벤트를 OIDC로 서버에 전달, 영수증을 GitHub 쪽에서 재계산해 검증 | 이 저장소의 파일을 그대로 복사. `pull_request_target`이라 main의 정의만 실행됨 |
| `.lasthuman.yml` | 게이트 정책 — 임계값, 중요 경로, 위험 패턴, 변경량 가중치 | `threshold`(기본 40), `signals.criticalPaths`(예: `app/auth/**: 30`), `patterns`, `linesChanged`. 정규식은 작은따옴표 |
| `CODEOWNERS` | 대시보드의 구역과 담당 열 | 구역 단위로 나눠야 "어느 모듈에 답할 사람이 없는지"가 보임. `*` 한 줄이면 구역이 하나가 됨 |

## C. 작성자 — PR마다

| # | 할 일 |
| --- | --- |
| 10 | 카드의 **Check this change** → 같은 App으로 GitHub 로그인(최초 1회, 세션 30분) |
| 11 | 두 문항에 보기 + 근거 한 줄 → 제출 → Accepted / Hold. Hold면 근거 발췌를 보고 다시 답하거나 코드를 고침(새 커밋이면 처음부터) |
| 12 | Human-verified가 되면 **사람이** 머지 |

## 도입 전에 알아둘 제약 (현재 코드 기준)

- **서버 1대 = 저장소 1개** 바인딩. 여러 저장소는 인스턴스 또는 설정 분리.
- Relay 워크플로는 대상 저장소에 **The Last Human 소스를 설치**해 재계산함(`pip install -e '.[bot]'`). 소스가 없는 저장소에는 버전을 고정한 verifier 패키지와 설치 템플릿이 필요.
- 게이트는 status(`last-human/human-verified`)와 보조 Check("The Last Human") **두 줄**로 보임. 권위는 status. 한 줄 통합은 다음 단계.
- 작성자 세션은 메모리에 30분. 서버 재시작 시 재로그인.
- 질문 생성은 모델 호출이라 드물게 정답 키가 틀린 세트가 나올 수 있음. 운영자는 `python -m lasthuman.server sync --pr N --regenerate`로 다시 뽑을 수 있음(영수증이 생긴 뒤에는 거부).

## 롤백

저장소 변수 `LASTHUMAN_RUNTIME`을 `app`이 아닌 값으로 바꾸고 브랜치 보호에서 필수 체크를 제거하면 게이트가 멈춤. 저장된 스냅샷·영수증은 서버에 남음.
