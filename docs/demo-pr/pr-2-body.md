## 배경

소프트 삭제된 주문이 정리되지 않고 계속 쌓여 `orders` 테이블이 4,100만 행에 도달했습니다. 활성 주문 조회의 p99가 지난 분기 대비 2.3배 늘었습니다.

## 변경 내용

- `migrations/002_purge_soft_deleted.sql` — 보존 기간 90일이 지난 소프트 삭제 행을 물리 삭제하고, `deleted_at`을 포함하도록 인덱스를 재구성합니다.
- `OrderRepository.purgeSoftDeleted(retentionDays)` — 배치에서 호출할 정리 메서드.

## 테스트

`npm test` 통과 (5/5).
