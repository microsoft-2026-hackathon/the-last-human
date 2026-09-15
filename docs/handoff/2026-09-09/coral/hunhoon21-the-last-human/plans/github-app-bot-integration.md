# GitHub App 봇 연동 가이드와 PR 실행 흐름

> 2026-09-09 공개 이관본. 구현 전 계획과 당시 상태를 보존한 참고자료이며 현재 동작은 소스와 `docs/runbooks/github-app.md`가 기준이다. 기기별 절대 경로와 내부 실행 식별자는 제외했다. 원본은 변경하지 않았다.

## Requirements Summary

- 작업 대상: `~/workspace/the-last-human`, `hunhoon21/the-last-human`, 조사 시 `main@b8d08a3`, 깨끗한 작업 트리. 기존 원격 PR이나 `daeungo1` 저장소는 수정하지 않는다.
- 사용자는 App 소유자/설치 대상을 `hunhoon21`로, 구현 범위를 전체 PR→봇→로그인 웹 면담→보완→성공 기록/게이트→머지/대시보드로 선택했다. 새 서버는 Flask + 기존 Jinja + 로컬 SQLite로 시작한다.
- 현재 App/개인키/로그인 설정/모델/외부 HTTPS 서비스가 없다. 코드·로컬 테스트 완료와 실제 GitHub 자동화 연결 완료를 구분한다. 마지막 외부 연결은 소유자의 설정 후에만 확인할 수 있다.
- 최신 사용자 답변: 관련된 사람이 확인하는 일반화는 열어두되 **초기에는 PR 작성자 중 1명으로 제한**한다. GitHub PR의 `user.id`를 현재 구현의 작성자 기준으로 사용하며 commit co-author/team/CODEOWNERS로 확장하지 않는다.
- ideation의 `docs/the-last-human-github-app-build-plan.md:19-25,124-143,198-217`에서 App=신원/권한, 봇=서비스, Actions=이벤트 전달, Azure 배포 마지막을 계승한다. 이전의 `daeungo1` 대상·공개 보완 댓글·CODEOWNERS 대안은 현재 저장소/최신 사용자 지시와 충돌하므로 채택하지 않는다.
- 절대 조건: 개인 점수/등급/순위/팀장 개인 이력 조회 없음, 조직 집계는 모듈 커버리지, 보류 이력 영구 저장 없음, 위험 기반/저위험 neutral, Action 독립 위험 재계산·현재 SHA 확인 (`.github/copilot-instructions.md:6-21`).
- 위험 규칙/가중치, 질문 프롬프트, pass/hold 기준, attestation schema는 인간 소유다 (`.github/copilot-instructions.md:39-48`). 기존 `risk.score`와 `interview.generate_questions/grade`를 그대로 사용하고 새로운 질문·판정 정책을 저작하지 않는다. 파싱·입력 유효성·UI 연결 결함만 회귀 테스트와 함께 보완한다.
- 구현 handoff에서 아래 저장/운영 계약도 승인받는다. 최종 위험 정책/프롬프트가 검수되기 전에는 개발용 status context로만 동작시킨다.

### 초기 운영·데이터 계약 (사용자 승인 대상)

- 서버가 GitHub App installation 신원으로 API를 호출한다. 실제 사용자의 GitHub App OAuth 로그인은 별도이고, 브라우저 payload의 actor/role/pass를 신뢰하지 않는다.
- 영구 성공 기록: 저장소 numeric ID/PR, 검토 head/base SHA, 정책·질문 버전, PR 작성자 numeric ID, App/installation ID, 확인 시각, 통과한 설명과 근거 앵커. 점수·밴드·보류 결과 제외. 작성자 본인만 상세 설명 조회; 공개 PR에는 최소 완료 문구/커밋/접근 제어된 기록 링크만.
- 답변·보완 결과·사용자 토큰은 프로세스 메모리에서 최대 30분, 세션 소유자만 조회. SQLite/로그/Actions artifact/Pages에 쓰지 않는다. 만료/프로세스 재시작으로 소실되면 재제출/재로그인을 명시하고 완료로 꾸미지 않는다.
- 질문 snapshot과 위험 분석/원본 코드 근거는 서버 비공개 SQLite에 저장한다. 공개/인가된 웹 응답에도 `expected_evidence`·정답 index를 보내지 않는다.
- 로컬 단일 프로세스·단일 백그라운드 작업자. Flask 요청은 작업 ID를 받고 결과 조회. 미완료 답변 자체는 재시작 복구를 약속하지 않지만, 성공 기록과 발행 outbox는 원자적으로 저장하고 재시작 후 재시도한다.
- 완료된 성공 기록은 보존한다. 새 head/base/정책 snapshot이면 새 확인이 필요하며 기존 기록을 새 커밋의 성공으로 복사하지 않는다. 같은 revision의 이미 성공한 기록은 늦은 보완 이벤트로 취소하지 않는다.
- App은 게이트의 단일 status writer. 내부 `neutral`은 Commit Status API에서 `success` + “확인 대상 아님”으로 투영한다. `pending`=대기/보완, `error`=시스템 오류. 기존 CI/리뷰는 그대로 유지한다.
- 봇이 성공 기록을 저장한 뒤 `workflow_dispatch`로 짧은 Action 검증을 요청한다. Action이 GitHub PR과 base 정책으로 위험도를 독립 재계산하고 서버 성공 기록의 PR/SHA/version 결속을 확인한 후 인증된 응답을 보낸다. 그 응답까지 맞아야 봇이 최종 `success`를 발행한다.
- 공개 댓글은 시작/성공만. 정상 hold의 상세 안내는 비공개 메모리 결과만. 댓글 발행은 안정된 publication ID + App attribution 재조회로 재시도 중복을 줄이고, 외부 exactly-once를 주장하지 않는다.

## Acceptance Criteria (testable, verifiable — register each as a Task during implementation)

- AC1: `docs/runbooks/github-app.md`가 hunhoon21 소유 App 등록/선택 저장소 설치, App/Client/Installation ID 용도, 최소 권한, 서버 환경설정, 로컬 실행/로그인, 공개 endpoint/Actions/required status 연결을 무엇·왜·확인 순서로 안내한다. 비밀 원문을 문서/브라우저/로그/PR에 넣지 않는다.
- AC2: App JWT→제한된 installation token→PR 조회/댓글/상태/검증 workflow dispatch API를 구현한다. API 오류/시간 초과를 명시하고 URL/저장소/설치 allowlist, token expiry, App attribution을 검증한다. 재시도 시 동일 시작/성공 댓글이 중복되지 않는 정상/응답 유실 케이스를 시험한다.
- AC3: 서버와 Action이 같은 GitHub PR 사실을 각자 읽고 **기존 diff/앵커/risk 규칙**으로 재계산한다. 신뢰한 base-side 설정, 고정 head/base SHA, 변경량/파일/정책 digest를 연결한다. PR 코드·설치 스크립트를 실행하지 않으며 AI 탐지 신호를 새로 추가하지 않는다. 불완전 diff/상한 초과/동일 SHA의 복수 PR/merge queue는 명시적으로 지원 외 처리한다.
- AC4: Flask 앱의 GitHub 로그인·서버 세션·CSRF·작성자 인가·작업 접수/조회가 연결된다. 비작성자/세션 위조/틀린 repo/설치·stale head는 거부한다. 비밀·미완료 raw 답변은 메모리에서 만료되고 재시작 시 재제출을 안내한다. 기본 3개 질문의 순서·문항 ID·보기를 고정하고 정답 키는 응답에서 제외한다.
- AC5: 기존 generate/grade를 이용해 질문 준비→웹 제출→비공개 보완→재제출→성공 기록까지 완료한다. dry-run은 개발 UI 표시뿐이며 실제 GitHub 성공 발행은 금지한다. 모델/파싱 오류·빈 질문·부족한 질문·빈 답·틀린 index는 성공이 아니다. 프로덕션 정책/프롬프트를 새로 쓰지 않는다.
- AC6: SQLite 성공 기록 + GitHub 발행 outbox를 원자적으로 저장하고 단일 worker가 처리한다. 중복 이벤트/제출/재시작/발행 오류/새 SHA 경합에서 중복 성공 기록·옛 성공의 재사용·늦은 pending 덮어쓰기가 없다. 로그인·보완·답변 원문은 영구 작업 이력으로 남지 않는다.
- AC7: trusted Actions PR relay와 성공 후 재검증 workflow를 구현한다. OIDC issuer/JWKS/audience/expiry, repo/owner numeric IDs, workflow_ref/ref/event를 검증한다. PR head 코드를 checkout/install/실행하지 않는다. 봇의 성공 기록과 실제 current SHA/risk 재계산이 일치한 응답 뒤에만 App이 success를 발행한다. PR opened/synchronize/reopened/closed와 명시적 재검사/수동 sync 경로를 제공하고 면담 중 runner 대기는 없다.
- AC8: 실제 merged PR과 해당 시점의 성공 기록을 연결하는 중복 없는 모듈 커버리지 대시보드를 구현한다. head SHA와 merge_commit_sha를 구분하고, 근거 앵커가 없는 모듈까지 인증으로 확장하지 않는다. no-data/작은 표본을 구분하며 개인 점수·이름별 집계·보류 이력이 응답에 없다.
- AC9: 기존 public comment grade/status writer와 새 App 경로가 동시에 gate를 쓰지 않도록 설정 기반 전환을 준비한다. 미설정 상태에서 현재 운영 경로를 무단 중지하지 않는다. 새 모드에서는 legacy grade/status job과 기존 dashboard collector가 동작하지 않으며 봇 댓글 재귀도 없다. 보호 규칙/변수 cutover·rollback은 사람이 실행할 가이드로 제공한다.
- AC10: 기존 pytest 기반으로 API/auth/snapshot/session/TTL/model/receipt/relay/outbox/dashboard의 최소 전체 흐름과 실패 경로를 시험한다. App·모델·HTTPS가 없는 현재 환경에서는 GitHub/모델 adapter를 대역으로 사용하되 결과를 실제 원격 완주로 표현하지 않는다. 실제 연결 시 남길 PR/comment/check/head/merge/dashboard 증거와 미완료 항목을 runbook에 명시한다.

## Execution Order (dependency graph, batches, file mapping — written after review loop, see step 4e)

### Dependency Graph

```text
AC1 registration/guide
 -> AC2 App transport/config
 -> AC3 snapshot/risk
 -> AC4 Flask/auth/session
 -> AC5 model/web loop
 -> AC6 durable success/publications
 -> AC7 Actions proof/event relay
 -> AC8 merge/dashboard
 -> AC9 controlled cutover
 -> AC10 integrated verification/guide completion
```

같은 server/app/config/test 파일을 확장하므로 승인 조건의 완료 관문은 직렬이다. 네트워크 경계의 API client와 별도 snapshot helper처럼 파일/인터페이스가 확정된 하위 구현만 병렬로 맡길 수 있다. 인간 소유 schema/판정 결정을 에이전트에 넘기지 않는다.

### Batches

| Batch | ACs | Dependencies | Parallel |
| --- | --- | --- | --- |
| 1 | AC1 | 없음 | 1 |
| 2 | AC2 | AC1 | 1 |
| 3 | AC3 | AC2 | 1 |
| 4 | AC4 | AC3 | 1 |
| 5 | AC5 | AC4 | 1 |
| 6 | AC6 | AC5 | 1 |
| 7 | AC7 | AC6 | 1 |
| 8 | AC8 | AC7 | 1 |
| 9 | AC9 | AC8 | 1 |
| 10 | AC10 | AC9 | 1 |

### File Mapping

| AC | Files |
| --- | --- |
| AC1 | `docs/runbooks/github-app.md` |
| AC2 | `src/lasthuman/server/{config,github}.py`, `pyproject.toml`, `tests/test_app_github.py` |
| AC3 | `src/lasthuman/server/snapshot.py`, `src/lasthuman/structure.py`(필요 시 helper 재사용), `tests/test_app_snapshot.py` |
| AC4 | `src/lasthuman/server/{__init__,app,auth,store,service}.py`, `tests/test_app_auth.py`, `tests/test_app_store.py` |
| AC5 | `src/lasthuman/server/{app,service}.py`, 기존/신규 Jinja 템플릿, `src/lasthuman/{interview,webui}.py`, `tests/test_choices.py`, `tests/test_app_flow.py` |
| AC6 | `src/lasthuman/server/{store,service}.py`, `tests/test_app_flow.py`, `tests/test_app_store.py` |
| AC7 | `src/lasthuman/server/{events,relay,app,service}.py`, `.github/workflows/lasthuman-app.yml`, `tests/test_app_events.py` |
| AC8 | `src/lasthuman/server/{app,service,store}.py`, 신규/기존 dashboard 템플릿, `tests/test_app_flow.py` |
| AC9 | `.github/workflows/{comprehension-gate,dashboard}.yml`, `docs/runbooks/github-app.md`, `README.md`, `.gitignore`, `tests/test_app_workflow.py` |
| AC10 | 관련 `tests/test_app_*.py`, `docs/runbooks/github-app.md`, 필요 시 위 파일의 범위 내 수정 |

## Mathematical Specification (if applicable)

- 비자명한 수학은 없다. 인증은 repo/PR/head/base/정책 digest/질문 버전/actor에 묶인다. 해시는 canonical JSON(정렬된 키, 고정 separators, UTF-8)의 SHA-256을 사용하되 해시 자체가 발급 신원을 증명하지는 않는다.
- 내부 neutral과 GitHub success projection을 혼동하지 않는다. neutral은 질문/인증 없이 통과시킬 대상 미달 경로다.
- 모듈별 분모는 선택 기간에 머지된 위험 대상 PR 중 해당 모듈을 변경한 고유 PR 수. 분자는 그중 머지 전 유효 성공 기록의 근거 앵커가 해당 모듈에 있는 PR 수. 무자료는 미측정, MIN_SAMPLE 미만은 비율 대신 작은 표본으로 표시한다.
- 모델 질문 수 기본 3개. 이것은 임의 점수/확률 산출이 아니라 사람이 승인한 개별 문항 계약의 개수다. 새 schema/판정 기준의 최종 확정은 human approval을 전제로 한다.

## Implementation Phases (with file:line references)

### P1. App/네트워크 경계

- 선택 저장소·App·installation과 audience/workflow/ref 설정을 startup validation한다. 비밀은 env 또는 private-key 파일 경로로만 로드. server extra 의존성으로 Flask, Authlib, PyJWT[crypto], requests, gunicorn을 사용하며 JWT/OAuth 검증을 직접 구현하지 않는다.
- `src/lasthuman/server/github.py`: 공식 GitHub API 고정 origin, 제한된 timeout/페이지/본문 크기, installation token 갱신, PR metadata, 동일 SHA PR 충돌 확인, commenter App attribution, stable publication marker, commit status, workflow dispatch.
- App 권한: Contents read, Pull requests write, Commit statuses write, Actions write(완료 후 verifier dispatch). Metadata 기본 read. 개인 계정 WRITE와 App 설치/관리 권한을 혼동하지 않는다.
- OAuth: 같은 App client ID/secret, PKCE S256/state, `GET /user` numeric ID, 사용자 토큰의 repo 접근과 PR 작성자 인가를 매 민감 요청마다 검증. 토큰은 signed cookie 안에도 넣지 않는다.
- 공식 근거: [App JWT](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app), [installation token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app), [user token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app), [PR timeline comments](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment).

### P2. 신뢰된 스냅샷과 기존 코어

- `snapshot.py`는 고정 allowlist의 GitHub 저장소를 별도 bare Git cache로 읽는다. PR ref와 base SHA를 fetch하고 `diff.collect_hunks`를 그대로 호출한다(`diff.py:77-96,204-206`). 불신 파일을 checkout하거나 설치하지 않는다.
- git 인증 헤더는 subprocess 환경으로만 전달하며 명령·에러에 토큰이 포함되지 않도록 한다. 고정 remote/hex SHA/숫자 PR만 사용한다. Git hook/filter/submodule 실행은 없다.
- base SHA에서 `.lasthuman.yml`/CODEOWNERS를 읽는다. head tree의 제한된 Python regular blob만 임시 안전 경로로 추출해 `structure.build_context`에 데이터로 제공하거나 동일 분석 helper를 재사용한다(`structure.py:120-178`). symlink/path traversal/대형 tree는 지원 외로 알린다.
- `risk.py`는 수정하지 않는다. 기존 AI hint를 수집하지 않고 `PrMeta.agent_hint=False`; 같은 입력 기준으로 server/Action에서 일치해야 한다. 오류를 neutral로 바꾸지 않는다.
- 기존 `generate_questions`의 `answerIndex=0`이 `or -1`로 바뀌는 파싱 문제, JSON/malformed 값 예외, 문항별 radio name 문제는 새 경로와 직접 관련된 호환 수정 범위다(`interview.py:228-245`, `templates/interview.html.j2:121-125`). 프롬프트 문자열과 판정 기준 자체는 바꾸지 않는다.

### P3. 로컬 웹·영구 성공 기록·메모리 제출

- `server/app.py`, `auth.py`, `store.py`, `service.py`를 중심으로 작은 단일 서비스. 신규 서버 진입점, health, login/callback/logout, PR 면담/제출/상태, 성공 기록, dashboard, authenticated Actions API.
- 기존 Jinja/렌더링 방식을 재사용하되 legacy clipboard 화면은 기존 모드에서 유지한다. 서버 모드에서는 role 선택 제거, per-question index ID, 직접 POST/ACK/poll/CSRF를 연결한다. UI 새 디자인/SPA 도입 없음.
- SQLite에는 PR 분석/질문 snapshot, 성공 receipt, pending publication만 저장한다. raw 미완료 답변과 hold feedback은 memory TTL. worker 재시작이 이를 복구하지 않는 것을 UI에서 명시한다.
- same revision의 동일 제출 ID는 처리 효과 1회, ID만 재사용하면서 본문을 바꾸면 충돌. 성공 시 record/outbox transaction, publication은 안정된 marker로 확인 후 실행. 에러는 private sanitized error message, 원문/모델 prompt/응답은 기록 금지.
- 현재 `cli.cmd_grade`의 모든 질문에 대한 채점/미답 처리와 ModelError 경계를 재사용한다(`cli.py:245-334`). `Attestation.band`, 브라우저 role, 댓글 actor 검사는 새 성공 원장의 권위가 아니다.

### P4. Actions relay와 최종 독립 확인

- 기본 경로는 App webhook 비활성 + GitHub-hosted Actions relay. `pull_request_target`는 신뢰된 base/default 소스만 checkout, PR head는 Git object 데이터로만 fetch한다. App private key/사용자 답변을 Actions에 두지 않는다.
- Relay는 OIDC로 repo/PR/current head 및 자체 계산한 risk/policy digest를 서버에 전달한다. 서버도 API/snapshot을 다시 계산한 뒤 비교한다. 인증은 PyJWT/JWKS로 issuer/audience/시간·repo/owner ID·workflow/ref/event를 모두 확인한다.
- 성공 기록을 만든 봇은 `workflow_dispatch`로 동일 trusted workflow의 verifier 경로를 요청한다. verifier는 서버에서 비민감 receipt binding만 읽고 자체 Github 현재 PR/risk와 대조한 뒤 authenticated report를 보낸다. 늦은 응답은 과거 기록 검증만 할 수 있고 새 SHA 상태는 바꾸지 않는다.
- workflow file은 trusted default branch에 배포되어야 실제 pull_request_target/dispatch 경로가 존재한다. 로컬 localhost는 GitHub-hosted runner에서 접근할 수 없으므로 실제 HTTPS 연결 전에는 manual sync와 mocked relay로만 확인한다.
- 환경 모드 변수(예 `LASTHUMAN_RUNTIME=app`)로 기존 `.github/workflows/comprehension-gate.yml:56-377`와 `dashboard.yml`의 legacy 경로를 함께 차단한다. 미설정 시 기존 경로는 보존. 새 relay mode에서도 미설정 endpoint를 성공으로 넘기지 않는다.
- [OIDC](https://docs.github.com/en/actions/reference/security/oidc), [pull_request_target 안전 경계](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target), [commit status 상태](https://docs.github.com/en/rest/commits/statuses#create-a-commit-status).

### P5. Merge·대시보드·가이드

- `server`의 trusted receipt + 실제 PR merged metadata를 사용하고 기존 댓글-marker collector를 새로운 권위로 사용하지 않는다. close without merge는 분모 제외, 중복 merge 이벤트는 1건, 성공 기록은 머지 시점 이전·검토 head에 한정.
- 기존 `ledger.zone_of`/`MIN_SAMPLE`/CODEOWNERS 분석을 재사용한다(`ledger.py:28,135-143,230+`). 기존 ledger의 모든 touched module을 attested로 처리하거나 max 인원을 고유 인원으로 쓰는 경로는 사용하지 않는다.
- `/dashboard`는 앱에 허용된 repo를 현재 조회할 수 있는 로그인 사용자에게 모듈 집계만 반환한다. 성공 원문은 해당 PR 작성자 본인만, 공개 PR 링크는 GitHub 권한에 따른다.
- `docs/runbooks/github-app.md`: 등록→권한→키 보관→로컬 command/서버/worker→사용자 로그인→실제 모델→HTTPS 공개→relay cutover→required App source→테스트 PR의 전 과정→rollback 순서. 구현된 env/commands를 정확히 기재한다.
- Azure 서비스의 자동 프로비저닝은 하지 않는다. 이 단계의 local SQLite는 단일 프로세스 개발 환경이며, 다중 인스턴스 운영 DB와 암호화·백업·App Service 운영 승인 전 production-ready라고 부르지 않는다.

## Risks & Mitigations

- 아직 App/모델/HTTPS 없음: 애플리케이션과 설정 가이드를 제공하되 실제 봇 댓글/최종 성공/원격 event 실행 완료는 미달로 기록. 소유자가 키를 직접 승인된 위치에 넣은 뒤 live 검증한다.
- 인간 소유 계약: 기존 프롬프트/가중치/pass-hold는 손대지 않고 새 성공 데이터 필드와 메모리 TTL은 handoff 승인 필요.
- public followup 댓글은 실패 이력을 남김: 도입하지 않는다. 시작/성공 댓글만.
- Action single writer와 verifier 역할 혼동: 봇만 status POST, Action은 independent report. 오래된 report/payload는 current GitHub metadata와 재대조.
- PR code execution/키 노출: trusted base code만 실행, PR blob은 읽기 데이터, git credential env 비출력, URL allowlist.
- remote API 응답 유실: publication ID/attribution 재조회, single worker 직렬화. atomic DB와 remote GitHub를 하나의 transaction처럼 주장하지 않는다.
- memory-only 재시작: raw answer resume는 불가하며 사용자에게 재제출 안내. 성공 receipt와 publication은 저장되어 중복 판정을 요구하지 않는다.
- SQLite 운영 한계: 하나의 process/worker 인스턴스로 제한. 다중 instance/DB 전환은 별도 운영 설계, 최소 HTTP server command도 이를 명시한다.
- 모델 입력/오류 노출: raw source/answers는 허가한 모델 서비스만 호출하고 응답/trace를 로그·PR로 내보내지 않는다.
- existing startup migration: storage schema는 새 service 소유 DB에만 적용하며 기존 저장소/사용자 데이터 삭제 없음.

## Verification Steps

- 기존 pytest만 사용한다. 먼저 runner를 실행하고 missing dependency가 있거나 manifest 변경 후에만 `.[dev,bot]` 의존성을 설치한다. 새 lint/type tooling을 도입하지 않는다.
- GitHub 및 model adapter를 fake로 교체한 HTTP flow: sync PR→author OAuth→questions→submit→hold private→resubmit pass→durable receipt→Action proof→status success→merged event→module dashboard.
- 별도 failures: 비작성자/CSRF/wrong OIDC claims/동일 SHA 여러 PR/stale head/부족한 질문/0번 정답/질문 공유 anchor/radio ID/응답 유실/중복 POST/TTL/restart 모델 오류/보호되지 않은 checkout.
- workflow YAML 및 request payload에 PR string 직접 보간·head checkout·App key·private answers·public hold가 없는지 검사. legacy off/new on의 조건과 parent default ref를 맞춘다.
- runbook command/var와 실제 CLI/env/parser/route를 대조. 최종 `git diff --check`와 source scope를 확인한다.
- live 단계는 소유자의 App/HTTPS/model 준비 후 허가된 PR에서 실제 comment attribution, current head status, 작성자 로그인, merge/dashboard 증분으로 입증한다. 허가 없이 기존 PR을 병합하거나 보호 규칙을 낮추지 않는다.

## Review Summary

- Phase 0 Frame Gate: 완료. 현재 패키지와 확인한 사용자 범위에 맞춰 역할·공개면·로컬/원격 실행 조건을 구분했다.
- Complexity Gate: 인증/API/저장/상태·재시도·workflow 변경이므로 면제 대상 아님.
- Phase 1: 요청되지 않아 미실행.
- 계획 검토: **attempted and failed, 0/1 rounds**. 모델 접근 설정 오류로 종합 검토 결과를 얻지 못했다. 전역 도구 설정은 변경하지 않았다.
- **Final verdict: NOT REVIEWED.** 요구사항 gap 분석과 공식 API 조사는 수행했지만 이를 architect/critic/resolver의 계획 승인으로 대신하지 않는다.
- 구현 전 새 성공 기록 필드·미완료 답변 30분 메모리 계약·성공 후 Action 검증 경로의 승인을 handoff에서 확인한다.
- 실제 App/모델/HTTPS/보호 설정은 미준비다. 소스 구현과 대역 기반 흐름이 완료되어도 실제 원격 자동화 수용 조건은 소유자의 설정과 live 관찰 전에는 미완료다.

## Implementation Handoff — 2026-09-08

- 복구 후 작업 브랜치: `feat/github-app-runtime`. 커밋·푸시는 하지 않았다.
- 구현: App installation 인증/API, 고정 PR 스냅샷·기존 위험 계산, Flask OAuth/작성자 인가, 웹 제출·비공개 보완, SQLite 성공 기록·발행 outbox, OIDC relay·독립 Action 확인, 머지/모듈 집계, 설정 기반 legacy 전환.
- 가이드: `docs/runbooks/github-app.md`; README에 연결했다. 비밀/미완료 답변은 Git/DB/공개 댓글로 저장하지 않는다. 운영 context는 App 설정과 공개 HTTPS 준비 후 활성화한다.
- Python 실행 환경은 `.work/venv`다. 시스템 Python 3.14에는 ensurepip/pytest가 없어 기존 uv와 Python 3.13으로 선언된 의존성을 설치했다.
- 전체 기존/신규 pytest 156개 통과. 실제 로컬 HTTP `/healthz`와 scheduler 기동·종료는 테스트 대역을 사용해 확인했다.
- 실제 GitHub 접근은 `hunhoon21/the-last-human` PR 1의 스냅샷 읽기만 수행했다. App 키가 아닌 현재 CLI의 읽기 접근으로 확인한 것이므로 App 인증 완주 증거가 아니다.
- 독립 구현 검토 후 202 응답의 완료 대기·재시작 재접수, 검증 dispatch 복구, 질문 생성 실패의 재준비 화면, 실제 merge parent와 저장된 base/head 연결을 보완했다. 도구 출력의 credential 마스킹을 소스의 리터럴 버그로 본 발견은 AST로 반증하여 제외했다. 수동 동기화의 pending 발행은 최종 success의 독립 Action 검증을 우회하지 않으므로 차단 사유로 채택하지 않았다.
- 외부 수용 조건 AC10은 **blocked**: App 등록/설치·개인키·OAuth client secret·승인 모델·공개 HTTPS·trusted main workflow·필수 상태의 기대 App 설정이 필요하다. 실제 봇 댓글/상태/머지/배포는 실행하지 않았다.
- 로컬 단일 프로세스/SQLite MVP다. 대규모 PR·binary·pure rename·merge queue·동일 head의 복수 PR은 지원 밖이며, 모호한 merge lineage는 미측정이다. 이 제한을 숨겨 운영 완료로 표시하지 않는다.
