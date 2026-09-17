# 발표 자료 — The Last Human · 4분 15초

해커톤 발표용 덱입니다. **88초 데모 영상을 가운데 두고 앞 103초 · 뒤 64초**를 슬라이드가 맡습니다. 영어판은 앞 95초 · 뒤 60초입니다.

구성 근거와 슬라이드별 발화는 [`../storyboard-v5.md`](../storyboard-v5.md)에 있습니다. 이 README는 **띄우고 넘기는 방법**만 다룹니다.

## 서사

심사 배점(시연 30 · 문제·가치 20 · 배포·확장·보안 20 · 새 시도 15 · 체계적 구체화 15)에 맞춰 짰습니다.

**Copilot의 PR은 급증했는데 검증이 못 따라갑니다 → Agentic AI의 전제는 사람의 통제입니다 → stack의 모든 층을 관측하는데 merge 한 층만 빠져 있습니다 → 우리 감은 틀렸고(METR) 불완전함은 이미 측정되었습니다(PR-MCI 논문) → 업계는 금지를 택했지만 우리는 확인을 택했습니다 → [데모] → 우리 PR에 먼저 걸어 측정했습니다 → 이렇게 신뢰합니다 → 이렇게 배포합니다 → 마지막 한 층에도 기록을 남겼습니다.**

핵심은 04입니다. arXiv 논문이 `PR-MCI verification mechanisms`가 필요하다고 지목했고, 우리가 만든 것이 정확히 그것입니다.

## 파일

| 파일 | 무엇 |
| --- | --- |
| [`index.html`](index.html) | **본 덱(한국어).** 본편 13장(255초) + Q&A 부록 3장 |
| [`index.en.html`](index.en.html) | **본 덱(영어).** 같은 구조·같은 빌드 단계. 대본은 [storyboard-v5-en.md](../storyboard-v5-en.md) |
| `backdata_1.png` | Satya Nadella 사내 공지 — 슬라이드 01 (**사내 자료**, 아래 주의 참조) |
| `backdata_2.png` | METR RCT 차트 — 슬라이드 03 (metr.org · CC-BY) |
| `backdata_3.png` | Godot 기여 정책 원문 — 슬라이드 05 (공개 블로그) |
| `backdata_4.png` | PR-MCI 논문 — 슬라이드 04 (arXiv 공개 논문) |
| [`architecture-simple.html`](architecture-simple.html) | 아키텍처 요약 단독 페이지 |
| [`architecture-details.html`](architecture-details.html) | 아키텍처 상세 — 9단계 흐름 · 신뢰 경계. **Q&A 전용** |
| [`verify_claims.py`](verify_claims.py) | 슬라이드의 위험 점수를 실제 채점기로 재현 |
| [`cases/`](cases/) | 재현에 쓰는 diff 3종 |
| `Demo_thelasthuman.mp4` | 데모 영상 **88.22초**. 자동 재생 (git에 올리지 않았습니다) |

## 띄우기

```bash
start docs/demo/presentation/index.html      # Windows
open  docs/demo/presentation/index.html      # macOS
```

폰트는 Pretendard(jsdelivr)와 JetBrains Mono(Google Fonts)를 받아옵니다. **발표장 네트워크가 불안하면 미리 한 번 열어 캐시**해 두십시오. 못 받아도 시스템 한글 폰트로 정상 표시됩니다.

## 조작

| 키 | 동작 |
| --- | --- |
| `→` `Space` | **다음 빌드 단계** → 단계를 다 쓰면 다음 슬라이드 |
| `←` | 한 단계 뒤로 → 첫 단계면 이전 슬라이드(마지막 단계 상태로) |
| `↓` `↑` | **빌드를 건너뛰고** 슬라이드 통째로 이동 |
| `F` | 전체화면 |
| `A` | **부록으로 점프** — A1 데이터 흐름 · A2 고객과 가치 · A3 제약과 다음 수 |
| `M` | 타이머 + 하단 진행바 숨기기 — 화면 공유 전에 끄십시오 |
| `H` | 좌측 하단 안내 숨기기 |
| `Home` `End` | 처음 / 본편 마지막 |

## 빌드 단계

요소가 하나씩 쌓입니다. 타이머에 `단계 2/6`으로 현재 위치가 표시됩니다.

초는 나레이션 **실측**이라 영어판(`index.en.html`)과 몇 초씩 다릅니다.

| 슬라이드 | 초 | 단계 | 무엇이 쌓입니까 |
| --- | --- | --- | --- |
| 00 타이틀 | 11 | — | 문제 상황 한 줄 → Trusted Agentic Coding |
| 01 Agentic AI의 전제 | 15 | 3 | Satya 인용 3문장 — human control → lose permission to operate → observability |
| 02 관측되지 않는 한 층 | 16 | 5 | SDLC 6단계가 켜지고 → **merge 칸이 점멸** → 해석 한 줄 |
| 03 METR | 13 | 1 | 차트 → 해석 한 줄 |
| 04 PR-MCI | 23 | 4 | 45.4% → 28.3%·3.5× → 논문 인용 → "그 mechanism을 만들었습니다" |
| 05 금지 vs 확인 | 12 | 2 | Godot의 답 → 우리의 답 |
| 06 어떻게 동작합니까 | 13 | 4 | 방향 반전 2줄 → 4 Step → 만들지 않는 것 3개 |
| 07 우리 PR에 걸었습니다 | 17 | 4 | 숫자 4개 **카운트업** → 가설·실험 → 학습① → 학습② |
| 08 아키텍처 | 19 | 6 | ①②PR·Relay → ③위험도(+미달 분기) → ④⑤질문·설명 → ⑥⑦receipt·Verify → **⑧Human-verified 화살표가 그려짐** → 보증 3줄 |
| 09 배포·보안·확장 | 13 | 4 | 도입 3갈래 → 기반 밴드 |
| 10 Closing | 15 | 3 | 인용 되돌아오기 → 해석 → CTA |

**리허설에서 손가락이 꼬이면 `↓`를 쓰십시오.** 빌드를 건너뛰고 슬라이드가 완성된 상태로 넘어갑니다.

## 데모 영상

`Demo_thelasthuman.mp4`가 이 폴더에 있으면 데모 슬라이드에서 자동 재생됩니다. 없으면 10개 큐 카드가 대신 뜹니다.

**데모 중에는 말하지 않습니다.** 시연이 30점으로 배점이 가장 크고, 해설을 얹으면 "동작 결과물"이 아니라 "설명"으로 읽힙니다.

실측 88.22초로 덱의 `data-dur="88"`과 맞습니다. 영상을 교체하면 이 값을 실제 길이로 바꾸십시오.

## 발표 전 점검

```bash
PYTHONIOENCODING=utf-8 python docs/demo/presentation/verify_claims.py
```

세 줄 모두 `[OK ]`가 나와야 합니다. `불일치`가 뜨면 `.lasthuman.yml`이 바뀐 것이므로 **슬라이드 07의 `30 / 40`을 고치고** 발표하십시오.

그 외:

- [ ] 07의 gate 이력(26 · 15 · 14 · 3)은 `hunhoon21/the-last-human`의 값입니다 — 이관을 물으면 `docs/migration/import.json`을 보여 주십시오
- [ ] 04의 논문 수치(45.4% · 28.3% vs 80.0% · 3.5×)가 원문과 맞는지 확인하십시오
- [ ] 이미지 4장이 모두 보이는지 확인하십시오 (파일이 없으면 빈 칸이 됩니다)
- [ ] 타이머(`M`)와 안내(`H`)를 껐는지 확인하십시오

## 주의 — backdata_1

`backdata_1.png`는 **사내 공지 캡처**입니다. 원문에 `Restricted | General` 분류와 열람 수가 함께 찍혀 있습니다.

사내 해커톤 발표에 쓰는 것은 문제가 없습니다. 다만 이 저장소는 **public**입니다. 커밋하면 사내 자료가 공개 저장소의 히스토리에 남고, fork와 캐시 때문에 되돌리기 어렵습니다.

그래서 **backdata_1.png는 커밋하지 않았습니다.** 덱은 로컬에서 정상 동작하지만, 저장소를 clone한 팀원에게는 슬라이드 01의 이미지가 비어 보입니다. 팀원에게는 파일을 따로 전달하십시오.

나머지 세 장(METR · Godot · arXiv)은 공개 출처라 커밋했습니다.

## 고칠 때

- **슬라이드 문구·시간** → `index.html`의 해당 `<section>`. `data-dur`가 그 슬라이드의 초입니다
- **아키텍처 그림** → 슬라이드 08의 SVG가 원본입니다. `architecture-simple.html`은 단독 열람용 사본이므로 한쪽을 고치면 다른 쪽도 맞추십시오
- **서사·발화·근거** → `../storyboard-v5.md`를 먼저 고치고 덱이 따라오게 하십시오

## 원칙

이 덱은 [`AGENTS.md`](../../../AGENTS.md)의 금지 넷 — 개인 점수·순위, 이름이 붙은 집계, 감점 기록, 팀장 조회 — 을 넘지 않습니다. **심사 점수를 올리려고 이 넷을 넘지 마십시오.** 넘는 순간 다른 제품이 됩니다.
