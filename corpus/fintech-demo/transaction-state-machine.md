---
title: Transaction State Machine
version: "1.8"
tags: [transactions, state-machine, lifecycle]
---

# Transaction State Machine

Every FinPay transaction moves through a well-defined sequence of states. The Transaction Processor owns all state transitions and persists each change to PostgreSQL before publishing a Kafka event. No transition is considered complete until both the database write and the event publish succeed (outbox pattern).

## States

| State | Description |
|---|---|
| `PENDING` | Transaction created, idempotency key registered, awaiting hub submission |
| `SUBMITTED` | Sent to the payment hub; awaiting confirmation |
| `CONFIRMED` | Hub acknowledged receipt; funds reserved |
| `SETTLED` | Funds transferred and confirmed by hub settlement file |
| `FAILED` | Hub rejected or timed out; no funds moved |
| `CANCELLED` | Cancelled by the merchant before submission |
| `REVERSED` | Settled transaction subsequently reversed (chargeback or recall) |

## State Transition Diagram

```
                     ┌──────────┐
              ┌─────▶│ PENDING  │──────────────────────┐
              │      └────┬─────┘                      │
              │           │ submit to hub              │ cancel (merchant request,
              │           ▼                            │ before submission)
              │      ┌──────────┐                      │
              │      │SUBMITTED │                      ▼
              │      └────┬─────┘               ┌───────────┐
              │           │                     │ CANCELLED │
              │     ┌─────┴──────┐              └───────────┘
              │     │            │
              │  confirmed    failed / timeout
              │     │            │
              │     ▼            ▼
              │ ┌──────────┐ ┌────────┐
              │ │CONFIRMED │ │ FAILED │
              │ └────┬─────┘ └────────┘
              │      │ settlement file received
              │      ▼
              │ ┌──────────┐
              └─│ SETTLED  │
                └────┬─────┘
                     │ chargeback / recall
                     ▼
                ┌──────────┐
                │ REVERSED │
                └──────────┘
```

## Transition Rules

### PENDING → SUBMITTED

Triggered when the Transaction Processor selects a hub and dispatches the transfer request.

- **Pre-conditions:** Transaction in `PENDING` state; idempotency key not already processed; hub circuit breaker in `CLOSED` state.
- **Actions:**
  1. Set `submitted_at` timestamp.
  2. Record the selected hub in `transactions.hub_id`.
  3. Write `SUBMITTED` state to DB (optimistic lock on `version` column).
  4. Publish `transaction.submitted` event with hub correlation ID.
- **Failure mode:** If the hub call fails before a response is received, the transaction remains `PENDING` and the retry scheduler re-attempts according to the hub's retry policy (see `resilience-patterns.md`).

### SUBMITTED → CONFIRMED

Triggered by a synchronous hub response (HTTP 200 with `status: accepted`) or an asynchronous hub webhook.

- **Actions:**
  1. Record hub's reference ID in `transactions.hub_reference`.
  2. Persist `CONFIRMED` state.
  3. Publish `transaction.confirmed` event.

### SUBMITTED → FAILED

Triggered when the hub returns a terminal error code or when the submission timeout elapses.

Terminal hub error codes that cause immediate `FAILED` transition (no retry):
- ArcaPay: `INVALID_ACCOUNT`, `ACCOUNT_CLOSED`, `BLACKLISTED_RECIPIENT`
- SwiftHub: `SANCTION_BLOCKED`, `INVALID_BIC`, `CURRENCY_NOT_SUPPORTED`
- LocalPay: `WALLET_NOT_FOUND`, `WALLET_SUSPENDED`

Non-terminal error codes trigger the retry policy. See `resilience-patterns.md` for retry behaviour and `payment-hub-integrations.md` for the full error code list per hub.

### CONFIRMED → SETTLED

Triggered by the Reconciliation Service after matching the transaction to a hub settlement file entry.

- **Actions:**
  1. Set `settled_at` timestamp.
  2. Persist `SETTLED` state.
  3. Publish `transaction.settled` event (triggers merchant webhook via Notification Service).

### SETTLED → REVERSED

Triggered by an ops team action (chargeback dispute or hub-initiated recall).

- **Pre-conditions:** Separate authorisation required (role: `ops_admin`).
- **Actions:**
  1. Create a reversal record in `transaction_audit`.
  2. Initiate a reverse transfer via the original hub if within reversal window.
  3. Persist `REVERSED` state.
  4. Publish `transaction.reversed` event.

### PENDING → CANCELLED

Only possible before the transaction reaches `SUBMITTED`.

- **Pre-conditions:** Merchant API call with valid merchant credentials.
- **Actions:**
  1. Set `cancelled_at` timestamp; record `cancellation_reason`.
  2. Release idempotency key reservation.
  3. Publish `transaction.cancelled` event.

## Failure Handling

### Timeout During Submission

If no response is received from the hub within the configured timeout:

1. Transaction remains `SUBMITTED` (not moved to `FAILED` immediately).
2. A background reconciliation check runs after 5 minutes to query the hub for status.
3. If hub confirms receipt → transition to `CONFIRMED`.
4. If hub has no record of the transaction → transition to `FAILED` and unblock retry.
5. If hub is still unreachable → escalate to manual ops review after 3 reconciliation checks.

### Partial Failure Recovery

The Transaction Processor uses the **outbox pattern**: state changes are first written to the `transaction_outbox` table within the same database transaction as the state update. A separate outbox relay process reads pending outbox entries and publishes them to Kafka. This guarantees that no Kafka event is lost if the service crashes between the DB write and the Kafka publish.

### Idempotency on Retry

Retried hub submissions always include the original `idempotency_key` in the hub request. Each hub honours this key for at least 24 hours, preventing duplicate fund movements if the original request was received by the hub but the acknowledgement was lost in transit.

## Audit Trail

Every state transition is written to the `transaction_audit` table by the Audit Service. The audit record includes:
- Previous state and new state
- Timestamp (microsecond precision)
- Actor (service name or user ID for manual transitions)
- Hub response payload (sanitised)

See `database-schema.md` for the full schema.
