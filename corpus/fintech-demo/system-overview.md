---
title: FinPay Platform — System Overview
version: "2.4"
tags: [architecture, overview, c4]
---

# FinPay Platform — System Overview

FinPay is a cloud-native payment processing platform that routes domestic and international money transfers through multiple payment hub integrations. It is built as a set of loosely coupled microservices communicating over an event bus, with synchronous REST APIs exposed to external clients.

## Architecture Principles

- **Event-driven by default** — state transitions emit domain events onto the Kafka bus; downstream services react asynchronously.
- **Idempotent operations** — every write operation accepts an idempotency key so clients can safely retry without risk of duplicate transactions.
- **Hub-agnostic routing** — the Payment Router selects the optimal hub at runtime based on currency pair, amount, and hub availability.
- **Observability first** — every service emits structured JSON logs, Prometheus metrics, and OpenTelemetry traces.

## Microservices

FinPay comprises eight core microservices:

### Payment Gateway

The public-facing API layer. Accepts inbound REST requests from merchant integrations and mobile clients. Responsible for:
- Request authentication (API key + HMAC signature verification)
- Schema validation and rate limiting (1,000 req/min per merchant)
- Idempotency key deduplication (Redis-backed, 24-hour TTL)
- Forwarding validated requests to the Transaction Processor

### Transaction Processor

The orchestration core. Owns the transaction lifecycle state machine (see `transaction-state-machine.md`). Responsibilities:
- Persisting transactions to PostgreSQL with optimistic locking
- Selecting the routing hub via the Payment Router
- Publishing `transaction.created`, `transaction.submitted`, `transaction.settled`, and `transaction.failed` events to Kafka
- Coordinating rollback and compensation flows on failure

### Payment Router

Evaluates routing rules at runtime to select the best payment hub for each transaction. Rules are stored in PostgreSQL and evaluated in priority order:
1. Currency pair support
2. Hub availability (pulled from the Circuit Breaker state store)
3. Cost (FX markup + fee)
4. Throughput quota

### FX Engine

Provides real-time and mid-market FX rates for all supported currency pairs. Rates are sourced from an external market data feed and cached in Redis with a 30-second TTL. The FX Engine applies the platform's configurable margin on top of mid-market rates before serving them to the Transaction Processor.

### Notification Service

Consumes `transaction.*` events from Kafka and delivers outbound webhooks to registered merchant endpoints. Features:
- Exponential backoff retry (up to 5 attempts over 2 hours)
- Webhook signature (HMAC-SHA256) for receiver verification
- Dead-letter queue for permanently failed deliveries

### Audit Service

Immutable append-only ledger of all state transitions. Consumes all `transaction.*` events and writes to `transaction_audit` table (see `database-schema.md`). Used for compliance reporting and dispute resolution.

### Reporting Engine

Generates merchant settlement reports on a configurable schedule (daily by default). Reads from a PostgreSQL read replica to avoid impacting transactional workloads. Outputs are stored in object storage (S3-compatible) and linked in the merchant portal.

### Reconciliation Service

Runs nightly to compare FinPay internal records against hub settlement files. Discrepancies are flagged in the `reconciliation_exceptions` table and surfaced to the ops team via PagerDuty alerts.

## Payment Hubs

FinPay integrates with three payment hubs. Each hub is wrapped by an adapter that normalises the hub's proprietary API into FinPay's internal `HubAdapter` interface (see `payment-hub-integrations.md`).

| Hub | Coverage | Typical Latency | Supported Currencies |
|---|---|---|---|
| **ArcaPay** | Domestic transfers | 200–800 ms | SGD, MYR, IDR, PHP, THB |
| **SwiftHub** | International wires | 1–3 s | 50+ currencies |
| **LocalPay** | Local e-wallets | 100–400 ms | SGD, MYR |

## Event Bus

FinPay uses Apache Kafka with three topic groups:

| Topic | Producers | Consumers |
|---|---|---|
| `finpay.transactions` | Transaction Processor | Audit Service, Notification Service, Reconciliation Service |
| `finpay.fx` | FX Engine | Transaction Processor, Reporting Engine |
| `finpay.dlq` | Notification Service | Ops alerting pipeline |

Events are serialised as JSON with Avro schema enforcement. Schema registry is hosted on Confluent Cloud.

## Infrastructure

- **Compute:** Kubernetes (EKS), 3-node cluster, `t3.medium` workers
- **Database:** PostgreSQL 16 on RDS, Multi-AZ, automated daily snapshots
- **Cache:** Redis 7 on ElastiCache, single node with replica
- **Object storage:** S3-compatible (MinIO in staging, AWS S3 in production)
- **Service mesh:** Istio — mTLS between all services, traffic policies, circuit breaking at the mesh layer
- **Secrets:** AWS Secrets Manager, rotated every 90 days

## Deployment Environments

| Environment | Purpose | Data |
|---|---|---|
| `dev` | Developer sandbox | Synthetic data only |
| `staging` | Pre-release validation | Anonymised production clone |
| `production` | Live traffic | Real transactions |

For deployment procedures, rollback steps, and environment-specific configuration, see `deployment-runbook.md`.
