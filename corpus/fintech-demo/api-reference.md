---
title: FinPay API Reference
version: "1.5"
tags: [api, rest, reference]
---

# FinPay API Reference

The FinPay REST API allows merchants to initiate transfers, query transaction status, cancel pending transactions, and retrieve FX rates. All requests must be authenticated using an API key.

## Authentication

Include the API key in the `Authorization` header:

```
Authorization: Bearer <api_key>
```

Every request must also include a request signature in the `X-FinPay-Signature` header:

```
X-FinPay-Signature: t=<unix_timestamp>,v1=<hmac_sha256_hex>
```

The signature is computed as `HMAC-SHA256(secret, "t=<timestamp>.<request_body>")`. Requests with a timestamp older than 300 seconds are rejected.

## Base URL

```
https://api.finpay.example/v1
```

All responses are JSON. Errors follow the RFC 7807 Problem Details format.

---

## POST /v1/transactions

Initiate a new transfer.

### Request Body

```json
{
  "idempotency_key": "ord-20240315-9901",
  "sender": {
    "account_number": "1234567890",
    "bank_code": "DBS",
    "name": "Acme Corp"
  },
  "recipient": {
    "account_number": "0987654321",
    "bank_code": "OCBC",
    "name": "Jane Doe"
  },
  "amount": "250.00",
  "currency": "SGD",
  "purpose_code": "P0301",
  "metadata": {
    "order_id": "ord-9901",
    "customer_ref": "cust-42"
  }
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `idempotency_key` | string | Yes | Unique key per merchant; safe to retry with the same key |
| `sender` | object | Yes | Sender account details |
| `recipient` | object | Yes | Recipient account details |
| `amount` | string | Yes | Decimal amount (use string to avoid float rounding) |
| `currency` | string | Yes | ISO 4217 3-letter currency code |
| `purpose_code` | string | No | Payment purpose code (required for some corridors) |
| `metadata` | object | No | Arbitrary key-value pairs passed through to webhooks |

### Response 201 — Created

```json
{
  "transaction_id": "a3f2c1b0-...",
  "state": "PENDING",
  "created_at": "2024-03-15T10:22:00Z"
}
```

### Response 200 — Duplicate (idempotent)

Returned when the same `idempotency_key` is submitted again. Returns the original transaction.

### Response 422 — Validation Error

```json
{
  "type": "https://finpay.example/errors/validation",
  "title": "Validation failed",
  "status": 422,
  "errors": [
    {"field": "currency", "message": "Currency THB is not supported for this merchant account"}
  ]
}
```

### Response 429 — Rate Limited

```json
{
  "type": "https://finpay.example/errors/rate-limited",
  "title": "Rate limit exceeded",
  "status": 429,
  "retry_after": 1
}
```

---

## GET /v1/transactions/{transaction_id}

Retrieve the current state of a transaction.

### Path Parameters

| Parameter | Type | Description |
|---|---|---|
| `transaction_id` | UUID | The transaction ID returned by POST /v1/transactions |

### Response 200

```json
{
  "transaction_id": "a3f2c1b0-...",
  "state": "CONFIRMED",
  "hub_id": "arcapay",
  "hub_reference": "ARC-2024-00123",
  "amount": "250.00",
  "currency": "SGD",
  "fx_rate": null,
  "failure_reason": null,
  "created_at": "2024-03-15T10:22:00Z",
  "submitted_at": "2024-03-15T10:22:01Z",
  "confirmed_at": "2024-03-15T10:22:02Z",
  "settled_at": null,
  "metadata": {"order_id": "ord-9901"}
}
```

### Response 404

```json
{
  "type": "https://finpay.example/errors/not-found",
  "title": "Transaction not found",
  "status": 404
}
```

---

## POST /v1/transactions/{transaction_id}/cancel

Cancel a transaction. Only transactions in `PENDING` state can be cancelled. Transactions already `SUBMITTED` or beyond cannot be cancelled via the API (contact support for reversal of settled transactions).

### Request Body

```json
{
  "reason": "Customer requested cancellation"
}
```

### Response 200

```json
{
  "transaction_id": "a3f2c1b0-...",
  "state": "CANCELLED",
  "cancelled_at": "2024-03-15T10:23:00Z"
}
```

### Response 409 — Conflict

Returned when the transaction is not in a cancellable state.

```json
{
  "type": "https://finpay.example/errors/conflict",
  "title": "Transaction cannot be cancelled",
  "status": 409,
  "detail": "Transaction is in state SUBMITTED and cannot be cancelled. Contact support for reversal options."
}
```

---

## GET /v1/fx/rates

Retrieve current FX rates for one or more currency pairs.

### Query Parameters

| Parameter | Type | Required | Description |
|---|---|---|---|
| `base` | string | Yes | Base currency (ISO 4217) |
| `quote` | string | Yes | Comma-separated quote currencies |

### Example Request

```
GET /v1/fx/rates?base=SGD&quote=MYR,USD,EUR
```

### Response 200

```json
{
  "base": "SGD",
  "rates": {
    "MYR": {"mid": 3.4521, "captured_at": "2024-03-15T10:21:45Z"},
    "USD": {"mid": 0.7432, "captured_at": "2024-03-15T10:21:45Z"},
    "EUR": {"mid": 0.6891, "captured_at": "2024-03-15T10:21:45Z"}
  }
}
```

Rates are sourced from the FX Engine's Redis cache and may be up to 30 seconds stale.

---

## GET /v1/transactions

List transactions for the authenticated merchant with pagination.

### Query Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state` | string | — | Filter by state (e.g. `SETTLED`) |
| `from` | ISO 8601 | — | Start of date range (inclusive) |
| `to` | ISO 8601 | — | End of date range (exclusive) |
| `limit` | integer | 20 | Max results per page (max: 100) |
| `cursor` | string | — | Pagination cursor from previous response |

### Response 200

```json
{
  "data": [
    {"transaction_id": "...", "state": "SETTLED", "amount": "250.00", "currency": "SGD", "created_at": "..."},
    {"transaction_id": "...", "state": "FAILED", "amount": "1000.00", "currency": "MYR", "created_at": "..."}
  ],
  "next_cursor": "eyJjcmVhdGVkX2F0IjoiMjAyNC0wM...",
  "has_more": true
}
```

---

## Webhooks

FinPay delivers real-time event notifications to the `webhook_url` registered for the merchant.

### Event Payload

```json
{
  "event_id": "evt-0001-abc",
  "event_type": "transaction.settled",
  "transaction_id": "a3f2c1b0-...",
  "state": "SETTLED",
  "amount": "250.00",
  "currency": "SGD",
  "settled_at": "2024-03-15T10:25:00Z",
  "metadata": {"order_id": "ord-9901"},
  "created_at": "2024-03-15T10:25:01Z"
}
```

### Webhook Verification

Verify the `X-FinPay-Webhook-Signature` header:

```python
import hmac, hashlib

expected = hmac.new(
    webhook_secret.encode(),
    msg=request_body,
    digestmod=hashlib.sha256
).hexdigest()

assert hmac.compare_digest(expected, received_signature)
```

Webhooks are retried up to 5 times with exponential backoff. Return HTTP 200 to acknowledge receipt. Any non-200 response triggers a retry.
