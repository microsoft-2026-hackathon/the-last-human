# 발표 스토리보드 v6 — The Last Human (한국어 · English)

**Hack for Agentic Coding 제출본.** 두 언어를 한 문서에 둡니다. 같은 덱, 같은 빌드 단계,
같은 논지이고 **문장과 시간만** 언어별로 다릅니다. v5(한국어)와 v5-en(영어)을 대체합니다.

| | 한국어 | English |
| --- | --- | --- |
| 덱 | [`presentation/index.html`](presentation/index.html) | [`presentation/index.en.html`](presentation/index.en.html) |
| 나레이션 | `presentation/video/narration.json` | `presentation/video/narration.en.json` |
| 기존 렌더 | `the-last-human-3m30.mp4` · **4:17.9** · 자막 49장 | `the-last-human-en.mp4` · **4:05.7** · 자막 50장 |
| 목소리 | `ko-KR-HyunsuMultilingualNeural` | `en-US-AndrewMultilingualNeural` |
| 데모 88초 | 앞 103s · 뒤 68s | 앞 95s · 뒤 63s |

슬라이드 구간의 기존 스크립트와 시간은 `narration*.json`과 `manifest*.json`에서 가져왔습니다.
**DEMO 절은 새로 촬영할 88초 화면 시연의 기준**이며, 이번 음성 시간은 단어 수 기반 예상값입니다.
기존 MP4·음성이 이 원고로 갱신됐다는 뜻은 아닙니다. 촬영·TTS 후 실제 길이를 측정하고,
영상 작업본의 나레이션·manifest와 이 문서를 함께 맞춥니다.

## 1. 한 줄 논지

> Copilot이 여는 PR은 급증했지만 **그것을 검증할 방법은 따라가지 못했습니다.** agent는 이미
> SDLC 전 단계에 들어와 있고 모든 단계가 기록되는데, merge의 신뢰만 기록이 없습니다.
> The Last Human이 그 한 층을 만들어 Agentic Coding을 **Trusted Agentic Coding**으로 옮깁니다.

> Copilot opens pull requests faster than teams can verify them. Agents are already in every
> stage of the SDLC and every stage leaves a record — **only trust at merge has none.**
> The Last Human builds that one layer, taking agentic coding to **trusted agentic coding.**

## 2. 이 문서로 만든 영상

기존 영상은 슬라이드 대본을 Azure Speech로 합성하고, 덱을 빌드 단계마다 캡처해 이어 붙인 것입니다.
새 DEMO는 실제 제품 화면을 녹화·편집해 교체합니다. 아래 명령은 해당 스크립트가 있는 영상 작업 사본에서
실행하는 기존 파이프라인이며, 이번 문서 변경만으로 녹화·음성 생성까지 수행되지는 않습니다.

```bash
cd docs/demo/presentation/video
python make_tts.py      # 나레이션 합성 + 길이 측정   (영어는 --lang en)
python make_shots.py    # 덱을 빌드 단계마다 캡처
python build_video.py   # 조립 + 자막 굽기
```

- 아래 표의 시간 합계(4:19 · 4:06)와 영상 길이(4:17.9 · 4:05.7)가 3초 안쪽으로
  다릅니다. 표는 구간마다 초 단위로 반올림한 값이고 영상은 실측을 그대로 이어 붙이기 때문입니다.
- MP4·캡처·음성 90MB는 저장소에 넣지 않았습니다. 위 세 명령으로 다시 만듭니다.
- **3분 30초 예산을 한국어 48초 · 영어 36초 넘습니다.** 더 줄이면 설명이 단문 나열로
  돌아가므로 여기서 멈췄습니다. 줄여야 하면 화면이 스스로 읽히는 **02 · 06 · 08 · 09**부터
  깎으십시오 — 00 · 04 · 05 · 07 · 10은 논지를 옮기는 장입니다.
- **화면이 대본보다 많이 말합니다.** 시간을 맞추느라 대본에서 뺀 수치가 슬라이드에는 그대로
  있습니다 — 04의 `45.4%`와 `3.5×`, 07의 `14 / 3`이 그렇습니다. 물으면 5장의 근거로 답하십시오.

## 3. 페이지별 스크립트

**표기** — `→n`은 그 지점에서 `→`를 눌러 다음 빌드 단계를 띄우라는 신호입니다.
한국어는 합쇼체이고, 괄호와 배지 이름은 읽지 않습니다.

### 00 · The Last Human

**English** — The Last Human

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 0:00–0:11 · 11s | "Copilot이 여는 PR은 급증했지만, 그것을 검증할 방법은 따라가지 못했습니다. 저희는 Trusted Agentic Coding을 위해 The Last Human을 만들었습니다." |
| English | 0:00–0:12 · 12s | "Copilot opens pull requests far faster than teams can verify them, and they reach production unverified. We built The Last Human to get from agentic coding to trusted agentic coding." |

**시사점** — **제품 설명으로 시작하지 않습니다.** 지금 조직이 겪고 있는 상황을 먼저 말하고, 프로젝트를 그 상황에 대한 답으로 놓습니다.
<br>**Why it matters** — **Do not open with the product.** Name the situation the audience is living in, then place the project as the answer to it.

### 01 · Agentic AI의 가장 중요한 전제 · `The Mandate` — 3단계

**English** — The one premise Agentic AI rests on · `The Mandate`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 0:11–0:26 · 15s | "Agentic AI에는 가장 중요한 전제가 있습니다. `→1` AI는 사람의 통제 아래 있어야 한다는 것입니다. `→2` 아니면 운영 권한을 잃는다고 말합니다. `→3` 그 방법이 모든 층을 관측하는 것입니다." |
| English | 0:12–0:25 · 13s | "Agentic AI rests on one premise above all others. `→1` AI has to stay under human control. `→2` Without that, we lose permission to operate at all. `→3` And the method proposed is observability at every layer." |

**시사점** — 우리 의견이 아니라 **회사가 이미 세운 전제**에서 출발합니다. 반박할 자리가 없습니다.
<br>**Why it matters** — We open from **a premise the company has already set**, not from our opinion. There is nothing here to argue with.

### 02 · 모든 단계가 관측됩니다. merge의 신뢰만 빼고 · `Where the Gap Is` — 5단계

**English** — Every stage leaves a record. Every stage but merge · `Where the Gap Is`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 0:26–0:42 · 16s | "agent는 이미 SDLC 전 단계에 있습니다. `→1` 구현은 Copilot agent가 맡고, `→2` 테스트와 리뷰도 자동입니다. `→3` 배포와 운영도 그렇습니다. `→4` merge 한 곳만 근거가 없습니다. `→5` 부하는 여기 쌓입니다." |
| English | 0:25–0:39 · 14s | "Agents are already in every stage of the SDLC. `→1` Copilot writes the implementation, `→2` tests and review run automatically, `→3` deploy and operate leave records too. `→4` Merge alone records nothing. `→5` The load lands here." |

**시사점** — **SDLC 7단계 중 6개는 자동화되고 기록이 남습니다.** merge만 사람의 판단이고 근거가 안 남습니다. Copilot이 PR을 여는 속도만큼 이 지점에 부하가 쌓입니다.
<br>**Why it matters** — **Six of the seven SDLC stages are automated and leave records.** Merge alone is human judgment and leaves none. Load piles up here exactly as fast as Copilot opens PRs.

### 03 · 우리 감은 틀렸습니다 · `Feeling Is Not Evidence` — 1단계

**English** — Our intuition was wrong · `Feeling Is Not Evidence`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 0:42–0:55 · 13s | "이 판단은 지금까지 사람의 감에 맡겨져 있었습니다. 모두가 빨라진다고 예측했습니다. `→1` 그러나 측정값은 19퍼센트 느려진 것이었습니다. 자기 보고는 근거가 아닙니다." |
| English | 0:39–0:49 · 10s | "This judgment has been left to intuition. Everyone predicted AI would make them faster. `→1` Measured, they were nineteen percent slower. Self-report is not evidence." |

**시사점** — 그 판단을 **감으로** 해 왔는데, 자기 보고는 근거가 되지 못한다는 것이 통제 실험으로 나왔습니다. 그래서 **기록**이 필요합니다.
<br>**Why it matters** — We have made that judgment **by feel**, and a controlled trial shows self-report is not evidence. Which is why we need a **record**.

### 04 · Agentic Coding의 불완전함은 이미 측정되었습니다 · `The Bottleneck, Named` — 4단계

**English** — The gaps in agentic coding are already measured · `The Bottleneck, Named`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 0:55–1:18 · 23s | "Agentic Coding의 불완전함은 이미 측정되고 있습니다. `→1` 올해 논문이 agentic PR 2만 3천 건을 분석했습니다. `→2` 설명과 실제 코드가 어긋나는 경우가 많았고, 그런 PR도 28퍼센트는 그대로 승인됐습니다. `→3` 논문은 검증 장치가 필요하다고 강조합니다. `→4` 저희는 여기서 영감을 얻어 그것을 만들었습니다." |
| English | 0:49–1:11 · 22s | "The gaps in agentic coding are already being measured. `→1` A paper this year analyzed twenty-three thousand agentic pull requests. `→2` Descriptions often did not match the code, and twenty-eight percent of those PRs were approved anyway. `→3` The authors call for a mechanism to verify this. `→4` We took that as our starting point and built one." |

**시사점** — **덱에서 가장 강한 장입니다.** 논문이 공백을 측정했고, 필요한 해법까지 지목했습니다. 그것이 우리가 만든 것입니다. 숫자는 반드시 출처와 함께 말합니다.
<br>**Why it matters** — **The strongest chapter in the deck.** A paper measured the gap and pointed at the fix that is needed. That fix is what we built. Say every number with its source.

### 05 · 업계의 답은 금지, 우리의 답은 확인 · `Ban or Check` — 2단계

**English** — A check, not a ban · `Ban or Check`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 1:18–1:30 · 12s | "일부 업계는 아예 금지를 택했습니다. `→1` 간편하지만 생산성까지 함께 포기하는 방향입니다. `→2` 저희는 위험한 변경만 확인하고 그 이해를 함께 남깁니다." |
| English | 1:11–1:24 · 13s | "Parts of the industry answered by banning AI contributions. `→1` Simple, but it gives up the productivity along with the risk. `→2` Instead of banning, we verify only the risky changes and keep the understanding." |

**시사점** — 금지를 깎아내리지 않습니다. **간편하지만 생산성까지 함께 포기한다**는 점만 짚고, 우리가 무엇을 더 하는지로 맺습니다.
<br>**Why it matters** — Do not belittle the ban. Make one point — **it gives up the productivity along with the risk** — and close on what we do instead.

### 06 · 설명의 방향을 뒤집었습니다 · `Reversing the Flow` · `Proof in Motion →` — 4단계

**English** — We reversed the direction of explanation · `Reversing the Flow` · `Proof in Motion →`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 1:30–1:43 · 13s | "그래서 설명의 방향을 뒤집었습니다. `→1` 다른 도구는 모델이 설명합니다. `→2` 여기서는 사람이 답합니다. `→3` 그 답은 commit에 묶입니다. `→4` 동작을 보시겠습니다." |
| English | 1:24–1:35 · 11s | "So we reversed the direction of explanation. `→1` Other tools have the model explain. `→2` Here the person answers the model. `→3` And that answer binds to the commit. `→4` Let me show you it working." |

**시사점** — 화살표 방향이 제품을 설명합니다. 그리고 **파이프라인 안에서** 돈다는 점이 중요합니다 — 별도 도구가 아닙니다.
<br>**Why it matters** — The direction of the arrow *is* the product. And it matters that it runs **inside the pipeline** — this is not a separate tool.

### DEMO · 실제 화면 88초 · English VO

**목표는 1분 20초–1분 30초, 기준 편집본은 88초입니다.** 아래 타임코드는 데모 시작을 `00:00`으로 둡니다.
기존 88초 슬롯을 유지하므로 앞뒤 슬라이드의 시간은 바꾸지 않습니다.

| | 전체 영상 안의 위치 | 진행 |
| --- | --- | --- |
| 한국어 덱 | 1:43–3:11 · 88s | 데모의 English VO를 재생하고 발표자는 중복 해설하지 않음 |
| English deck | 1:35–3:03 · 88s | Play the embedded English VO; do not add live narration |

**이 데모의 연결:** Copilot이 PR을 만듦 → 기존 검사·승인은 있지만 사람의 설명은 대기 → 작성자가 로그인해
코드 근거로 설명 → Hold 문항을 같은 커밋에서 보완 → 실제 gate 성공과 merge 가능 상태 →
ORG의 모듈 분포 → repo의 기록·선언된 담당 → 장애 조사에 활용할 문의 경로.

각 컷은 관객의 다음 질문에 답합니다. **왜 merge가 막히는가 → 누가 설명하는가 → 왜 Hold인가 →
어떻게 보완하는가 → 무엇이 열리는가 → 조직에서는 어떻게 활용하는가** 순서입니다.
리뷰어의 승인 자체가 없다는 뜻이 아니라, 작성자가 코드 근거로 설명하는 확인이 아직 남았다는 점을 구분합니다.

영상 진입 전 도입 참고: `Now, let's see how The Last Human adds trust and human control to an agentic coding workflow.`
이 문장은 발표자가 데모 전에 말할 수 있는 참고 문구이며 아래 88초 화면 시연과 VO 단어 수에는 포함하지 않습니다.

**추가 커밋과 실제 merge 클릭은 하지 않습니다.** 최초 PR 작성 커밋은 필요하지만 면담 중에는 코드를
바꾸지 않습니다. 바뀌는 것은 작성자의 설명입니다. 알려진 재시도 문제를 고쳤다거나, 확인 통과가
코드의 안전을 보증한다고 말하지 않습니다. 아래 메시지·촬영 지시는 제작 문서용이며 제품 화면에
소개 문장·개발 이력·해설 카드를 덧붙이지 않습니다.

#### 장면·메시지·English VO

| 컷 | 화면 목표 | 전달할 메시지 | 화면과 실제 동작 | English VO (문장 단위 · 촬영·편집 후 확정) |
| --- | --- | --- | --- | --- |
| D01 | 7.5s | 사람이 작업을 요청하고 에이전트가 구현·PR 생성까지 수행 | GitHub Copilot app 화면을 왼쪽에, 흐름 참고 패널을 오른쪽에 합성. 프롬프트 전문을 읽히고, 도구 호출 구간은 빠른 감기로 압축한 뒤 실제 `Pull request created` 줄로 끝냄 | We ask GitHub Copilot to handle failures in token refresh, and it opens a pull request. |
| D02 | *T1b에 포함* | 방금 요청한 작업이 검토 대상이 됨 | GitHub PR 목록에서 해당 PR 한 행을 클릭. 다른 PR은 강조하지 않음 | The pull request is ready for review. |
| D03 | 12.0s (D02 포함) | 검사와 승인이 끝났어도 작성자의 설명 확인이 남아 있음 | 실제 CI 성공 4건과 다른 리뷰어의 Approve를 먼저 보여 준 뒤 스크롤. 비활성 Merge와 `last-human/human-verified`의 pending · Awaiting author explanation · **Required**를 강조 | Tests and lint pass, and a reviewer approves.<br>To merge, the author still needs to explain the change from the code. |
| D04 | 5.5s | 작성자 자신의 GitHub 계정으로 면담에 진입 | PR 카드의 `Check this change` → `Authorize the-last-human-app` 계정 선택 → 작성자 계정의 `Continue` → 면담 화면. 사용하지 않는 계정 행은 가림 | The author signs in with their GitHub account. |
| D05 | 23.0s (D06 포함) | 호출되는 코드의 동작을 빠뜨린 설명은 보완이 필요 | 두 문항에 답해 한 번에 `Submit answers`. 채점 대기를 압축하고 첫 문항 `Accepted`, 둘째 `Hold`를 읽히는 데 시간을 배정 | In this example there are two questions.<br>The first answer is accepted.<br>Unfortunately, the second is on hold. |
| D06 | *T3에 포함* | 힌트를 따라 호출 관계를 이해하고 설명만 바로잡음 | `Look here` 안내와 `http_client.py` 발췌를 읽음. 마지막 문장에 맞춰 둘째 문항의 근거만 정정 — 코드는 바꾸지 않음 | The hint points to the called file.<br>The change retries three times; so does the code it calls.<br>So one call can become nine.<br>Only the explanation changes. |
| D07 | 11.0s | 통과한 설명이 현재 커밋에 해당함을 확인한 뒤 작성자가 merge 여부를 결정 | 재제출 → 두 문항 Accepted → 영수증 → 같은 SHA의 `Human-verified` 필수 status success → **All checks have passed · 6 successful checks** → 활성 `Merge pull request`. merge 버튼은 클릭하지 않음 | An independent check confirms the explanation matches this commit.<br>All checks pass, and the author can decide whether to merge. |
| D08 | 21.0s (D09 포함) | 한 PR을 넘어 조직 범위를 파악하고, 볼 수 있는 범위가 권한으로 정해짐을 밝힘 | ORG 대시보드 Demo. `Demo data` 배지를 계속 노출. 저장소 목록 → `0 confirmed authors` 카드 선택 → 해당 모듈 행 | Beyond one pull request, every repository your account can open is here.<br>One module has nobody who has confirmed a change yet. |
| D09 | *T5에 포함* | 저장소 단위의 확인 기록과 예외 처리를 살펴봄 | repo 대시보드 Demo로 이동. `Human-verified before merge` → `Merged without verification`. `Declared owner` 열은 데모 픽스처가 비어 있어 강조하지 않음 | Inside a repository, three quarters of gated changes were human-verified.<br>Three were merged as an exception — recorded, not blocked. |
| D10 | 12.0s | 장애가 생기면 살펴볼 코드와 상의할 출발점으로 활용 | GitHub 사후 분석의 재시도 문구·출처 → `.github/CODEOWNERS`의 선언된 담당 → 대시보드의 `CAN ANSWER` → `MAX_ATTEMPTS = 3` | GitHub's outage showed how a retry loop can amplify traffic during recovery.<br>The record names who answers for that code, and how many have explained it. |

**대본 원칙(2026-09-17 확정)**

- 문장을 짧게 끊고 **문장 사이에 0.8–1.2초 쉼**을 둠. 쉼은 컷 WAV를 문장 단위로 잘라 배치 간격으로 만들며, **나레이션 배속은 쓰지 않음**.
- 화면이 이미 말하는 문장은 대본에서 뺌. v6 초안 28문장 → **최종 20문장 · 음성 실측 72.4초**.
- 화면에 없는 기능은 말하지 않음. 초안의 `sorts repositories by risk`는 ORG 목록이 이름순 정렬이라 삭제함(구현은 이슈 #34).
- `3 × 3 = 9`가 성립하려면 **두 개의 3을 모두 말해야 함**. D06 둘째 문장이 `token.py`와 호출된 코드의 재시도 횟수를 함께 말함.
- 확인한 사람의 이름은 말하지 않음. `DECLARED OWNER`는 *누구에게 물어볼지*, `CAN ANSWER`는 *설명해 본 사람 수*이며 둘은 다름.

#### D01 · Copilot에 입력할 요청 (실제 사용본)

먼저 이슈를 올리고, Copilot app에는 그 이슈를 참조하는 한 줄만 입력함.

```text
Read issue #32 in microsoft-2026-hackathon/the-last-human and implement it.
Work only inside sample-app. Add tests, create a branch, commit, push,
and open a pull request that closes the issue.
```

이슈와 요청 어디에도 **질문의 문구·보기·정답이나 `3 × 3 = 9`, `MAX_ATTEMPTS`를 넣지 않음.** 작성자가 호출 계층을
스스로 보지 못했다는 것이 D05–D06의 논지이므로, 이슈에 답이 적혀 있으면 데모가 성립하지 않음.

**PR 번호 불일치(2026-09-17 결정).** 이 촬영본은 실제로 **PR #33**(`fix/token-refresh-transient`)을 만들었고,
D02–D07은 앞서 준비된 **PR #28**(`enh/auth-resilience-demo`)을 씀. 제출 시한 때문에 재촬영하지 않기로 하고,
D01에서는 **"이 요청이 PR을 만들었다"까지만** 보여 주며 브랜치명·PR 번호가 드러나는 구간은 편집에서 제외함.
두 PR의 변경 파일(`sample-app/app/auth/token.py`, `sample-app/tests/test_token.py`)과 변경 성격은 같음.
시간이 생기면 #33으로 T1b–T4를 재촬영해 번호를 일치시킴.

#### D01 · 흐름 참고 패널

GHCP 화면 오른쪽에 붙이는 패널은 `.work/demo-v6/assets/flow-panel-d01.png`임.
`Issue → Agent → [the diff: token.py] --calls--> http_client.py (not in the diff)` 까지만 담고,
**`3 × 3 = 9`는 넣지 않음** — D05–D06의 답을 D01에서 미리 보여 주게 되기 때문임.
숫자가 들어간 전체 도해(`assets/flow-diagram.png`)는 D06에 쓸 수 있음.

#### D04–D07 · 로그인과 같은 커밋에서의 보완

- D04는 **사용자 OAuth의 계정 선택 화면**입니다. App 설치 화면이나 이미 인증이 끝난 화면으로
  설명하지 않습니다. 실제 PR 작성자 계정을 선택하고 후속 승인 화면이 있으면 같은 흐름에 포함합니다.
- 내레이션은 `GitHub account`라고 표현합니다. OIDC는 Actions와 TLH Server 사이의 인증이며,
  사용자의 로그인 계정 명칭으로 쓰지 않습니다.
- 사용하지 않는 계정 행, 브라우저 주소의 OAuth code/state, 터미널의 토큰·환경 파일은 촬영하지 않습니다.
  계정 선택이 이미 유지돼 해당 화면이 생략된다면, 리허설에서 정상 로그인 경로를 준비합니다.
- 첫 답변은 변경 안의 실제 반환 동작에 근거합니다. 둘째는 피호출자가 재시도하지 않는다고 오해한
  선택을 한 뒤, 보류 근거를 읽고 설명을 고칩니다. **실제 생성 문항과 정답 위치를 먼저 확인**하며,
  예상과 다르게 나온 문항에 녹화된 정답·배지를 덮어씌우지 않습니다.
- 재시도 관계가 실제 문항일 때의 보완 근거 예시:
  `ensure_fresh calls refresh up to three times; post_json can make three attempts per call.`
  이는 두 계층이 재시도 대상 실패에 대해 끝까지 반복하는 경우의 상한입니다. 모든 요청이 항상
  아홉 번 실행된다는 뜻은 아닙니다.
- Accepted 문항은 그대로 두고 Hold 문항을 보완합니다. 촬영 중 head SHA뿐 아니라 PR 제목·본문,
  base·정책도 바꾸지 않아 같은 snapshot을 유지합니다. 새 커밋 후 재확인 장면은 이번 컷에 없습니다.
- 문항 Accepted, receipt 생성, Actions 검증, GitHub status success는 다른 단계입니다.
  D07은 **실제 게시된 현재 SHA의 성공 상태**와 기존 리뷰·CI 조건까지 충족됐을 때 촬영합니다.
  독립 검증은 통과한 설명의 인증이 현재 snapshot에 해당하는지 확인합니다. 답변의 의미나 코드의
  안전성을 다시 판정하는 단계로 표현하지 않으며, 마지막 merge 선택은 작성자에게 남깁니다.

#### D08–D10 · 대시보드의 순서와 가치

**기본 순서는 ORG Demo → repo Demo → 선언된 담당입니다.** 먼저 여러 저장소를 포함한 관측 범위와 모듈 분포를
보여 주고, 한 저장소로 좁혀 기록과 문의처를 확인합니다. repo만 보여 주는 것보다 조직이 얻는 가치를
드러내면서도, 마지막에는 실제 코드와 협업 경로로 돌아올 수 있습니다.
모듈별 확인 기록은 **어디를 살펴볼지**, 선언된 담당은 **누구와 상의할지**를 연결합니다.
대사를 늘리거나 전문가 목록을 암시하지 않고, 모듈 카드 → 관련 행 → 코드·문의처 순서로 보여 줍니다.

| 보여 줄 정보 | 관객이 이해할 가치 | 말하지 않을 것 |
| --- | --- | --- |
| ORG의 모듈 분포 | 확인 기록이 어디에 쌓이고 어디를 더 살펴볼지 파악 | 조직에서 검증된 전문가가 총 몇 명이라는 주장 |
| repo의 확인 건수·근거 | 담당 선언과 별도로, 어떤 변경에 확인 기록이 있는지 파악 | 높은 비율만으로 코드 전체가 안전하다는 주장 |
| 현재 Declared owner / CODEOWNERS | 질문을 보낼 공식 담당자·팀을 찾음 | 해당 담당자가 익명 확인 기록의 작성자라는 추정 |
| Demo 출처 배지 | 화면의 기록이 예제 자료임을 구분 | 예제의 숫자를 실측·고객 실적으로 소개 |

ORG Demo의 가상 저장소명은 진입 기준인 the-last-human repo 대시보드로 연결됩니다.
이는 가상 저장소의 실데이터를 여는 것이 아니므로, 이동 후 실제 대상 이름과 Demo 배지를 계속 보여 줍니다.
이번 화면 시연은 repo에서도 Demo를 유지하며 Actual로 전환하지 않습니다. 내레이션은 저장소 단위의
기록과 문의처에 집중하고, 데이터 모드 전환이나 가상 링크의 구현 구조를 해설하지 않습니다.
**D07에서 merge를 클릭하지 않으므로 이 PR 때문에 Can answer가 0→1로 변하는 연출은 하지 않습니다.**
예제 기록을 방금 확인한 PR의 결과로 소개하지 않습니다. 실제 모듈 집계는 적격한 확인 뒤 머지 사실이
기록돼야 반영됩니다.

현재 담당자가 화면에서 확인되지 않으면 최신 `.github/CODEOWNERS`를 열어 확인합니다.
과거 스냅샷의 담당을 현재 담당처럼 단정하거나 비공개 receipt에서 사람 이름을 역추적하지 않습니다.

#### D10 선택안 · Copilot과 상의할 출발점 찾기

**기본안은 담당·코드를 직접 여는 장면입니다.** Copilot 질의 장면을 쓰려면 D10의 같은 12초를
교체합니다. 추가 컷으로 붙여 88초를 넘기지 않습니다.

```text
Read .github/CODEOWNERS and the token-refresh code.
Who is the declared owner to consult, and which functions should we inspect first?
```

코드가 있는 작업공간에서 실제 질의를 실행하고 나온 결과만 촬영합니다. 결과는
`Declared owner: <current CODEOWNERS entry>`와 `Inspect: ensure_fresh / post_json`처럼
근거 파일·함수를 짧게 보여 주는 형태가 적합합니다. 특정 답변을 제품이 자동으로 낸 것처럼 합성하지 않습니다.

Copilot이 TLH의 비공개 대시보드나 receipt를 자동 조회하는 연동은 이 장면의 전제가 아닙니다.
필요한 공개 코드·CODEOWNERS 또는 사용자가 확인한 자료를 명시적으로 제공해야 합니다.
“이 사람이 인증을 통과했으니 전문가”가 아니라 **선언된 문의처와 조사할 코드**를 찾습니다.
실제 결과가 이 조건을 충족하지 않으면 기본안으로 촬영합니다.

GitHub 사례의 연결점은 **복구 중 재시도가 트래픽을 증폭할 수 있다는 점**입니다.
짧게 인용할 문구는 `a client-side retry loop that increased traffic during recovery`입니다.
출처는 [GitHub 사후 분석](https://github.blog/news-insights/company-news/the-august-17-outage-and-the-work-ahead/).
원래 장애의 주원인은 용량 문제이며, 이 샘플 PR이 그 장애를 일으켰다거나 TLH가 이를 예방했을 것이라고
말하지 않습니다. 대시보드는 진단·협업의 출발점을 제공하지 장애를 자동으로 해결하지 않습니다.

#### 음성 실측 (2026-09-17)

`en-US-AndrewMultilingualNeural`, **rate 0%**, 컷별 WAV(`.work/demo-v6/voice/cuts/`). 아래는 계산값이 아니라 **실측**임.

| 컷 | 문장 수 | 음성 실측 |
| --- | ---: | ---: |
| D01 | 1 | 6.2s |
| D02 | 1 | 2.6s |
| D03 | 2 | 6.8s |
| D04 | 1 | 2.4s |
| D05 | 3 | 7.8s |
| D06 | 4 | 10.3s |
| D07 | 2 | 8.2s |
| D08 | 2 | 9.0s |
| D09 | 2 | 9.7s |
| D10 | 2 | 9.4s |
| **합계** | **20** | **72.4s** |

- 최종 영상 길이 ≈ `음성 72.4 + 문장 사이 쉼 13회 + 컷별 도입·여운 7회`. **목표는 90초 이내**이며 88초 슬롯을 그대로 씀.
- 쉼은 0.8–1.0초, 페이지가 바뀌는 자리만 1.2초까지. 컷 도입 0.7초, 끝 여운 0.7–0.8초.
- **TTS가 길면 대본을 줄이며, 음성 속도를 올려 맞추지 않음.** 이 원칙 때문에 v6 초안 28문장을 20문장으로 줄임.
- 길이가 더 필요하면 다음 순서로 줄임 — ① D05 첫 문장 ② D10 블로그 인용 구간 ③ 컷 여운.

#### 촬영 단위와 편집 원칙

| 테이크 | 컷 | 확보할 실제 화면 | raw |
| --- | --- | --- | --- |
| T1 | D01 | Copilot app의 프롬프트·작업·PR 생성. 흐름 참고 패널을 오른쪽에 합성 | `~/Desktop/ghcp-develop.mov` → 합성본 `raw/t1-copilot.mov` |
| T1b | D02–D03 | PR 목록 한 행, CI 4건·Approve, `last-human/human-verified` pending **Required**, 비활성 Merge | `t1b-pr.mov` |
| T2 | D04 | GitHub 계정 선택·승인과 실제 면담 진입 | `t2-signin.mov` |
| T3 | D05–D06 | 일괄 제출, Accepted/Hold, 힌트·발췌, 같은 커밋에서 설명 보완 | `t3-answer-hold.mov` |
| T4 | D07 | 재제출 이후 실제 검증·게시 완료, **All checks have passed · 6 successful**, merge 가능 상태 | `t4a-resubmit` · `t4b-accepted` · `t4c-verified` · `t4d-checks` |
| T5 | D08–D09 | ORG Demo의 버킷·필터·저장소 이동, repo Demo의 KPI 카드. `Demo` 배지 상시 노출 | `t5-dashboards.mov` |
| T6 | D10 | 출처가 있는 장애 문구, CODEOWNERS의 선언된 담당, 대시보드 `CAN ANSWER`, `MAX_ATTEMPTS = 3` | `t6-investigate.mov` (+ `t5-dashboards.mov` 한 컷) |

#### 촬영·편집에서 확정된 것 (2026-09-17)

**측정의 기준 타임라인.** `screencapture -v`가 만드는 raw는 VFR이라 `ffmpeg -ss <t> -i raw.mov`(fast seek)로 뽑은
프레임이 렌더러 타임라인(`fps=30,trim=…`)과 **최대 2–3초 어긋남.** 테이크마다 CFR 프록시를 먼저 만들고
시각·좌표는 프록시에서만 잼. `events/<take>.json`의 `t`는 프록시보다 0.8–1.0초 빠름(캡처 시작 지연).

**박스 규칙.** 편집 전에 박스만 얹은 시트(`out/<take>-boxes.jpg`)를 먼저 확인하고 확정한 뒤 편집함.
행 단위 스포트라이트는 컨테이너 테두리 기준 좌우 대칭, 세로는 대상 잉크와 위아래 요소의 중간점,
배지처럼 작은 대상은 중심 기준 좌우 같은 거리로 잡음. 좌표는 눈대중하지 않고 픽셀로 실측함.

**화면에 담지 않는 것.** 사용하지 않는 계정 행, Copilot app 사이드바의 무관한 프로젝트명과 계정명,
OAuth code/state, 터미널의 토큰·환경 파일. `Demo` 배지는 계속 노출하되 하이라이트하지 않음.

**촬영본의 한계로 남은 것.**

| 항목 | 내용 | 해소 방법 |
| --- | --- | --- |
| T3 정지 프레임 | Q2가 Hold 상태로 화면에 남아 있는 실촬 구간이 1초뿐이라, 힌트·발췌 구간이 정지 프레임이 됨 | 힌트를 읽으며 실제로 스크롤하는 꼬리 재촬영 |
| Q2의 틀린 보기 | 정정 클릭이 스크롤보다 먼저 일어나 **틀린 보기가 화면에 찍히지 않음.** D05는 Hold 배지만 짚음 | 재촬영 |
| `Declared owner` | repo 대시보드 데모 픽스처가 비어 있어 담당자를 CODEOWNERS 원문으로 우회함 | 이슈 #35 |
| ORG 롤업·위험도 정렬 | 미구현이라 D08 대본에서 해당 표현을 뺌 | 이슈 #34 |
| 조사 동선 | zone 행에서 영수증·코드로 가는 클릭 동선이 없음 | 이슈 #36 |

- v4의 `.work/demo-v4/rec/out/T1–T7.mp4`와 `rec/edit/T*.json`은 화면 강조·읽기 속도의 참고입니다.
  새 컷의 기준은 이 DEMO 절이며, v4의 코드 수정·새 커밋·merge·0→1 장면을 그대로 재사용하지 않습니다.
- 원본은 1920×1080으로 녹화하고 최종본은 30fps로 맞춥니다. 첫 분할 화면 이후에는 한 번에 하나의
  관심 영역을 보여 줍니다. 줌·클릭 링·행 강조는 실제 요소 위치를 따라갑니다.
- 생성·채점·검증의 대기는 성공이 확인된 실제 장면 사이의 컷으로 줄입니다. 이를 실시간 응답이나
  성능 수치로 주장하지 않습니다. 가짜 Approved·Accepted·success·Merge 상태는 합성하지 않습니다.
- 자막은 위 VO의 문장만 사용하고 상태 배지·코드 근거를 가리지 않습니다. 계정 비밀, 제작 메모,
  이전 PR 번호, `TBC`, 개발·브랜치 이력, 안내 오버레이는 영상에 노출하지 않습니다.

영상이 끝나면 **바로 07로 넘깁니다.**

### 07 · 우리 PR에 먼저 걸었고, 우리가 멈췄습니다 · `We Went First` — 4단계

**English** — We went first, and it stopped us · `We Went First`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 3:11–3:28 · 17s | "이 gate를 우리 PR에 먼저 걸었습니다. `→1` 26건이 발동했고 15건이 멈췄습니다. `→2` 판단이 들어간 파일마다 저희가 멈췄습니다. `→3` 인증 코드를 지우는 변경은 임계값을 못 넘습니다. `→4` 대기로 merge된 12건도 보고합니다." |
| English | 3:03–3:19 · 16s | "We put this gate on our own pull requests first. `→1` It fired on twenty-six, and fifteen of them stopped. `→2` Every file carrying judgment stopped us. `→3` Deleting authentication code stays under the threshold. `→4` And twelve merged while pending. We report that too." |

**시사점** — 가설 → 실험 → 측정 → 학습이 한 장에 있습니다. **못 잡는 것과 숨기고 싶은 수치까지** 같이 말하는 것이 이 장의 힘입니다.
<br>**Why it matters** — Hypothesis → experiment → measurement → learning on one page. The power of this slide is that it also states **what the gate misses and the number we would rather hide.**

### 08 · gate를 여는 주체는 서버 하나, 검증하는 주체는 Actions · `Trust by Design` — 6단계

**English** — One server opens the gate; Actions verifies it · `Trust by Design`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 3:28–3:47 · 19s | "PR이 지나가는 경로입니다. `→1` Actions가 위험도를 다시 계산합니다. `→2` 미달이면 확인 없이 merge됩니다. `→3` 넘으면 작성자가 코드로 답합니다. `→4` 영수증은 commit에 묶입니다. `→5` 대조해서 일치할 때만 열립니다. `→6` 서버 말만으로는 열리지 않습니다." |
| English | 3:19–3:37 · 18s | "This is the path one pull request travels. `→1` Actions recomputes the risk itself. `→2` Below the threshold it merges with no check. `→3` Above it, the author answers from the code. `→4` The receipt binds to the commit. `→5` Only a match opens the gate. `→6` The server's word alone is not enough." |

**시사점** — "봇이 자기가 통과시킨 것 아니냐"에 대한 답입니다. **서버가 통과라고 말해도 열리지 않습니다.**
<br>**Why it matters** — This is the answer to "didn't the bot just pass itself?" **The gate does not open even when the server says pass.**

### 09 · 도입은 세 갈래, 기반은 장기 시크릿 0개 · `Ready to Ship` — 4단계

**English** — Three tracks, zero long-lived secrets · `Ready to Ship`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 3:47–4:00 · 13s | "도입은 세 갈래입니다. `→1` 운영자가 서버를 한 번 올리고, `→2` 저장소마다 파일 셋과 required check. `→3` 작성자는 두 문항에 답합니다. `→4` 장기 시크릿은 없습니다." |
| English | 3:37–3:50 · 13s | "Adoption runs on three tracks. `→1` An operator stands up the server once, `→2` three files per repository, required check last. `→3` The author answers two questions. `→4` There are no long-lived secrets." |

**시사점** — 남의 저장소에 걸 수 있느냐에 대한 답입니다. **required check를 마지막에 거는 순서**가 그림에 있습니다.
<br>**Why it matters** — The answer to "can I run this on my repo?" The diagram shows **the required check going on last.**

### 10 · AI는 책임질 수 없습니다 · `What Is at Stake` — 3단계

**English** — AI cannot take responsibility · `What Is at Stake`

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 4:00–4:15 · 15s | "AI는 책임질 수 없지만, 사람은 책임질 수 있습니다. `→1` 그러려면 이해가 기록으로 남아야 합니다. `→2` 속도를 줄이는 게 아니라, 감당할 속도를 올리는 것입니다. `→3` 지금 이 저장소에서 동작합니다." |
| English | 3:50–4:03 · 13s | "AI cannot take responsibility, but a person can. `→1` For that, understanding has to be on record. `→2` Not slowing down, but raising the speed we can carry. `→3` It is running on this repository right now." |

**시사점** — 01에서 말한 전제로 **되돌아와 해소합니다.** 속도를 줄이는 것이 아니라 조직이 감당할 수 있는 속도를 올리는 것입니다.
<br>**Why it matters** — **Return to the premise stated in 01 and resolve it.** This is not about slowing down; it is about raising the speed an organization can carry.

### TAG · 마무리 카드

**English** — Closing card

| | 시간 | 스크립트 |
| --- | --- | --- |
| 한국어 | 4:15–4:19 · 4s | "AI가 만든 코드, 사람이 책임지는 결정." |
| English | 4:03–4:06 · 3s | "AI-generated code. Human-owned decisions." |

**시사점** — 화면에 함께 뜨는 `Copilot can fly. The pilot in command is still responsible.`는 **읽지 않습니다.** Q&A 동안 이 화면이 남습니다.
<br>**Why it matters** — The second line on screen, `Copilot can fly. The pilot in command is still responsible.`, is **never read aloud.** This screen stays up through Q&A.

## 4. 리허설 지침

- 손이 꼬이면 **`↓`** 를 누르십시오. 빌드를 건너뛰고 슬라이드가 완성된 상태로 넘어갑니다.
- 데모 88초에는 **내장 English VO만 재생**하고 발표자는 중복 해설하지 않습니다.
- 촬영 전에 실제 테스트·린트 성공과 다른 리뷰어의 Approve를 확보합니다. 작성자의 자기 승인을
  리뷰 승인처럼 사용하지 않습니다.
- `last-human/human-verified`를 기대 발급 App과 함께 실제 필수 상태로 설정해야 D03의 merge 차단을
  촬영할 수 있습니다. 2026-09-17 준비 조회에서는 필수 상태·리뷰 규칙이 설정돼 있지 않았습니다.
  이는 촬영 전 운영자가 확인·설정할 전제이지, 이번 문서 커밋으로 변경한 설정이 아닙니다.
- 질문 2개, 필요한 사용자 OAuth 화면, 같은 snapshot, ORG Demo → repo Demo의 기록·선언된 담당과
  현재 CODEOWNERS 문의처를 리허설에서 확인합니다. 설정·로그인·자료가 준비되지 않으면 성공 화면을 만들어 대신하지 않습니다.
- 발표 전 **`M`으로 타이머와 진행바를, `H`로 안내를** 끄십시오.
- 시간이 밀리면 자르는 순서 — **03 마지막 문장 → 06의 Step 설명 → 09의 기반 밴드 설명.**
- 부록은 `A` 키입니다 — A1 데이터 흐름 · A2 고객과 가치 · A3 제약과 다음 수. 시간 예산 밖입니다.

## 5. 수치와 근거 · Numbers and evidence

발표 전 재현 확인 — 세 줄 모두 `[OK ]`가 나와야 합니다.

```bash
PYTHONIOENCODING=utf-8 python docs/demo/presentation/verify_claims.py
```

| 주장 · Claim | 실측 · Measured | 근거 · Source |
| --- | --- | --- |
| 위험 fixture는 gate가 켜집니다<br>The risk fixture trips the gate | **55 / 40 → 발동**은 기존 fixture 값. 실제 촬영 PR은 변경량을 포함한 현재 snapshot의 점수·사유를 사용하며 이 값을 고정 자막으로 쓰지 않음<br>**55 / 40 → fires** is the existing fixture result, not a guaranteed score for the recorded PR | `.lasthuman.yml` + `risk.score()` · 실제 촬영은 PR 카드의 현재 평가<br>Use the current PR evaluation for footage |
| 저위험 문서 PR은 지나갑니다<br>A low-risk docs PR passes through | **0 / 40 → 미발동**<br>**0 / 40 → does not fire** | 같음<br>same |
| 인증 경로 −2줄은 못 잡습니다<br>A −2-line auth change is missed | **30 / 40 → 미발동**<br>**30 / 40 → does not fire** | 같음. 07의 근거<br>same. The evidence behind 07 |
| 임계값 40 · 중요 경로 auth 30 / db 20 / migrations 30<br>Threshold 40 · critical paths auth 30 / db 20 / migrations 30 | 설정 그대로<br>exactly as configured | `.lasthuman.yml` |
| 판단 3파일은 각각 +30<br>The three judgment files are +30 each | `risk.py` · `interview.py` · `attest.py` | `.lasthuman.yml` |
| 검증은 5단계<br>Verification is five steps | 워크플로에 5개 step<br>5 steps in the workflow | `.github/workflows/lasthuman-app.yml` |
| 필수 status 식별자<br>Required status identifier | `last-human/human-verified` | `src/lasthuman/server/config.py:21` |
| Copilot의 작업이 데모 PR로 이어집니다<br>The Copilot task produces the demo PR | D01의 실제 작업과 생성된 PR 링크를 D02로 연결. 공동 작성 trailer만으로 실제 작성 과정을 증명하지 않음<br>Record the task and follow its resulting PR; a coauthor trailer alone is not proof of the workflow | DEMO D01–D02 |
| 규모 — commit 109 · PR 36 · test 519 · workflow 4<br>Scale — 109 commits · 36 PRs · 519 tests · 4 workflows | `git rev-list --count HEAD` 등<br>`git rev-list --count HEAD` and friends | 2026-09-16 기준<br>as of 2026-09-16 |

### gate 이력 — 07의 근거 · the evidence behind 07

**이력은 이 저장소가 아니라 개발 저장소에 있습니다.** `microsoft-2026-hackathon/the-last-human`은
2026-09-15에 만들어진 이관 대상이라 PR이 7건뿐입니다. 실제 이력은 `hunhoon21/the-last-human`에
당시 이름인 `comprehension-gate`로 남아 있습니다. 근거는 [`docs/migration/import.json`](../migration/import.json)입니다.

| 값 · Value | 수 · Count |
| --- | --- |
| gate가 발동한 PR<br>PRs where the gate fired | **26** (전체 29건 중)<br>**26** (of 29 total) |
| 설명 대기로 한 번 이상 멈춘 PR<br>PRs held at least once awaiting explanation | **15** |
| 통과 기록으로 끝난 PR<br>PRs that ended with a pass record | **14** |
| 멈췄다가 통과한 PR<br>PRs held, then passed | **3** (#22 · #12 · #5) |
| 대기 상태로 merge된 PR<br>PRs merged while still pending | **12** |

**"26건을 다 막아 세웠다"고 말하지 않습니다.** 12건은 gate가 대기 중인 채로 merge됐고,
required check를 계속 걸어 두지 않았기 때문입니다. 물으면 그대로 답합니다.
*We do not claim we stopped all 26 — twelve merged while the gate was still pending.*

### 외부 출처 — 발표 전 링크 재확인 · re-check before presenting

| 화면 · Screen | 출처 · Source | 슬라이드 |
| --- | --- | --- |
| `backdata_1` — human control · lose permission to operate · observability | Satya Nadella · 사내 공지<br>Satya Nadella · internal announcement | 01 |
| `backdata_2` — 예측은 speedup, 관측은 +19% slowdown<br>`backdata_2` — forecasts said speedup, measurement said +19% slowdown | METR RCT · metr.org · CC-BY | 03 |
| `backdata_4` — 45.4% · 28.3% vs 80.0% · 3.5× · PR-MCI verification mechanisms | Gong, Pinna, Bian, Zhang · arXiv 2026-01-26 · agentic PR 23,247건<br>Gong, Pinna, Bian, Zhang · arXiv 2026-01-26 · 23,247 agentic PRs | 04 |
| `backdata_3` — AI cannot take responsibility | Godot 기여 정책 2026-06<br>Godot contribution policy 2026-06 | 05 |
| 복구 중 재시도 트래픽 증폭<br>Retry amplification during recovery | [GitHub: The August 17 outage, and the work ahead](https://github.blog/news-insights/company-news/the-august-17-outage-and-the-work-ahead/) · 2026-08-20. 주원인은 용량 문제이며 이 샘플과 동일한 장애라는 뜻이 아님<br>The primary cause was capacity; the sample is not a recreation of the incident | DEMO D10 |

`backdata_1`은 사내 자료라 **공개 저장소에 커밋하지 않았습니다.** 팀원에게는 파일을 따로 전달하십시오.
*`backdata_1` is internal material and is not committed to the public repository.*

## 6. Q&A 대비 · Q&A preparation

| 질문 · Question | 답 · Answer |
| --- | --- |
| "gate도 AI한테 물어보면 되지 않습니까?"<br>"Couldn't you just ask an AI to pass the gate?" | 코드와 질문을 함께 주면 상당수 맞힙니다 — 사실입니다. 다만 **어떤 코드를 줄지 먼저 알아야** 합니다. 질문은 특정 hunk를 가리키고 근거로 코드 위치를 요구합니다. 목표는 부정행위 차단이 아니라 **승인 전 최소 한 번은 코드를 보게 만드는 것**이고, 지금은 그 한 번조차 없습니다.<br>Give a model the code and the question and it gets many of them right — that is true. But **you have to know which code to give it first.** The questions point at a specific hunk and demand a code location as evidence. The goal is not to stop cheating; it is to make someone look at the code **at least once** before approving. Today there is not even that once. |
| "모든 PR을 막으면 개발이 마비되지 않습니까?"<br>"Won't blocking every PR paralyze development?" | 전수 적용하지 않습니다. 임계값 미달은 `Not required`로 지나갑니다. 이번 88초는 확인 대상 변경에 집중하며 저위험 문서 PR은 별도 근거로 제시합니다.<br>Below-threshold changes pass as `Not required`. This 88-second sequence focuses on a gated change; a low-risk PR is separate evidence. |
| "모델이 죽으면 어떻게 됩니까?"<br>"What happens if the model goes down?" | 처리 오류를 자동 통과로 바꾸지 않습니다. 유효한 확인이 없으면 필수 gate는 성공하지 않으며, 정상 복구와 재시도가 필요합니다. 인프라 오류는 작성자의 감점 기록이 아닙니다.<br>An infrastructure error is not converted into a pass. The required gate does not succeed without valid confirmation; recovery and retry are needed. Infrastructure failure is not a personal penalty. |
| "보안은 어떻습니까?"<br>"What about security?" | App private key · client secret · 모델 자격은 **서버에만** 둡니다. 경계를 넘는 것은 단기 OIDC와 metadata뿐입니다. 다만 **"비밀이 전혀 없다"고는 말하지 않습니다** — Actions도 단기 GitHub 자격은 갖습니다.<br>The App private key, client secret, and model credentials stay **on the server only.** All that crosses the boundary is short-lived OIDC and metadata. But **we never claim "no secrets at all"** — Actions holds short-lived GitHub credentials too. |
| "서버 하나로 여러 저장소가 됩니까?"<br>"Can one server cover many repositories?" | 기본 fixed 모드는 저장소 하나이며, first-event 모드는 등록된 저장소를 분리해 처리합니다. ORG Actual은 연결·인가된 범위이고 Demo는 별도 예제입니다. 조직의 모든 저장소를 자동 수집한다고 말하지 않습니다.<br>Fixed mode serves one repository. First-event mode isolates registered repositories. Organization Actual shows connected, authorized records; Demo is separate sample data, not a complete organization inventory. |
| "코드가 그대로인데 통과하면 문제가 해결된 것입니까?"<br>"Does passing without a code change mean the problem is fixed?" | 아닙니다. 이번에는 같은 변경의 설명을 보완해 merge 가능 상태까지만 보여 줍니다. 실제 merge는 하지 않으며, 버그 수정·코드 안전 보증과 이해 확인은 구분합니다.<br>No. The author corrects the explanation of the same change. We show merge readiness, not an actual merge or a bug fix. |
| "누구에게 물어볼지는 TLH가 검증한 사람을 추천합니까?"<br>"Does the dashboard recommend a verified expert?" | 아닙니다. 확인 작성자 수와 CODEOWNERS의 선언된 담당은 별개입니다. 담당을 문의 경로로 사용하며 비공개 면담 이력에서 전문가 명단을 만들지 않습니다.<br>No. Anonymous confirmation counts and declared CODEOWNERS contacts are separate. We use the declared contact, not a private interview-derived expert list. |
| "사내망에도 둘 수 있습니까?"<br>"Can it run on an internal network?" | webhook 수신자가 아니라 **relay** 구조라 가능합니다. self-hosted runner와 사내 접속 경로가 필요하고, 대가는 runner 기동 지연 20–40초입니다.<br>Yes, because it is a **relay** rather than a webhook receiver. It needs a self-hosted runner and an internal route, and it costs 20–40 seconds of runner start-up. |
| "Copilot과 경쟁하는 것입니까?"<br>"Are you competing with Copilot?" | 아닙니다. **Copilot이 연 PR을 조직이 받아들일 수 있게 만드는 층**입니다. 신뢰 층이 없으면 조직은 rubber-stamp하거나 금지하는데, 어느 쪽이든 Copilot의 생산성은 실현되지 않습니다.<br>No. This is **the layer that makes a Copilot-opened PR acceptable to an organization.** Without a trust layer an org either rubber-stamps or bans, and neither realizes Copilot's productivity. |
| "Global 진출은 가능합니까?"<br>"Would this work globally?" | GitHub PR 흐름 위에서만 동작해 지역·언어 종속이 없습니다. 정책은 `.lasthuman.yml` 한 파일이고, 모델은 조직이 고른 Azure 리전을 씁니다. 다만 구조 분석은 현재 **Python 중심**입니다.<br>It runs purely on the GitHub PR flow, so it has no regional or language dependency. Policy is one file, `.lasthuman.yml`, and the model runs in whichever Azure region the organization picks. Structural analysis, though, is currently **Python-centric.** |
| "기간 내에 얼마나 만들었습니까?"<br>"How much did you build in the time?" | commit 109건, PR 36건, test 519건, 동작 중인 workflow 4개입니다. 그리고 **그 PR들이 이 gate를 통과했습니다.**<br>109 commits, 36 PRs, 519 tests, and 4 live workflows. And **those PRs went through this gate.** |
| "개인 평가로 쓰이지 않겠습니까?"<br>"Won't this be used to evaluate people?" | 개인 점수·순위·팀장 조회는 요청받아도 만들지 않습니다. 대시보드는 이름이 아니라 **수**를 셉니다. 만드는 순간 다른 제품이 됩니다.<br>We will not build individual scores, rankings, or a manager lookup, even if asked. The dashboard counts **numbers, never names.** The moment it does, it is a different product. |

## 7. 레드라인 점검 · Redline check

[AGENTS.md](../../AGENTS.md)의 금지 넷을 발표 자료가 넘지 않는지 확인합니다.
**심사 점수를 올리려고 이 넷을 넘지 않습니다.**

- [x] 개인 점수·등급·순위가 어느 화면에도 없습니다 — 집계는 구역별 **수**뿐입니다<br>No individual score, grade, or ranking on any screen — aggregates are **counts** per zone only
- [x] 사람 이름이 붙은 집계가 없습니다 — CODEOWNERS 담당자는 "선언된 담당"이지 성과 지표가 아닙니다<br>No name-attributed aggregate — a CODEOWNERS owner is a "declared owner," not a performance metric
- [x] 감점·보류 이력이 화면에 남지 않습니다 — 데모의 `Hold`는 진행 중 상태입니다<br>No penalty or hold history on screen — the `Hold` in the demo is an in-progress state
- [x] 팀장 조회 화면이 없습니다 — A1의 `조직 → Server`는 같은 대시보드를 읽을 뿐입니다<br>No manager lookup screen — `Org → Server` in A1 only reads the same dashboard
- [x] AI 탐지로 발동한다는 표현이 없습니다 — 06의 Step 1은 위험도입니다<br>Nothing claims the gate fires on AI detection — Step 1 in 06 is risk

---

덱 조작법은 [presentation/README.md](presentation/README.md), 영상 파이프라인은
[presentation/video/README.md](presentation/video/README.md)입니다. 새 데모의 컷·대사는 이 문서의
DEMO 절을 따르고, [storyboard-v4.md](storyboard-v4.md)는 이전 녹화·편집의 참고로만 사용합니다.
도입 절차는 [onboarding.md](../runbooks/onboarding.md)입니다.
