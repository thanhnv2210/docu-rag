---
title: Payment Hub Integrations
version: "3.1"
tags: [integrations, hubs, adapters]
---

# Payment Hub Integrations

FinPay integrates with three external payment hubs. Each hub has a dedicated adapter that implements the internal `HubAdapter` interface, normalising hub-specific request/response formats, error codes, and retry semantics into a unified model.

## HubAdapter Interface

```python
class HubAdapter(ABC):
    @abstractmethod
    async def submit(self, transfer: TransferRequest) -> HubResponse: ...

    @abstractmethod
    async def query_status(self, hub_reference: str) -> HubStatus: ...

    @abstractmethod
    async def cancel(self, hub_reference: str) -> bool: ...
```

All adapters are registered in the `HubRegistry` and selected by the Payment Router at runtime.

---

## ArcaPay

ArcaPay handles domestic transfers within Southeast Asia. It is the preferred hub for SGD, MYR, IDR, PHP, and THB transfers due to its low latency and competitive fee structure.

### Connection Details

- **Protocol:** REST over HTTPS
- **Auth:** OAuth 2.0 client credentials (token TTL: 3,600 s; auto-refreshed)
- **Base URL (production):** configured via `ARCAPAY_BASE_URL` env var
- **Timeout:** 8 seconds (connect: 3 s, read: 5 s)
- **Rate limit:** 500 req/s per credential set

### Request Format

```json
{
  "reference": "FP-20240315-0001",
  "idempotency_key": "idem-abc123",
  "sender": {
    "account_number": "...",
    "bank_code": "...",
    "name": "..."
  },
  "recipient": {
    "account_number": "...",
    "bank_code": "...",
    "name": "..."
  },
  "amount": "150.00",
  "currency": "SGD",
  "purpose_code": "P0301"
}
```

### Response Codes

| Code | Category | Meaning | Retry? |
|---|---|---|---|
| `SUCCESS` | Terminal-success | Transfer accepted | No |
| `PROCESSING` | Transient | Hub processing, poll for status | Yes (poll) |
| `TIMEOUT` | Transient | Hub did not process in time | Yes (exponential) |
| `RATE_LIMITED` | Transient | Exceeded rate limit | Yes (after 1 s backoff) |
| `INVALID_ACCOUNT` | Terminal-failure | Recipient account not found | No |
| `ACCOUNT_CLOSED` | Terminal-failure | Recipient account closed | No |
| `INSUFFICIENT_FUNDS` | Terminal-failure | Sender funds insufficient | No |
| `BLACKLISTED_RECIPIENT` | Terminal-failure | Compliance block | No |
| `INVALID_PURPOSE_CODE` | Terminal-failure | Purpose code not accepted | No |
| `HUB_UNAVAILABLE` | Transient | ArcaPay system maintenance | Yes (exponential, circuit breaker) |

### Webhook Notifications

ArcaPay sends asynchronous status updates to `POST /internal/webhooks/arcapay`. The payload is signed with HMAC-SHA256 using the shared secret configured in `ARCAPAY_WEBHOOK_SECRET`. FinPay validates the signature before processing.

### Retry Policy for ArcaPay

Transient errors (`TIMEOUT`, `RATE_LIMITED`, `HUB_UNAVAILABLE`) are retried using the platform's exponential backoff policy (see `resilience-patterns.md`). The ArcaPay adapter overrides the base timeout to **8 seconds** and sets `max_attempts = 4`. `RATE_LIMITED` responses add a mandatory 1-second initial delay before the first retry.

---

## SwiftHub

SwiftHub handles international wire transfers across 50+ currencies. It is the hub of last resort for currency pairs not covered by ArcaPay or LocalPay.

### Connection Details

- **Protocol:** REST over HTTPS with mutual TLS (client cert required)
- **Auth:** API key in `X-SwiftHub-Key` header
- **Base URL (production):** configured via `SWIFTHUB_BASE_URL` env var
- **Timeout:** 15 seconds (international wires have higher processing latency)
- **Rate limit:** 100 req/s

### Response Codes

| Code | Category | Meaning | Retry? |
|---|---|---|---|
| `ACCEPTED` | Terminal-success | Wire accepted for processing | No |
| `PENDING_COMPLIANCE` | Transient | Under compliance review (up to 24 h) | Poll only |
| `GATEWAY_TIMEOUT` | Transient | SwiftHub upstream timed out | Yes (exponential) |
| `SANCTION_BLOCKED` | Terminal-failure | OFAC/UN sanctions match | No |
| `INVALID_BIC` | Terminal-failure | BIC/SWIFT code invalid or inactive | No |
| `CURRENCY_NOT_SUPPORTED` | Terminal-failure | Currency pair not available | No |
| `AMOUNT_EXCEEDS_LIMIT` | Terminal-failure | Transfer exceeds daily or per-tx limit | No |
| `CORRESPONDENT_UNAVAILABLE` | Transient | Correspondent bank offline | Yes (exponential, long backoff) |

### Retry Policy for SwiftHub

SwiftHub has higher latency and its transient failures often reflect prolonged upstream issues. The adapter sets `max_attempts = 3` with an extended initial backoff of **5 seconds**. `CORRESPONDENT_UNAVAILABLE` errors use a separate slow-retry schedule: 5 min, 15 min, 60 min.

---

## LocalPay

LocalPay specialises in local e-wallet transfers for Singapore and Malaysia. It offers the lowest latency of the three hubs (100–400 ms) and supports instant credit to supported wallet providers.

### Connection Details

- **Protocol:** REST over HTTPS
- **Auth:** JWT signed with RS256 (asymmetric key pair, rotated quarterly)
- **Base URL (production):** configured via `LOCALPAY_BASE_URL` env var
- **Timeout:** 5 seconds
- **Rate limit:** 2,000 req/s

### Supported Wallet Providers

| Provider | Country | Currency |
|---|---|---|
| GrabPay | SG, MY | SGD, MYR |
| PayNow | SG | SGD |
| DuitNow | MY | MYR |
| ShopeePay | SG, MY | SGD, MYR |

### Response Codes

| Code | Category | Meaning | Retry? |
|---|---|---|---|
| `CREDITED` | Terminal-success | Funds credited to wallet | No |
| `QUEUED` | Transient | Wallet provider processing | Yes (poll) |
| `WALLET_NOT_FOUND` | Terminal-failure | Wallet ID does not exist | No |
| `WALLET_SUSPENDED` | Terminal-failure | Wallet suspended by provider | No |
| `DAILY_LIMIT_EXCEEDED` | Terminal-failure | Recipient daily limit reached | No |
| `PROVIDER_TIMEOUT` | Transient | Wallet provider did not respond | Yes (exponential) |
| `PROVIDER_MAINTENANCE` | Transient | Scheduled maintenance window | Yes (long backoff) |

### Retry Policy for LocalPay

LocalPay's short timeout (5 s) means `PROVIDER_TIMEOUT` errors are more frequent during peak hours. The adapter sets `max_attempts = 5` with a 500 ms initial backoff. The circuit breaker threshold for LocalPay is set lower (5 failures in 30 s) due to its higher throughput.

---

## Hub Error Code Mapping

The following table maps hub-specific terminal failure codes to FinPay's internal `FailureReason` enum:

| FinPay FailureReason | ArcaPay | SwiftHub | LocalPay |
|---|---|---|---|
| `INVALID_RECIPIENT` | `INVALID_ACCOUNT`, `ACCOUNT_CLOSED` | `INVALID_BIC` | `WALLET_NOT_FOUND` |
| `COMPLIANCE_BLOCK` | `BLACKLISTED_RECIPIENT` | `SANCTION_BLOCKED` | — |
| `RECIPIENT_SUSPENDED` | — | — | `WALLET_SUSPENDED` |
| `LIMIT_EXCEEDED` | — | `AMOUNT_EXCEEDS_LIMIT` | `DAILY_LIMIT_EXCEEDED` |
| `CURRENCY_UNSUPPORTED` | — | `CURRENCY_NOT_SUPPORTED` | — |

This mapping is used by the Transaction Processor when constructing the failure record and the merchant-facing error response.
