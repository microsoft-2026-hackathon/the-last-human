# The Last Human

**Who Actually Understood This Merge?**
Agentic Coding 레포의 머지 전 이해 검증 게이트 · 내부 코드네임 STAMP

에이전트가 코드를 쓰는 시대에, 그 코드를 이해한 사람이 있는지 확인할 방법이 없습니다.
승인은 기록되지만 이해는 기록되지 않습니다.

모든 AI 리뷰 도구는 사람에게 설명을 **전달**합니다.
The Last Human은 사람에게 설명을 **요구**합니다.

## 구성

| 패키지 | 역할 | 상태 |
| --- | --- | --- |
| `packages/core` | diff 파싱, 위험 점수, 인증 형식. 확장과 Action이 공유 | `diff.ts` 완료 |
| `packages/extension` | VS Code 확장 — 면담 진행, 인증 생성 | 예정 9/3 |
| `packages/action` | GitHub Action — 게이트. 재계산 후 검증 | 예정 9/10 |
| `packages/ledger` | 모듈별 이해 커버리지 집계 | 예정 9/12 |
| `demo-repo` | 시연용 주문 서비스. 게이트가 판정할 대상 코드 | PR 4개 완료 |

구현 순서는 **core → extension → action**입니다. 순서를 바꾸면 위험 점수 로직이 두 벌로 갈라집니다.

## 시작하기

```bash
npm install
npm run build
```

### diff 파싱 확인

```bash
node packages/core/dist/cli/diff-cli.js --repo . --base main --head pr-1-auth-retry
```

```
files 1  hunks 3  +31 -13
  modified demo-repo/src/auth/token.js  +31 -13  hunks=3

  demo-repo/src/auth/token.js:L1   +7 -1
  demo-repo/src/auth/token.js:L22  +23 -11
  demo-repo/src/auth/token.js:L67  +1 -1
```

`--json`을 붙이면 hunk 전문이 나옵니다.

## 시연

`demo-repo/`는 게이트가 판정할 대상인 가짜 주문 서비스입니다.
시연 PR 네 개가 이 저장소의 브랜치로 올라가 있고, 각각 다른 것을 증명합니다.

| 브랜치 | 무엇을 증명하는가 |
| --- | --- |
| `pr-1-auth-retry` | 주력 데모. 설명은 재시도를 주장하지만 코드에 루프가 없다. AI 리뷰는 초록, 사람은 막힌다 |
| `pr-2-purge-soft-deleted` | 위험 신호. 마이그레이션 + 원시 DELETE가 동시에 걸려 점수가 높다 |
| `pr-3-readme-typo` | **반증. 전수 적용하지 않는다.** 임계값 아래라 중립 통과 |
| `pr-4-external-rate-limit` | OSS 모드. 외부 기여는 기본 발동 |

PR 본문과 심어둘 AI 리뷰 코멘트 원문은 [docs/demo-pr/](docs/demo-pr/)에 있습니다.

게이트 설정은 루트의 [`.lasthuman.yml`](.lasthuman.yml) 하나이고, 시연 서비스와 게이트 자신의 코드를 함께 다룹니다.
개발 기간 동안 이 게이트를 이 저장소 자신의 PR에도 겁니다.

## 지켜야 할 선

개인 점수 없음. 순위 없음. 감점 없음. 팀장 조회 불가.
자세한 내용은 [.github/copilot-instructions.md](.github/copilot-instructions.md).

## 문서

계획서 v2.0: [docs/plan-v2.html](docs/plan-v2.html)
