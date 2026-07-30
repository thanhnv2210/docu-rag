---
title: Database Schema
version: "2.0"
tags: [database, postgresql, schema]
---

# Database Schema

FinPay uses PostgreSQL 16 as its primary datastore. All tables live in the `finpay` schema. The database runs on RDS Multi-AZ with a read replica used by the Reporting Engine.

## Table: transactions

The primary record for every payment initiated through FinPay.

```sql
CREATE TABLE finpay.transactions (
    id                  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id         UUID        NOT NULL REFERENCES finpay.merchants(id),
    idempotency_key     TEXT        NOT NULL,
    hub_id              TEXT,                       -- set after hub selection
    hub_reference       TEXT,                       -- hub's own transaction ID
    state               TEXT        NOT NULL DEFAULT 'PENDING',
    failure_reason      TEXT,                       -- set on FAILED state
    amount              NUMERIC(18,6) NOT NULL,
    currency            CHAR(3)     NOT NULL,
    sender_account      JSONB       NOT NULL,
    recipient_account   JSONB       NOT NULL,
    fx_rate             NUMERIC(12,6),              -- mid-market rate at time of tx
    fx_margin           NUMERIC(6,4),               -- platform margin applied
    purpose_code        TEXT,
    metadata            JSONB       DEFAULT '{}',
    version             INTEGER     NOT NULL DEFAULT 0,  -- optimistic lock
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    submitted_at        TIMESTAMPTZ,
    confirmed_at        TIMESTAMPTZ,
    settled_at          TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ,
    cancellation_reason TEXT
);

-- Idempotency key must be unique per merchant
CREATE UNIQUE INDEX idx_tx_idempotency
    ON finpay.transactions (merchant_id, idempotency_key);

-- Common query patterns
CREATE INDEX idx_tx_state          ON finpay.transactions (state);
CREATE INDEX idx_tx_merchant_created ON finpay.transactions (merchant_id, created_at DESC);
CREATE INDEX idx_tx_hub_reference  ON finpay.transactions (hub_reference) WHERE hub_reference IS NOT NULL;
```

### Optimistic Locking

The `version` column is used for optimistic concurrency control. All UPDATE statements include `WHERE id = $1 AND version = $2` and increment `version` by 1. If the update affects 0 rows, a `ConcurrentModificationException` is raised and the caller retries.

---

## Table: transaction_audit

Immutable audit log. Written exclusively by the Audit Service. No row is ever updated or deleted.

```sql
CREATE TABLE finpay.transaction_audit (
    id              BIGSERIAL   PRIMARY KEY,
    transaction_id  UUID        NOT NULL REFERENCES finpay.transactions(id),
    previous_state  TEXT,
    new_state       TEXT        NOT NULL,
    actor           TEXT        NOT NULL,   -- service name or user ID
    hub_payload     JSONB,                  -- sanitised hub response
    metadata        JSONB       DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_transaction ON finpay.transaction_audit (transaction_id, created_at);
```

---

## Table: fx_rates

Point-in-time FX rate snapshots used for transaction pricing and historical reporting.

```sql
CREATE TABLE finpay.fx_rates (
    id              BIGSERIAL   PRIMARY KEY,
    base_currency   CHAR(3)     NOT NULL,
    quote_currency  CHAR(3)     NOT NULL,
    mid_rate        NUMERIC(12,6) NOT NULL,
    bid_rate        NUMERIC(12,6),
    ask_rate        NUMERIC(12,6),
    source          TEXT        NOT NULL DEFAULT 'market_feed',
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_fx_pair_time ON finpay.fx_rates (base_currency, quote_currency, captured_at DESC);
```

The FX Engine writes a new row every 30 seconds per active currency pair. The Transaction Processor queries the latest row at the time of transaction creation and stores the rate in `transactions.fx_rate`.

---

## Table: payment_hubs

Configuration and live status of registered payment hub adapters.

```sql
CREATE TABLE finpay.payment_hubs (
    id                  TEXT        PRIMARY KEY,   -- e.g. 'arcapay', 'swifthub', 'localpay'
    display_name        TEXT        NOT NULL,
    supported_currencies TEXT[]     NOT NULL,
    circuit_state       TEXT        NOT NULL DEFAULT 'CLOSED',  -- CLOSED, OPEN, HALF_OPEN
    circuit_opened_at   TIMESTAMPTZ,
    last_failure_at     TIMESTAMPTZ,
    failure_count       INTEGER     NOT NULL DEFAULT 0,
    metadata            JSONB       DEFAULT '{}',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## Table: merchants

Registered merchants that use the FinPay API.

```sql
CREATE TABLE finpay.merchants (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT        NOT NULL,
    api_key_hash    TEXT        NOT NULL,   -- bcrypt hash of the API key
    webhook_url     TEXT,
    webhook_secret  TEXT,                   -- HMAC secret for webhook signing
    daily_limit     NUMERIC(18,6) DEFAULT 1000000,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## Table: reconciliation_exceptions

Discrepancies surfaced by the Reconciliation Service.

```sql
CREATE TABLE finpay.reconciliation_exceptions (
    id              BIGSERIAL   PRIMARY KEY,
    transaction_id  UUID        REFERENCES finpay.transactions(id),
    hub_id          TEXT        NOT NULL,
    exception_type  TEXT        NOT NULL,   -- MISSING_IN_HUB, MISSING_IN_FINPAY, AMOUNT_MISMATCH
    finpay_amount   NUMERIC(18,6),
    hub_amount      NUMERIC(18,6),
    notes           TEXT,
    resolved        BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);
```

---

## Partitioning Strategy

The `transactions` and `transaction_audit` tables are range-partitioned by `created_at` (monthly partitions). Partitions older than 13 months are archived to cold storage and detached from the live table. This keeps query performance stable as data volume grows.

```sql
-- Example: create partition for a given month
CREATE TABLE finpay.transactions_2024_03
    PARTITION OF finpay.transactions
    FOR VALUES FROM ('2024-03-01') TO ('2024-04-01');
```

## Connection Pooling

All services connect via PgBouncer in transaction-mode pooling. The pool is sized at 20 connections per service instance. Direct connections to PostgreSQL are reserved for migrations and DBA access.
