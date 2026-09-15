# 2분 스토리라인 — The Last Human

**AI가 만든 코드는 쏟아지고, 검토의 부담은 사람에게 쌓인다. 결과물을 이해하고 유지보수할 책임도 여전히 사람에게 남는다.** The Last Human은 중요한 PR에서 작성자의 설명과 코드 근거를 연결하고, 그 확인을 머지 조건과 프로젝트의 모듈별 커버리지로 이어 준다.

## 구성

**문제의 실재 → 마지막 인간의 등장 → TLH 소개 → TLH 활용 시나리오(PR 소개, 게이트 진입, 설명 보완, 확인 완료, 머지, 대시보드) → 런타임 아키텍처 → 마무리**

도입부는 하나의 변경이 아니라 AI와 함께 만들어지는 코드·소프트웨어 전반의 이해와 책임을 묻는다. 제품의 PR 활용 시나리오는 30초 이후에 구체적인 적용 사례로 등장한다.

본편은 같은 PR·커밋에서 설명을 보완하고 확인을 완료하는 흐름이다. 질문을 하나씩 나열하거나 코드 퀴즈처럼 보여주지 않는다. 봇은 확인 과정을 진행하고, 사람은 변경을 설명하고 최종 머지를 결정한다.

## 제품 소개와 구성요소

타이틀 직후 30–40초에 **“The Last Human은 중요한 변경의 머지 전에 PR 작성자의 설명과 확인 근거를 연결한다”**고 소개한다. 구성요소를 한꺼번에 나열하지 않고, 사용하는 장면에서 이름과 역할을 보여준 뒤 마지막 런타임 그림에서 관계를 정리한다.

| 요소 | 역할 | 주요 등장 장면 |
| --- | --- | --- |
| **TLH Gate** | 작성자의 유효한 확인 근거를 머지 조건으로 요구하는 관문. 단순히 Action 실행을 성공시키는 기능이 아니다. | 40–51초 확인 대기, 67–81초 성공 상태 |
| **TLH Bot** | 작성자와 상호작용하며 확인 요청·설명 보완·기록 발급·PR 반영을 진행한다. | 40–81초 시작 댓글·웹 면담·완료 댓글 |
| **TLH Dashboard** | 선택한 저장소의 모듈별 확인 커버리지와 근거 PR을 연결한다. | 90–106초 프로젝트 현황 |
| **Verified human(가칭)** | **해당 PR을 작성하고, 현재 커밋의 변경을 설명해 확인 근거를 남긴 사람**이다. 작성자와 별개의 확인자나 봇을 뜻하지 않는다. | 26–30초 PR 작성자 등장, 67–81초 확인 완료 역할 표시 |

`Verified human(가칭)`은 해당 PR·커밋에서 확인을 완료한 작성자의 역할 명칭이다. 사람에게 영구적인 자격·실력 등급을 부여하거나 다른 PR까지 검증되었다고 표시하지 않는다. 완료 화면에는 작성자·대상 커밋·근거를 함께 보여주고, 확인 전에는 단순히 PR 작성자로 표시한다.

총 13장면, 120초다. 표의 English VO를 순서대로 합친 것이 전체 영어 대본이다. 실제 음성·장면을 조합한 결과물이 120초를 넘으면 문장과 컷을 줄인다.

표기: **[근거]** 공식 자료·실제 코드, **[연출]** 개념·인물·타이틀 장면, **[TARGET]** 구현 후 실제 동작을 촬영할 장면. 모의 화면은 리허설용이며 최종 제품 동작의 증거를 대체하지 않는다.

## 장면별 스토리라인

| 시간 | 전달 메시지 | 화면 / 행동 / 증거 | English VO | 촬영 전제 |
| --- | --- | --- | --- | --- |
| 00:00-00:06 | AI가 만든 코드는 쏟아지고, 사람이 읽고 검토할 부담은 쌓인다. | [연출] 생성된 코드·파일·설명이 검토 대기 화면에 겹겹이 쌓인다. 사람이 모두 읽기 버거운 상황을 기계적인 리듬으로 압축하고, 다음 컷의 Godot 발표로 현장 근거를 연결한다. 특정 PR이나 기능 소개로 시작하지 않는다. | AI-generated code is pouring in. The burden of reviewing it still falls on people. | “홍수”는 시각적 비유다. 가짜 처리량 카운터나 출처 없는 증가율을 실제 통계처럼 보여주지 않는다. |
| 00:06-00:14 | 생성이 쉬워져도 검토·유지보수의 부담과 책임은 사람에게 남는다. | [근거] [Godot의 공식 발표][godot-policy]에서 AI 기여는 늘었지만 검토 작업과 리뷰어 수는 그대로라는 설명을 보여준다. 이어 기여자가 코드를 이해하고 필요할 때 고칠 수 있어야 한다는 책임 원칙을 강조한다. | Godot reports more AI contributions without more reviewers. People still carry responsibility for understanding and maintaining the code. | 공식 발표의 짧은 발췌와 출처 표시. Godot이 보고한 상황을 모든 프로젝트의 수치로 일반화하거나 모든 AI 기여가 금지되었다고 표현하지 않는다. |
| 00:14-00:22 | 작업 완료와 이해는 같은 것이 아니다. | [근거] [학습 실험][skill-study]의 집단 평균을 `AI 지원 50% / 비지원 67%`로 보여준다. 하단에 `새 라이브러리 학습 · 52명, 주로 주니어 · 후속 평가 평균`을 표시한다. 제품의 개인 점수 화면처럼 꾸미지 않는다. | In a study of developers learning a new library, AI-assisted participants scored lower on the follow-up assessment. | 연구 수치의 자체 제작 도표. 실제 유지보수 사고 증가나 모든 개발자의 이해 부족을 입증한 결과로 확대하지 않는다. |
| 00:22-00:26 | 이렇게 만들어지는 결과물을, 누가 이해하고 책임질 것인가. | [연출] 자료 화면과 기계적인 리듬이 사라진다. 생성된 코드와 소프트웨어 결과물로 채워진 모니터만 남긴다. 특정 diff나 머지 버튼이 아니라 만들어지는 결과 전체를 향해 질문하고, 짧은 정적 뒤 사람의 실루엣으로 이어진다. | Who understands what's being created, and takes responsibility? | 연구 점수의 해석을 묻는 장면이 아니라, AI와 함께 만드는 결과물의 이해와 책임을 묻는 장면이다. |
| 00:26-00:30 | THE LAST HUMAN | [연출] 카메라가 물러나며 어두운 공간의 개발자 어깨·손·실루엣이 드러난다. 짧은 저음 임팩트와 함께 제목이 크게 등장한다. 같은 모니터의 실제 PR로 전환한다. | The Last Human. | 오리지널 촬영·그래픽·음향. 영화 장면·캐릭터·고유 음악이나 배우 음성을 재현하지 않는다. |
| 00:30-00:40 | 제품을 한 문장으로 소개하고 이번 PR에 적용한다. | [TARGET] `Human confirmation before merge`라는 짧은 문구와 함께 실제 PR로 들어간다. 인증 서버의 일시적 실패에 대응하도록 토큰 갱신을 개선하는 PR의 제목·목적·영향 범위를 보여준다. 위험도 기준으로 확인 대상임을 표시하며 코드의 세부 줄을 확대하지 않는다. | Meet The Last Human: a human checkpoint before merge. This PR improves resilience during temporary authentication failures. | 촬영할 PR의 요구사항·실제 동작·커밋을 함께 고정한다. AI 작성 여부 자체를 발동 조건으로 보이지 않는다. |
| 00:40-00:51 | TLH Gate와 TLH Bot의 역할을 실제 흐름에서 보여준다. | [TARGET] PR 이벤트가 Actions를 거쳐 TLH Bot에 전달된다. PR 작성자가 봇 댓글의 `Open confirmation` 링크로 웹 면담에 들어간다. `TLH Gate`의 대기 상태와 `TLH Bot`의 시작 댓글을 짧게 강조한다. 질문 번호별 소개 대신 하나의 확인 과정으로 보여준다. | TLH Gate awaits confirmation. The bot invites the PR author to explain the change and connect that explanation to the code. | 로그인한 설명 주체가 해당 PR 작성자여야 한다. 봇의 시작 댓글·현재 PR/커밋·웹 진입·필수 게이트를 연결하고 로그인 토큰·OTP는 촬영하지 않는다. |
| 00:51-01:07 | 보완이 필요하면 다음 행동을 안내하고 기다린다. | [TARGET] 중요한 영향 설명이 빠진 합성 답변을 제출한다. 웹에 `Needs clarification`과 코드 근거 안내가 나타난다. 개발자가 관련 코드를 확인하고 설명을 보충한다. PR 게이트는 `pending`을 유지하며 공개 댓글에는 보완 원문이나 개인 실패 이력을 남기지 않는다. | An important impact is missing. The gate stays pending while the bot guides the author to check the code and complete the explanation. | 사람이 검수한 합성 사례를 사용한다. 실제 판정 기준은 별도 승인하며, 대기·타이핑을 편집하면 `Time compressed`를 표시한다. |
| 01:07-01:21 | PR 작성자의 확인이 현재 커밋에 연결된다. | [TARGET] 보완 설명을 제출하면 TLH Bot이 작성자 신원과 현재 커밋의 연결을 확인한다. 성공 기록의 작성자 옆에 `Verified human(가칭)`을 표시하고 대상 커밋·설명 근거를 함께 보여준다. PR에는 완료 댓글과 TLH Gate의 성공 상태가 반영된다. PR 작성자와 기록을 발급한 봇은 별개다. | The PR author's confirmation is now tied to this commit. The bot stores the evidence and updates TLH Gate. | 저장된 성공 기록이 댓글과 체크의 원본이어야 한다. 명칭은 가칭이며 영구적인 사용자 인증 배지로 표시하지 않는다. 실제 처리 대기를 숨겨 즉시 판정되는 성능처럼 보이지 않는다. |
| 01:21-01:30 | 기존 조건을 충족한 뒤 사람이 머지한다. | [TARGET] 같은 PR의 필수 상태를 보여준다. 기존 CI·리뷰 조건도 충족한 상태에서 개발자가 `Merge`를 누르고 `Merged`까지 보여준다. 봇이나 모델이 자동으로 머지하는 장면은 없다. | Existing tests and reviews still apply. The developer checks the results and merges the pull request. | 필수 상태와 보호 규칙이 실제 적용된 데모 저장소. 성공 배지나 머지 결과를 모의 화면으로 대체하지 않는다. |
| 01:30-01:46 | TLH Dashboard에서 프로젝트의 확인 범위와 공백을 파악한다. | [TARGET] TLH Dashboard에서 저장소·기간을 선택한다. 모듈별 머지 전 확인 커버리지에 방금 머지한 PR이 반영되고, 해당 항목에서 근거 PR로 이동한다. 관리자도 팀과 같은 프로젝트 현황을 보며 개인 점수·순위·이해 이력 조회는 제공하지 않는다. | TLH Dashboard shows confirmation coverage by module for the selected repository, with links to the underlying pull requests, not individual performance scores. | 동일한 실제 성공 기록·머지 사실·기간으로 집계한다. 예시 이력이 포함되면 `Demo data`로 표시하고 실제 증분과 구분한다. |
| 01:46-01:57 | Gate·Bot·Dashboard와 PR 작성자의 관계를 정리한다. | [TARGET] 아래 런타임 그림으로 전환한다. TLH Gate·TLH Bot·TLH Dashboard와, 현재 PR의 확인을 완료한 작성자인 `Verified human(가칭)`을 연결한다. Actions·웹·LLM·성공 기록은 이 관계를 구현하는 경로로 보여준다. 전체 18단계나 세부 API를 읽히게 하지 않는다. | GitHub and Azure connect TLH Gate, Bot, and Dashboard. The PR author remains responsible for the explanation. | 실제 구현과 일치하는 구성도로 제작한다. LLM은 보조 수단이며 GitHub App 설치만으로 서비스가 실행되는 것처럼 표현하지 않는다. |
| 01:57-02:00 | AI-generated code. Human-owned decisions. | [연출] 개발자의 뒷모습 또는 제품명으로 닫는다. 새로운 기능 설명을 추가하지 않는다. | AI-generated code. Human-owned decisions. | 긴장감보다 안정된 느낌으로 마무리한다. |

## 게이트의 보완·성공 표현

| 상태 | 웹 면담 | PR 게이트·기록 |
| --- | --- | --- |
| 설명 보완 필요 | 빠진 영향과 확인할 코드 근거를 안내한다. 작성자가 보완해 재제출한다. | `pending` 유지. 보류 원문·개인 실패 이력을 공개하거나 영구 저장하지 않는다. |
| 확인 완료 | 현재 커밋에 연결된 성공 결과와 근거를 보여준다. | 성공 기록 저장 후 해당 커밋의 상태와 완료 댓글에 반영한다. |
| 모델·연동 오류 | 처리 오류와 재시도 경로를 안내한다. 사람의 비통과로 취급하지 않는다. | 오류를 성공으로 바꾸지 않는다. 이미 성공 기록을 저장했다면 답변을 다시 받는 대신 실패한 연동을 재시도한다. |

본편의 실패·회복은 **설명 보완 → 재제출 → 확인 완료**다. 보완 안내 예시는 “실패가 호출자에게 어떻게 전달되는지도 확인해 주세요”이며 최종 질문 생성 프롬프트나 판정 기준은 아니다. 실제 사용자의 보류 화면이 아니라 허가된 데모 계정·합성 답변을 촬영한다.

## 대시보드에서 전달할 가치

대시보드는 개인을 평가하는 관리자 전용 화면이 아니다. 프로젝트 책임자도 팀과 같은 현황 화면에서 저장소를 선택해 **어느 모듈의 변경에 사람의 확인 근거가 남았는지** 살펴본다.

| 화면 요소 | 전달하는 의미 |
| --- | --- |
| 저장소·기간 선택 | 관심 있는 프로젝트와 변경 기간을 살펴본다. |
| 모듈별 머지 전 확인 커버리지 | 선택 기간에 머지된 확인 대상 PR 중, 머지 전에 해당 검토 리비전의 유효한 성공 기록이 있던 비율을 모듈별로 본다. |
| 근거 PR 연결 | 집계에서 실제 변경과 확인 근거를 따라간다. 사람 이름을 기준으로 이력을 조회하지 않는다. |

조직 집계는 모듈 단위다. 확인하지 않은 모듈까지 단순히 PR의 변경 파일이라는 이유로 확인 범위에 포함하지 않는다. 모듈별 근거 앵커와 해당 PR의 기록을 연결하며, 확인 대상이 없거나 표본이 적으면 미측정·표본 부족을 구분한다. 사후 확인이 머지 전 확인 비율을 소급해서 올리지 않는다.

이 수치는 사람의 역량·완전한 이해·코드의 안전도를 뜻하지 않는다. 고정한 목표 비율을 실제 결과처럼 맞추지 않고 촬영 시점의 관측값을 사용한다. 개별 리비전의 성공 기록은 보존하되 조직 화면에는 모듈별 커버리지만 집계한다.

## 런타임 아키텍처 장면

```text
PR author <-> Web UI <-> TLH Bot on Azure <-> LLM
                            ^
PR -> Actions --------------+
^                           |
+-- TLH Gate / comments ----+
                            |
                      Success records
                            |
                      TLH Dashboard
                    (linked to merge)
```

그림의 `PR author`는 해당 PR을 작성한 사람이며, 확인 완료 시 `Verified human(가칭)`으로 표시되는 주체다. TLH Bot은 GitHub App의 신원·권한을 사용하는 실행 서비스다. 모델이 GitHub 상태를 직접 변경하거나 머지를 승인하지 않는다.

1. PR 생성·수정과 실제 머지 이벤트가 Actions를 통해 봇에 전달된다. Actions가 면담 내내 대기하는 구조는 아니다.
2. 봇이 웹 면담을 진행하고 모델의 도움으로 설명과 근거를 대조한다.
3. 저장한 성공 기록을 PR 체크·완료 댓글에 반영하고, 실제 머지 사실과 연결해 대시보드를 갱신한다.

상태 발행은 봇 한 곳으로 일원화한다. **Action의 독립 위험 재계산과 현재 커밋 인증 확인은 유지해야 하는 실행 조건**이며, 이 그림에서는 세부 검증 단계를 생략한다. 새 커밋에는 이전 확인을 재사용하지 않지만 과거 성공 기록을 삭제하지는 않는다.

[Copilot cloud agent][cloud-agent]의 저장소 조사·변경·PR 작업, [GitHub의 필수 상태 검사][required-checks], Azure의 서비스 운영을 하나의 개발 흐름으로 연결한다. 공개 API 조합의 기술적 독점성이나 다른 제품에 유사 기능이 없다는 주장은 하지 않는다.

## 촬영 전제

- 주력 PR의 요구사항·실제 동작·촬영 커밋을 함께 확정한다. 구현하지 않은 재시도를 구현한 것처럼 소개하지 않는다. 본편에서는 코드 수정 없이 같은 리비전의 설명 보완과 성공을 보여준다.
- 게이트는 AI 작성 여부가 아니라 위험도에 따라 발동한다. 임계값 미달 PR은 `neutral` 경로로 지나가며, 본편은 확인 대상으로 선정된 PR을 다룬다.
- 본편의 설명·보완·확인은 해당 PR 작성자 1명이 수행한다. `Verified human(가칭)`은 그 작성자의 현재 커밋 확인 역할이며 다른 담당자로 대체해 보여주지 않는다. 기본 질문 수는 3개지만 영상은 문항 수나 번호별 설명을 중심으로 하지 않는다.
- 코드 기준 `2210cb5`는 공개 PR 댓글에 답변 블록을 붙여넣는 흐름이고, 기본 질문 수 2개·작성자와 별도 리뷰어를 요구한다. 로그인·비공개 웹 제출·서버 보유 성공 기록·봇 발급 상태·대시보드 연결은 별도 구현이 필요하다 (`src/lasthuman/templates/interview.html.j2:141-211`, `src/lasthuman/cli.py:411-420`, `src/lasthuman/attest.py:107-131`, `.github/workflows/comprehension-gate.yml:244-307`).
- 질문 근거·프롬프트·판정 기준·인증 형식·저장 수명은 사람이 승인한다. 사전 검수한 버전 고정 질문을 사용할 수 있지만 실시간 생성인 것처럼 표현하지 않는다.
- [Godot 발표][godot-policy]는 AI 기여 증가에도 검토 작업과 리뷰어 수는 그대로인 상황, 기여자의 이해·수정 책임, AI 기여 제한을 직접 다룬다. 도입부의 코드 “홍수”는 이 현장 문제를 전달하는 비유이며 업계 전체의 증가율을 뜻하지 않는다. 우리 게이트가 해당 프로젝트의 기여 정책을 대체한다는 메시지도 아니다.
- [Shen·Tamkin의 연구][skill-study-paper]는 주로 주니어인 개발자 52명이 새로운 라이브러리를 학습하는 실험이다. 후속 평가 평균 50%와 67%를 실제 운영 장애나 장기 유지보수 실패의 인과 증거로 확장하지 않는다.
- 이 문서에서 인용한 OSS 정책과 연구는 문제를 설명하는 근거다. 해당 프로젝트가 The Last Human을 사용했다는 증거로 제시하지 않는다.
- 영화에서는 긴장감·규모감·정적 뒤 등장이라는 연출 원리만 참고한다. 배우·캐릭터·장면·고유 음악을 복제하지 않고, 사용 권한이 있는 영상·음향·폰트로 제작한다.

## 추가후보: 코드와 PR 설명의 불일치를 수정하고 새 커밋을 확인하는 경로

재시도를 주장하는 PR에서 작성자가 실제 동작과의 차이를 발견한다. PR 작성자가 코드를 수정하고 새 커밋을 올리면, 봇이 그 작성자에게 새 리비전의 확인을 요청한다. 이전 답변을 그대로 재사용하지 않고 변경된 코드의 설명·근거를 확인한 뒤 성공 기록과 PR 상태를 갱신한다.

시연 소재인 `24d0db0`의 [토큰 갱신 코드][retry-candidate]는 `backoff_for()`를 정의하지만 호출하지 않으며, 일시적 오류에서 기존 토큰을 반환한다. 이 소재로 **불일치 발견 → 실제 수정 → 새 SHA의 확인 → 성공**을 구성할 수 있다. 실제로 구현하고 촬영해 본 뒤 본편의 보완·성공·머지·대시보드·아키텍처를 읽을 시간이 남고 총 120초를 지킬 수 있을 때 포함한다.

[godot-policy]: https://godotengine.org/article/contribution-policy-2026/
[skill-study]: https://www.anthropic.com/research/AI-assistance-coding-skills
[skill-study-paper]: https://arxiv.org/abs/2601.20245
[retry-candidate]: https://github.com/daeungo1/the-last-human/blob/24d0db04cdc82e2e6f84991672681ef51c6402ed/sample-app/app/auth/token.py#L35-L66
[cloud-agent]: https://docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent
[required-checks]: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging
