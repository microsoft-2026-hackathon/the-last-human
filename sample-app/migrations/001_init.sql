CREATE TABLE orders (
  id           BIGINT PRIMARY KEY,
  customer_id  BIGINT      NOT NULL,
  status       VARCHAR(16) NOT NULL,
  total_cents  INT         NOT NULL,
  created_at   TIMESTAMP   NOT NULL,
  shipped_at   TIMESTAMP   NULL,
  deleted_at   TIMESTAMP   NULL
);

CREATE INDEX idx_orders_customer_status ON orders (customer_id, status);
