# Mac에서 첫 데모 PR 한 바퀴 돌리기

작성 기준: 2026-09-10. 대상 저장소: `hunhoon21/the-last-human`.

**목표:** 새 PR → App 봇 안내 → 작성자 로그인 → 비공개 면담 → 성공 기록 → Actions 독립 검증 → App 상태 통과 → 실제 머지 → 모듈 대시보드.

이 문서는 운영자가 순서대로 실행할 가이드다. 문서 작성만으로 App 등록, Azure 리소스 생성, 키 교체, 원격 실행이 완료된 것은 아니다. 제품 계약과 전체 설정 설명은 [GitHub App 런북](github-app.md)을 함께 참고한다.

## 0. 먼저 이해할 연결 구조

새 FastAPI 앱을 만들지 않는다. 이미 있는 `src/lasthuman/server/`의 **Flask 서버**를 사용한다.

| 구성 요소 | 하는 일 |
| --- | --- |
| Mac의 Flask 서버 | PR 분석, 로그인, 면담, 성공 기록, 봇 발행 작업 처리 |
| GitHub App | 서버가 GitHub API를 호출하는 봇 신원과 사용자 OAuth 로그인 |
| GitHub Actions | PR 이벤트 전달, 위험도와 현재 커밋의 독립 재검증 |
| Microsoft Dev Tunnels | 공개 HTTPS 주소를 Mac의 `127.0.0.1:8000`으로 연결 |
| Azure OpenAI | PR에 맞는 질문 생성과 답변 근거 판정 |
| SQLite | 성공 기록, snapshot, 발행 대기 작업, 머지 집계 저장 |

```text
GitHub PR
   -> Actions on trusted main
   -> 공개 HTTPS 터널 -> Mac Flask 서버
                            -> GitHub App API: pending / 면담 링크
                            -> 작성자 브라우저: GitHub OAuth / 비공개 답변
                            -> Azure OpenAI: 질문 / 근거 판정
                            -> SQLite: 성공 receipt / 발행 작업
                            -> 검증 Actions 요청
   <- 독립 재검증 완료 후 App의 success
   -> 사람이 머지
   -> Actions closed 이벤트 -> 서버 -> 모듈 대시보드
```

`localhost`는 Actions에서 직접 접속할 수 없다. 터널이 그 사이를 연결한다. App을 설치한다고 서버가 GitHub 안에서 자동 실행되는 것도 아니다. 이번 방식은 Mac과 터널이 켜져 있는 동안만 동작한다. 별도 서버에 배포하면 Mac을 꺼도 동작하지만, 그 배포는 이번 첫 데모 범위 밖이다.

**질문은 현재 어떻게 만드는가**

- 고정 질문지를 쓰지 않는다. 모델을 호출하지 않는 `risk.py`가 먼저 위험도와 이유를 계산한다.
- 서버가 위험 상위 diff hunk, PR 제목·본문, 호출자·임포터 등 구조 사실을 모델에 전달한다.
- 생성 요청의 strict JSON Schema는 실제 제공된 앵커만 선택하도록 제한한다. 응답은 `{"questions": [...]}`로 받고 기존 질문 목록으로 변환한다. 앵커 생성 규칙이나 인증 형식은 바꾸지 않는다.
- App 경로는 질문 3개를 요구한다. 현재 프롬프트는 4지선다와 판단 근거 한 줄을 요구하며, 구조 정보가 있으면 구조 질문도 포함하도록 지시한다.
- 선택지의 정답 여부는 코드로 비교하고, 작성자가 쓴 근거는 모델로 판정한다. 정답·기대 근거는 브라우저에 보내지 않는다.
- 보완 피드백은 메모리에만 둔다. 성공한 답변만 private receipt에 저장한다.
- 모델의 통과 판정만으로 머지를 허용하지 않는다. 이후 Actions와 서버가 현재 SHA·base·정책·snapshot 결속을 다시 확인한다.

관련 구현: [`interview.py`](../../src/lasthuman/interview.py), [`service.py`](../../src/lasthuman/server/service.py). 위험 규칙, 질문 프롬프트, 판정 기준, 인증 schema는 이 가이드에서 바꾸지 않는다.

**App 이전 Actions/Pages 방식과의 차이**

기존 workflow도 `lasthuman score → lasthuman page → generate_questions()` 순서로 질문을 만들었다. `QUESTION_PROMPT`의 `{title}`, `{body}`, `{reasons}`, `{hunks}`, `{structure}`에 PR 사실을 채워 모델에 전달했다. 별도의 system-role 메시지를 동적으로 만드는 구조가 아니라, 코드에 정의된 프롬프트를 Chat Completions의 user 메시지로 보내는 방식이다.

| 항목 | 기존 Actions/Pages 경로 | 현재 App 경로 |
| --- | --- | --- |
| 질문 수 | CLI 기본 요청 2개 | 서버가 3개 요구 |
| 모델 키가 없을 때 | workflow가 `--dry-run`을 선택해 결정적 스텁 질문 생성 | 자동 스텁 대체 없이 모델 설정 오류로 처리 |
| 화면·답변 경로 | 정적 면담 페이지와 PR 답변 댓글 | 작성자 로그인 후 비공개 웹 제출 |
| 질문 생성의 중심 | 기존 `QUESTION_PROMPT`와 PR 문맥 | 같은 생성 함수를 trusted snapshot 문맥으로 호출 |

기존 화면에 `[dry-run]` 표시가 있었다면 실제 모델이 만든 질문이 아니다. 이는 파이프라인·화면 시연용이며, 실제 이해 판정의 증거로 사용하지 않는다. 특정 과거 PR의 실행이 어느 경로였는지는 해당 실행 로그와 설정을 따로 확인해야 한다.

## 1. 실행 원칙과 터미널 구분

첫 연결은 촬영하지 않고 진행한다. 로그인·키 화면을 닫은 뒤 데모 장면만 촬영한다. 예상 준비 시간은 자격 증명·승인·모델 할당량에 따라 달라지며, 최초 `main` 반영이 막히면 여기서 중단할 수 있다.

| 터미널 | 용도 | 기본 위치 |
| --- | --- | --- |
| A | 설치, 설정, GitHub 조회, 데모 PR 편집 | `~/workspace/the-last-human` |
| B | 터널 전용, 계속 실행 | 위치 무관 |
| C | 승인된 서버 전용, 계속 실행 | 저장소 안 `.work/app-runtime` |

아래 `REPLACE_...` 값은 직접 확인한 값으로 바꾼다. 다른 PR 번호, App ID, 설치 ID를 추측해서 넣지 않는다. 새 터미널에서는 환경 변수가 공유되지 않으므로 해당 단계의 `export` 또는 환경 파일 로드를 다시 실행한다.

**진행 중 보완:** 단계 번호는 그대로다. 의존 명령은 `&&`로 연결해 앞 명령이 실패하면 뒤 작업을 실행하지 않도록 했다. 오류가 나면 다음 블록도 실행하지 말고 해당 단계에서 멈춘다. 이미 성공한 키 발급·리소스 생성·PR 생성은 문서가 갱신됐다는 이유로 반복하지 않는다.

**하지 않을 것:** `git push --force`, `gh pr merge --admin`, 보호 규칙 해제, 가짜 success 게시, 모델 없는 dry-run을 실제 통과로 사용, 개인키·답변을 Git/채팅/녹화에 노출.

## 2. GitHub 계정과 현재 저장소 확인

**터미널 A**

**이미 clone한 저장소를 사용한다. 다시 clone하거나 초기화하는 단계가 아니다.** `git --version`, `gh --version`은 도구 설치 여부가 불확실할 때만 확인한다. GitHub CLI가 없으면 승인된 설치 방법을 따른다. Homebrew를 사용한다면 미설치 시 `brew install gh`로 설치할 수 있다.

기존 저장소로 이동하고, 이후 명령에서 사용할 대상 이름을 현재 터미널에 설정한다. `TLH_REPO`는 디렉터리나 Git 설정에 자동 저장되는 값이 아니므로 새 터미널에서는 다시 지정한다.

```bash
cd "$HOME/workspace/the-last-human" &&
export TLH_REPO='hunhoon21/the-last-human' &&
git status --short --branch &&
git remote -v
```

Git SSH 인증과 `gh`의 API 인증은 별개다. 이미 로그인했는지 먼저 확인한다.

```bash
gh auth status --hostname github.com
```

올바른 계정이 active이고 인증이 유효하면 아래 로그인 블록은 **생략**한다. 미로그인·만료 상태일 때만 실행한다. 다른 계정이 active라면 원하는 계정으로 전환한 뒤 진행한다.

```bash
gh auth login --hostname github.com --git-protocol ssh --web
```

인증을 마쳤다면 아래로 원격 대상·권한·변수를 확인한다. 이는 저장소 파일이 이미 있다는 사실과 별개의 초기 연결 확인이다. 방금 같은 내용을 확인했다면 반복하지 않아도 된다.

```bash
gh api user --jq '{login,id}' &&
gh repo view "$TLH_REPO" --json nameWithOwner,defaultBranchRef,viewerPermission &&
gh variable list --repo "$TLH_REPO"
```

다음 단계에서 최신 원격 브랜치를 참조하도록 갱신한다. `fetch`는 원격 추적 정보를 갱신할 뿐 현재 브랜치를 바꾸거나 작업 파일을 덮어쓰지 않는다. 방금 fetch했다면 생략해도 된다.

```bash
git fetch origin
```

**기대 결과:** 올바른 계정으로 로그인하며 대상 저장소가 맞고 기본 브랜치는 `main`이다. 데모 PR 작성자와 나중에 브라우저로 로그인하는 계정은 같아야 한다.

GitHub에서 저장소의 `Settings → Actions → General`과 `Settings → Rules → Rulesets` / `Branches`를 연다. Actions 사용 가능 여부, 허용된 Actions, 기존 필수 상태와 기대 발급자, 리뷰, 최신 base 반영 조건을 기록한다. `CODEOWNERS`에 지정된 리뷰가 필요하면 담당자에게 요청한다.

**중단 조건:** 대상 계정·저장소가 다르거나 필요한 설정 권한이 없다. workflow 목록이 비어 있다는 사실만으로 Actions 설정이 꺼졌다고 단정하지 않는다. `LASTHUMAN_RUNTIME=app`이 이미 설정되어 있다면 현재 서버·writer의 상태를 먼저 확인하고, 이 문서의 최초 도입 절차를 그대로 반복하지 않는다.

## 3. uv와 Python 3.12 준비

이미 `uv --version`이 동작한다면 설치를 반복하지 않는다. Intel Mac에서 Homebrew가 Rust를 소스 빌드하다 실패한 경우에도 Rust·LLVM을 재설치하지 말고 승인된 uv 사전 빌드 배포 경로를 사용한다.

```bash
uv --version &&
uv python install 3.12
```

uv가 아직 없다면 [공식 독립 설치기](https://docs.astral.sh/uv/getting-started/installation/)를 사용한다. 설치 스크립트를 먼저 내려받아 확인한 뒤 실행하며, 기본 설치기는 사용자 셸의 PATH 설정도 수정할 수 있다.

```bash
UV_INSTALLER="$(mktemp -t uv-install)" &&
curl -LsSf https://astral.sh/uv/install.sh -o "$UV_INSTALLER"
```

내용을 확인한 뒤 같은 터미널에서 설치한다. 마지막 명령은 방금 만든 임시 파일만 지운다.

```bash
UV_INSTALL_DIR="$HOME/.local/bin" sh "$UV_INSTALLER" &&
export PATH="$HOME/.local/bin:$PATH" &&
uv --version &&
rm "$UV_INSTALLER"
```

회사 관리 단말에서 공개 패키지 파일 서버가 차단되면 조직이 지정한 PyPI index를 사용자 `~/.config/uv/uv.toml`에 설정한다. `uv`는 `pip config`를 읽지 않는다. 사내 URL·인증 정보는 이 공개 저장소에 넣지 않고 사용자 설정에만 둔다. TLS 검증을 끄거나 비승인 미러로 우회하지 않는다.

프로젝트에 `.python-version` 등 명시적 환경이 생겼다면 그 환경을 먼저 따른다. 작성 시점의 `requires-python = ">=3.11"`에는 3.12가 포함된다.

아래 가상환경 생성은 `.venv`가 없을 때 한 번만 실행한다. 기존 환경이 있으면 Python 버전과 용도를 확인하고 재사용한다. 임의로 삭제·덮어쓰지 않는다.

```bash
uv venv --python 3.12 .venv &&
uv pip install --python .venv/bin/python -e '.[dev,bot]' &&
.venv/bin/python --version &&
.venv/bin/python -m pytest tests/test_model_auth.py tests/test_app_readiness.py tests/test_app_http.py tests/test_app_events.py -q
```

**기대 결과:** 선택한 Python 버전으로 기존 App 경로의 검사가 끝난다. Node.js가 없으면 일부 웹 스크립트 항목은 건너뛸 수 있다. 이 단계는 실제 App·모델·HTTPS 연결을 증명하지 않는다.

이 저장소는 현재 `uv.lock` 기반 프로젝트가 아니므로, 첫 연결을 위해 `uv init`, `uv add`, lockfile 전환을 하지 않는다.

### 3.1 키를 발급받기 전에 비공개 저장 파일 준비

```bash
umask 077 &&
mkdir -p "$HOME/.config/the-last-human/github-app" &&
chmod 700 "$HOME/.config/the-last-human" "$HOME/.config/the-last-human/github-app" &&
touch "$HOME/.config/the-last-human/github-app/runtime.env" &&
chmod 600 "$HOME/.config/the-last-human/github-app/runtime.env"
```

`touch`는 기존 내용을 지우지 않는다. 기존 파일이 있으면 내용을 보존하고 필요한 항목만 갱신한다. 이후 키를 얻을 때마다 다음 편집기로 **즉시 이 파일에 저장**한다. 다른 메모장, 채팅, 저장소 파일을 임시 보관 장소로 쓰지 않는다.

```bash
nano "$HOME/.config/the-last-human/github-app/runtime.env"
```

`nano`에서는 `Ctrl-O`, Enter로 저장하고 `Ctrl-X`로 닫는다. 아직 모든 환경 변수가 없어도 된다. 파일을 실행하는 것은 7단계에서 설정을 완성한 뒤다.

## 4. Azure 모델과 Entra 인증 준비

**브라우저, 녹화 중지 상태**

이번 경로는 **API 키 인증이 비활성화된 리소스에서 Azure CLI 로그인 신원을 재사용**한다. `disableLocalAuth=true`를 바꾸지 않는다. 이미 만든 프로젝트와 `gpt-4.1-mini` 배포는 그대로 사용하며 재생성하지 않는다.

1. [Azure Portal](https://portal.azure.com/)에서 프로젝트의 `Parent resource`를 연다. 프로젝트, 부모 리소스, 모델 배포는 서로 다른 대상이다.
2. 부모 리소스와 구독에서 **Tenant ID와 Subscription ID**를 확인한다. 둘 다 GUID이며 프로젝트 이름이나 사용자 이메일이 아니다.
3. 부모 리소스의 Overview → JSON View에서 `properties.disableLocalAuth`를 확인한다. `true`이면 이 절차의 Entra 경로를 사용한다. 조직 정책을 해제하거나 키를 재생성해서 해결하지 않는다.
4. 모델 배포의 상태가 `Succeeded`인지 확인하고 **실제 배포 이름**을 적어 둔다. 모델 목록의 이름과 배포 이름은 다를 수 있다. 현재 예시는 둘 다 `gpt-4.1-mini`일 때다.
5. 배포 화면의 View Code에서 추론 endpoint와 `get_bearer_token_provider`에 전달하는 scope를 확인한다. 기본 예제가 Responses라면 Chat Completions용 예제로 바꿔 확인한다. `gpt-4.1-mini`를 사용하더라도 프로젝트 URL을 추론 endpoint로 쓰지는 않는다.
6. Mac에서 접근 가능한 승인된 네트워크와 추론 권한을 확인한다. Azure 로그인 성공이 모델 리소스 사용 권한까지 보장하지 않는다. 필요한 권한은 해당 리소스 종류와 조직 기준에 맞춰 관리자에게 요청한다.
7. 배포 유형은 실시간 호출용으로 두고 할당량·예산을 확인한다. 이 가이드는 기존 `temperature=0.2`를 사용하는 Chat Completions 호출을 유지한다. 다른 reasoning 모델을 이름만 바꿔 넣지 않는다.

리소스를 아직 만들지 않았다면 승인된 구독·지역에서 Azure OpenAI/Foundry 리소스와 실시간 모델 배포를 먼저 만든다. 계정·지역별 가용성과 과금이 다르므로 [공식 생성 절차](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/create-resource)를 따른다.

| 설정 | 넣을 값 |
| --- | --- |
| `LASTHUMAN_PROVIDER` | `azure` |
| `LASTHUMAN_AUTH_MODE` | `azure-cli` |
| `LASTHUMAN_MODEL` | 실제 배포 이름, 예: `gpt-4.1-mini` |
| `LASTHUMAN_ENDPOINT` | View Code에서 확인한 **전체 Chat Completions URL** |
| `AZURE_TENANT_ID` | 해당 리소스의 tenant GUID |
| `AZURE_SUBSCRIPTION_ID` | 해당 리소스의 subscription GUID |
| `AZURE_OPENAI_SCOPE` | View Code의 토큰 scope. 아래 두 지원 값 중 실제 서비스에 맞는 값 |

지원 scope는 `https://ai.azure.com/.default` 또는 `https://cognitiveservices.azure.com/.default`다. 일반 ARM 관리용 토큰이나 Microsoft Graph 토큰으로 대체하지 않는다.

**세 값을 찾는 위치**

| 값 | Azure 화면에서 찾는 위치 |
| --- | --- |
| Subscription ID | 부모 리소스의 Overview → Essentials → `Subscription ID`. 구독 표시 이름이 아닌 GUID를 복사 |
| Tenant ID | 해당 구독이 속한 디렉터리로 전환한 뒤 Microsoft Entra ID → Overview → `Tenant ID`. 구독의 Properties에서도 연결된 디렉터리를 확인 |
| OpenAI scope | 배포의 View Code → Python → Entra 인증 예제에서 `get_bearer_token_provider`의 scope 문자열 |

scope는 별도 GUID나 배포 속성 이름이 아니다. 예를 들어 코드가 아래와 같다면 `AZURE_OPENAI_SCOPE` 값은 두 번째 인자의 URL 전체다.

```python
token_provider = get_bearer_token_provider(
    DefaultAzureCredential(),
    "https://ai.azure.com/.default",
)
```

예제가 `https://cognitiveservices.azure.com/.default`를 사용하면 그 값을 그대로 넣는다. 코드 줄의 오른쪽이 잘려 보이면 Copy code로 로컬 편집기에 복사해 함수 이름을 검색한다. 자기 프로젝트 주소에 `/.default`를 붙여 새 scope를 만들지 않는다.

이미 Azure CLI 로그인이 되어 있다면 실제 리소스의 구독 ID로 두 GUID를 함께 확인할 수 있다. 로그인 전이라면 포털에서 확인한다.

```bash
az account show --subscription 'REPLACE_WITH_RESOURCE_SUBSCRIPTION_GUID' \
  --query '{AZURE_TENANT_ID:tenantId,AZURE_SUBSCRIPTION_ID:id}' --output json
```

전체 URL은 예를 들어 `https://<resource>.services.ai.azure.com/openai/v1/chat/completions` 또는 `https://<resource>.openai.azure.com/openai/v1/chat/completions` 형식이다. 실제 View Code의 주소를 사용하며 `https://.../api/projects/...`나 `/responses`를 넣지 않는다. Azure CLI 모드는 HTTPS Public Azure 추론 호스트와 Chat Completions 경로만 허용한다. 사용자 지정 프록시·APIM·다른 클라우드로 토큰을 보내는 기능은 이 모드의 범위 밖이다.

View Code의 OpenAI SDK 예제에서 `base_url`이 `.../openai/v1/`로 끝난다면 그것은 기본 주소다. 이 앱의 `LASTHUMAN_ENDPOINT`에는 같은 호스트의 **`.../openai/v1/chat/completions`**를 넣는다. SDK가 자동으로 붙이던 경로를 직접 HTTP 호출에서는 명시해야 한다.

기존 deployment-scoped API를 쓰는 환경은 `LASTHUMAN_ENDPOINT`를 설정하지 않고 `AZURE_OPENAI_ENDPOINT`와 `AZURE_OPENAI_API_VERSION`으로 URL을 만들 수 있다. **전체 URL과 resource origin을 혼용하지 않는다.**

### 4.1 Azure CLI 준비

이미 `az --version`이 동작하면 설치를 건너뛴다. 설치 경로가 여러 개라면 `command -v az`로 App 프로세스가 사용할 실행 파일을 확인한다.

Intel Mac에서 Homebrew 설치가 어려운 경우, 조직이 승인한 패키지 index와 `uv tool`의 격리 환경을 사용하는 설치 예시는 다음과 같다. 프로젝트 `.venv`나 시스템 Python에 Azure CLI를 섞지 않는다. 이 방식은 Homebrew 대신 PyPI 배포 패키지를 사용하므로, 설치가 실패하면 오류를 확인하고 승인된 설치 방법으로 해결한다.

```bash
uv tool install --python 3.12 azure-cli &&
export PATH="$HOME/.local/bin:$PATH" &&
az --version
```

`uv tool`의 실행 파일 위치를 별도로 바꾼 환경은 그 위치를 PATH에 넣는다. 이 문서의 기본 위치는 `~/.local/bin`이다. App 서버를 띄우는 터미널 C에서도 같은 `az`를 찾을 수 있어야 한다.

### 4.2 해당 tenant로 로그인

**터미널 A, 녹화 중지 상태.** 아래 값은 4단계에서 확인한 실제 값으로 바꾼다.

```bash
export AZURE_TENANT_ID='REPLACE_WITH_TENANT_GUID' &&
export AZURE_SUBSCRIPTION_ID='REPLACE_WITH_SUBSCRIPTION_GUID' &&
export AZURE_OPENAI_SCOPE='REPLACE_WITH_PORTAL_SCOPE'
```

브라우저에서 Azure Portal에 로그인한 것과 로컬 Azure CLI 로그인은 별개다. CLI가 처음이거나 해당 계정의 재로그인이 필요할 때 실행한다.

```bash
az login --tenant "$AZURE_TENANT_ID" &&
az account set --subscription "$AZURE_SUBSCRIPTION_ID" &&
az account show --subscription "$AZURE_SUBSCRIPTION_ID" \
  --query '{subscriptionId:id,tenantId:tenantId,name:name}' --output json
```

계정 선택과 MFA를 완료하고, 출력의 tenant/subscription이 의도한 리소스와 같은지 확인한다. 이미 올바른 계정으로 로그인돼 있으면 로그인 명령을 반복하지 않아도 된다. 브라우저나 조건부 액세스 오류가 발생하면 임의로 다른 계정·정책으로 우회하지 않는다.

앱은 subscription을 지정해 자격 증명을 사용하고, 그 구독의 tenant가 설정값과 일치하는지 매 요청 전에 별도로 확인한다. Azure CLI의 토큰 명령에는 `--tenant`와 `--subscription`을 동시에 넘길 수 없으므로, 로그인에는 tenant를 쓰고 토큰 요청에는 subscription만 지정한다.

### 4.3 토큰 취득만 먼저 확인

다음은 Entra에 토큰을 요청하지만 **모델을 호출하지 않으므로 추론 비용은 발생하지 않는다.** 토큰 원문 대신 만료 메타데이터만 출력한다.

```bash
az account get-access-token \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --scope "$AZURE_OPENAI_SCOPE" \
  --query '{expires_on:expires_on,tokenType:tokenType}' --output json
```

토큰 취득은 로그인·scope 경로 확인이지 모델 RBAC까지의 확인은 아니다. 실제 모델 호출은 8단계에서 한 번 수행한다. `accessToken`을 출력하거나 복사해 `runtime.env`·Actions Secret에 넣지 않는다.

CLI 로그인 상태는 CLI가 사용자 캐시에서 관리한다. App은 SDK 토큰 제공자를 프로세스에서 재사용하며 필요할 때 새 토큰을 취득한다. `~/.azure` 캐시를 직접 읽거나 복사·공유하지 않는다. macOS CLI 캐시의 암호화를 가정하지 말고 민감한 사용자 파일로 관리한다. 회사 정책에 따라 `az login`이나 MFA가 다시 필요할 수 있으며, 인증 실패를 성공으로 처리하지 않는다.

### 4.4 API 키를 허용하는 다른 환경

API 키가 허용된 별도 환경에서는 기존 방식도 유지된다. `LASTHUMAN_AUTH_MODE`를 생략하거나 `default`로 두고 `LASTHUMAN_API_KEY`를 설정한다. 수동 Bearer 토큰 경로도 기존처럼 사용할 수 있지만 자동 갱신되지 않는다. **이번 `disableLocalAuth=true` 리소스를 위해 설정을 해제하지 않는다.**

**중단 조건:** CLI 설치·로그인 실패, 필요한 추론 권한 없음, tenant/subscription/scope 불일치, 네트워크 차단 또는 요청 형식 미지원. GitHub 토큰을 Azure 자격 증명으로 대체하지 않는다.

Azure OpenAI로 PR 문맥과 답변이 전송된다. 첫 데모는 이 공개 저장소의 샘플 코드만 사용하며 고객 코드·개인정보를 넣지 않는다. 앱의 보류 비보존 정책이 모델 공급자의 보존 정책까지 보증하는 것은 아니므로 조직의 데이터 처리 조건을 확인한다.

## 5. 기존 GitHub App에서 새 개인키 발급

**새 App을 만들지 않고 기존 App을 우선 재사용한다.**

1. GitHub `Settings → Developer settings → GitHub Apps`에서 Windows에서 사용하던 제품 App의 `Edit`를 연다. Dev Tunnels용 App이나 runner용 App과 혼동하지 않는다.
2. `Private keys → Generate a private key`로 새 PEM을 내려받는다. 기존 Windows 키는 아직 삭제하지 않는다.
3. App ID와 Client ID를 기록한다. 개인키 재발급으로 이 ID들이 바뀌지는 않는다.
4. OAuth client secret을 확보하지 못했다면 `Generate a new client secret`으로 새로 만든다. **화면을 떠나기 전에** 3.1단계의 환경 파일에 `TLH_CLIENT_SECRET='발급받은 값'`으로 저장한다. App ID와 Client ID도 함께 기록한다. **PEM과 client secret은 다른 자격 증명**이다.
5. 아래 권한을 확인한다. 권한을 바꿨다면 설치 업데이트 승인도 마친다.
6. `Install App` / `Configure`에서 `hunhoon21/the-last-human`만 선택됐는지 확인하고 Installation ID를 기록한다. 기존 설치가 맞으면 재설치하지 않는다.

| Repository permission | 설정 |
| --- | --- |
| Metadata | Read-only |
| Contents | Read-only |
| Pull requests | Read and write |
| Commit statuses | Read and write |
| Actions | Read and write |

**Webhook은 끈다.** 이벤트는 Actions relay가 전달한다. Contents write나 Checks write를 추가할 필요는 없다.

**터미널 A**

```bash
umask 077 &&
mkdir -p "$HOME/.local/share/the-last-human/demo" &&
chmod 700 "$HOME/.local/share/the-last-human" "$HOME/.local/share/the-last-human/demo"
```

실제 다운로드 파일명을 넣어 이동한다. 대상 파일이 이미 있으면 덮어쓰지 말고 기존 용도를 확인한다.

```bash
mv -i "$HOME/Downloads/REPLACE_WITH_DOWNLOADED_FILENAME.pem" \
  "$HOME/.config/the-last-human/github-app/private-key-2026-09-10.pem" &&
chmod 600 "$HOME/.config/the-last-human/github-app/private-key-2026-09-10.pem"
```

키 원문 대신 공개키 fingerprint를 GitHub 화면과 비교할 수 있다.

```bash
openssl rsa \
  -in "$HOME/.config/the-last-human/github-app/private-key-2026-09-10.pem" \
  -pubout -outform DER | openssl sha256 -binary | openssl base64
```

**기대 결과:** 새 키 항목의 fingerprint와 일치한다. PEM은 symlink가 아니며 group/other 권한이 없어야 한다. 기존 키 폐기는 18단계에서 새 키의 실제 인증을 확인한 후 진행한다.

## 6. 공개 HTTPS 터널 주소 확보

Microsoft Dev Tunnels는 개발용 preview 서비스다. 운영 SLA를 전제하지 않는다. 이 절차는 조직이 승인한 개발용 터널 사용을 전제로 한다.

**터미널 A — 미설치일 때만**

```bash
brew install --cask devtunnel &&
devtunnel --version
```

**터미널 B**

```bash
devtunnel user login &&
devtunnel create --expiration 1d
```

출력된 실제 Tunnel ID를 아래에 넣는다. 임의 ID나 다른 터널의 ID를 사용하지 않는다.

```bash
export TLH_TUNNEL_ID='REPLACE_WITH_CREATED_TUNNEL_ID' &&
devtunnel port create "$TLH_TUNNEL_ID" -p 8000 --protocol http &&
devtunnel access create "$TLH_TUNNEL_ID" --port-number 8000 --anonymous --expiration 8h &&
devtunnel host "$TLH_TUNNEL_ID"
```

`access create`의 포트 옵션은 `--port-number` 또는 `-p`다. 설치된 CLI에서 옵션 오류가 나면 `devtunnel access create --help`를 기준으로 확인한다. 포트 생성은 성공하고 접근 권한 설정만 실패했다면 포트를 다시 만들지 말고 다음 두 명령부터 재개한다.

```bash
devtunnel access create "$TLH_TUNNEL_ID" --port-number 8000 --anonymous --expiration 8h &&
devtunnel host "$TLH_TUNNEL_ID"
```

**이 터미널을 계속 켜 둔다.** `https://...-8000....devtunnels.ms` 형태의 출력된 HTTPS 주소를 기록한다. `-inspect` 주소가 아니라 **8000 포트의 웹 접속 URL**이다. 호스트명은 출력값을 그대로 사용하고 규칙으로 조립하지 않는다.

아직 Flask 서버를 시작하지 않았으므로 이 시점에 앱 화면이 안 나오는 것은 정상이다.

`--anonymous`는 **터널 입구**를 Actions가 브라우저 로그인 없이 통과하게 하는 설정이다. 인터넷에서 앱에 도달할 수 있게 되므로 8000 포트와 시연 시간에만 적용한다. **앱의 GitHub OAuth·작성자 인가·Actions OIDC 검증은 그대로 유지된다.**

터널 서비스에서 TLS가 종료된다. traffic inspector, verbose 로그, 요청/응답 덤프를 녹화·공유하지 않는다. 데이터 보존 조건이 허용되지 않으면 공개 터널 대신 승인된 배포 환경을 준비하고 여기서 멈춘다.

## 7. 환경 파일 작성과 OAuth callback 연결

**터미널 A, 녹화 중지 상태**

```bash
touch "$HOME/.config/the-last-human/github-app/runtime.env" &&
chmod 600 "$HOME/.config/the-last-human/github-app/runtime.env" &&
nano "$HOME/.config/the-last-human/github-app/runtime.env"
```

4·5단계에서 확인한 Azure 식별자와 GitHub App의 키·ID는 유지하고 나머지 항목을 완성한다. 아래는 **Entra/Azure CLI 경로**의 전체 형식 예시다. 실제 값을 예시 placeholder로 덮어쓰지 않는다. 아직 남아 있는 `REPLACE_...`만 실제 값으로 바꾼다.

```bash
TLH_APP_ID='REPLACE_WITH_APP_ID'
TLH_CLIENT_ID='REPLACE_WITH_CLIENT_ID'
TLH_CLIENT_SECRET='REPLACE_WITH_NEW_CLIENT_SECRET'
TLH_PRIVATE_KEY_FILE="$HOME/.config/the-last-human/github-app/private-key-2026-09-10.pem"
TLH_INSTALLATION_ID='REPLACE_WITH_INSTALLATION_ID'
TLH_REPOSITORY='hunhoon21/the-last-human'
TLH_REPOSITORY_ID='1361123778'
TLH_OWNER_ID='36983960'
TLH_BASE_URL='https://REPLACE_WITH_ACTUAL_TUNNEL_HOST'
TLH_SECRET_KEY='REPLACE_WITH_RANDOM_SECRET'
TLH_DATABASE="$HOME/.local/share/the-last-human/demo/lasthuman.sqlite3"
TLH_MODE='live'
TLH_STATUS_CONTEXT='last-human/human-verified'
TLH_CHECK_RUNS='false'
TLH_CHECK_NAME='The Last Human'
TLH_WORKFLOW='lasthuman-app.yml'
TLH_WORKFLOW_REF='refs/heads/main'
TLH_OIDC_AUDIENCE='hunhoon21/the-last-human'
TLH_PRESENTATION_NAME='The Last Human'
TLH_PRESENTATION_LOCALE='ko'
TLH_PRESENTATION_MAX_CHARS='6000'
TLH_PRESENTATION_REASON_LIMIT='3'
TLH_PRESENTATION_DETAIL_LIMIT='10'
TLH_PRESENTATION_PATHS_PER_GROUP='2'

LASTHUMAN_PROVIDER='azure'
LASTHUMAN_AUTH_MODE='azure-cli'
LASTHUMAN_MODEL='gpt-4.1-mini'
LASTHUMAN_ENDPOINT='https://REPLACE_WITH_RESOURCE_HOST/openai/v1/chat/completions'
AZURE_TENANT_ID='REPLACE_WITH_TENANT_GUID'
AZURE_SUBSCRIPTION_ID='REPLACE_WITH_SUBSCRIPTION_GUID'
AZURE_OPENAI_SCOPE='REPLACE_WITH_PORTAL_SCOPE'
unset LASTHUMAN_API_KEY LASTHUMAN_TOKEN
```

실제 배포 이름이 `gpt-4.1-mini`와 다르면 그 이름으로 바꾼다. `TLH_CHECK_RUNS` 기본값은 `false`다. 보조 Check를 켜려면 먼저 GitHub App 권한과 설치 승인에 `Checks: Read and write` 를 추가한 뒤 trusted 서버를 재시작해야 하며, 표시 이름 `TLH_CHECK_NAME` 은 필수 status context `last-human/human-verified` 와 다르게 유지한다. `TLH_PRESENTATION_*` 값은 공개 카드/보조 Check의 이름·locale·표시 예산만 바꾼다. Azure access token은 이 파일에 저장하지 않는다. 마지막 `unset`은 이전 수동 토큰/API 키를 현재 셸에서 제거하며, GitHub App의 `TLH_CLIENT_SECRET`이나 PEM에는 영향을 주지 않는다.

`TLH_SECRET_KEY`는 32자 이상의 랜덤 값으로 만든다. Mac에서는 아래 명령으로 clipboard에 넣은 값을 환경 파일에 붙여 넣을 수 있다. 키를 터미널에 표시하지 않는다.

```bash
openssl rand -hex 32 | pbcopy
```

붙여 넣은 후 clipboard를 비운다.

```bash
printf '' | pbcopy
```

환경 파일은 shell 코드로 읽힌다. 직접 작성하고 확인한 파일만 source한다. 다운로드한 환경 파일을 검토 없이 실행하지 않는다. `LASTHUMAN_ENDPOINT`는 기본 URL 조합보다 우선한다. 이 예시는 전체 v1 Chat Completions 주소를 사용하므로, 프로젝트 주소나 다른 모델의 오래된 주소가 남아 있지 않게 한다.

GitHub App 설정의 **Callback URL**을 정확히 다음과 같이 바꾼다.

```text
https://실제-터널-호스트/auth/github/callback
```

Homepage URL은 저장소 URL이어도 된다. 개발용 localhost callback과 혼용하지 않는다. 이 가이드는 실제 상태 게시를 위해 처음부터 `TLH_MODE=live`를 사용한다.

**기대 결과:** `TLH_BASE_URL`은 path 없는 HTTPS origin, callback은 그 origin 뒤의 `/auth/github/callback`이다. `TLH_CLIENT_SECRET`도 최소 32자여야 한다.

## 8. 새 키와 모델 연결을 각각 확인

**터미널 A — 저장소 루트**

```bash
set -a &&
. "$HOME/.config/the-last-human/github-app/runtime.env"
TLH_ENV_STATUS=$?
set +a
test "$TLH_ENV_STATUS" -eq 0
```

파일 읽기에 실패하면 여기서 멈춘다. 마지막 상태가 실패이므로 다음 블록을 실행하지 않는다. 이전 터미널의 설정값이 남아 있다는 이유로 계속 진행하지 않는다.

다음은 설정을 읽고 새 PEM으로 installation token을 발급받아 대상 저장소를 확인한다. 토큰·키를 출력하지 않으며 PR 댓글이나 상태는 만들지 않는다.

```bash
.venv/bin/python - <<'PY'
from lasthuman.server.config import Settings
from lasthuman.server.github import GitHubClient

settings = Settings.from_env()
client = GitHubClient(settings)
client.verify_repository()
repo = client.repository_info()
print({"repository": repo["full_name"], "id": repo["id"], "app_auth": "ok"})
PY
```

4.3단계의 토큰 메타데이터 요청이 성공했는지 먼저 확인한다. 그다음 아래에서 **새 Azure CLI 인증 경로를 사용하는 실제 Azure OpenAI 요청**을 한 번 보낸다. 소량의 비용이 발생하며 PR이나 답변 대신 고정된 무해한 문장만 보낸다. 코드와 `.[bot]` 의존성이 갱신되지 않았다면 먼저 설치를 마친다.

```bash
.venv/bin/python - <<'PY'
from lasthuman.interview import call_model

response = call_model("Reply with a short greeting. Do not include any other content.")
if not response.strip():
    raise SystemExit("Model returned an empty response")
print("Model connection returned non-empty text")
PY
```

**기대 결과:** GitHub App 인증과 Azure 모델 호출이 각각 끝난다. 모델 호출 시 개인 토큰을 복사하거나 API 키를 활성화하지 않는다. 이 단계는 OAuth 사용자 로그인이나 질문 품질까지 확인하는 것은 아니다. 실제 질문 3개·근거 판정은 첫 PR에서 확인한다.

이 인사 호출은 생성용 스키마를 보내지 않는다. 실제 질문 생성에는 strict `response_format`이 필요하므로, 첫 PR에서 제공된 앵커의 질문 3개가 준비되는지까지 별도로 확인한다. endpoint가 구조화 출력을 거부하면 모델/API 호환성을 해결하고 다시 진행하며 자유 형식으로 우회하지 않는다.

**중단 조건:** CLI/선택 의존성 없음, 재로그인 필요, tenant/subscription/scope 불일치, 401/403/404, 배포 이름 불일치, 네트워크 차단, 요청 형식 오류. 원인을 고치기 전에는 runtime 전환이나 PR 변경 확인을 진행하지 않는다. 토큰 취득은 성공하고 모델 요청만 403이면 추론 RBAC·endpoint를 별도로 확인한다.

## 9. 기능 브랜치를 trusted main에 먼저 도입

**이 단계는 제품의 첫 데모 PR과 다르다.**

작성 시점에 기능 코드는 `feat/github-app-runtime@42556fb`에 있고 `main`에는 없다. 당시 전체 변경은 15,524줄로 새 snapshot의 10,000줄 한도를 넘는다. 큰 최초 도입 PR을 새 App으로 자기 검증하려 하지 않는다.

1. 관련 머지를 잠시 동결할 시간을 정한다. 기존 리뷰·CI·보호는 유지한다.
2. 아직 `LASTHUMAN_RUNTIME=app`으로 바꾸지 않는다.
3. 이번 Entra 인증 코드·의존성·가이드 변경이 미커밋 상태라면 아래 대상 파일을 함께 검토한다. 이미 커밋되어 있으면 이 블록을 건너뛴다. 문서만 push하고 인증 코드를 로컬에 남기면 trusted main에서 새 모드를 사용할 수 없다.

```bash
git status --short --branch &&
git --no-pager diff -- pyproject.toml src/lasthuman/interview.py \
  src/lasthuman/model_auth.py tests/test_model_auth.py tests/test_app_readiness.py \
  docs/runbooks/first-demo-macos.md docs/runbooks/github-app.md
```

새 파일은 unstaged diff에 보이지 않을 수 있으므로 내용도 직접 확인한다. 다른 작업이 이미 staging되어 있다면 먼저 그 작업과 이번 커밋을 분리한다. 위 내용을 확인한 뒤 해당 파일만 추가한다.

```bash
git add pyproject.toml src/lasthuman/interview.py src/lasthuman/model_auth.py \
  tests/test_model_auth.py tests/test_app_readiness.py \
  docs/runbooks/first-demo-macos.md docs/runbooks/github-app.md &&
git commit -m "feat(model): add Azure CLI authentication and demo setup" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

현재 브랜치가 기능 브랜치인지 확인한 뒤 push한다. 다른 브랜치라면 아래 명령을 그대로 실행하지 않는다.

```bash
git push origin feat/github-app-runtime &&
gh pr list --repo "$TLH_REPO" --head feat/github-app-runtime --state open
```

이미 PR이 있으면 그 PR을 사용한다. 없을 때만 만든다.

```bash
gh pr create --repo "$TLH_REPO" --base main --head feat/github-app-runtime \
  --title "Introduce the GitHub App runtime" \
  --body "Bootstrap the trusted App runtime and relay workflow. This is not evidence of a completed App demo. Preserve existing reviews and branch protection."
```

4. 기존 보호를 만족하는 정상 리뷰·머지 절차로 `main`에 반영한다. 앱의 상태 발급자를 요구하는 규칙을 도입 전부터 걸어 순환 의존성을 만들지 않는다.
5. 기존 필수 게이트 때문에 최초 도입이 불가능하면 **중단하고 정당한 최초 도입 절차를 합의**한다. 필요하면 독립적으로 리뷰 가능한 변경으로 분리한다. 이 문서는 보호 해제, admin 우회, 스텁 인증으로 green 만들기를 안내하지 않는다.

**터미널 A — 머지된 뒤**

```bash
git fetch origin main &&
git --no-pager show origin/main:.github/workflows/lasthuman-app.yml &&
git show origin/main:src/lasthuman/model_auth.py > /dev/null &&
git show origin/main:src/lasthuman/server/app.py > /dev/null
```

**기대 결과:** 새 인증 코드·의존성과 relay workflow가 실제 `origin/main`에 있다. 인증부 수정도 `interview.py`의 policy fingerprint를 바꾸므로 App 서버와 Actions의 trusted 소스 버전을 맞춘다. 이 조건을 만족하지 않으면 다음 단계로 가지 않는다.

## 10. 서버는 승인된 main의 별도 worktree에서 실행

데모 PR을 편집하는 폴더에서 서버를 띄우면 브랜치 전환으로 서버가 읽는 코드·정책이 바뀔 수 있다. **서버와 PR 편집 폴더를 분리한다.**

**터미널 A**

```bash
TLH_TRUSTED_SHA="$(git rev-parse --verify origin/main)" &&
export TLH_TRUSTED_SHA &&
git worktree add --detach .work/app-runtime "$TLH_TRUSTED_SHA"
```

`.work/app-runtime`이 이미 있으면 새로 생성하거나 강제로 덮어쓰지 않는다. 기존 서버를 멈춘 상태에서 그 폴더의 SHA·변경 여부를 확인하고 사용한다.

**터미널 C**

```bash
cd "$HOME/workspace/the-last-human/.work/app-runtime" &&
git status --short --branch &&
git rev-parse HEAD
```

이 폴더의 SHA가 터미널 A에서 기록한 승인 SHA와 같고, 작업 파일에 변경이 없는지 확인한다. `git status`가 성공 종료했다는 사실만으로 clean인 것은 아니다. 확인 전에는 아래 설치·실행 블록을 실행하지 않는다.

환경 생성은 이 worktree에서도 최초 한 번만 한다. 기존 `.venv`가 올바른 환경이라면 첫 번째 생성 명령은 생략한다.

```bash
uv venv --python 3.12 .venv &&
uv pip install --python .venv/bin/python -e '.[bot]'
```

설치가 성공한 뒤 같은 터미널 C에서 실행한다.

```bash
cd "$HOME/workspace/the-last-human/.work/app-runtime" &&
export PATH="$HOME/.local/bin:$PATH" &&
set -a &&
. "$HOME/.config/the-last-human/github-app/runtime.env"
TLH_ENV_STATUS=$?
set +a
test "$TLH_ENV_STATUS" -eq 0 &&
if test "${LASTHUMAN_AUTH_MODE:-default}" = azure-cli; then command -v az; fi &&
caffeinate -i .venv/bin/gunicorn \
  --bind 127.0.0.1:8000 --workers 1 --threads 4 --timeout 180 \
  'lasthuman.server.app:create_app()'
```

경로 이동이나 환경 파일 읽기가 실패하면 서버를 실행하지 않는다. 이후 재시작은 **새 터미널에서도 절대 경로 진입을 포함하는 19단계**를 사용한다. 원래 저장소의 `.venv`로 서버를 실행하지 않는다.

**터미널 C도 계속 켜 둔다.** `caffeinate`는 실행 중 idle sleep을 억제하지만 덮개를 닫거나 네트워크를 끊어도 계속 동작하게 만드는 것은 아니다. 개발용 reloader, 여러 worker, `--preload`를 사용하지 않는다. access log에 OAuth query나 답변을 기록하는 옵션을 추가하지 않는다.

같은 DB를 사용하는 `serve`, `sync`, `flush`를 다른 프로세스로 동시에 실행하지 않는다.

**터미널 A**

```bash
curl --fail --silent --show-error --max-time 15 \
  -H 'Accept: application/json' \
  "$TLH_BASE_URL/healthz"
```

**기대 결과:** JSON의 `status`가 `alive`, `background`가 `ok`, `scheduler`가 `running`이다. HTML 로그인 페이지나 터널 안내 페이지는 이 결과가 아니다.

브라우저에서 `$TLH_BASE_URL/auth/github`를 연다. Dev Tunnels의 최초 접근 안내가 나오면 주소를 확인한 후 진행한다. GitHub OAuth로 **데모 PR을 만들 계정**에 로그인하고 `/dashboard`로 이동한다. 자료가 없는 화면은 정상이다.

**중단 조건:** 공개 URL의 JSON 응답 또는 OAuth callback이 실패한다. 터널 권한·origin·호스트를 먼저 고친다. `TRUSTED_HOSTS`, OAuth state, CSRF 또는 OIDC 검사를 제거해서 통과시키지 않는다.

## 11. Actions와 App 상태 발급자 연결

**터미널 A**

```bash
gh variable list --repo "$TLH_REPO" &&
gh workflow list --repo "$TLH_REPO" &&
gh run list --repo "$TLH_REPO" --limit 20
```

이전 변수값과 필수 상태의 발급자를 기록해 둔다. UI에서도 관련 실행을 확인하고, 이미 진행 중인 legacy 발행 작업이 끝난 뒤 전환한다. 취소가 필요하면 승인한 개별 run ID만 취소한다.

```bash
test -n "$TLH_REPO" &&
test -n "$TLH_BASE_URL" &&
test -n "$TLH_OIDC_AUDIENCE" &&
gh variable set TLH_BOT_URL --repo "$TLH_REPO" --body "$TLH_BASE_URL" &&
gh variable set TLH_OIDC_AUDIENCE --repo "$TLH_REPO" --body "$TLH_OIDC_AUDIENCE" &&
gh variable set LASTHUMAN_RUNTIME --repo "$TLH_REPO" --body app
```

변수가 비어 있거나 앞 저장이 실패하면 App 전환은 실행되지 않는다. 값을 다시 로드하고 원인을 해결한 뒤 이 블록을 다시 실행한다. `LASTHUMAN_RUNTIME=app` 명령만 따로 실행하지 않는다.

**실제 저장소 동작을 바꾸는 단계다.** `LASTHUMAN_RUNTIME=app`은 relay를 켜고 dashboard workflow를 끈다. 예전 `.github/workflows/comprehension-gate.yml` 파일은 이미 제거된 상태여야 하며, 그래도 권위 있는 최종 신호는 계속 `last-human/human-verified` commit status다. App 개인키·client secret·모델 키를 Actions secrets에 넣지 않는다. 예전 Actions 실행 기록은 삭제되지 않는다.

이번 가이드는 `last-human/human-verified` context를 사용한다. 별도 staging 이름만 쓰면 기존 필수 context를 갱신할 writer가 없어질 수 있으므로, 그것을 병행 검증이라고 간주하지 않는다. 다음 단계의 App pending이 보이고 기대 발급자 설정을 마칠 때까지 관련 PR을 머지하지 않는다.

보조 Check는 여기서 자동으로 켜지지 않는다. 필요하면 먼저 App owner가 `Checks: Read and write` 권한과 설치 업데이트를 승인하고, 터미널 C의 trusted `runtime.env`에 `TLH_CHECK_RUNS=true` 와 필요한 `TLH_CHECK_NAME`/`TLH_PRESENTATION_*` 값을 반영한 뒤 서버를 재시작한다. 이미 열려 있는 PR은 설정 변경만으로 다시 렌더링되지 않으므로 새 PR 이벤트를 만들거나 승인된 재동기화 절차를 실행한다.

기존에 열린 PR이 변수 변경만으로 자동 재처리되는 것은 아니다. 아래에서 새 PR 이벤트를 만든다.

## 12. 첫 위험 PR 만들기

**터미널 A — 원래 저장소 루트, 서버 worktree가 아님**

```bash
cd "$HOME/workspace/the-last-human" &&
git status --short --branch
```

변경이 없는지 먼저 확인한 뒤 실행한다.

```bash
git fetch origin main &&
git switch -c demo/auth-refresh-clock-20260910 origin/main
```

이름이 이미 있으면 새 고유 이름을 사용한다. dirty worktree가 있으면 먼저 변경을 보존하고 정리한다. 강제 checkout이나 자동 stash로 덮지 않는다.

### 12.1 샘플 변경

`sample-app/app/auth/token.py`의 기존 `ensure_fresh` 함수만 다음처럼 바꾼다. **게이트의 위험 규칙을 바꾸는 작업이 아니다.**

```python
async def ensure_fresh(
    transport: Transport,
    token: TokenSet,
    *,
    now: float | None = None,
) -> TokenSet:
    """Refresh near-expiry tokens, optionally using an explicit decision time."""
    if not is_expired(token, now):
        return token
    return await refresh(transport, token)
```

의도는 기존 호출자의 동작은 유지하면서, 만료 판단에 명시적 시각을 넘겨 경계 동작을 재현할 수 있게 하는 것이다. 실제 갱신 후 만료 시각 계산은 기존 `refresh()`에 그대로 둔다.

### 12.2 기존 테스트 파일에 추가

`sample-app/tests/test_token.py` 끝에 추가한다. 기존 테스트를 지우거나 약화하지 않는다. `asyncio`, `json`, `BASE`, `CLOCK_SKEW_SEC`, `ensure_fresh`는 이미 이 파일에 있다.

```python
def test_explicit_now_preserves_token_outside_refresh_window():
    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        raise AssertionError("Refresh was not expected")

    result = asyncio.run(
        ensure_fresh(
            transport,
            BASE,
            now=BASE.expires_at - CLOCK_SKEW_SEC - 1,
        )
    )
    assert result is BASE


def test_explicit_now_refreshes_at_window_boundary():
    calls: list[dict[str, str]] = []

    async def transport(method: str, url: str, body: str) -> tuple[int, str]:
        assert method == "POST"
        calls.append(json.loads(body))
        return 200, json.dumps({"access_token": "new", "expires_in": 3600})

    result = asyncio.run(
        ensure_fresh(
            transport,
            BASE,
            now=BASE.expires_at - CLOCK_SKEW_SEC,
        )
    )
    assert len(calls) == 1
    assert calls[0]["refresh_token"] == BASE.refresh_token
    assert result.access_token == "new"
    assert result.refresh_token == BASE.refresh_token
```

```bash
.venv/bin/python -m pytest -c sample-app/pytest.ini sample-app/tests/test_token.py -q &&
git diff --check &&
git --no-pager diff -- sample-app/app/auth/token.py sample-app/tests/test_token.py
```

결과와 변경 내용을 읽고 다른 staging 항목이 없는지 확인한 뒤 커밋한다.

```bash
git add sample-app/app/auth/token.py sample-app/tests/test_token.py &&
git commit -m "feat(sample): allow an explicit token refresh decision time" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" &&
.venv/bin/python -m lasthuman score --base origin/main --head HEAD
```

**기대 결과:** 기존 정책의 auth 중요 경로와 추가된 async 코드로 위험 대상이 된다. 작성 시점 기준으로 이 두 신호만 합쳐도 45점이며 임계값은 40이다. 실제 점수에는 변경량·메타데이터가 반영될 수 있다. 로컬 preview와 Actions의 PR 사실 기반 최종 계산을 구분한다.

`triggered`가 false이거나 diff가 의도와 다르면 이유를 확인한다. 게이트 임계값이나 가중치를 낮춰 시연하지 않는다.

### 12.3 push와 PR 생성

```bash
git push -u origin HEAD &&
gh pr create --repo "$TLH_REPO" --base main \
  --title "Allow an explicit decision time for token refresh" \
  --body "Add an optional keyword-only decision time to ensure_fresh. Existing callers keep the default clock behavior. Add boundary coverage for preserving a fresh token and refreshing at the skew boundary. The refresh operation itself still uses its existing expiry calculation." &&
DEMO_PR="$(gh pr view "$(git branch --show-current)" --repo "$TLH_REPO" --json number --jq .number)" &&
test -n "$DEMO_PR" &&
DEMO_HEAD="$(gh pr view "$DEMO_PR" --repo "$TLH_REPO" --json headRefOid --jq .headRefOid)" &&
test -n "$DEMO_HEAD" &&
export DEMO_PR DEMO_HEAD &&
gh pr view "$DEMO_PR" --repo "$TLH_REPO" --web
```

실제로 생성된 PR 번호를 사용한다. 번호·SHA 조회가 실패하거나 비어 있으면 다음 단계로 진행하지 않는다. 과거 다른 저장소에서 사용한 샘플 번호를 그대로 쓰지 않는다.

## 13. pending 확인 후 필수 App 발급자 지정

```bash
gh run list --repo "$TLH_REPO" --workflow lasthuman-app.yml --limit 10
gh api "repos/$TLH_REPO/commits/$DEMO_HEAD/status" \
  --jq '.statuses[] | select(.context == "last-human/human-verified") | {state,context,creator:.creator.login,description,target_url}'
```

**기대 결과:** workflow 파일은 계속 `lasthuman-app.yml`이고, UI의 run title은 준비 단계에서 `Relay PR #...`로 보인다. 제품 App 봇의 시작 댓글과 현재 SHA의 `last-human/human-verified: pending`이 보인다. 면담 링크는 공개 터널 origin의 `/prs/<번호>`다.

GitHub `Settings → Rules → Rulesets` 또는 `Branches`에서 `main`의 필수 상태를 확인한다.

1. `last-human/human-verified`를 필수로 지정한다.
2. 기대 발급자를 **이번 제품 GitHub App**으로 지정한다. `Any source`나 `GitHub Actions`로 그대로 두지 않는다.
3. 기존 CI·리뷰와 최신 base 반영 조건은 유지한다.
4. `Last Human · relay` 작업 이름이나 보조 Check 표시 이름 `The Last Human` 만 필수로 지정하고 끝내지 않는다.

App이 상태를 한 번 보내기 전에는 선택 목록에 나타나지 않을 수 있다. 기존 규칙에서 발급자를 바꾸는 동안에는 머지 동결을 유지한다. UI에서 지정할 수 없거나 기존 규칙과 충돌하면 멈추고 관리자와 해결한다.

**중단 조건:** 다른 봇의 status, 다른 SHA, 로컬 URL, 미등록 workflow, OIDC 오류, 또는 시작 댓글만 있고 App status가 없다.

## 14. 작성자 면담과 독립 검증

1. PR의 App 댓글에서 면담 링크를 연다. 이미 로그인했다면 계정이 PR 작성자와 같은지 확인한다.
2. 현재 SHA와 위험 이유를 읽고 질문 3개를 확인한다. 별도 reviewer가 대신 면담할 수 있는 경로는 현재 없다.
3. 코드를 직접 읽고 선택지와 근거 한 줄을 작성한다. 정답을 맞추기 위해 정책이나 프롬프트를 수정하지 않는다.
4. 보완이 필요하면 본인 화면의 힌트를 참고해 다시 답한다. 공개 PR 댓글에 답변을 붙이지 않는다.
5. 통과 후 성공 receipt가 저장되면 `awaiting_verification` 단계로 이동한다. 여기서 아직 최종 머지가 허용된 것은 아니다.
6. App이 요청한 `workflow_dispatch` 실행이 receipt를 독립 검증하도록 기다린다.
7. App의 성공 댓글과 현재 SHA의 `last-human/human-verified: success`를 확인한다.

`TLH_CHECK_RUNS=true`를 승인해 둔 환경이라면 같은 snapshot에 대해 보조 Check 하나가 더 보일 수 있다. 이 표시는 현재 SHA/base/policy 설명 보강용이며, 머지 조건의 권위는 계속 `last-human/human-verified` status다. 권한/API 오류가 나면 보조 Check 쪽 운영 오류로 다루고, private 답변이나 보류 세부 내용은 공개하지 않는다.

```bash
gh run list --repo "$TLH_REPO" --workflow lasthuman-app.yml --limit 10
gh api "repos/$TLH_REPO/commits/$DEMO_HEAD/status" \
  --jq '.statuses[] | select(.context == "last-human/human-verified") | {state,context,creator:.creator.login,description,target_url}'
```

성공 status의 target은 본인만 볼 수 있는 `/receipts/<id>`다. relay가 초록이라는 사실만으로 성공을 선언하지 않는다.

**촬영:** 위험 이유, 면담 화면의 동작, 검증 완료, 상태 전환을 중심으로 담는다. 로그인 화면, 환경 파일, 키, OAuth query, private 성공 답변 원문과 보완 내용은 녹화·공개 자료에 남기지 않는다.

로그인·미완료 답변의 메모리 수명은 최대 30분이다. 오래 기다리다 세션이 만료되면 다시 로그인한다. 이미 저장된 성공 receipt를 복구해야 하는 상황에서 새 성공 답변을 반복 제출하지 않는다.

## 15. 실제 머지와 대시보드

PR 화면에서 기존 CI·리뷰·최신 base 조건과 필수 App 상태가 모두 충족됐는지 확인한다. 첫 데모는 **일반 merge commit**을 사용한다. merge queue는 현재 지원하지 않으며, 사용할 수 없는 경우 관리자 우회로 건너뛰지 않는다.

**터미널 A**

```bash
gh pr view "$DEMO_PR" --repo "$TLH_REPO" \
  --json state,headRefOid,baseRefName,mergeStateStatus,reviewDecision
```

현재 SHA·보호 조건이 맞을 때만 머지한다.

```bash
gh pr merge "$DEMO_PR" --repo "$TLH_REPO" --merge --match-head-commit "$DEMO_HEAD" &&
gh pr view "$DEMO_PR" --repo "$TLH_REPO" --json state,mergedAt,mergeCommit
```

새 head가 생겼다면 `--match-head-commit` 때문에 머지를 중단해야 한다. 최신 SHA를 대입해 우회하지 말고 새 커밋의 평가·면담·검증으로 돌아간다. `--admin`, `--auto`, `--rebase`를 추가하지 않는다.

터널과 서버를 아직 끄지 않는다. 머지 후 `closed` 이벤트의 relay가 완료되기를 기다리고, 로그인한 브라우저에서 `$TLH_BASE_URL/dashboard`를 새로고침한다.

**기대 결과:** 실제 머지가 한 번 집계되고, 성공 답변의 anchor가 속한 모듈에 인증 건수가 반영된다. 샘플 1개에서는 `small_sample`이나 건수 중심 표시가 정상이다. 화려한 그래프나 100% 커버리지를 기대하지 않는다. 이름별 순위·보류 횟수는 만들지 않는다.

단일 커밋 squash도 지원하지만, 첫 가이드의 여러 커밋 가능성을 고려해 일반 merge를 기본으로 한다. 다중 커밋 squash/rebase 등 계보를 확정하지 못하는 방식은 미측정으로 남을 수 있다.

## 16. 저위험 PR 장면 추가

먼저 위험 PR의 머지·집계까지 끝낸 다음 진행한다. 서로 다른 PR에 같은 head를 재사용하지 않는다.

```bash
git fetch origin main &&
git switch -c demo/readme-note-20260910 origin/main
```

`sample-app/README.md` 끝에 다음 문장 하나만 추가한다.

```text
The sample workload is also used for local demonstrations.
```

```bash
git --no-pager diff -- sample-app/README.md
```

변경 내용과 staging 상태를 확인한 뒤 실행한다.

```bash
git add sample-app/README.md &&
git commit -m "docs(sample): clarify the demo workload" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" &&
.venv/bin/python -m lasthuman score --base origin/main --head HEAD
```

저위험으로 예상되는지 확인한 뒤 PR을 만든다.

```bash
git push -u origin HEAD &&
gh pr create --repo "$TLH_REPO" --base main \
  --title "Clarify the sample workload documentation" \
  --body "Documentation-only clarification of the sample workload."
```

**기대 결과:** 현재 정책에서 `triggered: false`다. 내부 상태는 `neutral`, 질문·receipt는 없고 GitHub commit status는 "확인 불필요"를 나타내는 `success`다. Commit Status API에는 neutral 값이 없다.

이 PR을 머지한다면 이 PR의 번호·head를 새로 확인하고 15단계의 보호·머지 절차를 반복한다. 위험 PR의 `DEMO_PR`·`DEMO_HEAD` 변수를 재사용하지 않는다.

새 커밋으로 이전 인증이 무효가 되는 촬영은 별도 위험 PR에서 진행한다. 첫 성공 PR을 일부러 닫고 다시 열어 재사용하지 않는다.

## 17. 막혔을 때 돌아갈 위치

| 증상 | 조치 |
| --- | --- |
| `access create`에서 `--port`를 인식하지 못함 | `--port-number 8000` 또는 `-p 8000` 사용. 이미 생성된 포트는 그대로 두고 권한 설정부터 재개 |
| 공개 health 응답이 HTML 또는 터널 로그인 | 6단계의 정확한 포트, anonymous 접근 만료, `Accept: application/json` 확인 |
| public health가 400/접속 불가 | origin과 포트, 터널·서버 프로세스, 호스트 전달을 확인. 보호 설정 제거 금지 |
| OAuth 실패 | 같은 App의 Client ID/secret, 공개 callback, PR 작성자 계정 확인 |
| Azure CLI 미설치/인증 의존성 없음 | 4.1단계의 CLI와 프로젝트 `.[bot]` 설치 확인. 터미널 C의 PATH도 확인 |
| Azure CLI 재로그인 필요 | 같은 OS 계정에서 4.2단계의 tenant 지정 `az login` 수행. 토큰을 수동 복사하지 않음 |
| Azure 401/403 | Entra scope·tenant·추론 RBAC·endpoint·네트워크 확인. `disableLocalAuth=true`를 임의로 해제하지 않음 |
| Azure 404/400 | resource endpoint와 배포 이름, API version, Chat Completions/temperature 및 strict `response_format` 지원 확인 |
| Azure 429/5xx | 할당량·장애 확인 후 제한적으로 재시도. 사람의 보류 기록으로 남기지 않음 |
| workflow가 실행되지 않음 | trusted main의 workflow, Actions 설정, `LASTHUMAN_RUNTIME`, 새 PR 이벤트 확인 |
| 질문 개수·유형 오류로 relay가 실패 | [제공 앵커 스키마와 제한 재생성](github-app.md#질문-생성-형식-오류) 및 서버 배포 버전을 확인. 질문 수·앵커·유형 기준을 낮추지 않음 |
| OIDC 거부 | repo/owner ID, workflow 파일/ref, audience, Actions event 확인 |
| 제출 stale/409 | 새 head/base/PR 메타 변경 여부 확인 후 웹에서 재동기화. 이전 receipt 재사용 금지 |
| 보완 화면이 사라짐 | 30분 TTL 또는 재시작이면 다시 로그인·미완료 답변 재작성. 보류를 복구용 DB에 저장하지 않음 |
| 대시보드에 머지가 없음 | closed relay, 지원 merge 방식, 서버 DB와 현재 로그인 확인 |

### 검증 요청이 오래 대기할 때

검증 dispatch는 최초를 포함해 총 3회이며, 응답이 없으면 5분 간격으로 다시 요청한다. 정상 경로가 항상 15분 걸린다는 뜻은 아니다. 현재 웹 UI는 소진 상태를 충분히 보여주지 못하므로 Actions 실행과 receipt 링크를 함께 확인한다.

원인을 해결한 후에는 기존 실패한 run을 재실행하거나 **원래 receipt ID**로 검증 workflow를 요청한다. 아래는 실제 원격 실행을 만드는 명령이다.

```bash
gh workflow run lasthuman-app.yml --repo "$TLH_REPO" --ref main \
  -f receipt_id='REPLACE_WITH_EXISTING_RECEIPT_ID'
```

receipt ID는 성공 기록 URL 또는 해당 검증 실행의 입력에서 확인한다. App ID나 snapshot ID로 대신하지 않는다. 아직 receipt가 없다면 이 명령을 쓰지 않는다. 현재 binding이 달라졌다면 원래 인증을 억지로 살리지 말고 현재 PR 평가로 돌아간다.

### 닫았다가 같은 head로 재열고 머지한 경우

현재 코드에는 이전 closed 작업을 재사용해 머지 집계가 누락될 수 있는 경로가 있다. 첫 촬영에서는 이 패턴을 피한다. 캐시 만료나 서버 재시작만으로 누락 이벤트가 자동 복구되지는 않는다.

복구하려면 **먼저 터미널 C 서버를 Ctrl-C로 중지**하고, 동일 trusted worktree와 동일 환경 파일을 사용해 다음을 실행한다. `sync`는 GitHub 조회·발행을 수행할 수 있다.

```bash
cd "$HOME/workspace/the-last-human/.work/app-runtime" &&
set -a &&
. "$HOME/.config/the-last-human/github-app/runtime.env"
TLH_ENV_STATUS=$?
set +a
test "$TLH_ENV_STATUS" -eq 0 &&
.venv/bin/python -m lasthuman.server sync --pr REPLACE_WITH_MERGED_PR_NUMBER &&
.venv/bin/python -m lasthuman.server flush
```

복구 명령이 성공한 후 19단계의 서버 재시작 명령으로 돌아가 재로그인한다. 다른 프로세스의 서버와 동시에 실행하지 않는다.

## 18. 첫 완주 후 키 폐기·백업·종료

1. 새 키로 App 인증과 실제 봇 발행이 되는 것을 확인한 뒤 GitHub App 설정에서 **Windows용으로 식별한 이전 private key만** 삭제한다. 다른 용도의 키는 지우지 않는다. 새 키가 어느 항목인지 fingerprint로 구분한다.
2. OAuth client secret도 교체했다면 이전 secret의 사용처가 없는지 확인하고 그 항목만 폐기한다. Azure 키의 무작정 재생성은 다른 앱에 영향을 줄 수 있으므로 별도 관리한다.
3. 진행 중인 면담·검증·closed 집계가 없는지 확인한다. 필요한 원격 기록은 PR URL, head SHA, workflow URL, 상태, merge SHA 정도로 한정한다.
4. 터미널 C에서 Ctrl-C로 서버를 중지한다. 이후 터미널 B에서 Ctrl-C로 터널 host를 중지한다.
5. 성공 DB를 보관한다. `.venv`나 snapshot cache와 달리 DB는 삭제 가능한 캐시가 아니다.

Azure CLI 로그인 캐시는 DB 백업에 포함하지 않는다. 데모 종료 때마다 로그아웃할 필요는 없지만, 계정 회수·장치 반환 등으로 로그아웃해야 한다면 인증 세션을 사용하는 다른 작업도 고려한다. 캐시 폴더를 수동으로 지우거나 공유하지 않는다.

**서버 중지 후, 터미널 A**

```bash
.venv/bin/python - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

database = Path(os.environ["TLH_DATABASE"])
if not database.is_file():
    raise SystemExit("Receipt database does not exist; nothing was backed up")
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
backup = database.with_name(f"lasthuman-backup-{stamp}.sqlite3")
os.umask(0o077)
with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
    with sqlite3.connect(backup) as target:
        source.backup(target)
print(f"Private database backup: {backup}")
PY
```

백업도 성공 답변을 포함하므로 비공개로 보관하고 Git·영상에 넣지 않는다. 원래 DB를 그대로 두고 다음 촬영 때 재사용한다.

터널을 더 이상 쓰지 않을 때만 **이번에 만든 정확한 ID**로 삭제한다. 터미널 B를 새로 열었다면 기록한 ID를 먼저 확인한다.

```bash
devtunnel delete "$TLH_TUNNEL_ID"
```

`delete-all`은 사용하지 않는다. 같은 터널을 재사용하려면 삭제하지 말고 만료와 접근 권한을 확인한다. URL이 바뀌면 서버 origin, callback, 저장소 `TLH_BOT_URL`을 함께 갱신해야 한다.

서버를 끌 때 편의상 `LASTHUMAN_RUNTIME`을 legacy로 돌리지 않는다. 새로운 PR이 서버 없이 진행되지 않더라도 보호를 유지한다. 실제 rollback이 필요하면 머지를 동결하고, 새 writer와 진행 중 relay를 정지한 뒤, 준비된 이전 writer·변수·기대 발급자를 함께 복구한다.

## 19. 다음 촬영 때의 짧은 재시작

같은 DB·키·App 설치가 있는지, trusted 정책 코드가 변경되지 않았는지 확인한다. `main`의 정책이나 workflow가 바뀌었으면 실행 중 서버를 멈추고 승인된 새 trusted 버전으로 맞춘 뒤 기존 열린 PR도 재평가한다.

**새 터미널 A**

```bash
cd "$HOME/workspace/the-last-human" &&
export PATH="$HOME/.local/bin:$PATH" &&
export TLH_REPO='hunhoon21/the-last-human' &&
set -a &&
. "$HOME/.config/the-last-human/github-app/runtime.env"
TLH_ENV_STATUS=$?
set +a
test "$TLH_ENV_STATUS" -eq 0
```

**새 터미널 B**

```bash
export TLH_TUNNEL_ID='REPLACE_WITH_PREVIOUSLY_RECORDED_TUNNEL_ID' &&
devtunnel show "$TLH_TUNNEL_ID" &&
devtunnel access list "$TLH_TUNNEL_ID"
```

터널 또는 8시간 익명 접근이 만료됐다면 6단계의 방식으로 필요한 기간만 갱신한다. 터널을 새로 만들어 URL이 바뀌면 서버 origin, callback, 저장소 `TLH_BOT_URL`을 먼저 일치시킨다. 이후 host한다.

```bash
devtunnel host "$TLH_TUNNEL_ID"
```

**새 터미널 C**

```bash
cd "$HOME/workspace/the-last-human/.work/app-runtime" &&
git status --short --branch &&
git rev-parse HEAD
```

경로가 정확한지, 변경이 없는지, SHA가 기록한 승인 버전인지 확인한다. 하나라도 다르면 서버를 시작하지 않는다. 모두 맞을 때만 다음을 실행한다.

```bash
cd "$HOME/workspace/the-last-human/.work/app-runtime" &&
export PATH="$HOME/.local/bin:$PATH" &&
set -a &&
. "$HOME/.config/the-last-human/github-app/runtime.env"
TLH_ENV_STATUS=$?
set +a
test "$TLH_ENV_STATUS" -eq 0 &&
if test "${LASTHUMAN_AUTH_MODE:-default}" = azure-cli; then command -v az; fi &&
caffeinate -i .venv/bin/gunicorn \
  --bind 127.0.0.1:8000 --workers 1 --threads 4 --timeout 180 \
  'lasthuman.server.app:create_app()'
```

Azure 인증이 유효하면 매번 `az login`을 반복할 필요는 없다. 재인증이 요구되면 4.2단계로 돌아간다. 10단계의 공개 health JSON과 GitHub 작성자 로그인을 확인하고, 새 고유 이름의 PR을 만들어 12~15단계를 반복한다. 이전 receipt를 새 PR·새 SHA의 인증으로 사용하지 않는다.

**한 바퀴 완료 조건:** 실제 작성자 면담, 원래 receipt의 독립 검증, 현재 SHA의 기대 App success, 기존 보호를 충족한 실제 머지, 해당 merge의 모듈 집계가 모두 연결되어 있어야 한다.

## 공식 참고자료

- [GitHub App 개인키 발급·교체·fingerprint](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps)
- [GitHub 필수 상태와 발급 App](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging)
- [Microsoft Dev Tunnels 설치](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started)
- [Dev Tunnels 명령·persistent tunnel·포트 접근](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/cli-commands)
- [Dev Tunnels TLS·접근 제어·최초 안내 페이지](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/security)
- [Azure OpenAI 리소스·모델 배포·키](https://learn.microsoft.com/en-us/azure/foundry-classic/openai/how-to/create-resource)
- [Azure 모델 수명과 배포 업데이트](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/working-with-models)
- [Azure CLI 로그인](https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli-interactively)
- [AzureCliCredential](https://learn.microsoft.com/en-us/python/api/azure-identity/azure.identity.azureclicredential)
- [Azure CLI 캐시와 MSAL](https://learn.microsoft.com/en-us/cli/azure/msal-based-azure-cli)
- [Azure 키 인증 비활성화](https://learn.microsoft.com/en-us/azure/ai-services/disable-local-auth)
