# The Last Human

**Who Actually Understood This Merge?**
Agentic Coding 레포의 머지 전 이해 검증 게이트 · 내부 코드네임 STAMP

에이전트가 코드를 쓰는 시대에, 그 코드를 이해한 사람이 있는지 확인할 방법이 없습니다.
승인은 기록되지만 이해는 기록되지 않습니다.

모든 AI 리뷰 도구는 사람에게 설명을 **전달**합니다.
The Last Human은 사람에게 설명을 **요구**합니다.

## 구성

| 경로 | 역할 | 상태 |
| --- | --- | --- |
| `src/lasthuman/diff.py` | diff 파싱과 고정 앵커 생성 | 완료 |
| `src/lasthuman/config.py` | `.lasthuman.yml` 로더 | 완료 |
| `src/lasthuman/risk.py` | 위험 점수. 순수 함수, 모델을 부르지 않음 | 완료 |
| `src/lasthuman/interview.py` | 질문 생성과 판정 | 예정 |
| `src/lasthuman/webui.py` | 면담 웹 UI 생성 | 예정 |
| `src/lasthuman/attest.py` | 인증 형식, 커밋 SHA 결속 | 예정 |
| `.github/workflows/` | 게이트 워크플로 | 예정 |
| `sample-app/` | 게이트가 판정할 샘플 워크로드 | 완료 |

## 시작하기

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

### diff와 위험 점수 확인

```bash
python -m lasthuman.cli score --base main --head pr-1-auth-retry
```

## 시연

`sample-app/`은 게이트가 판정할 대상인 가짜 주문 서비스입니다.
시연 PR 네 개가 이 저장소의 브랜치로 올라가 있고, 각각 다른 것을 증명합니다.

| 브랜치 | 무엇을 증명하는가 |
| --- | --- |
| `pr-1-auth-retry` | 주력 데모. 설명은 재시도를 주장하지만 코드에 루프가 없다. AI 리뷰는 초록, 사람은 막힌다 |
| `pr-2-purge-soft-deleted` | 위험 신호. 마이그레이션 + 원시 DELETE가 동시에 걸려 점수가 높다 |
| `pr-3-readme-typo` | **반증. 전수 적용하지 않는다.** 임계값 아래라 중립 통과 |
| `pr-4-external-rate-limit` | OSS 모드. 외부 기여는 기본 발동 |

PR 본문과 심어둘 AI 리뷰 코멘트 원문은 [docs/demo-pr/](docs/demo-pr/)에 있습니다.

게이트 설정은 루트의 [`.lasthuman.yml`](.lasthuman.yml) 하나이고, 샘플 워크로드와 게이트 자신의 코드를 함께 다룹니다.
개발 기간 동안 이 게이트를 이 저장소 자신의 PR에도 겁니다.

## 지켜야 할 선

개인 점수 없음. 순위 없음. 감점 없음. 팀장 조회 불가.
자세한 내용은 [.github/copilot-instructions.md](.github/copilot-instructions.md).

## 문서

계획서 v2.0: [docs/plan-v2.html](docs/plan-v2.html)
