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

## 브랜치 규칙

`main`은 보호됩니다. 머지하려면 테스트, 린트, 그리고 이해 인증이 필요합니다.
