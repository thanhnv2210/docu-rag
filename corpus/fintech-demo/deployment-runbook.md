---
title: Deployment Runbook
version: "1.6"
tags: [deployment, kubernetes, runbook, operations]
---

# Deployment Runbook

This runbook covers deploying, configuring, and operating the FinPay platform on Kubernetes. It is intended for the platform engineering and on-call operations teams.

## Prerequisites

- `kubectl` configured for the target cluster
- `helm` 3.x installed
- AWS CLI with credentials for ECR image push
- Access to AWS Secrets Manager (read-only sufficient for deploy; write access for secret rotation)

---

## Environment Configuration

FinPay services are configured entirely through environment variables. Values are injected at runtime from AWS Secrets Manager via the External Secrets Operator.

### Core Variables (all services)

| Variable | Description |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string (PgBouncer endpoint) |
| `KAFKA_BOOTSTRAP_SERVERS` | Comma-separated Kafka broker addresses |
| `REDIS_URL` | Redis connection URL |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARN`, `ERROR` |
| `ENVIRONMENT` | `dev`, `staging`, `production` |

### Transaction Processor Variables

| Variable | Description |
|---|---|
| `ARCAPAY_BASE_URL` | ArcaPay API base URL |
| `ARCAPAY_CLIENT_ID` | OAuth client ID |
| `ARCAPAY_CLIENT_SECRET` | OAuth client secret (from Secrets Manager) |
| `ARCAPAY_WEBHOOK_SECRET` | HMAC secret for validating ArcaPay webhooks |
| `ARCAPAY_TIMEOUT_MS` | Per-request timeout override (default: 8000) |
| `SWIFTHUB_BASE_URL` | SwiftHub API base URL |
| `SWIFTHUB_API_KEY` | SwiftHub API key (from Secrets Manager) |
| `SWIFTHUB_TLS_CERT_PATH` | Path to mounted client certificate for mTLS |
| `SWIFTHUB_TIMEOUT_MS` | Per-request timeout override (default: 15000) |
| `LOCALPAY_BASE_URL` | LocalPay API base URL |
| `LOCALPAY_KEY_PATH` | Path to RS256 private key for JWT signing |
| `LOCALPAY_TIMEOUT_MS` | Per-request timeout override (default: 5000) |

### Retry and Circuit Breaker Variables

| Variable | Default | Description |
|---|---|---|
| `RETRY_MAX_ATTEMPTS` | `3` | Default max retry attempts (overridden per hub) |
| `RETRY_INITIAL_DELAY_MS` | `500` | Initial backoff delay |
| `RETRY_MULTIPLIER` | `2.0` | Exponential multiplier |
| `CB_FAILURE_THRESHOLD` | `5` | Failures before OPEN |
| `CB_WINDOW_SECONDS` | `60` | Sliding window for failure counting |
| `CB_OPEN_DURATION_S` | `60` | How long to stay OPEN before probing |

---

## Kubernetes Deployment

### Namespace

All FinPay services run in the `finpay` namespace:

```bash
kubectl create namespace finpay
```

### Deploying a Service

Each service has a Helm chart under `charts/<service-name>/`. A full platform deploy:

```bash
# Deploy all services
helm upgrade --install finpay-platform charts/finpay-platform \
  --namespace finpay \
  --values charts/finpay-platform/values.production.yaml \
  --set image.tag=$(git rev-parse --short HEAD)
```

### Rolling Update

Kubernetes handles rolling updates automatically when a new image tag is pushed. Each service is configured with:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 1
    maxUnavailable: 0
```

This ensures zero-downtime deploys: a new pod must pass its readiness probe before the old pod is terminated.

### Readiness and Liveness Probes

```yaml
readinessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 10
  periodSeconds: 5
  failureThreshold: 3

livenessProbe:
  httpGet:
    path: /health
    port: 8080
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 5
```

The `/health` endpoint returns HTTP 200 only when the service has established DB and Kafka connections. A failing readiness probe removes the pod from the load balancer without killing it; a failing liveness probe triggers a pod restart.

---

## Database Migrations

Migrations are managed with Flyway and run as a Kubernetes Job before the main deploy:

```bash
kubectl apply -f k8s/jobs/migrate.yaml -n finpay
kubectl wait --for=condition=complete job/finpay-migrate -n finpay --timeout=120s
```

The migration job uses the same `DATABASE_URL` as the services. Migrations are forward-only. Roll forward rather than rolling back schema changes.

---

## Rollback Procedure

### Application Rollback (< 5 minutes)

If a new deploy causes errors, roll back to the previous Helm release:

```bash
helm rollback finpay-platform -n finpay
```

This restores the previous image tags without changing database state.

### Full Rollback with DB Snapshot

If a migration introduced data corruption:

1. Notify stakeholders and open an incident.
2. Scale down all services: `kubectl scale deploy --all --replicas=0 -n finpay`
3. Restore the RDS snapshot from just before the migration ran (RDS Console → Restore to point in time).
4. Update `DATABASE_URL` to point to the restored instance.
5. Roll back the Helm release.
6. Scale services back up.
7. Validate using smoke tests.

> **Warning:** Full rollback loses any transactions processed after the snapshot time. Reconciliation must be run against hub records to recover any missing transaction state.

---

## Health Checks and Monitoring

### Key Metrics (Prometheus)

| Metric | Alert Threshold |
|---|---|
| `finpay_tx_failed_total` | > 10/min sustained for 5 min |
| `finpay_hub_circuit_open` | Any hub OPEN for > 2 min |
| `finpay_retry_dlq_total` | > 5 new DLQ entries in 10 min |
| `finpay_api_latency_p99` | > 3 s for 5 min |
| `db_pool_wait_time_ms` | > 500 ms avg |

Dashboards are available in Grafana under the **FinPay Operations** folder.

### Log Queries (CloudWatch)

**Failed transactions in the last hour:**
```
fields @timestamp, transaction_id, hub_id, failure_reason
| filter event_type = "transaction.failed"
| sort @timestamp desc
| limit 50
```

**Circuit breaker state changes:**
```
fields @timestamp, hub_id, previous_state, new_state
| filter event_type = "circuit_breaker.state_changed"
| sort @timestamp desc
```

---

## Scaling

### Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: transaction-processor-hpa
spec:
  scaleTargetRef:
    kind: Deployment
    name: transaction-processor
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
```

The Payment Gateway and Transaction Processor autoscale between 2 and 10 replicas. The Reconciliation Service and Reporting Engine run as single-replica CronJobs (no HPA needed).

---

## On-Call Runbook

### Hub OPEN alert

1. Check Grafana for the affected hub's error rate and latency.
2. Query `SELECT * FROM finpay.payment_hubs WHERE id = '<hub_id>'` to confirm circuit state.
3. Check the hub's status page (URL in the `metadata` column).
4. If the hub is recovering, the circuit will self-heal via the HALF_OPEN probe within `open_duration_s`.
5. To manually reset the circuit: `UPDATE finpay.payment_hubs SET circuit_state='CLOSED', failure_count=0 WHERE id='<hub_id>';` — use with caution.

### DLQ spike alert

1. Consume from `finpay.dlq` to inspect failure reasons.
2. If failures are transient (hub outage now resolved), replay via the ops tool: `finpay-ops dlq replay --topic finpay.transactions --limit 100`
3. If failures are data issues, discard after investigation.
