# 발표 스토리보드 v6 — The Last Human (한국어 · English)

**Hack for Agentic Coding 제출본.** 두 언어를 한 문서에 둡니다. 같은 덱, 같은 빌드 단계,
같은 논지이고 **문장과 시간만** 언어별로 다릅니다. v5(한국어)와 v5-en(영어)을 대체합니다.

| | 한국어 | English |
| --- | --- | --- |
| 덱 | [`presentation/index.html`](presentation/index.html) | [`presentation/index.en.html`](presentation/index.en.html) |
| 나레이션 | `presentation/video/narration.json` | `presentation/video/narration.en.json` |
| 영상 | `the-last-human-3m30.mp4` · **4:17.9** · 자막 49장 | `the-last-human-en.mp4` · **4:05.7** · 자막 50장 |
| 목소리 | `ko-KR-HyunsuMultilingualNeural` | `en-US-AndrewMultilingualNeural` |
| 데모 88초 | 앞 103s · 뒤 64s | 앞 95s · 뒤 60s |

**이 문서의 스크립트와 시간은 `narration*.json`과 `manifest*.json`에서 생성했습니다.**
사람이 옮겨 적지 않으므로 영상과 어긋날 수 없습니다. 문장을 고치면 나레이션을 고치고
영상을 다시 만드십시오 — 반대 방향으로는 고치지 마십시오.

## 1. 한 줄 논지

> Copilot이 여는 PR은 급증했지만 **그것을 검증할 방법은 따라가지 못했습니다.** agent는 이미
> SDLC 전 단계에 들어와 있고 모든 단계가 기록되는데, merge의 신뢰만 기록이 없습니다.
> The Last Human이 그 한 층을 만들어 Agentic Coding을 **Trusted Agentic Coding**으로 옮깁니다.

> Copilot opens pull requests faster than teams can verify them. Agents are already in every
> stage of the SDLC and every stage leaves a record — **only trust at merge has none.**
> The Last Human builds that one layer, taking agentic coding to **trusted agentic coding.**

## 2. 이 문서로 만든 영상

3장의 스크립트를 그대로 Azure Speech로 합성하고, 덱을 빌드 단계마다 캡처해 이어 붙인 것입니다.

```bash
cd docs/demo/presentation/video
python make_tts.py      # 나레이션 합성 + 길이 측정   (영어는 --lang en)
python make_shots.py    # 덱을 빌드 단계마다 캡처
python build_video.py   # 조립 + 자막 굽기
```

- 아래 표의 시간 합계(4:15 · 4:03)와 영상 길이(4:17.9 · 4:05.7)가 3초 안쪽으로
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

### DEMO · 88초 · 말하지 않습니다

| | 시간 | |
| --- | --- | --- |
| 한국어 | 1:43–3:11 · 88s | **배점이 가장 큰 구간입니다.** 해설을 얹으면 "동작 결과물"이 아니라 "설명"으로 읽힙니다 |
| English | 1:35–3:03 · 88s | **The highest-scoring stretch.** Narrating over it turns working software back into a description |

영상이 끝나면 **바로 07로 넘깁니다.** 88초 동안 다음 문장을 준비하십시오.
데모 영상 자체는 English VO이며, 내부 구성은 [storyboard-v4.md](storyboard-v4.md)가 단일 출처입니다.

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

### 마무리 카드 · 무음

한국어 4:15 · English 4:03 지점에서 `AI-generated code. Human-owned decisions.`가 뜹니다.
**읽지 않습니다.** Q&A 동안 이 화면이 남습니다.

## 4. 리허설 지침

- 손이 꼬이면 **`↓`** 를 누르십시오. 빌드를 건너뛰고 슬라이드가 완성된 상태로 넘어갑니다.
- 데모 88초 동안 **발표자는 말하지 않습니다.**
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
| 데모 PR은 gate가 켜집니다<br>The demo PR trips the gate | **55 / 40 → 발동** (중요 경로 +30 · `except\s` +10 · `async`/`await` +15)<br>**55 / 40 → fires** (critical path +30 · `except\s` +10 · `async`/`await` +15) | `.lasthuman.yml` + `risk.score()` |
| 저위험 문서 PR은 지나갑니다<br>A low-risk docs PR passes through | **0 / 40 → 미발동**<br>**0 / 40 → does not fire** | 같음<br>same |
| 인증 경로 −2줄은 못 잡습니다<br>A −2-line auth change is missed | **30 / 40 → 미발동**<br>**30 / 40 → does not fire** | 같음. 07의 근거<br>same. The evidence behind 07 |
| 임계값 40 · 중요 경로 auth 30 / db 20 / migrations 30<br>Threshold 40 · critical paths auth 30 / db 20 / migrations 30 | 설정 그대로<br>exactly as configured | `.lasthuman.yml` |
| 판단 3파일은 각각 +30<br>The three judgment files are +30 each | `risk.py` · `interview.py` · `attest.py` | `.lasthuman.yml` |
| 검증은 5단계<br>Verification is five steps | 워크플로에 5개 step<br>5 steps in the workflow | `.github/workflows/lasthuman-app.yml` |
| 필수 status 식별자<br>Required status identifier | `last-human/human-verified` | `src/lasthuman/server/config.py:21` |
| 데모 PR을 Copilot이 썼습니다<br>Copilot wrote the demo PR | `Co-authored-by: Copilot` | `docs/runbooks/first-demo-macos.md` |
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

`backdata_1`은 사내 자료라 **공개 저장소에 커밋하지 않았습니다.** 팀원에게는 파일을 따로 전달하십시오.
*`backdata_1` is internal material and is not committed to the public repository.*

## 6. Q&A 대비 · Q&A preparation

| 질문 · Question | 답 · Answer |
| --- | --- |
| "gate도 AI한테 물어보면 되지 않습니까?"<br>"Couldn't you just ask an AI to pass the gate?" | 코드와 질문을 함께 주면 상당수 맞힙니다 — 사실입니다. 다만 **어떤 코드를 줄지 먼저 알아야** 합니다. 질문은 특정 hunk를 가리키고 근거로 코드 위치를 요구합니다. 목표는 부정행위 차단이 아니라 **승인 전 최소 한 번은 코드를 보게 만드는 것**이고, 지금은 그 한 번조차 없습니다.<br>Give a model the code and the question and it gets many of them right — that is true. But **you have to know which code to give it first.** The questions point at a specific hunk and demand a code location as evidence. The goal is not to stop cheating; it is to make someone look at the code **at least once** before approving. Today there is not even that once. |
| "모든 PR을 막으면 개발이 마비되지 않습니까?"<br>"Won't blocking every PR paralyze development?" | 전수 적용하지 않습니다. 임계값 미달은 `Not required`로 지나갑니다. 데모 안에서 문서 PR이 그대로 merge되는 장면으로 증명됩니다.<br>We do not apply it to everything. Anything below the threshold passes as `Not required`. The demo proves it — a docs PR merges untouched. |
| "모델이 죽으면 어떻게 됩니까?"<br>"What happens if the model goes down?" | 사람을 막지 않습니다. **실패 방향에 일관성이 있습니다** — 사람이 답을 빠뜨리면 막고, 인프라가 죽으면 통과시킵니다. 미통과는 failure가 아니라 pending입니다.<br>It does not block people. **Failure has a consistent direction** — if a person skips the answer it blocks; if the infrastructure dies it lets through. Not-yet-passed is pending, not failure. |
| "보안은 어떻습니까?"<br>"What about security?" | App private key · client secret · 모델 자격은 **서버에만** 둡니다. 경계를 넘는 것은 단기 OIDC와 metadata뿐입니다. 다만 **"비밀이 전혀 없다"고는 말하지 않습니다** — Actions도 단기 GitHub 자격은 갖습니다.<br>The App private key, client secret, and model credentials stay **on the server only.** All that crosses the boundary is short-lived OIDC and metadata. But **we never claim "no secrets at all"** — Actions holds short-lived GitHub credentials too. |
| "서버 하나로 여러 저장소가 됩니까?"<br>"Can one server cover many repositories?" | 지금은 **서버 1대 = 저장소 1개** 바인딩입니다. 패키징에서 정리 중이고 숨기지 않습니다.<br>Today it is **one server to one repository.** We are addressing that in packaging and we are not hiding it. |
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
[presentation/video/README.md](presentation/video/README.md), 데모 영상 내부는
[storyboard-v4.md](storyboard-v4.md), 도입 절차는 [onboarding.md](../runbooks/onboarding.md)입니다.
