# orderly

주문 서비스. The Last Human 게이트가 판정할 샘플 워크로드입니다.

| 경로 | 역할 |
| --- | --- |
| `app/auth/` | 액세스 토큰 발급과 갱신 |
| `app/orders/` | 주문 조회와 상태 전이 |
| `app/db/` | 데이터베이스 클라이언트 (풀 관리 포함) |
| `migrations/` | 스키마 마이그레이션 |

## 개발

```bash
python -m pytest sample-app/tests
```

## 토큰 응답 API

`app.auth.token.refresh(transport, token)`은 검증된
`TokenSet(access_token: str, refresh_token: str, expires_at: float)`을 반환합니다.
`expires_at`은 현재 Unix 시각에 응답의 `expires_in`을 더한 값입니다.

- 성공 응답은 JSON 객체여야 하며, `access_token`은 공백만으로 이루어지지 않은 문자열이어야 합니다.
- `expires_in`은 양수인 유한한 `int` 또는 `float`이어야 합니다. 불리언과 숫자 문자열은 허용하지 않으며,
  계산된 만료 시각이 유한한 `float` 범위를 벗어나도 거부합니다.
- `refresh_token`을 생략하면 기존 값을 유지합니다. 명시적으로 제공한 값은 공백만으로 이루어지지 않은
  문자열이어야 하며, `null`과 빈 문자열은 기존 값으로 대체하지 않고 거부합니다. 토큰 문자열은 수정하지 않습니다.
- 빈 본문, 잘못된 JSON, 객체가 아닌 JSON, 누락되거나 유효하지 않은 필드는
  `app.auth.token.TokenResponseError(ValueError)`로 전달합니다.

호출자는 `from app.auth.token import TokenResponseError`로 이 예외를 가져와 처리할 수 있습니다.
오류 메시지에는 토큰이나 응답 원문을 넣지 않으며, 변환된 파싱·범위 오류의 원인 traceback도 노출하지 않습니다.
`ensure_fresh()`와 `SessionStore.current_token()`도 이 예외를 그대로 전달합니다.
실패한 갱신은 세션의 기존 토큰을 교체하지 않으며, 만료되지 않은 토큰은 요청 없이 그대로 재사용합니다.
HTTP 오류(`app.http_client.HttpError`)와 그 밖의 전송 오류는 변환하지 않습니다.
HTTP 계층의 기존 재시도 정책은 유지하며, 토큰 응답 검증 실패에는 추가 재시도를 하지 않습니다.

## 브랜치 규칙

`main`은 보호됩니다. 머지하려면 테스트, 린트, 그리고 이해 인증이 필요합니다.
