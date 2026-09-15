# 2분 스토리라인 — The Last Human (v4)

**모든 코드에는 책임질 사람이 필요함. 코드를 점점 에이전트가 쓰는 지금, 사람이 정말 이해했는지를 확인할 방법이 없었음.** The Last Human은 위험한 변경이 머지되기 전에 작성자가 코드에 대고 설명하게 하고, 그 확인을 머지 조건과 모듈별 커버리지로 이어 줌. 금지가 아니라 확인임.

## 1. v2 대비 변경

### 시간 배분

| 구간 | v2 | v4 | 이유 |
| --- | --- | --- | --- |
| 도입 | 00:00–00:24 (24초) | 00:00–00:25 (25초) | 전제 → 긴장 → 질문 → 답의 순서. 제품이 질문의 답 자리에 놓임 |
| 시연 본편 | 00:24–01:26 (62초) | 00:25–01:34 (69초) | 보류 → 옆 파일 발견 → 코드 수정 → 새 커밋 재확인 → 머지. 발견 직후 GitHub 장애 한 컷(8초) |
| 반증 | 01:26–01:31 (5초) | 01:34–01:39 (5초) | 한 문장 |
| 대시보드 | 01:31–01:46 (15초) | 01:39–01:53 (14초) | 저장소·기간 선택 장면 제외. 세 문장 + 0.5초 침묵 |
| 런타임 아키텍처 | 01:46–01:57 (11초) | — | 슬라이드로 이동 (7장) |
| 마무리 | 01:57–02:00 (3초) | 01:53–02:00 (7초) | "Pilot in command" 5초 + 무음 태그라인 카드 2초 |

### 이야기

| 지점 | v2 | v4 | 왜 바꿨나 |
| --- | --- | --- | --- |
| 오프닝 | 코드 "홍수" 연출 → Godot 인용 → 타이틀 | 전제("모든 코드에는 책임질 사람이 필요함") → Godot의 금지 → 질문("사람이 정말 이해했는지 어떻게 확인하나?") → 답(The Last Human) | 통계·인용을 나열하면 주제 전환처럼 튐. 전제에 대한 한 반응(금지)을 보여 준 뒤 그 대안을 묻는 질문의 답으로 제품을 놓아야 "왜 또 게이트냐"가 저절로 풀림 |
| 본편 | 같은 리비전에서 설명 보완 → 재제출 → 통과. 코드 수정 없음 (새 커밋 경로는 "추가 후보") | Q2 보류 → 옆 파일에서 결함 발견 → 코드 수정 → 새 커밋에 게이트가 다시 물음 → 통과 → 머지 | 새 커밋이 인증을 무효로 만드는 장면이 "인증은 SHA에 묶인다"를 말 없이 증명함. v2가 이 원칙을 문장으로 설명하던 시간이 화면으로 바뀜 |
| 질문의 모양 | 질문을 하나씩 나열하거나 코드 퀴즈처럼 보이지 않게 | 두 문항이 동시에 뜨고 한 번에 제출되는 실제 제품 흐름. 화면 라벨(Accepted / Hold)과 같은 단어만 말함 | v2의 우려를 제품 구조 안에서 해결함. Q1은 6초 안에 지나가고 Q2만 머묾 — 보기가 전부 실제 파일인 화면이 "퀴즈가 아니라 저장소를 열어야 답하는 확인"임을 보여 줌 |
| GitHub 2026-08-17 장애 | 없음 | 발견 직후 한 컷. "같은 종류의 루프" · "게이트는 버그를 찾은 게 아니라 찾아야 할 사람에게 물었다"까지만 | 결함이 실재함을 보여 주되, The Last Human이 그날을 막았거나 줄였을 것이라는 주장은 하지 않음. 사후 분석은 원인을 AI에 귀속하지 않음 |
| 책임의 표현 | "Verified human(가칭)" — 확인을 완료한 작성자의 역할 명칭 | **Human-verified** — 변경의 상태. 사람은 "on record"로 남음 | 검증되는 것은 변경이지 사람이 아님. 카드 · 대시보드 KPI · 계약 여섯째 줄이 같은 단어를 씀. 사람에게 등급을 주는 인상을 피함 |
| 프레임 | 확인 절차 | 책임 프레임 — 이해가 책임의 전제 | 오프닝의 다섯 단어(responsible · understood · from the code · ban · check)가 본편·대시보드·결말에서 돌아옴 |
| 런타임 아키텍처 | 영상 안 11초 | 슬라이드로 | 본편이 늘어난 만큼 설명 장면을 뺌. 실현 가능성은 실제 PR·체크·머지 화면이 이미 증명함 |

## 2. 구성

**전제 → 긴장(Godot) → 질문 → 답(The Last Human) → 본편(Q1 Accepted · Q2 Hold · 옆 파일 발견 · 수정 · 재확인 · 머지) → 반증(저위험은 그냥 지나감) → 대시보드(모듈별로 답할 수 있는 사람 수) → 기장은 여전히 사람.**

오프닝의 다섯 단어 — responsible · understood · from the code · ban · check — 가 본편과 대시보드, 결말에서 전부 돌아옴. 이해가 책임의 전제임.

본편은 두 문항이 동시에 뜨고 한 번에 제출되는 실제 제품 흐름을 따름. 질문을 퀴즈처럼 나열하지 않고, 화면의 라벨(Accepted / Hold / Human Verified)과 같은 단어만 말함.

## 3. 제품 소개와 구성요소

제품은 00:15–00:25에 질문의 답으로 소개함: **"before a risky change merges, its author explains it — from the code. A check, not a ban."** 구성요소는 한꺼번에 나열하지 않고, 쓰이는 장면에서 화면에 보이는 이름 그대로 등장시킴. 내레이션은 일반어(the gate · the bot · the dashboard)를 씀.

| 요소 | 화면에 보이는 이름 | 역할 | 등장 |
| --- | --- | --- | --- |
| **TLH Gate** | status check `comprehension-gate` (필수 상태 검사) | 작성자의 유효한 확인 없이는 머지할 수 없게 하는 관문. 위험도가 임계값에 미치지 않는 변경은 `Comprehension check not required`로 지나감 | 00:25 pending · 01:24 verified · 01:34 not required |
| **TLH Bot** | GitHub App "The Last Human" · 카드 "Awaiting author explanation" → "Human Verified" | 위험 근거를 요약한 카드를 올리고, 웹 면담에서 질문을 만들고 답을 채점하며, 결과를 현재 커밋에 묶인 기록으로 남기고 PR 상태를 갱신함 | 00:41 카드 · 00:51–01:16 면담 · 01:24 Human Verified |
| **TLH Dashboard** | "Comprehension dashboard" · 열 "Can answer" · KPI "Human-verified before merge" | CODEOWNERS 구역별로 게이트가 발동한 PR 중 머지 전에 확인된 비율, 답할 수 있는 사람 수, 예외, 근거 PR을 보여 줌. 이름 없이 수만 | 01:39 |
| **Human-verified** | 카드 "Human Verified" + SHA · 대시보드 KPI | 현재 커밋의 변경을 작성자가 코드에 대고 설명해 확인 근거를 남긴 **변경의 상태**. 새 커밋이 오면 무효가 되고 게이트가 다시 물음. 사람에게 영구적인 자격이나 등급을 주지 않음 | 01:24 · 01:39 |

## 4. 장면별 스토리라인과 테이크

표기: **[근거]** 공식 자료 · **[연출]** 그래픽 · **[TARGET]** 실제 제품 동작 촬영. 테이크(T1–T7)는 화면 상태가 같은 구간으로 묶은 촬영 단위이며, 편집에서 컷 길이에 맞춤.

| # | 시간 | 테이크 | 메시지 | 화면 · 행동 · 증거 | English VO |
| --- | --- | --- | --- | --- | --- |
| 1 | 00:00–00:07 | — | 전제 — 모든 코드에는 책임질 사람이 필요함 [연출] | 에이전트가 올린 PR 제목들이 흐르다 머지 버튼 하나에 멈춤. 가짜 카운터 없음 | More of our code is written by agents. Every piece of code still needs someone responsible for it. |
| 2 | 00:07–00:15 | — | 긴장 — 가장 세게 받아들인 곳은 금지로 갔음 [근거] | Godot 기여 정책 원문 카드 + 출처·날짜. 자막: "In a controlled study, developers who built with AI understood 17 points less of what they had just built — Anthropic 2026, n=52, mostly junior" | Godot, a major open-source engine, answered with a ban: "AI cannot take responsibility." |
| 3 | 00:15–00:25 | — | 질문 → 답 [연출] | 어두워짐 → 질문 한 줄 → 실루엣 → 제목 | So how do you check that a person actually understood it? The Last Human: before a risky change merges, its author explains it — from the code. A check, not a ban. |
| 4 | 00:25–00:35 | **T1** | 위험한 변경의 실물 [TARGET] | PR #22 "fix(auth): make token refresh resilient to transient IdP failures". 상단 → 머지 박스: 초록 체크 4 · `The Last Human` 진행 중 · `comprehension-gate` pending **Required** · Merge 버튼 비활성. 강조: 제목의 "token refresh" 밑줄, `comprehension-gate` 줄 스포트라이트 "touches authentication — the gate is on" | This example changes how the service refreshes login tokens. An agent wrote it; it touches authentication, so the gate is on. |
| 5 | 00:35–00:41 | **T1** | 다섯 줄은 코드를, 여섯 번째 줄은 비어 있음 [연출→TARGET] | 초록 체크 4개 순차 링 → 우측 오버레이 "What the green checks guarantee": It builds · Tests pass · Style rules hold · A person approved · No conflicts ✓, 여섯째 **A person understood this — human-verified** 빈 칸 → 잠긴 Merge 버튼 스포트라이트. 자막 "94% say AI code looks better at review — New Relic 2026" | Every check is green. None says a person understood it. The merge waits. |
| 6 | 00:41–00:51 | **T2** | 봇이 무엇을 묻나 [TARGET] | 봇 카드 "The Last Human · Awaiting author explanation" · Stages → **Check this change** 클릭 → 면담 페이지: Why this change is gated(위험 근거) · 두 문항 동시 표시. Q1 "What happens if the identity provider returns a 503 error on the first refresh attempt in ensure_fresh()?" · Q2 "How does the retry logic in ensure_fresh() interact with the retry behavior of post_json() in sample-app/app/http_client.py?" 강조: Q1 → Q2 순서로 스포트라이트 | The bot asks the author to explain the change: what comes back when the refresh fails, and how many times one request can hit the login server. |
| 7 | 00:51–00:57 | **T3** | 둘 다 답하고 제출 → 첫째 Accepted [TARGET] | Q1 정답 선택 + 근거 한 줄 → Q2 보기 "ensure_fresh() retries up to 3 times, but post_json() does not retry, so total attempts equal 3." 선택 + 근거 → **Submit answers** → 첫 카드 **Accepted**. 채점 대기는 Time compressed | The author answers both from the code and submits. The first holds up — accepted. |
| 8 | 00:57–01:04 | **T4** | 둘째 Hold [TARGET] | 둘째 카드 **Hold** · "Not yet — one more place to look." 보기 넷 순차 하이라이트 | The second doesn't. The answer isn't in the change — it's in a file the change calls. Hold. |
| 9 | 01:04–01:16 | **T4** | 본인이 발견함 [TARGET] | "Look here — Open sample-app/app/http_client.py and check what the code actually does." → 발췌 `sample-app/app/http_client.py` L14–29(`MAX_ATTEMPTS = 3`) + L38–57(`for attempt in range(1, MAX_ATTEMPTS + 1):`). L49 줌 · 자막 "3 × 3 = 9" · 2초 침묵 | The hold shows the neighboring file. There it is — a second retry loop, already in place. Three times three. *(2s)* The author sees it for the first time. |
| 10 | 01:16–01:24 | **T4** | 이 결함은 실재함 · 우리는 한 단계 앞 [근거] | 좌측 보류 화면 유지, 우측 GitHub 2026-08-17 사후 분석 원문 "client-side retry loop…" + "retry budgets" 하이라이트 · 7h 47m | This kind of loop made GitHub's August outage worse. The gate didn't find the bug — it asked the person who had to. |
| 11 | 01:24–01:34 | **T5** | 고침 → 게이트가 다시 물음 → 사람이 머지 [TARGET] | 수정 커밋 `fix(auth): stop retrying in ensure_fresh — post_json already retries` → 카드가 "Awaiting author explanation"으로 복귀 → 새 질문(Q2 "How many times in total can a single token refresh request reach the identity provider…?" → "Up to 3 times") → 둘 다 **Accepted** → 카드 **Human Verified** + SHA · 여섯째 줄 채워짐 → **Merge pull request** | They fix it. A new commit, so the gate asks again. This time it holds up — human-verified, on record. A person merges. |
| 12 | 01:34–01:39 | **T6** | 금지가 아님 [TARGET] | 저위험 문서 PR #27: `The Last Human — Check not required` · `comprehension-gate — Comprehension check not required` · Merge 활성 | Nothing is banned. Low-risk changes go straight through. |
| 13 | 01:39–01:53 | **T7** | 조직은 이제 누가 답할 수 있는지 앎 [TARGET · Demo data] | 대시보드: KPI "Human-verified before merge" → Coverage by zone의 `sample-app/app/auth/` 행, Can answer **0 → 1** (머지 전 정지 화면과 분할) → "counts, never names". Demo data 칩 | Per module: how many people can answer for it. Authentication had no one on record — now one. Counts, never names. *(0.5s)* If that loop ever fires here, someone already understood this change. |
| 14 | 01:53–01:58 | — | 기장은 여전히 사람임 [연출] | PIC 카드 | Copilot can fly. The pilot in command is still responsible. |
| 15 | 01:58–02:00 | — | 마무리 [연출] | 태그라인 카드, 무음: "AI-generated code. Human-owned decisions." | *(무음)* |

### 테이크 상태

편집은 테이크 단위로 진행하며, 나레이션(TTS)에 영상을 맞춤. **음성 속도는 사람이 알아듣는 속도를 넘기지 않음** — 컷이 넘치면 영상 쪽을 압축하거나 정지 프레임을 줄이고, 음성을 빠르게 하지 않음. 렌더 길이가 컷 길이와 다르면 비고에 적음.

| 테이크 | 컷 | raw | 편집 | 검토 | 렌더 길이 / 컷 길이 | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| T1 | 4·5 | ✓ | ✓ | 대기 | 16.5s / 16s | 여운 1초 |
| T2 | 6 | ✓ | ✓ | 대기 | 10.5s / 10s | Q1 문장이 끝난 뒤 스크롤 |
| T3 | 7 | ✓ | ✓ | 대기 | 6.5s / 6s | 채점 대기 Time compressed · T2에서 0.3s 크로스페이드 |
| T4 | 8·9·10 | ✓ | — | — | — / 27s | GitHub 인용 원문 확정 필요 |
| T5 | 11 | 일부 | — | — | — / 10s | Merge 클릭 장면은 #22 머지 시 촬영 |
| T6 | 12 | ✓ | — | — | — / 5s | |
| T7 | 13 | — | — | — | — / 14s | #22 머지 후 대시보드 |

## 5. English VO — 대본과 TTS 실측

이어 읽는 대본:

> More of our code is written by agents. Every piece of code still needs someone responsible for it.
>
> Godot, a major open-source engine, answered with a ban: "AI cannot take responsibility."
>
> So how do you check that a person actually understood it? The Last Human: before a risky change merges, its author explains it — from the code. A check, not a ban.
>
> This example changes how the service refreshes login tokens. An agent wrote it; it touches authentication, so the gate is on.
>
> Every check is green. None says a person understood it. The merge waits.
>
> The bot asks the author to explain the change: what comes back when the refresh fails, and how many times one request can hit the login server.
>
> The author answers both from the code and submits. The first holds up — accepted.
>
> The second doesn't. The answer isn't in the change — it's in a file the change calls. Hold.
>
> The hold shows the neighboring file. There it is — a second retry loop, already in place. Three times three. *(2s)* The author sees it for the first time.
>
> This kind of loop made GitHub's August outage worse. The gate didn't find the bug — it asked the person who had to.
>
> They fix it. A new commit, so the gate asks again. This time it holds up — human-verified, on record. A person merges.
>
> Nothing is banned. Low-risk changes go straight through.
>
> Per module: how many people can answer for it. Authentication had no one on record — now one. Counts, never names. *(0.5s)* If that loop ever fires here, someone already understood this change.
>
> Copilot can fly. The pilot in command is still responsible.
>
> *(card, silent)* AI-generated code. Human-owned decisions.

Azure Speech(en-US-AndrewMultilingualNeural)로 컷 단위 생성한 실측. 침묵은 SSML break로 음성 파일 안에 있음. 여유 0.3초 미만인 컷은 문장을 늘리면 넘침. 전 컷 rate 0%(자연 속도). 자연 속도로 120초에 맞추기 위해 대본을 약 45단어 줄임(컷 2·4·6·8·11·12).

| # | 시작 | 컷 길이 | TTS 실측 | 여유 | rate | 비고 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 00:00 | 7s | 6.0s | +1.0s | 0% | |
| 2 | 00:07 | 8s | 6.7s | +1.3s | 0% | |
| 3 | 00:15 | 10s | 9.7s | +0.3s | 0% | |
| 4 | 00:25 | 10s | 8.9s | +1.1s | 0% | T1 |
| 5 | 00:35 | 6s | 5.2s | +0.8s | 0% | T1 |
| 6 | 00:41 | 10s | 8.5s | +1.5s | 0% | T2 |
| 7 | 00:51 | 6s | 5.3s | +0.7s | 0% | T3 |
| 8 | 00:57 | 7s | 5.8s | +1.2s | 0% | T4 |
| 9 | 01:04 | 12s | 11.3s | +0.7s | 0% | T4 · 2s 침묵 포함 |
| 10 | 01:16 | 8s | 7.1s | +0.9s | 0% | T4 |
| 11 | 01:24 | 10s | 8.6s | +1.4s | 0% | T5 |
| 12 | 01:34 | 5s | 3.6s | +1.4s | 0% | T6 |
| 13 | 01:39 | 14s | 13.1s | +0.9s | 0% | T7 · 0.5s 침묵 포함 |
| 14 | 01:53 | 5s | 4.4s | +0.6s | 0% | |
| 15 | 01:58 | 2s | — | — | — | 무음 카드 |

합계 120초 · 음성 283단어(약 104초) + 의도된 침묵 2.5초 · 컷 사이 숨 약 14초. 시간이 넘치면 자르는 순서: 컷 10의 둘째 문장 → 컷 12를 6→5초 → 컷 2의 연구 자막. 컷 9의 침묵과 대시보드는 자르지 않음.

## 6. 대시보드 — 세 줄 정의

- **분모**는 게이트가 발동한 PR. 임계값 미달로 지나간 PR은 커버리지에 들어가지 않음.
- **표본 5 미만** 구역은 비율을 내지 않고 수만 보여줌.
- **사후 인증은 소급하지 않음.** 머지 뒤 확인은 머지 전 비율을 올리지 않음. 30일 이력은 시드(Demo data 칩), `auth/` 0 → 1만 실제 증분.

## 7. 런타임 아키텍처

영상에서는 뺐고 발표 슬라이드와 Q&A에서 씀. Bot이 유일한 쓰기 주체, Actions는 이벤트만 나름, 모델은 보조.

```
PR author <-> Web interview <-> TLH Bot (GitHub App / Azure OpenAI) <-> LLM
                                     ^
PR -> Actions (events only) ---------+
^                                    |
+-- status comprehension-gate / card -+
                                     |
                              Receipts (server-owned, bound to SHA)
                                     |
                              Dashboard (per CODEOWNERS zone)
```

---

출처: [Godot contribution policy 2026][godot-policy] · [Anthropic, AI assistance and coding skills (2026)][skill-study] · GitHub 2026-08-17 incident post-mortem · New Relic 2026 survey.

[godot-policy]: https://godotengine.org/article/contribution-policy-2026/
[skill-study]: https://www.anthropic.com/research/AI-assistance-coding-skills
