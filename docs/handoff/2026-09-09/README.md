# 노트북 이관 파일

`feat/github-app-runtime`의 코드·테스트·워크플로·실행 가이드는 저장소에 그대로 둔다. 아래 Coral 파일만 필요에 따라 새 PC의 사용자 디렉터리로 복사한다. 새 PC에 같은 이름의 파일이 있으면 먼저 비교하고 덮어쓰지 않는다.

| 저장소 안 경로 | 새 PC의 목적지 |
| --- | --- |
| `coral/hunhoon21-the-last-human/plans/github-app-bot-integration.md` | `~/.coral/projects/hunhoon21-the-last-human/plans/github-app-bot-integration.md` |
| `coral/daeungo1-the-last-human/analysis/2026-09-08-hackathon-design-gaps.md` | `~/.coral/projects/daeungo1-the-last-human/analysis/2026-09-08-hackathon-design-gaps.md` |
| `coral/daeungo1-the-last-human/plans/pr-evidence-storyboard-2026-09-08.md` | `~/.coral/projects/daeungo1-the-last-human/plans/pr-evidence-storyboard-2026-09-08.md` |

세 문서는 공개 가능한 코드·설계 중심의 이관본이다. 내부 실행 식별자·미팅/챌린지 내용은 제외했으며 원본과 바이트 단위로 같은 백업은 아니다. 과거의 예정/미완료 표시와 경로는 당시 상황을 설명한다. 현재 실행 방법은 [`github-app.md`](../../runbooks/github-app.md), 영상 구성은 [`storyboard.md`](../../demo/storyboard.md)를 우선한다.

## 이어서 작업할 때의 기준

- GitHub 대상은 `hunhoon21/the-last-human`. 초기 확인자는 PR 작성자 1명이다.
- Flask/Jinja/SQLite 단일 프로세스의 로컬 구현이다. 관련자 전체로 인가를 확장하거나 운영 DB/다중 인스턴스를 도입한 상태가 아니다.
- 성공 기록과 GitHub 발행 작업은 지속 저장한다. 로그인 토큰·미완료 답변·보완 피드백은 최대 30분 메모리 전용이며 재시작 후 복구하지 않는다.
- 봇만 GitHub 상태를 발행한다. 성공 기록은 Action의 독립 위험도·현재 커밋 확인까지 맞아야 최종 성공 상태로 반영된다.
- `LASTHUMAN_RUNTIME=app` 전환 전에는 기존 경로가 유지된다. 새 코드와 workflow가 trusted `main`에 있고 외부 서비스가 준비된 뒤 전환한다.
- 실제 App 인증·외부 모델·공개 HTTPS를 연결한 전체 원격 흐름은 아직 완료되지 않았다. 로컬 코드가 있다는 이유로 기존 보호 규칙을 해제하지 않는다.
- 대시보드는 현재 한 저장소의 모듈 건수 중심이다. 여러 저장소 선택, 그래프, 근거 PR 탐색 UI는 후속 작업이다.
- `pr-types.md` 초안은 이번 이관 시점의 작업 폴더에서 확인되지 않았다. 초기 분석/제작 계획은 포함하지만 초안 파일 자체를 복원한 것으로 보지 않는다.

## Git 밖에서 별도로 보존하거나 다시 설정할 항목

| 대상 | 처리 |
| --- | --- |
| App 개인키 | 새 PC에서 새 키를 생성·설정한 뒤 GitHub의 기존 키를 폐기한다. PEM은 Git에 넣지 않는다. |
| `~/.config/the-last-human/github-app/runtime.env` | 새 PC에서 작성한다. 이전 PC의 해당 파일은 이관 확인 시 빈 파일이었다. ID·Client secret·모델 설정을 별도로 확보한다. |
| `~/workspace/ideation/docs/the-last-human-github-app-build-plan.md` | 사내 자료와 함께 승인된 비공개 경로로 별도 이전한다. 이 공개 저장소에는 원문을 포함하지 않는다. |
| `~/workspace/ideation/docs/the-last-human-friday-demo-plan.md` | 동일. |
| `~/workspace/ideation/docs/the-last-human-evidence.md` | 동일. |
| `~/workspace/ideation/docs/the-last-human-mockup.html` | 동일. |
| Copilot 세션 원문 | 필요할 경우 승인된 비공개 경로로 별도 보관한다. 토큰·개인 정보가 포함될 수 있어 공개 Git에 넣지 않는다. |

가상환경·Python 캐시·스냅샷 Git 캐시는 새 PC에서 재생성한다. 기본 운영 DB `.work/lasthuman.sqlite3`는 이관 확인 시 없었다. 이후 별도 DB를 생성했다면 가상환경과 달리 삭제 가능한 캐시로 취급하지 않는다.
