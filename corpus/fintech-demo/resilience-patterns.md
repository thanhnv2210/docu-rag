---
title: Resilience Patterns
version: "2.2"
tags: [resilience, retry, circuit-breaker, idempotency]
---

# Resilience Patterns

FinPay operates in a distributed environment where any downstream dependency — payment hubs, databases, or external APIs — can fail or degrade at any time. This document describes the resilience patterns applied across the platform.

## Retry Policy

### Base Retry Configuration

All hub-facing operations use exponential backoff with jitter as the default retry strategy:

| Parameter | Default Value |
|---|---|
| `initial_delay_ms` | 500 |
| `multiplier` | 2.0 |
| `max_delay_ms` | 30,000 |
| `max_attempts` | 3 |
| `jitter_factor` | 0.2 |

The effective delay for attempt `n` is:

```
delay = min(initial_delay * multiplier^(n-1), max_delay) * (1 + jitter_factor * random(-1, 1))
```

For 3 attempts with default settings, the approximate delay sequence is:
- Attempt 2: ~500 ms
- Attempt 3: ~1,000 ms

### Per-Hub Retry Overrides

Each hub adapter can override the base retry configuration. The overrides are documented in `payment-hub-integrations.md` and summarised here:

| Hub | `max_attempts` | `initial_delay_ms` | Notes |
|---|---|---|---|
| ArcaPay | 4 | 500 | `RATE_LIMITED` adds mandatory 1,000 ms before first retry |
| SwiftHub | 3 | 5,000 | `CORRESPONDENT_UNAVAILABLE` uses slow schedule: 5 min, 15 min, 60 min |
| LocalPay | 5 | 500 | Higher attempt count to handle wallet provider peak-hour timeouts |

### Retryable vs. Non-Retryable Errors

Only transient errors are retried. Terminal failure codes (e.g. `INVALID_ACCOUNT`, `SANCTION_BLOCKED`, `WALLET_NOT_FOUND`) cause an immediate transition to `FAILED` state with no retry. The full list of terminal codes per hub is in `payment-hub-integrations.md`.

The Transaction Processor evaluates the hub's response code against a `RetryPolicy.is_retryable(code)` method that consults the hub-specific error code mapping before deciding whether to schedule a retry or commit the `FAILED` state.

### Retry Scheduling

Retries are not executed in-process. Instead, the Transaction Processor:
1. Persists the transaction with `state = SUBMITTED` and increments `retry_count`.
2. Publishes a `retry.scheduled` event to the `finpay.retry` Kafka topic with the target attempt timestamp.
3. The Retry Worker service consumes this topic and re-invokes the hub adapter at the scheduled time.

This decouples retry scheduling from the hot path and survives service restarts.

---

## Circuit Breaker

The circuit breaker prevents cascading failures when a hub experiences sustained outages. FinPay uses a per-hub circuit breaker with three states:

### States

| State | Behaviour |
|---|---|
| `CLOSED` | Normal operation; all requests pass through |
| `OPEN` | Hub is unhealthy; requests fail immediately without calling the hub |
| `HALF_OPEN` | Probe phase; a single test request is allowed through |

### State Transitions

```
CLOSED ──(failure threshold exceeded)──▶ OPEN
  ▲                                        │
  │                                        │ (timeout elapsed: open_duration_s)
  │                                        ▼
  └────(probe succeeds)─────────── HALF_OPEN
             (probe fails → back to OPEN)
```

### Configuration

| Parameter | Default | ArcaPay | SwiftHub | LocalPay |
|---|---|---|---|---|
| `failure_threshold` | 5 failures | 5 | 3 | 5 |
| `window_seconds` | 60 s | 60 s | 120 s | 30 s |
| `open_duration_s` | 60 s | 60 s | 300 s | 30 s |
| `probe_timeout_s` | 10 s | 8 s | 15 s | 5 s |

LocalPay has a lower `window_seconds` (30 s) and `open_duration_s` (30 s) because its higher throughput means failures are detected and recovered more quickly.

### Circuit Breaker State Store

Circuit breaker state is stored in PostgreSQL (`finpay.payment_hubs.circuit_state`). Using the database rather than in-memory state ensures that all Transaction Processor instances share a consistent view of hub health. State transitions are serialised with `SELECT ... FOR UPDATE`.

### Interaction with Routing

The Payment Router queries the circuit breaker state before selecting a hub. If the preferred hub is `OPEN`, the router falls back to the next eligible hub for the currency pair. If all eligible hubs are `OPEN`, the transaction moves to `FAILED` with reason `NO_HUB_AVAILABLE`.

---

## Idempotency

Every write operation in FinPay is idempotent. This allows clients and internal services to safely retry any operation without risk of duplicate effects.

### External API Idempotency

Merchants include an `idempotency_key` in `POST /v1/transactions` requests (see `api-reference.md`). The Payment Gateway:
1. Checks the key against a Redis deduplication store (TTL: 24 h).
2. If found, returns the original response immediately without re-processing.
3. If not found, processes the request and stores the response keyed by `(merchant_id, idempotency_key)`.

### Hub Submission Idempotency

When submitting a transfer to a hub, the Transaction Processor includes the FinPay transaction's `idempotency_key` in the hub request (mapped to the hub's own idempotency field). Each hub honours this key for at least 24 hours. If a submission is retried due to a timeout, the hub recognises the duplicate and returns the original result rather than executing a second transfer.

### Database Write Idempotency

All state transition UPDATE statements are conditioned on the current `version` column value (optimistic locking). If two processes attempt the same state transition concurrently, only one succeeds; the other detects a version mismatch and aborts.

---

## Dead Letter Queue

Messages that cannot be processed after all retry attempts are routed to the dead letter queue (DLQ) on the `finpay.dlq` Kafka topic.

### DLQ Entry Schema

```json
{
  "original_topic": "finpay.transactions",
  "original_offset": 4821,
  "transaction_id": "a3f2c1b0-...",
  "failure_reason": "MAX_RETRIES_EXCEEDED",
  "last_error": "ArcaPay returned HUB_UNAVAILABLE after 4 attempts",
  "first_failed_at": "2024-03-15T10:22:00Z",
  "last_failed_at": "2024-03-15T10:28:00Z",
  "payload": { ... }
}
```

### DLQ Processing

DLQ entries trigger a PagerDuty alert to the on-call engineer. The ops team can:
1. **Replay** — re-publish the message to the original topic once the underlying issue is resolved.
2. **Discard** — mark as resolved with a reason (e.g. test transaction, known data issue).
3. **Escalate** — route to manual payment processing if the transaction cannot be retried.

A weekly DLQ report is generated by the Reporting Engine summarising DLQ volume by hub, failure reason, and merchant.

---

## Timeout Strategy

| Operation | Timeout |
|---|---|
| ArcaPay submission | 8 s (connect: 3 s, read: 5 s) |
| SwiftHub submission | 15 s (connect: 5 s, read: 10 s) |
| LocalPay submission | 5 s (connect: 2 s, read: 3 s) |
| Hub status query | 5 s |
| DB write (transactional) | 3 s |
| Redis read | 500 ms |

Timeouts are configured per-hub in environment variables and can be adjusted without code changes (see `deployment-runbook.md`).
