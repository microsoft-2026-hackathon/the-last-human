**AI Code Review** · 3 files analyzed · no blocking issues

전반적으로 잘 구조화된 변경입니다. 검토 결과를 요약합니다.

**✅ 오류 처리** — 일시적 실패와 영구 실패를 `HttpError.transient`로 분리한 것은 적절한 접근입니다. 상태 코드 분류(429/5xx)도 업계 관행과 일치합니다.

**✅ 재시도 정책** — 지수 백오프 상수(`BASE_BACKOFF_MS`, `MAX_RETRIES`)가 매직 넘버 없이 명시적으로 선언되어 있어 조정이 쉽습니다.

**✅ 하위 호환성** — `module.exports` 시그니처가 유지되어 호출자 변경이 필요하지 않습니다.

**💡 제안 (non-blocking)** — 재시도 지터는 PR 설명대로 후속 과제로 남기는 데 동의합니다. 다만 인스턴스 수가 늘어나는 시점을 이슈로 걸어두면 좋겠습니다.

**Verdict: LGTM** — 머지 가능합니다.
