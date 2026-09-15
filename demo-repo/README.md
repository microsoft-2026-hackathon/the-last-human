# orderly

주문 서비스. The Last Human 게이트 시연용 저장소입니다.

## 구성

| 경로 | 역할 |
| --- | --- |
| `src/auth/` | 액세스 토큰 발급과 갱신 |
| `src/orders/` | 주문 조회와 상태 전이 |
| `src/db/` | 데이터베이스 클라이언트 |
| `migrations/` | 스키마 마이그레이션 |

## 개발

```bash
npm install
npm test
```

## 브랜치 규칙

`main`은 보호됩니다. 머지하려면 테스트, 린트, 그리고 이해 인증이 필요합니다.
