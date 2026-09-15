# GitHub App 로컬 실행과 전환 가이드

Mac에서 새 App 개인키·Azure OpenAI·Microsoft Dev Tunnels를 준비하고 실제 PR을 머지하는 순서는 [첫 데모 PR 가이드](first-demo-macos.md)를 따른다. 아래 문서는 설정과 런타임 계약을 설명하는 기준 문서다.

## 지금 기준

- GitHub App 서버, 브라우저 로그인, PR 동기화, 제출, receipt, Actions relay 코드는 이미 있다.
- 아직 운영자 작업은 App 등록, 키 보관, 저장소 설치, 모델 자격 증명, 공개 HTTPS, 브랜치 보호, 실PR 확인이다.
- 이 경로는 Flask + SQLite 단일 프로세스 기준이다. `.work/lasthuman.sqlite3` 는 개인 개발용 저장소이며, 배포용 영구 저장소를 대신하지 않는다.

문서에 적은 owner/repo/origin 값은 현재 데모 기준 예시다. 실제 운영에서는 `TLH_REPOSITORY`,
`TLH_REPOSITORY_ID`, `TLH_OWNER_ID`, `TLH_BASE_URL`, `TLH_CHECK_NAME`,
`TLH_PRESENTATION_NAME`, `TLH_PRESENTATION_LOCALE`, 각 presentation limit 값을 대상에 맞게 바꾸며,
이를 위해 renderer/service 소스를 고치지 않는다.

| 항목 | 현재 결정 |
| --- | --- |
| App owner | `hunhoon21` |
| 대상 저장소 | `hunhoon21/the-last-human` |
| 저장소 ID | `1361123778` |
| owner ID | `36983960` |
| 기본 워크플로 | `lasthuman-app.yml` |
| trusted ref | `refs/heads/main` |
| 개발 상태 이름 | `comprehension-gate-dev` |
| 실사용 상태 이름 | `comprehension-gate` |
| 웹훅 | 끔 |

## GitHub App 설정

1. GitHub `Settings -> Developer settings -> GitHub Apps`에서 기존 제품 App이 있는지 먼저 확인한다. 있으면 재사용하고 필요한 개인키·client secret만 교체한다. 없을 때만 `New GitHub App`에서 새 App을 만든다.
2. owner는 `hunhoon21` 개인 계정으로 둔다.
3. `Homepage URL` 은 `https://github.com/hunhoon21/the-last-human` 으로 둔다.
4. `Callback URL` 은 `http://localhost:8000/auth/github/callback` 으로 둔다.
5. `Webhook` 은 켜지 않는다.
6. 설치 가능 범위는 `Only on this account`로 두고 아래 권한 표를 설정한 뒤 App을 생성한다.
7. App 설정의 `Generate a private key`로 PEM 파일을 내려받고, `Generate a new client secret`으로 사용자 로그인용 비밀을 만든다. 키 원문은 채팅이나 PR에 보내지 않는다.
8. `Install App`에서 `Only select repositories`를 고르고 `the-last-human` 하나만 설치한다. 권한 변경 후에는 설치 업데이트 승인도 마친다.
9. App 설정의 `App ID`·`Client ID`, 설치 상세 URL의 `Installation ID`를 구분해 환경 파일에 넣는다. App ID와 설치 ID는 비밀키나 토큰이 아니다.

같은 App을 OAuth 로그인과 App API 호출 둘 다에 쓴다. 로그인은 PKCE `S256` 과 `state` 를 함께 사용한다.

| 권한 | 값 |
| --- | --- |
| Repository metadata | 기본 읽기 |
| Contents | Read-only |
| Pull requests | Read and write |
| Commit statuses | Read and write |
| Actions | Read and write |
| Checks | 기본은 끔. 보조 App Check를 쓸 때만 Read and write |

`Contents`는 코드 읽기, `Pull requests`는 PR 조회·일반 댓글, `Commit statuses`는 권위 있는 최종 게이트 결과, `Actions`는 성공 기록의 독립 재검사를 요청하는 `workflow_dispatch`에 사용한다. `Checks`는 기본값 `TLH_CHECK_RUNS=false`일 때 요청하지 않고, owner가 App 권한 변경과 설치 업데이트를 모두 승인한 뒤에만 켠다. Checks 전용 installation token은 `checks:write`만 별도로 요청하고 캐시한다. 기본 토큰은 기존 권한을 유지하므로 Checks 권한 오류가 댓글·최종 상태·검증 요청을 함께 막지 않는다. Actions의 `GITHUB_TOKEN` 권한을 넓히거나 코드를 자동으로 push하는 권한은 요청하지 않는다.

## 로컬 비밀 파일과 환경 변수

로컬 비밀 파일은 `~/.config/the-last-human/github-app/runtime.env`로 둔다. 먼저 디렉터리와 빈 환경 파일을 준비한다.

```bash
mkdir -p "$HOME/.config/the-last-human/github-app"
chmod 700 "$HOME/.config/the-last-human" "$HOME/.config/the-last-human/github-app"
touch "$HOME/.config/the-last-human/github-app/runtime.env"
chmod 600 "$HOME/.config/the-last-human/github-app/runtime.env"
```

내려받은 PEM을 이 디렉터리의 `private-key.pem`으로 이동한다. 아래 변수 표를 보고 환경 파일을 편집한다. 각 줄은 `변수명='값'` 형식이며, App/설치 ID와 비밀은 본인 설정에서 가져온다. `TLH_SECRET_KEY`는 로컬에서 `openssl rand -hex 32`로 생성해 보관한다. 이 파일은 실행 전에 내용을 직접 확인한 운영자 소유 파일이어야 한다.

```bash
chmod 600 "$HOME/.config/the-last-human/github-app/private-key.pem"
set -a
. "$HOME/.config/the-last-human/github-app/runtime.env"
set +a
```

| 변수 | 값 또는 규칙 |
| --- | --- |
| `TLH_APP_ID` | GitHub App 설정 화면의 숫자 ID |
| `TLH_CLIENT_ID` | GitHub App 설정 화면의 client ID |
| `TLH_CLIENT_SECRET` | GitHub App 설정 화면에서 만든 secret |
| `TLH_PRIVATE_KEY_FILE` | `~/.config/the-last-human/github-app/private-key.pem` |
| `TLH_INSTALLATION_ID` | `hunhoon21/the-last-human` 설치의 숫자 ID |
| `TLH_REPOSITORY` | `hunhoon21/the-last-human` |
| `TLH_REPOSITORY_ID` | `1361123778` |
| `TLH_OWNER_ID` | `36983960` |
| `TLH_BASE_URL` | 개발은 `http://localhost:8000`, 실사용은 운영자가 정한 공개 `https` origin |
| `TLH_SECRET_KEY` | 32자 이상 랜덤 문자열 |
| `TLH_DATABASE` | 기본값 `.work/lasthuman.sqlite3` |
| `TLH_MODE` | `development` 또는 `live` |
| `TLH_STATUS_CONTEXT` | 개발은 `comprehension-gate-dev`, 실사용은 `comprehension-gate` |
| `TLH_CHECK_RUNS` | 기본값 `false`. owner가 `Checks: Read and write` 승인과 설치 업데이트를 끝낸 뒤에만 `true` |
| `TLH_CHECK_NAME` | 보조 Check 표시 이름. 기본값 `The Last Human` |
| `TLH_WORKFLOW` | `lasthuman-app.yml` |
| `TLH_WORKFLOW_REF` | `refs/heads/main` |
| `TLH_OIDC_AUDIENCE` | 서버와 Actions가 똑같이 쓰는 audience 문자열 |
| `TLH_PRESENTATION_NAME` | 공개 카드/보조 Check의 표시 이름. 기본값 `The Last Human` |
| `TLH_PRESENTATION_LOCALE` | 기본값 `ko`, 선택값 `en` |
| `TLH_PRESENTATION_MAX_CHARS` | 공개 카드 전체 예산. 기본값 `6000` |
| `TLH_PRESENTATION_REASON_LIMIT` | 대표 reason group 수. 기본값 `3` |
| `TLH_PRESENTATION_DETAIL_LIMIT` | detail row 수. 기본값 `10` |
| `TLH_PRESENTATION_PATHS_PER_GROUP` | reason group당 path 예시 수. 기본값 `2` |
| `LASTHUMAN_PROVIDER` | 모델 공급자 선택 |
| `LASTHUMAN_AUTH_MODE` | 생략/빈 값/`default`는 기존 자격 증명 경로, `azure-cli`는 명시적 Entra CLI 경로 |
| `LASTHUMAN_MODEL` | 배포명 또는 모델명 |
| `LASTHUMAN_API_KEY` | 기존 `default` 모드의 모델 API 키. `azure-cli` 모드에서는 사용하지 않음 |
| `LASTHUMAN_TOKEN` | 기존 `default` 모드의 수동 Bearer 토큰. 자동 갱신하지 않음 |
| `LASTHUMAN_ENDPOINT` | 전체 Chat Completions URL. 지정하면 기본 endpoint 조합보다 우선 |
| `AZURE_OPENAI_ENDPOINT` | 전체 URL을 지정하지 않을 때 사용하는 Azure OpenAI resource origin |
| `AZURE_OPENAI_API_VERSION` | 기본 deployment-scoped URL의 API 버전 |
| `AZURE_TENANT_ID` | `azure-cli` 모드에서 필요한 tenant GUID |
| `AZURE_SUBSCRIPTION_ID` | `azure-cli` 모드에서 필요한 subscription GUID |
| `AZURE_OPENAI_SCOPE` | `azure-cli` 모드에서 필요한 실제 추론 scope |

추가 제약은 아래와 같다.

- `TLH_CLIENT_SECRET` 와 `TLH_SECRET_KEY` 는 32자 미만이면 서버 시작 자체가 거부된다.
- `TLH_PRIVATE_KEY_FILE` 는 symlink 이면 안 되고, group/other 권한이 있으면 거부된다.
- `TLH_BASE_URL` 은 path, query, fragment, 내장 자격 증명을 포함하면 안 된다.
- 개발 모드는 loopback host만 허용한다. 실사용 모드는 `https` 만 허용한다.
- 실사용 모드는 `TLH_WORKFLOW_REF=refs/heads/main` 을 강제한다.
- 실사용 모드에서 상태 이름이 `-dev` 로 끝나면 거부된다.
- Actions 쪽에는 App private key, client secret, 모델 자격 증명을 넘기지 않는다.
- `TLH_CHECK_NAME` 은 보조 Check 표시 이름이고, branch protection에 걸어 두는 권위 있는 값은 계속 `TLH_STATUS_CONTEXT=comprehension-gate` 다.
- `TLH_PRESENTATION_LOCALE=en` 은 카탈로그 분리용 seam 이다. scorer가 주는 근거 문자열은 원문 언어로 남을 수 있으므로, 완전한 자동 번역을 약속하지 않는다.
- 개발/`localhost` 경로는 production Check를 발행하지 않는다.
- 이 문서는 Azure 자원을 자동으로 만들지 않는다. 기존 모델 자격 증명만 연결한다.

## Azure CLI / Entra 모델 인증

API 키 인증이 비활성화된 리소스는 `LASTHUMAN_AUTH_MODE=azure-cli`를 사용한다. `disableLocalAuth=true`를 바꾸지 않으며, 모델 인증을 위해 App 서버를 Actions로 옮기지 않는다.

```text
Mac의 Azure CLI 로그인
  -> CLI가 관리하는 사용자 로그인 캐시
  -> App의 AzureCliCredential + 재사용하는 SDK 토큰 제공자
  -> 프로세스 메모리의 단기 Bearer 토큰
  -> Azure 모델

Actions -> 기존 OIDC relay / 독립 검증 (개인 Azure 자격 증명 전달 없음)
```

`azure-identity`는 `.[bot]` 선택 의존성이다. 기본 패키지와 기존 API 키/수동 토큰 경로는 Azure SDK 없이도 유지된다. 새 모드는 `LASTHUMAN_PROVIDER=azure`에서만 동작하고, 이전 환경에 API 키나 수동 토큰이 남아 있어도 그것으로 fallback하지 않는다. CLI·의존성·로그인·갱신이 실패하면 모델 처리 오류로 안내한다.

### 로컬 준비와 로그인

이미 `az --version`이 동작하면 설치를 반복하지 않는다. Intel Mac의 Homebrew 소스 빌드가 막힌 환경에서는 승인된 Python 패키지 index를 통해 다음과 같이 CLI를 독립된 uv tool 환경에 설치할 수 있다. 공식 macOS 설치 안내의 권장 경로와 다른 격리 설치 방식이며, 조직에서 허용하는 배포 경로를 사용한다.

```bash
uv tool install --python 3.12 azure-cli &&
export PATH="$HOME/.local/bin:$PATH" &&
az --version
```

리소스의 실제 tenant/subscription GUID와 View Code의 scope를 먼저 `runtime.env`에 설정한다. 정상적으로 로드한 뒤 서버를 실행할 **같은 OS 계정**에서 로그인한다.

```bash
az login --tenant "$AZURE_TENANT_ID" &&
az account set --subscription "$AZURE_SUBSCRIPTION_ID" &&
az account show --subscription "$AZURE_SUBSCRIPTION_ID" \
  --query '{subscriptionId:id,tenantId:tenantId,name:name}' --output json
```

포털의 브라우저 로그인은 CLI 로그인을 대신하지 않는다. 필요한 모델 추론 권한도 별도로 있어야 한다. 회사 정책으로 MFA나 재로그인이 요구되면 로그인 단계로 돌아간다. 무기한 자동 인증을 보장하지 않는다.

다음 요청은 모델을 호출하지 않고 토큰 취득 경로와 만료 메타데이터만 확인한다. `accessToken` 자체를 출력하지 않는다.

```bash
az account get-access-token \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --scope "$AZURE_OPENAI_SCOPE" \
  --query '{expires_on:expires_on,tokenType:tokenType}' --output json
```

토큰 명령에 `--tenant`와 `--subscription`을 동시에 사용하지 않는다. 앱도 subscription으로 요청하며, 선택한 구독의 tenant가 `AZURE_TENANT_ID`와 일치하는지 SDK 토큰 사용 전에 별도 확인한다.

### 모드 설정과 경계

```bash
LASTHUMAN_PROVIDER='azure'
LASTHUMAN_AUTH_MODE='azure-cli'
LASTHUMAN_MODEL='REPLACE_WITH_DEPLOYMENT_NAME'
LASTHUMAN_ENDPOINT='https://REPLACE_WITH_RESOURCE_HOST/openai/v1/chat/completions'
AZURE_TENANT_ID='REPLACE_WITH_TENANT_GUID'
AZURE_SUBSCRIPTION_ID='REPLACE_WITH_SUBSCRIPTION_GUID'
AZURE_OPENAI_SCOPE='REPLACE_WITH_PORTAL_SCOPE'
```

지원 scope는 `https://ai.azure.com/.default`와 `https://cognitiveservices.azure.com/.default`다. 실제 서비스의 View Code와 맞는 값을 사용한다. 기본 ARM 토큰, Graph 토큰, 프로젝트 endpoint, Responses endpoint를 대신 넣지 않는다.

OpenAI SDK의 `base_url`이 `.../openai/v1/`라면, 이 앱의 `LASTHUMAN_ENDPOINT`에는 `.../openai/v1/chat/completions`까지 포함한다. 기본 주소만 넣으면 Azure 호출 전에 형식 오류로 중단한다.

자동 취득한 사용자 토큰은 HTTPS Public Azure 추론 호스트(`*.openai.azure.com`, `*.services.ai.azure.com`, `*.cognitiveservices.azure.com`)의 Chat Completions로만 보낸다. 다른 호스트로의 redirect, 사용자 지정 API 프록시·APIM, sovereign cloud는 현재 CLI 모드에서 지원하지 않는다. 기존 `default` 모드의 OpenAI 호환 endpoint 지원은 유지된다.

토큰 제공자는 프로세스에서 재사용하며 갱신 판단은 SDK/CLI에 맡긴다. 토큰을 `runtime.env`, SQLite, Actions Secrets에 저장하거나 CLI 캐시를 다른 컴퓨터·사용자에게 복사하지 않는다. macOS의 Azure CLI 캐시를 Keychain 암호화 저장소라고 가정하지 말고 민감한 사용자 파일로 관리한다.

이 Azure 로그인은 서버 운영자의 모델 호출 권한이다. 웹에서 변경 확인을 수행하는 PR 작성자의 GitHub OAuth와는 별개이며, 참여자에게 서버 운영자의 Azure 자격 증명을 나눠 주지 않는다.

인증 코드 수정도 `interview.py`의 policy fingerprint를 바꾸므로 새 코드·의존성을 trusted `main`에 반영하고 App 실행 소스와 맞춘다. 질문 프롬프트·위험 점수·pass/hold·receipt schema는 바꾸지 않는다.

## 로컬 실행

기본은 `uv`와 프로젝트별 `.venv`, Python 3.12다. 프로젝트에 별도 버전이 지정되어 있으면 그 환경을 우선한다. 기존 가상환경이 있으면 용도와 버전을 확인하고 재사용하며 임의로 덮어쓰지 않는다.

```bash
uv python install 3.12
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[dev,bot]'
. .venv/bin/activate
```

현재 의존성 선언을 그대로 사용한다. 이 절차를 위해 `uv init`이나 lockfile 전환은 하지 않는다.

서버 실행은 아래로 고정한다.

```bash
set -a
. "$HOME/.config/the-last-human/github-app/runtime.env"
set +a
python -m lasthuman.server serve --host 127.0.0.1 --port 8000
```

확인은 아래 순서로 한다.

```bash
curl http://localhost:8000/healthz
```

- 브라우저와 callback 은 `http://localhost:8000` 으로 쓴다.
- 서버 바인드는 `127.0.0.1` 이어도 된다. 런타임이 `TRUSTED_HOSTS` 에 `localhost` 와 `127.0.0.1` 둘 다 넣는다.
- `/healthz` 는 무인증 liveness 이다. 로그인과 App 검증은 따로 필요하다.

특정 PR을 수동으로 다시 읽고 바로 배출하려면 아래를 쓴다.

```bash
PR_NUMBER=1
python -m lasthuman.server sync --pr "$PR_NUMBER"
python -m lasthuman.server flush
```

- `sync --pr` 는 웹훅 대체가 아니라 수동 재동기화 경로다.
- `PR_NUMBER=1`은 예시다. 먼저 자신이 연동하기로 승인한 PR 번호로 바꾼다. CLI `sync`는 실제 GitHub를 조회하고 봇 댓글을 게시할 수 있다.
- CLI `sync`는 한 번 동기화한 뒤 발행 대기 작업을 한 번 처리한다. 같은 DB를 쓰는 별도 프로세스에서 `serve`와 `sync`/`flush`를 동시에 실행하지 않는다. 서버 실행 중에는 웹의 동기화 버튼을 사용한다.
- `flush` 는 남아 있는 outbox 를 다시 보낸다.
- 질문 생성과 채점에는 모델 자격 증명이 필요하다. 모델이 없으면 실제 질문 경로는 진행되지 않는다.

## 접근 제약과 데이터 보관

| 경로 또는 데이터 | 실제 동작 |
| --- | --- |
| `/healthz` | 무인증 |
| `/dashboard` | GitHub 로그인 필요 |
| `/prs/<pr>` | 해당 PR 작성자만 접근 가능 |
| `/receipts/<receipt_id>` | 해당 receipt 작성자만 접근 가능 |
| `/api/actions/*` | GitHub Actions OIDC 토큰 필요 |
| 브라우저 cookie | opaque SID 만 저장한다. GitHub token 은 들어가지 않는다 |
| user OAuth token | 서버 메모리에만 최대 30분 둔다 |
| Azure 사용자 로그인 상태 | App 실행 OS 계정의 Azure CLI 관리 캐시. 앱 DB·Actions에 복사하지 않음 |
| Azure 모델 access token | `azure-cli` 모드의 SDK 제공자가 프로세스 메모리에서 관리 |
| raw answer, 보류 피드백, request_id | 서버 메모리에만 두고 재시작 또는 TTL 이후 사라진다 |
| snapshot, 질문, receipt, outbox, merge 기록 | `.work/lasthuman.sqlite3` 에 저장된다 |
| successful answers | receipt 에만 저장되며 작성자 본인 경로로만 다시 본다 |

`TLH_SECRET_KEY` 는 Flask 설정에 쓰이지만, 브라우저 세션 식별자는 메모리 세션을 가리키는 opaque SID 다.

## 서버가 실제로 하는 일

1. `/auth/github` 는 같은 GitHub App의 OAuth client로 로그인 URL을 만들고, PKCE와 `state` 를 세션에 묶는다.
2. `/auth/github/callback` 은 code 교환 뒤 GitHub 사용자 정보와 대상 저장소 접근을 다시 확인한다.
3. `/prs/<pr>/sync` 또는 Actions relay 는 PR 메타데이터와 snapshot 을 다시 읽는다.
4. snapshot 은 GitHub API와 `git fetch` 로 만든다. PR head를 checkout 해서 실행하지 않는다.
5. snapshot 이 트리거 대상이 아니면 질문 없이 `neutral` 로 저장한다. 실사용 자동 경로에서는 성공 상태를 큐에 넣는다.
6. snapshot 이 트리거 대상이면 시작 comment 를 큐에 넣고, 실사용 공개 origin 일 때만 pending 상태도 큐에 넣는다.
7. 질문 생성에는 제공된 앵커 목록을 enum으로 제한한 Structured Outputs를 사용한다. 응답의 `questions` 배열을 기존 질문 목록으로 변환하며, App은 여전히 정확히 3개를 요구한다. 작성자는 모든 질문에 정확히 한 번씩 답해야 한다.
8. 답변이 보류되면 힌트만 메모리에 남고, raw answer 는 DB에 저장하지 않는다.
9. 답변이 통과되면 receipt 와 successful answers 를 저장하고, receipt 검증용 `workflow_dispatch` 를 outbox 에 넣는다.
10. 검증 단계는 receipt 의 binding 과 새로 읽은 snapshot binding 을 다시 비교한다. `repository_id`, `pr`, `head_sha`, `base_sha`, `policy_version`, `snapshot_id`, `score`, `triggered` 가 하나라도 달라지면 verified 로 바꾸지 않는다.
11. verified 뒤에는 성공 comment 와 성공 상태를 큐에 넣는다.
12. 백그라운드 작업자는 일시적인 발행 오류를 제한된 간격으로 재시도한다. 검증 workflow를 접수했지만 5분 동안 검증 결과가 없으면 다시 요청하며 총 3회로 제한한다. 이후에는 실패한 Actions 실행을 확인하고 재실행한다. 성공 기록을 새로 만들거나 보완 답변을 공개하지 않는다.

머지 집계는 저장된 PR head와 머지 커밋의 부모 관계로 머지 직전 base를 대조한다. 일반 merge commit과 단일 커밋의 squash를 지원하며, 다중 커밋 rebase/squash처럼 현재 경로에서 기준 리비전을 확정하지 못하는 형태는 미측정으로 남긴다.

snapshot 은 아래 형태를 지원하지 않는다.

- merge queue PR
- 같은 head SHA 를 공유한 열린 PR
- 200개 초과 파일
- 10000줄 초과 변경
- 4 MiB 초과 diff
- binary 파일
- text patch 없는 파일
- 순수 rename

## Actions wiring

질문 생성 방식이 바뀌어도 Actions의 책임은 바뀌지 않는다. 아래 relay는 계속 trusted main에서 위험도와 인증 결속을 독립 검증하며, 모델 호출은 App 서버에서 수행한다. 새로운 CI 작업, Azure 자격 증명 전달, 자동 머지 조건은 추가하지 않는다.

`LASTHUMAN_RUNTIME=app` 을 켜면 `lasthuman-app.yml` relay workflow 가 동작하고 `dashboard.yml` 은 `vars.LASTHUMAN_RUNTIME != 'app'` 조건 때문에 멈춘다. 예전 `.github/workflows/comprehension-gate.yml` 파일은 제거되지만, 권위 있는 최종 신호는 그대로 commit status context `comprehension-gate` 다. 파일을 지워도 과거 Actions 실행 기록과 이미 남은 PR 댓글은 삭제되지 않는다.

`lasthuman-app.yml` 의 실제 성격은 아래와 같다.

- 이벤트는 `pull_request_target` 의 `opened`, `synchronize`, `reopened`, `edited`, `labeled`, `unlabeled`, `closed` 와 `workflow_dispatch` 하나다.
- workflow token 권한은 `contents: read`, `pull-requests: read`, `statuses: read`, `id-token: write` 만 쓴다.
- run title은 준비 단계에서 `Prepare PR #...`, receipt 재검사에서는 `Verify receipt ...` 로 보인다.
- workflow 는 PR head가 아니라 저장소 기본 브랜치를 checkout 한다.
- relay 코드는 `python -m lasthuman.server.relay` 로 trusted source 에서만 돈다.
- PR 이벤트에서는 metadata-only binding 을 서버에 넘긴다.
- receipt 검증 이벤트에서는 서버에서 receipt binding 을 받고, Actions 쪽에서도 snapshot 을 다시 읽어 같은 binding 인지 확인한 뒤 검증 요청을 보낸다.
- 서버는 OIDC 에서 issuer, audience, repository, repository_id, owner_id, workflow 파일, ref, event name 을 모두 확인한다.
- 보조 App Check는 선택 사항이다. `TLH_CHECK_RUNS=true`여도 현재 SHA/base/policy/snapshot 설명 보강용이며, 필수 merge gate나 branch protection 이름을 대신하지 않는다.

`workflow_dispatch` 검증 경로는 다섯 개의 실제 Actions step으로 보인다.

1. `1. Load verification receipt` — 성공 기록과 새 서버 API의 준비 여부를 확인한다.
2. `2. Read current PR snapshot` — Actions가 현재 PR의 snapshot을 독립 계산한다.
3. `3. Compare receipt and current change` — 작성자와 코드·정책·위험도 결속을 대조한다.
4. `4. Wait for server verification` — 서버 재검증 작업의 완료를 기다린다.
5. `5. Confirm GitHub gate success` — GitHub의 최신 상태가 현재 receipt의 성공인지 확인한다.

각 step은 `python -m lasthuman.server.relay --verification-step <stage> --state-file "$STATE_FILE"`로 독립 실행된다. `STATE_FILE`은 `RUNNER_TEMP/tlh-verification/state.json`의 runner-local 임시 파일이다. receipt 식별자·binding 등 명시적인 메타데이터만 저장하고, 답변 원문·질문 원문·raw diff·token은 저장하거나 artifact로 올리지 않는다. 재실행은 첫 단계부터 새 run attempt의 상태를 만든다.

첫 서버 호출은 `workflow_dispatch` 전용 OIDC 인증 `GET /api/actions/receipts/<id>/publication`이다. 새 경로가 없는 backend는 검증 요청을 보내기 전에 실패한다. 승인된 변경을 trusted `main`에 merge한 뒤 서버를 업데이트하고, 새 면담이나 검증 실행을 시작한다. 실행 중인 서버를 업데이트하지 않고 workflow만 먼저 사용하면 완료할 수 없다.

마지막 단계는 최대 180초 동안 게시를 기다린다. 서버가 현재 설정의 정확한 outbox 이벤트에 기록한 status ID와 GitHub의 최신 context별 status ID·성공 상태·receipt URL을 대조하며, PR의 head/base/작성자가 여전히 같은지도 확인한다. `verified_at`, outbox 처리 시각, 보조 Check의 성공만으로 완료하지 않는다. Actions에는 상태 쓰기 권한을 주지 않는다.

401/403, 잘못된 응답·상태 파일, 오래된 변경, 건너뛴 게시와 시간 초과는 명시적 실패다. 허용된 대기 상태만 제한된 시간 안에서 재조회한다. 로컬 테스트와 PR CI는 구현의 근거이며, 실제 trusted 서버와 현재 receipt로 다섯 단계를 완주하는 운영 확인은 별도로 남긴다.

중요한 점 두 가지가 있다.

- `TLH_BOT_URL` 은 공개 `https` origin 이어야 한다. 이 런북에서는 `TLH_BASE_URL` 과 같은 공개 origin 으로 맞춘다.
- `Prepare PR`의 성공은 면담 통과를 뜻하지 않는다. 기존 relay는 짧은 서버 작업을 최대 180초 기다리고, 서버 재시작으로 작업이 사라지면 같은 요청을 최대 2회 다시 접수한다. `Verify receipt`의 다섯 단계 성공은 조회 시점에 해당 receipt의 GitHub gate 성공까지 확인했다는 뜻이다. 사람의 면담을 runner에서 기다리지는 않으며, 새 변경 이후의 인증 재사용도 허용하지 않는다. 최종 머지 조건은 GitHub App의 commit status다.

## 개발 모드와 실사용 모드 차이

| 항목 | 개발 모드 | 실사용 모드 |
| --- | --- | --- |
| `TLH_MODE` | `development` | `live` |
| `TLH_BASE_URL` | loopback origin | 공개 `https` origin |
| commit status 게시 | 하지 않음 | pending/success 게시 |
| 보조 App Check | 발행하지 않음 | 기본값은 꺼짐. 승인 후에만 발행 |
| 시작 comment | 필요하면 로컬 전용 안내만 남김 | `/prs/<pr>` 링크 포함 |
| success status target | 없음 | `/receipts/<id>` 링크 |
| 상태 이름 기본값 | `comprehension-gate-dev` | `comprehension-gate` |

`localhost` 개발 경로에서는 상태를 GitHub에 쓰지 않는다. 대신 동기화가 comment 를 남길 수는 있고, 그 comment 는 클릭 가능한 `localhost` 링크 대신 head SHA 확인 안내만 넣는다.

## 실사용 전환 순서

1. trusted `main` 기준 코드부터 배포한다. 이때 `LASTHUMAN_RUNTIME` 은 아직 `app` 으로 켜지 않는다.
2. 공개 `https` origin, 영구 파일시스템, SQLite 백업 경로를 먼저 준비한다. DB 파일을 임시 컨테이너 파일시스템이나 배포 산출물 안으로 복사해 쓰지 않는다.
3. 실사용 서버는 개발용 `serve` 대신 WSGI 서버 한 프로세스로 띄운다.

```bash
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 'lasthuman.server.app:create_app()'
```

4. 실사용 환경에서 `TLH_MODE=live`, 공개 `TLH_BASE_URL`, `TLH_WORKFLOW_REF=refs/heads/main`, `TLH_STATUS_CONTEXT=comprehension-gate`, 올바른 `TLH_OIDC_AUDIENCE` 를 맞춘다.
5. 저장소 변수 `LASTHUMAN_RUNTIME=app` 과 `TLH_BOT_URL` 을 넣는다. audience 를 기본 저장소명과 다르게 쓸 때만 `TLH_OIDC_AUDIENCE` 도 같이 맞춘다.
   - `TLH_CHECK_RUNS` 는 기본값 `false`다. 보조 Check가 필요하면 먼저 GitHub App 설정에서 `Checks: Read and write` 를 추가하고 owner/installation 승인을 마친다.
   - 승인 뒤에는 trusted 서버의 `runtime.env` 에 `TLH_CHECK_RUNS=true` 와 필요하면 `TLH_CHECK_NAME`, `TLH_PRESENTATION_NAME`, `TLH_PRESENTATION_LOCALE`, 각 presentation limit 값을 넣는다.
   - `TLH_CHECK_NAME` 기본값 `The Last Human` 은 보조 Check 표시 이름일 뿐이며, 필수 상태 이름 `TLH_STATUS_CONTEXT` 와 다르게 유지한다.
6. 기존 CI, 리뷰, strict up-to-date 보호는 그대로 둔다. 준비되지 않은 백엔드 때문에 기존 보호를 먼저 내리지 않는다.
7. 변수 변경 뒤에는 새 PR 이벤트를 만들거나 기존 이벤트를 다시 실행한다. 이미 열려 있던 PR이 자동으로 다시 흘렀다고 가정하지 않는다.
8. App이 상태를 한 번 발행한 뒤 저장소 `Settings → Rules → Rulesets` 또는 `Branches`에서 대상 브랜치의 필수 상태를 `comprehension-gate`로 지정하고, 기대 발급자로 설치한 App을 선택한다. `TLH App relay` 작업 이름이나 보조 Check 표시 이름 `The Last Human` 만 필수로 선택하면 안 된다. 기존 CI·리뷰와 최신 base 반영 조건을 유지한다.
9. 허가된 PR에서 시작 댓글의 App attribution, 작성자 로그인, 비공개 보완, 성공 기록의 SHA, 검증 workflow, 최종 상태, 실제 머지, 대시보드 증분을 차례로 확인한다. 댓글은 기록 표시용이고 게이트의 권위는 서버 성공 기록과 독립 재검사다.

## 업데이트 / 재시작 / 재동기화

- trusted `main`의 코드, `runtime.env`, App 권한을 바꿨으면 서버를 그 trusted revision으로 다시 시작한다.
- 문구 카탈로그·렌더링 코드·locale·표시 예산·서버 origin은 별도의 presentation revision으로 추적한다. 재시작 후 현재 PR을 재동기화하면 기존 댓글과 같은 평가의 Check를 갱신하며, origin 변경 시 최종 status 링크도 갱신한다. 표시 변경 자체로 질문을 다시 생성하거나 기존 receipt의 policy binding을 바꾸지는 않는다.
- `TLH_PRESENTATION_NAME`은 카드와 Check 상세의 표시 이름이다. `TLH_CHECK_NAME`은 GitHub Check 조회에 쓰이는 식별 이름이므로 진행 중인 평가에서는 유지한다. 저장소·설치 자체를 바꾸는 경우에는 해당 ID와 별도의 `TLH_DATABASE`도 함께 설정해 다른 저장소의 운영 상태를 재사용하지 않는다.
- 공개 카드/보조 Check의 이름·locale·표시 예산을 바꿔도 질문 규칙·receipt 형식·commit status context는 바꾸지 않는다.
- 이미 열려 있는 PR은 설정 변경만으로 다시 렌더링되지 않는다. 서버가 떠 있으면 웹의 재동기화 경로를 쓰고, 서버를 내린 상태에서 수동 복구가 필요하면 승인된 절차로 `sync --pr` 와 `flush` 를 실행한다.
- 권한/API 오류는 명시적으로 드러나야 하며, 보조 Check 발행이 실패했다고 commit status 성공으로 조용히 대체하지 않는다.
- workflow 파일을 지우거나 이름을 다듬어도 예전 Actions 실행 기록은 그대로 남는다.

실사용 연결을 같은 저장소에서 먼저 연습해야 하면, 보호에 아직 걸지 않은 별도 상태 이름을 써도 된다. 다만 실사용 모드는 `-dev` 로 끝나는 이름을 거부하므로 `tlh-app-staging` 같은 별도 이름만 쓴다.

## 롤백

1. 전환 중에는 새 PR 동작을 멈추고 App 서버의 상태 발행을 중지한다. 진행 중인 새 relay/검증 실행도 개별 실행에서 취소한다.
2. 저장소 변수 `LASTHUMAN_RUNTIME`을 `app`이 아닌 값으로 돌린다. 이때 dashboard workflow 는 다시 실행될 수 있지만, 제거된 `.github/workflows/comprehension-gate.yml` 이 자동으로 되살아나지는 않는다. App writer가 이미 중지됐는지 확인한다.
3. 승인된 이전 버전과 해당 필수 상태의 기대 발급자를 함께 복구한다. 신뢰할 수 있는 기존 writer가 준비되기 전에는 상태를 대기/오류로 유지하고 보호 규칙을 풀지 않는다.
4. open PR을 자동 머지하거나 force-push 로 덮지 않는다. pending 을 억지로 green 으로 바꾸지 않는다.

## 빠른 문제 해결

| 증상 | 먼저 볼 것 |
| --- | --- |
| 로그인 401 | App 설치 범위, `TLH_CLIENT_ID`, `TLH_CLIENT_SECRET`, callback origin 일치 여부 |
| 제출 403 | PR 작성자 본인인지, `X-CSRF-Token` 또는 form `csrf_token` 이 있는지 |
| 제출 409 stale | PR이 바뀌었거나 닫혔다. 다시 sync 후 다시 제출 |
| `/healthz` 가 503 | outbox 재시도·SQLite 접근 실패·게시 스레드 종료 여부를 확인한다. `scheduler_stopped`는 요청된 스케줄러가 실제로 살아 있지 않다는 뜻이다. |
| workflow는 초록인데 상태가 안 바뀜 | 서버 작업 완료와 GitHub 발행 완료는 별개다. `/healthz`, 검증 Actions 실행, 현재 SHA의 App status를 함께 확인한다. 현재 receipt 화면은 상세 발행·재시도 상태를 모두 보여주지 않는다. |
| 첫 `receipt` 단계가 404 | 새 publication 경로의 배포 여부와 receipt가 해당 서버 DB에 존재하는지 확인한다. backend 업데이트 또는 올바른 receipt 확인 후 첫 단계부터 다시 실행한다. |
| 마지막 `publication` 단계 시간 초과 | App의 게시 대기·실패·skip 사유와 GitHub의 최신 context별 status를 확인한다. 과거의 성공이나 보조 Check만 보고 완료로 간주하지 않는다. |
| 로컬에서 comment 만 생기고 상태가 없음 | 정상이다. `localhost` 개발 경로는 상태를 쓰지 않는다 |
| 질문 생성이 안 됨 | 모델 자격 증명과 endpoint 설정 확인 |
| 질문 준비 중 개수·유형 오류 | 모델 응답의 형식 문제와 연결·인증 오류를 구분한다. [질문 생성 형식 오류](#질문-생성-형식-오류)를 참고한다. |
| 질문 생성 요청이 HTTP 400으로 실패 | 모델·API 버전의 `response_format` / strict `json_schema` 지원 확인. 스키마를 제거해 자동 우회하지 않음 |
| 모델 거절·불완전 종료 | 처리 오류로 중단. 거절 원문을 공개하지 않고 작성자의 보류나 통과로 기록하지 않음 |
| Azure CLI 인증 실패 | 같은 OS 계정의 `az login`, tenant/subscription/scope, 터미널 PATH와 `.[bot]` 의존성 확인 |
| 토큰 취득은 되는데 Azure 모델이 403 | 해당 리소스의 추론 RBAC와 endpoint를 확인. API 키 인증을 임의로 활성화하지 않음 |
| snapshot 지원 불가 | merge queue, shared head, 과대 diff, binary, 순수 rename 여부 확인 |
| `hunk new_start must be a positive integer`로 게시 스레드 종료 | 파일 삭제·내용 비우기의 `+0,0`은 유효한 diff다. 0 시작 줄 복원 수정이 포함된 trusted main으로 서버를 갱신·재시작하고 PR을 다시 동기화한다. DB나 대기 작업을 삭제하지 않는다. |
| 게시 대기열의 `stored_snapshot_invalid` | 해당 스냅샷 복원을 확인한다. 원문 데이터는 공개하지 않으며 기존 재시도 정책을 사용한다. 이 오류 하나가 다른 PR의 정상 게시를 중단시키지는 않는다. |

### 질문 생성 형식 오류

**대응:** 질문 생성 요청의 strict JSON Schema에서 앵커를 제공된 목록으로 제한한다. 응답을 로컬에서도 검증하고, 내용 형식이 잘못된 전체 묶음은 최대 한 번 다시 생성한다. 코드 반영과 실제 배포·PR 재실행은 별개다. 두 번 모두 유효하지 않으면 처리 오류로 중단하며, 성공을 보장하거나 질문 수를 줄이지 않는다.

2026-09-10 [Pylint 전용 PR #3](https://github.com/hunhoon21/the-last-human/pull/3)의
[`TLH App relay` 실행](https://github.com/hunhoon21/the-last-human/actions/runs/34447656851)에서
`Relay metadata-only event` 단계가 다음 메시지와 종료 코드 1로 실패했다.
해당 PR의 Pylint 검사는 통과했지만, 별도 App relay는 실패하고 `comprehension-gate`는 대기 상태였다.
PR의 머지 여부는 이 현상의 해결 여부를 뜻하지 않는다.

```text
relay job error: generated question type is invalid
```

이후 [데모 PR #5](https://github.com/hunhoon21/the-last-human/pull/5)의
[relay 실행](https://github.com/hunhoon21/the-last-human/actions/runs/34450685218)에서는
`generated question count is invalid`가 관측됐다. 같은 PR head의 독립 진단에서는 모델이 JSON 질문 3개를 반환했으나, 허용된 앵커를 사용한 질문이 2개여서 필터 후 부족해지는 경로를 확인했다. 질문 원문·정답·토큰은 이 기록에 포함하지 않는다.

**현재 처리 경계**

1. [`generate_questions`](../../src/lasthuman/interview.py)는 `risk.top_hunks`에 있는 앵커만 schema의 `anchor.enum`에 넣는다. 같은 앵커를 여러 질문이 선택할 수 있다. 주변 줄 번호로 범위를 넓히거나 임의의 코드 위치로 치환하지 않는다.
2. 모델 응답은 `{"questions": [...]}`이며, 질문의 기존 필드는 그대로다. 로컬에서는 정확한 필드, 허용 유형(`claim`, `consequence`, `rationale`, `structure`), 내용·기대 근거, 앵커와 요청 문항 수를 확인한다. 잘못된 객관식 보기를 조용히 서술형으로 바꾸거나 빈 보기 제거로 정답 위치를 바꾸지 않는다.
3. JSON 문법 오류와 응답 형식 오류가 **하나의 재시도 예산**을 공유한다. 정상 응답은 한 번 호출하며, 내용 형식이 맞지 않을 때만 **같은 프롬프트·같은 스키마로** 전체를 한 번 다시 생성한다. 과거의 `JSON 배열만` 보정 문구는 사용하지 않는다. 이전 부분 결과와 새 결과를 합치거나 질문을 복제하지 않는다.
4. 재생성 후에도 조건을 충족하지 못하면 `ModelError`로 중단한다. App은 이를 모델 처리 오류로 표시한다. `model_error`에는 인증·API 오류도 포함되므로 그 코드만으로 형식 오류라고 단정하지 않는다.
5. [`BotService._validated_questions`](../../src/lasthuman/server/service.py)의 개수·유형·앵커 검사는 별도의 안전장치로 유지된다. 생성기와 서비스가 같은 허용 유형 목록을 사용하지만 기준을 넓히지는 않는다.
6. `BotService.sync`는 질문 생성 전에 pending snapshot과 시작 알림 작업을 저장한다. 준비가 실패해도 대기가 보일 수 있으며, 이는 작성자의 보류가 아니라 **변경 확인 시작 전의 시스템 처리 오류**다.
7. HTTP/API 스키마 거부·인증·네트워크 오류, 모델 거절과 `finish_reason`이 `stop`이 아닌 불완전 종료는 내용 재생성으로 처리하지 않는다. 생성 요청에서 자유 형식 출력으로 조용히 fallback하지 않는다.

**지원 조건과 외부 계약**

- 현재 Azure `gpt-4.1-mini`의 Chat Completions와 기존 Entra 인증을 사용한다. 다른 모델/API를 사용한다면 strict `response_format` 지원을 확인한다. 인사 응답만 성공했다고 구조화된 질문 생성까지 지원한다고 보지 않는다.
- Azure 지원 subset에 맞춰 root object, 필수 필드, `additionalProperties: false`와 enum을 사용한다. 문항 수의 `minItems/maxItems` 제약은 API에 보내지 않으며 정확히 N개인지는 코드로 계속 확인한다.
- 생성기는 보수적인 Chat Completions 호환성 예산을 적용한다. 유형을 포함한 enum 값 합계 500개, 속성명·enum 문자열 합계 15,000자, 앵커 250개 초과 시 앵커 문자열 합계 7,500자를 넘으면 호출 전에 중단한다. 후보를 자르거나 넓히지 않으며, 공급자가 더 큰 스키마를 지원해도 이 경계는 그대로다.
- `Question` 목록과 웹 API, 선택지 ID, 성공 기록·판정·SHA 결속 형식은 변경하지 않는다. 새 wrapper는 모델 전송/파싱 경계 안에서만 사용한다. 정답과 기대 근거는 기존처럼 서버 안에 둔다.
- `call_model()`의 일반 호출과 답변 근거 판정은 생성용 스키마를 보내지 않는다. 명시적 dry-run도 그대로 유지하지만 실제 인증의 증거로 사용하지 않는다.
- 스키마 builder는 policy fingerprint가 읽는 `interview.py`에 둔다. 소스 위치와 파일 변경 시 digest 변경을 회귀 시험으로 고정한다. 이는 fingerprint 범위 유지 장치이지 전체 실행 코드의 무결성을 증명하는 것은 아니다.
- 모델이 허용된 ID를 반환한다는 것과 질문 내용이 올바르다는 것은 다르다. 질문 품질 고도화는 별도로 진행한다.

**배포와 재개**

- 수정이 trusted `main`에 반영된 뒤 터미널 C 서버를 중지하고 해당 worktree와 의존성을 갱신해 재시작한다. `interview.py` 변경은 policy fingerprint를 바꾸므로 Actions와 서버의 버전을 맞춘다.
- 제한 재생성 수정 #7 위의 후속 변경도 PR 대상은 `main`으로 둔다. 현재 snapshot은 `main` 대상만 지원하므로 기능 브랜치를 PR base로 삼으면 relay가 거부한다. #7이 미머지 상태라면 해당 커밋도 diff에 포함됨을 명시하고, #7 머지 뒤에는 중복 변경이 없는지 확인한다. PR이 열렸다는 이유로 실행 중인 서버를 feature 코드로 바꾸지 않는다.
- 필요한 경우 데모 PR도 최신 base에 맞춘 뒤 현재 head에 대한 이벤트를 발생시킨다. 이전 SHA의 인증을 재사용하지 않는다.
- 기존 실패 실행을 재실행하거나 웹에서 다시 동기화하고, 실제 질문 3개가 준비됐는지 확인한다. 코드 반영만으로 이전 실패가 자동 복구되지는 않는다.
- 이후 작성자의 변경 확인, 독립 검증, 현재 SHA의 App 상태 게시까지 완료해야 실제 흐름이 복구된 것이다. 원래 관측된 유형의 정확한 원문은 여전히 확인되지 않았으며, 모델이 항상 유효한 응답을 낸다고 보장하지 않는다.

## 첫 연동 전 최소 점검

선택한 가상환경을 활성화한 저장소 루트에서 다음을 실행한다.

```bash
python -m pytest tests/test_app_readiness.py tests/test_app_http.py tests/test_app_events.py -q
python -m lasthuman.server --help
```

이 점검은 실제 Authlib의 PKCE/토큰 교환 형식, 로컬 HTTP를 통한 모델 응답 처리, Gunicorn 프로세스 기동·종료, 웹 제출·보완·재시도와 Actions 작업 복구를 다룬다. GitHub와 모델 응답은 시험용 대역이며 App 설치·외부 모델 접근이 확인됐다는 의미는 아니다. 웹 제출 스크립트의 재시도 점검에는 기존 Node.js를 사용하고, 없으면 해당 항목만 건너뛴다.

실제 첫 연결은 **App 설치·키 설정 → `/healthz` 응답 → PR 작성자 로그인 → 허가된 PR 수동 동기화 → 웹 답변·보완** 순서다. 모델 응답이 비어 있거나 잘못되면 사람의 보류가 아닌 처리 오류로 안내한다. 일시적 오류 후 같은 답변을 다시 제출할 수 있으며, 네트워크 응답 유실 시에는 동일 요청 ID로 중복 처리를 피한다.

**원격 PR 자동화는 별도다.** 새 코드와 `lasthuman-app.yml`이 신뢰된 `main`에 있어야 하고, 공개 HTTPS·모델·App 설치와 저장소 변수 전환을 완료한 뒤 새 PR 이벤트를 발생시켜야 한다. 작업 브랜치에 파일이 있다는 것만으로 Actions가 새 봇 경로를 실행하지 않는다. 마지막으로 해당 SHA의 App 발급 필수 상태와 실제 머지·대시보드 반영을 확인한다.

## 공식 참고자료와 실제 연결 완료 기준

- [GitHub App 등록](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)
- [App 설치](https://docs.github.com/en/apps/using-github-apps/installing-your-own-github-app)
- [Installation token 인증](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
- [같은 App의 사용자 로그인](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app)
- [Actions OIDC](https://docs.github.com/en/actions/reference/security/oidc)
- [필수 상태 검사와 발급 App](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging)
- [AzureCliCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.azureclicredential)
- [Azure CLI 로그인](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-interactively)
- [MSAL 기반 Azure CLI와 캐시](https://learn.microsoft.com/en-us/cli/azure/msal-based-azure-cli)
- [Azure Structured Outputs의 요청 형식과 지원 JSON Schema](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs)

현재 구현의 자동화 시험은 GitHub/모델 대역을 사용한다. 실제 App·모델·공개 HTTPS의 일부 연결이 관측되어도, 위 9단계의 실제 PR·댓글·현재 커밋 상태·머지·대시보드 결과를 확보하기 전에는 원격 자동화 완주로 간주하지 않는다. 개인키·토큰·보류 원문은 그 증거에 포함하지 않는다.
