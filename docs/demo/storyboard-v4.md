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
| 아키텍처 | 01:46–01:57 (11초) | — | 슬라이드로 이동 (7장) |
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
| 아키텍처 | 영상 안 11초 | 슬라이드로 | 본편이 늘어난 만큼 설명 장면을 뺌. 실현 가능성은 실제 PR·체크·머지 화면이 이미 증명함 |

## 2. 구성

**전제 → 긴장(Godot) → 질문 → 답(The Last Human) → 본편(Q1 Accepted · Q2 Hold · 옆 파일 발견 · 수정 · 재확인 · 머지) → 반증(저위험은 그냥 지나감) → 대시보드(모듈별로 답할 수 있는 사람 수) → 기장은 여전히 사람.**

오프닝의 다섯 단어 — responsible · understood · from the code · ban · check — 가 본편과 대시보드, 결말에서 전부 돌아옴. 이해가 책임의 전제임.

본편은 두 문항이 동시에 뜨고 한 번에 제출되는 실제 제품 흐름을 따름. 질문을 퀴즈처럼 나열하지 않고, 화면의 라벨(Accepted / Hold / Human Verified)과 같은 단어만 말함.

## 3. 제품 소개와 구성요소

제품은 00:15–00:25에 질문의 답으로 소개함: **"before a risky change merges, its author explains it — from the code. A check, not a ban."** 구성요소는 한꺼번에 나열하지 않고, 쓰이는 장면에서 화면에 보이는 이름 그대로 등장시킴. 내레이션은 일반어(the gate · the bot · the dashboard)를 씀.

넷 다 있어야 하는 층임. Gate는 "이 변경"에, Dashboard는 "이 조직"에 답하고, Bot은 Gate를 채우는 유일한 방법이며, Relay는 성공 기록이 현재 코드에도 유효한지 독립적으로 확인함.

이 넷은 제품의 기능 역할이지 각각 설치하는 서비스가 아님. **Bot은 TLH Server에서 실행되고, GitHub App은 서버가 GitHub에 접근하는 신원·권한 주체**임. App 등록, 대상 저장소 설치, 작성자의 OAuth 로그인은 서로 다른 절차임.

| 층 | 화면에 보이는 이름 | 역할 | 왜 있어야 하나 | 등장 |
| --- | --- | --- | --- | --- |
| **Gate** | 필수 체크 "Last Human — Awaiting author explanation" → "Human-verified · SHA" / "Not required" | 필수 gate로 설정된 위험한 변경은 작성자의 유효한 확인 없이는 머지 불가. 임계값 미달은 Not required로 지나감 | 제품의 약속 그 자체. 없으면 조언 도구 | 00:25 대기 · 01:24 통과 · 01:34 미발동 |
| **Bot** | 같은 줄의 카드 · 웹 면담 "Explain the change — from the code" · Accepted / Hold | 위험 근거 요약, 질문 생성, 채점, 현재 커밋에 묶인 기록 | Gate를 채우는 유일한 방법 | 00:41–01:24 |
| **Relay** | "Last Human · relay / Relay PR #n", "Verify receipt …" | 이벤트·snapshot binding을 OIDC로 전달하고, 현재 코드의 snapshot·위험도를 독립 재계산해 성공 기록과 대조 | 코드와 기록의 결속을 GitHub 쪽에서도 확인 · 공유 장기 시크릿 없이 저장소에 묶인 신원 · 네트워크 조건을 갖추면 사내망 배치 가능 | 체크 목록 · Actions |
| **Dashboard** | "Last Human dashboard" · "Human-verified before merge" · "Can answer" | CODEOWNERS 구역별 확인 비율·답할 수 있는 사람 수·예외·근거 PR. 이름 없이 수만 | Gate는 변경에, Dashboard는 조직에 답함. 어디까지 에이전트를 열어도 되는지의 지도 | 01:39 |

**Human-verified**는 Gate가 통과했을 때의 변경의 상태. 새 커밋이 오면 무효가 되고 Gate가 다시 물음. 사람에게 자격이나 등급을 주지 않음. Relay의 대가: 러너 기동 지연(20–40초), Actions 분 사용, 체크 목록에 한 줄 더.

위 표는 Gate의 상태를 요약한 표현임. 필수 status의 기본 식별자는 `last-human/human-verified`이며, 보조 Check "The Last Human"은 `TLH_CHECK_RUNS=true`일 때 별도로 표시됨. Required 여부와 발급 App은 저장소 관리자가 보호 규칙으로 지정함.

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
| 10 | 01:16–01:24 | **T4** | 이 결함은 실재함 · 우리는 한 단계 앞 [근거] | 좌측 보류 화면 유지, 우측 패널에 GitHub 사후 분석 원문 — "Errors in those services triggered a **client-side retry loop** that increased traffic during recovery." · 대응 "consistent retry limits, **retry budgets**, and variable timeouts" · 캡션 "GitHub Blog · The August 17 outage, and the work ahead · Vlad Fedorov · 2026-08-20 · 7h 47m" | This kind of loop made GitHub's August outage worse. The gate didn't find the bug — it asked the person who had to. |
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
| T4 | 8·9·10 | ✓ | ✓ | 대기 | 27.5s / 27s | 우측 인용 패널(안 A) |
| T5 | 11 | 일부 | 가편집 | 대기 | 11.7s / 10s | Merge 클릭 2s는 TBC 카드 — #22 머지 시 촬영·교체 |
| T6 | 12 | ✓ | ✓ | 대기 | 5.0s / 5s | |
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

## 7. 아키텍처와 데이터 흐름

영상에서는 뺐고 발표 슬라이드와 Q&A에서 씀. 서버는 TLH 운영자가 두는 곳에서 돌고, TLH gate·카드·보조 Check의 쓰기 주체는 서버 하나, 모델은 보조.

### GitHub App 연결과 최초 설치

| 순서 | 담당 | 준비·설정 |
| --- | --- | --- |
| 1 | TLH 운영자 | **TLH Server 운영 + GitHub App 등록**. 서버의 HTTPS 주소와 같은 App의 OAuth callback을 연결 |
| 2 | 고객 저장소 관리자 | **대상 저장소에 App 설치**. 선택 저장소와 필요한 권한 승인 |
| 3 | 운영자 / 저장소 관리자 | 서버의 repository·installation 연결 + 신뢰된 workflow·정책 파일 적용 + 필수 status와 발급 App 설정 |
| 4 | PR 작성자 | 같은 App의 **별도 OAuth 로그인** 후 TLH Server에서 변경 설명 |

App 설치는 저장소 접근 권한을 부여하는 절차임. **서버 호스팅, workflow·정책 파일 추가, 보호 규칙 설정을 자동으로 해 주지는 않음.** 서버는 선택 저장소의 installation token으로 GitHub API를 호출하고, 작성자는 user OAuth로 신원을 확인함. Actions의 단기 workflow/OIDC 토큰은 이 둘과 다른 실행 신원임.

보조 Check는 App의 `Checks: Read and write` 권한 승인 후 전용 토큰으로 게시함. 고객 조직에 설치할 때는 App의 설치 가능 범위와 조직 승인 조건을 충족해야 함.

**저장소 연결:** 서버는 하나의 repository·installation 설정을 사용하며, App 설치만으로 다른 저장소가 자동 연결되지는 않음. 기본 workflow는 대상 저장소의 TLH 소스를 설치하므로, TLH 소스가 없는 저장소에는 버전을 고정한 별도 verifier 배포와 설치 템플릿이 필요함. 상세 설정은 [App 실행 가이드](../runbooks/github-app.md)를 따름.

### 설치 이후의 런타임 흐름

그림의 GitHub 영역은 Gate의 상태를 한 줄로 요약한 개념도임. 실제 status와 보조 Check는 별도로 표시됨.

```
                     The Last Human — Component architecture & data flow

┌────────────────────────────────────────── GitHub ──────────────────────────────────────────┐
│                                                                                             │
│  Repository (code · CODEOWNERS)              Pull request                                   │
│                                              ┌──────────────────────────────────────────┐   │
│                                              │ Checks                                   │   │
│                                              │ ● Last Human — Awaiting author explanation│  │
│                                              │     → Human-verified · <sha>             │   │
│                                              │     / Not required        Required  ◄──⑥─┼─┐ │
│                                              │ ✓ Last Human · relay                     │ │ │
│                                              │ ✓ repo CI (tests · lint)                 │ │ │
│                                              └──────────────────────────────────────────┘ │ │
│                                                        ▲ ⑧ a person merges                │ │
│  GitHub Actions (runners)                              │                                   │ │
│  ┌───────────────────────────────────────────────┐     │                                   │ │
│  │ Relay — on PR open / new commit          ①    │     │                                   │ │
│  │   reads PR → computes snapshot/risk           │     │                                   │ │
│  │   forwards binding + OIDC identity ──────────┼─────┼──── ② ────────────────────────┐   │ │
│  │                                               │     │                                │   │ │
│  │ Verify — dispatched by the server        ⑤    │     │                                │   │ │
│  │   trusted runtime → read PR → recompute       │     │                                │   │ │
│  │   → compare with server's receipt             │     │                                │   │ │
│  │   → submit binding + OIDC                ─────┼─────┼─── ⑤' verify request ──────┐  │   │ │
│  │ Observe ⑥' latest GitHub gate → ✓ / ✗         │     │                             │  │   │ │
│  │ to server: metadata + short-lived tokens      │     │                             │  │   │ │
│  │ no App/model keys · no gate writes            │     │                             │  │   │ │
│  └───────────────────────────────────────────────┘     │                             │  │   │ │
└────────────────────────────────────────────────────────┼─────────────────────────────┼──┼───┼─┘
                                                         │                             ▼  ▼   │
┌────────────────────┐                 ┌──────────────────────────────────────────────────────┴─┐
│ Author (browser)   │── ③ answers ──►│ TLH Server (Bot)                                        │
│ GitHub sign-in     │◄─ Accepted/Hold─│ where the org runs it — reachable by runners & people   │
│ two questions,     │   + evidence    │ (public tunnel today · VPN + self-hosted runners for    │
│ one line each      │                 │  a private deployment) · not a GitHub webhook receiver  │
└────────────────────┘                 │                                                         │
                                       │  Gate logic   diff → risk score → structure facts →     │
┌────────────────────┐                 │               questions → grading                       │
│ Org / leads        │── ⑨ reads ────►│  Receipts     ④ bound to head SHA (new commit ⇒ void)  │
│ (browser)          │                 │  Publisher    ⑥ status · card · supplemental Check     │
└────────────────────┘                 │  Dashboard    ⑦ per-CODEOWNERS-zone coverage           │
                                       │  Store        snapshots · questions · receipts · merges │
                                       │  Verify       re-check receipt/current PR/binding       │
                                       │  Policy       risk rules · prompts (human-approved,     │
                                       │               versioned into every snapshot)            │
                                       └───────────────────────────┬─────────────────────────────┘
                                                                   │ ②' questions · grading
                                                                   ▼
                                       ┌─────────────────────────────────────────────────────────┐
                                       │ Model (Azure OpenAI) — assistive only                   │
                                       │ writes the questions · grades the one-line evidence     │
                                       │ no GitHub write or merge authority                      │
                                       └─────────────────────────────────────────────────────────┘
```

**데이터 흐름**

| # | From → To | 무엇이 움직이나 | 왜 중요한가 |
| --- | --- | --- | --- |
| ① | GitHub → Actions | PR 열림 / 새 커밋이 Relay를 시작 | 우리 쪽에 웹훅 수신자가 필요 없음 |
| ② | Relay → Server | PR 메타데이터·재계산한 snapshot binding + **OIDC 신원**(repo · workflow · repo id) | 신뢰된 실행인지와 서버 계산의 일치를 확인. PR 닫힘 이벤트는 별도 메타데이터 경로 |
| ②' | Server ↔ Model | diff 사실 → 질문 두 개; 근거 한 줄 → 평가 | 모델 평가는 면담 판정에 쓰이지만 GitHub 게시·머지 권한은 없음 |
| ③ | Author ↔ Server | 보기 + 근거 한 줄 → Accepted / Hold + 근거 발췌 | 원문은 공개 PR에 남기지 않음. 평가에 필요한 근거는 모델에 전달 |
| ④ | Server | head SHA 등 현재 검토 리비전에 묶인 성공 영수증 | 새 커밋에 이전 인증을 재사용하지 않음. 과거 성공 기록은 보존 |
| ⑤ | Server → Actions | **Verify** dispatch: 신뢰된 runtime으로 대상 PR을 읽어 **snapshot·위험도를 재계산**, 영수증의 binding과 비교 | PR head 코드를 실행하거나 영수증을 다시 만들거나 답변을 재채점하지 않음 |
| ⑤' | Actions → Server | 재계산한 binding + OIDC로 검증 요청 | 서버도 receipt·현재 PR·새 snapshot을 대조한 뒤 verified 기록과 발행 작업 저장 |
| ⑥ | Server → GitHub | 상태: Awaiting → **Human-verified · sha** / Not required; 카드·보조 Check | TLH 게시 주체는 서버. Human-verified 발행은 검증 뒤, 대기·미발동 상태는 별도 경로 |
| ⑥' | Actions → GitHub | 현재 SHA의 최신 gate status를 읽고 해당 receipt의 실제 성공 게시 확인 | 서버 응답이나 과거 성공만으로 완료 처리하지 않음 |
| ⑦ | Store + CODEOWNERS → Dashboard | 구역별 머지 전 확인 비율, 답할 수 있는 사람 수, 예외, 근거 PR | 이름 없이 수만 |
| ⑧ | Person → GitHub | Merge | 봇은 절대 아님 |
| ⑨ | Org → Server | 대시보드 열람 | 다음에 에이전트를 어디까지 열지 |

영수증의 결속은 head SHA뿐 아니라 repo/PR·base SHA·정책·snapshot·위험도와 작성자·App/installation·질문 버전도 포함함. `Human-verified · sha`는 화면 표현이며 SHA를 status 식별자에 붙이지 않음.

**구성요소와 실행 위치**

| 구성요소 | 실행 위치 | 갖고 있는 것 | 할 수 없는 것 |
| --- | --- | --- | --- |
| **Gate** | GitHub — PR의 필수 status | 제품 상태: awaiting / human-verified / not required | 스스로 판단 — Bot이 채움 |
| **Bot** | TLH Server | App 자격증명, 대상 repository/installation, 정책 | 보호된 업무 API는 인가된 workflow·사용자만 이용 가능 |
| **Relay** | GitHub Actions | 단기 workflow/OIDC 토큰, 신뢰된 runtime, 대상 코드 읽기·snapshot 계산 | App private key·모델 자격 보유, TLH gate·카드·Check 게시, 답변 재채점 |
| **Dashboard** | 같은 서버 | 집계 수치 + 표기된 데모 시드 | 이름·순위·개인 이력 표시 |
| **Model** | Azure OpenAI | 추론 요청·평가 결과. 서비스의 데이터 처리·보관은 계약과 설정에 따름 | GitHub 게시·머지, TLH 기록 직접 갱신 |

**신뢰 경계**: GitHub App installation ↔ Server(선택 저장소의 API 권한) · GitHub ↔ Actions(실행 신원) · Actions ↔ Server(OIDC와 binding 대조) · 사람 ↔ Server(같은 App의 OAuth 로그인과 작성자 인가).

App private key·client secret·모델 자격은 서버에서 관리하고 Actions에 넘기지 않음. **Actions에도 단기 GitHub/OIDC 자격은 있으므로 “비밀이 전혀 없음”은 아님.** App 비밀을 프롬프트에 의도적으로 넣지 않지만 코드·설명 근거는 모델에 전달되므로, 민감 정보 유입 방지와 모델 서비스의 보관 정책은 별도로 다룸. 보류 답변·피드백은 임시 상태로만 두고 성공한 근거만 영구 보관함.

**웹훅 대신 Relay인 이유**: 현재 코드와 성공 기록의 결속을 GitHub 쪽에서도 독립 계산·확인하고 실행 로그로 남김. 사전 공유한 장기 시크릿 대신 OIDC를 사용하며, self-hosted 러너와 사용자 접속 경로를 갖추면 서버를 사내망에 둘 수 있음. 현재 데모의 공개 터널도 같은 TLH Server 연결 방식임. 대가: 러너 기동 지연, 체크 목록 한 줄.

*필수 status는 `last-human/human-verified`, 보조 Check "The Last Human"은 선택 사항임. 저위험은 제품상 neutral/Not required이고 commit status에는 `success`, 보조 Check에는 `neutral`로 게시됨. Required 적용은 저장소 보호 규칙이 담당함.*

---

출처: [Godot contribution policy 2026][godot-policy] · [Anthropic, AI assistance and coding skills (2026)][skill-study] · [GitHub, The August 17 outage, and the work ahead (2026-08-20)][gh-aug17] · New Relic 2026 survey.

[godot-policy]: https://godotengine.org/article/contribution-policy-2026/
[skill-study]: https://www.anthropic.com/research/AI-assistance-coding-skills
[gh-aug17]: https://github.blog/news-insights/company-news/the-august-17-outage-and-the-work-ahead/
