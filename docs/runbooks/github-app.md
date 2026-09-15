# GitHub App 로컬 실행과 전환 가이드

Mac에서 새 App 개인키·Azure OpenAI·Microsoft Dev Tunnels를 준비하고 실제 PR을 머지하는 순서는 [첫 데모 PR 가이드](first-demo-macos.md)를 따른다. 아래 문서는 설정과 런타임 계약을 설명하는 기준 문서다.

## 지금 기준

- GitHub App 서버, 브라우저 로그인, PR 동기화, 제출, receipt, Actions relay 코드는 이미 있다.
- 아직 운영자 작업은 App 등록, 키 보관, 저장소 설치, 모델 자격 증명, 공개 HTTPS, 브랜치 보호, 실PR 확인이다.
- 이 경로는 Flask + SQLite 단일 프로세스 기준이다. `.work/lasthuman.sqlite3` 는 개인 개발용 저장소이며, 배포용 영구 저장소를 대신하지 않는다.

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

`Contents`는 코드 읽기, `Pull requests`는 PR 조회·일반 댓글, `Commit statuses`는 게이트 결과, `Actions`는 성공 기록의 독립 재검사를 요청하는 `workflow_dispatch`에 사용한다. 코드를 자동으로 push하는 권한은 요청하지 않는다.

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
| `TLH_WORKFLOW` | `lasthuman-app.yml` |
| `TLH_WORKFLOW_REF` | `refs/heads/main` |
| `TLH_OIDC_AUDIENCE` | 서버와 Actions가 똑같이 쓰는 audience 문자열 |
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
7. 질문은 현재 3개를 기대한다. 작성자는 모든 질문에 정확히 한 번씩 답해야 한다.
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

`LASTHUMAN_RUNTIME=app` 을 켜면 새 relay workflow 가 동작하고, 기존 `comprehension-gate.yml` 과 `dashboard.yml` 은 `vars.LASTHUMAN_RUNTIME != 'app'` 조건 때문에 멈춘다.

`lasthuman-app.yml` 의 실제 성격은 아래와 같다.

- 이벤트는 `pull_request_target` 의 `opened`, `synchronize`, `reopened`, `edited`, `labeled`, `unlabeled`, `closed` 와 `workflow_dispatch` 하나다.
- workflow token 권한은 `contents: read`, `pull-requests: read`, `id-token: write` 만 쓴다.
- workflow 는 PR head가 아니라 저장소 기본 브랜치를 checkout 한다.
- relay 코드는 `python -m lasthuman.server.relay` 로 trusted source 에서만 돈다.
- PR 이벤트에서는 metadata-only binding 을 서버에 넘긴다.
- receipt 검증 이벤트에서는 서버에서 receipt binding 을 받고, Actions 쪽에서도 snapshot 을 다시 읽어 같은 binding 인지 확인한 뒤 검증 요청을 보낸다.
- 서버는 OIDC 에서 issuer, audience, repository, repository_id, owner_id, workflow 파일, ref, event name 을 모두 확인한다.

중요한 점 두 가지가 있다.

- `TLH_BOT_URL` 은 공개 `https` origin 이어야 한다. 이 런북에서는 `TLH_BASE_URL` 과 같은 공개 origin 으로 맞춘다.
- workflow가 초록이라고 곧바로 게이트가 통과된 것은 아니다. relay는 접수된 짧은 서버 작업이 끝날 때까지 최대 180초 기다리며, 서버 재시작으로 작업이 사라지면 동일 요청을 최대 2회 다시 접수한다. 사람의 면담을 기다리는 것은 아니다. 최종 머지 조건은 GitHub App의 commit status다.

## 개발 모드와 실사용 모드 차이

| 항목 | 개발 모드 | 실사용 모드 |
| --- | --- | --- |
| `TLH_MODE` | `development` | `live` |
| `TLH_BASE_URL` | loopback origin | 공개 `https` origin |
| commit status 게시 | 하지 않음 | pending/success 게시 |
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
6. 기존 CI, 리뷰, strict up-to-date 보호는 그대로 둔다. 준비되지 않은 백엔드 때문에 기존 보호를 먼저 내리지 않는다.
7. 변수 변경 뒤에는 새 PR 이벤트를 만들거나 기존 이벤트를 다시 실행한다. 이미 열려 있던 PR이 자동으로 다시 흘렀다고 가정하지 않는다.
8. App이 상태를 한 번 발행한 뒤 저장소 `Settings → Rules → Rulesets` 또는 `Branches`에서 대상 브랜치의 필수 상태를 `comprehension-gate`로 지정하고, 기대 발급자로 설치한 App을 선택한다. `TLH App relay` 작업 이름만 필수로 선택하면 안 된다. 기존 CI·리뷰와 최신 base 반영 조건을 유지한다.
9. 허가된 PR에서 시작 댓글의 App attribution, 작성자 로그인, 비공개 보완, 성공 기록의 SHA, 검증 workflow, 최종 상태, 실제 머지, 대시보드 증분을 차례로 확인한다. 댓글은 기록 표시용이고 게이트의 권위는 서버 성공 기록과 독립 재검사다.

실사용 연결을 같은 저장소에서 먼저 연습해야 하면, 보호에 아직 걸지 않은 별도 상태 이름을 써도 된다. 다만 실사용 모드는 `-dev` 로 끝나는 이름을 거부하므로 `tlh-app-staging` 같은 별도 이름만 쓴다.

## 롤백

1. 전환 중에는 새 PR 동작을 멈추고 App 서버의 상태 발행을 중지한다. 진행 중인 새 relay/검증 실행도 개별 실행에서 취소한다.
2. 저장소 변수 `LASTHUMAN_RUNTIME`을 `app`이 아닌 값으로 돌린다. 이때부터 기존 경로가 다시 실행될 수 있으므로 App writer가 이미 중지됐는지 확인한다.
3. 승인된 이전 버전과 해당 필수 상태의 기대 발급자를 함께 복구한다. 신뢰할 수 있는 기존 writer가 준비되기 전에는 상태를 대기/오류로 유지하고 보호 규칙을 풀지 않는다.
4. open PR을 자동 머지하거나 force-push 로 덮지 않는다. pending 을 억지로 green 으로 바꾸지 않는다.

## 빠른 문제 해결

| 증상 | 먼저 볼 것 |
| --- | --- |
| 로그인 401 | App 설치 범위, `TLH_CLIENT_ID`, `TLH_CLIENT_SECRET`, callback origin 일치 여부 |
| 제출 403 | PR 작성자 본인인지, `X-CSRF-Token` 또는 form `csrf_token` 이 있는지 |
| 제출 409 stale | PR이 바뀌었거나 닫혔다. 다시 sync 후 다시 제출 |
| `/healthz` 가 503 | outbox 재시도 중이거나 SQLite 접근 실패 |
| workflow는 초록인데 상태가 안 바뀜 | 서버 작업 완료와 GitHub 발행 완료는 별개다. `/healthz`, 검증 Actions 실행, 현재 SHA의 App status를 함께 확인한다. 현재 receipt 화면은 상세 발행·재시도 상태를 모두 보여주지 않는다. |
| 로컬에서 comment 만 생기고 상태가 없음 | 정상이다. `localhost` 개발 경로는 상태를 쓰지 않는다 |
| 질문 생성이 안 됨 | 모델 자격 증명과 endpoint 설정 확인 |
| 질문 준비 중 개수·유형 오류 | 모델 응답의 형식 문제와 연결·인증 오류를 구분한다. [질문 생성 형식 오류](#질문-생성-형식-오류)를 참고한다. |
| Azure CLI 인증 실패 | 같은 OS 계정의 `az login`, tenant/subscription/scope, 터미널 PATH와 `.[bot]` 의존성 확인 |
| 토큰 취득은 되는데 Azure 모델이 403 | 해당 리소스의 추론 RBAC와 endpoint를 확인. API 키 인증을 임의로 활성화하지 않음 |
| snapshot 지원 불가 | merge queue, shared head, 과대 diff, binary, 순수 rename 여부 확인 |

### 질문 생성 형식 오류

**대응:** 생성 응답을 검증하고, 유효한 전체 질문 묶음을 최대 한 번 다시 생성한다. 코드의 반영과 실제 배포·PR 재실행은 별개다. 두 번 모두 유효하지 않으면 처리 오류로 중단하며, 성공을 보장하거나 질문 수를 줄이지 않는다.

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

1. [`generate_questions`](../../src/lasthuman/interview.py)는 JSON 구조, 기존 허용 유형(`claim`, `consequence`, `rationale`, `structure`), 내용·기대 근거, 앵커와 유효한 질문 수를 확인한다.
2. JSON 문법 오류와 응답 형식 오류가 **하나의 재시도 예산**을 공유한다. 정상 응답은 한 번 호출하며, 형식이 맞지 않을 때만 최대 두 번째 호출을 한다. 인증·네트워크·모델 API 오류는 이 파서에서 재시도하지 않는다.
3. 형식 오류는 원래 프롬프트로 전체 묶음을 다시 생성한다. JSON 문법 오류에만 기존 JSON 안내 문구를 사용한다. 이전 부분 결과와 새 결과를 합치거나, 질문을 복제하거나, 허용되지 않은 앵커를 임의 치환하지 않는다.
4. 재생성 후에도 조건을 충족하지 못하면 `ModelError`로 중단한다. App은 이를 모델 처리 오류로 표시한다. `model_error`에는 인증·API 오류도 포함되므로 그 코드만으로 형식 오류라고 단정하지 않는다.
5. [`BotService._validated_questions`](../../src/lasthuman/server/service.py)의 개수·유형·앵커 검사는 별도의 안전장치로 유지된다. 생성기와 서비스가 같은 허용 유형 목록을 사용하지만 기준을 넓히지는 않는다.
6. `BotService.sync`는 질문 생성 전에 pending snapshot과 시작 알림 작업을 저장한다. 준비가 실패해도 대기가 보일 수 있으며, 이는 작성자의 보류가 아니라 **변경 확인 시작 전의 시스템 처리 오류**다.

**배포와 재개**

- 수정이 trusted `main`에 반영된 뒤 터미널 C 서버를 중지하고 해당 worktree와 의존성을 갱신해 재시작한다. `interview.py` 변경은 policy fingerprint를 바꾸므로 Actions와 서버의 버전을 맞춘다.
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

현재 구현의 자동화 시험은 GitHub/모델 대역을 사용한다. 실제 App·모델·공개 HTTPS의 일부 연결이 관측되어도, 위 9단계의 실제 PR·댓글·현재 커밋 상태·머지·대시보드 결과를 확보하기 전에는 원격 자동화 완주로 간주하지 않는다. 개인키·토큰·보류 원문은 그 증거에 포함하지 않는다.
