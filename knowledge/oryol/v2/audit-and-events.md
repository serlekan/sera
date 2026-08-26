# Oryol Audit, Outbox & Event Ingestion Architecture v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Reliable Outbox Dispatching, Lease Locks, Non-Cascading Audit & System Actors

---

## 1. Distinct Roles: Audit vs. Outbox vs. Observability

| Subsystem | Primary Purpose | Storage | Cascade on Org Delete | Mutability |
|---|---|---|---|---|
| **Compliance Audit Log** (`audit_events`) | Forensics, legal compliance, "Who did what and when?" | Cloudflare D1 + R2 cold archive | **NEVER** (Preserved under legal retention) | **Strictly Append-Only** |
| **Transactional Outbox** (`outbox_events`) | Reliable asynchronous integration & domain state broadcasts | Cloudflare D1 (Temporary buffer) | **NEVER cascade pending tombstones** (drained to completion) | Mutated by Dispatcher (Lease/Status) |
| **Observability & Metrics** | System health, query latency, error rates, token usage | Cloudflare Analytics / Tail Workers | Transient TTL | Aggregated metrics |

---

## 2. Canonical Transactional Outbox Schema (`outbox_events`)

```sql
CREATE TABLE outbox_events (
    event_id TEXT PRIMARY KEY,                 -- evt_<ulid>
    schema_version INTEGER NOT NULL DEFAULT 1, -- Contract version
    organization_id TEXT NOT NULL,             -- org_<ulid> (No cascade delete)
    producer TEXT NOT NULL,                    -- 'oryol-core', 'oryol-mail', 'oryol-crm'
    aggregate_type TEXT NOT NULL,              -- 'organization', 'mailbox', 'message', 'deal'
    aggregate_id TEXT NOT NULL,                -- Target business object ID
    aggregate_version INTEGER NOT NULL,        -- Monotonic entity version for ordering
    event_type TEXT NOT NULL,                  -- 'mail.message.received', 'core.org.deleted'
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    actor_context TEXT NOT NULL,               -- JSON: { principal_id, ip, client }
    correlation_id TEXT NOT NULL,              -- Distributed trace ULID
    causation_id TEXT,                         -- Causation trace ULID
    idempotency_key TEXT UNIQUE NOT NULL,      -- Dedup key (e.g. 'evt_<agg_id>_<ver>')
    payload TEXT NOT NULL,                     -- Validated JSON payload
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'published', 'dead_letter', 'retry')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,                          -- Worker instance ID holding claim
    lease_expires_at DATETIME,                 -- Lease expiration timestamp
    last_error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME
);

CREATE INDEX idx_outbox_dispatch ON outbox_events(status, next_attempt_at)
WHERE status IN ('pending', 'retry');
```

---

## 3. Dispatcher Claim Algorithm, Ordering & Retries

### 3.1 Worker Atomic Lease Claim
An outbox dispatcher worker queries for eligible events using an atomic lease update:
```sql
UPDATE outbox_events
SET status = 'processing',
    lease_owner = :worker_id,
    lease_expires_at = datetime('now', '+30 seconds'),
    attempt_count = attempt_count + 1
WHERE event_id IN (
    SELECT event_id FROM outbox_events
    WHERE status IN ('pending', 'retry')
      AND next_attempt_at <= datetime('now')
      AND (lease_owner IS NULL OR lease_expires_at <= datetime('now'))
    ORDER BY occurred_at ASC
    LIMIT 50
);
```
Only the worker holding `lease_owner` prior to `lease_expires_at` may publish to Cloudflare Queues and mark `status = 'published'`.

### 3.2 Retry, Backoff & Dead Letter Queue (DLQ)
- **Backoff Formula**: `delay = min(3600, 2^(attempt_count) * 1.5 + jitter_ms)`
- **Max Attempts**: If `attempt_count >= 10`, event transitions to `status = 'dead_letter'`.
- **Replay Authorization**: Replay of dead-lettered events is restricted to privileged platform operators, emits an audit event (`core.outbox.replay_initiated`), and retains the original `event_id` and `idempotency_key`.

### 3.3 Aggregate Ordering via `aggregate_version`
- Cloudflare Queues transport is strictly **at-least-once**.
- Ordering guarantees are enforced per-aggregate using `aggregate_version`. Downstream consumers reject or buffer events with impossible version gaps (`expected_version > received_version`).

---

## 4. Consumer Inbox Deduplication Schema (`inbox_events`)

```sql
CREATE TABLE inbox_events (
    id TEXT PRIMARY KEY,                       -- in_<ulid>
    consumer_name TEXT NOT NULL,               -- e.g. 'oryol-crm-sync'
    event_id TEXT NOT NULL,                    -- evt_<ulid> from producer
    organization_id TEXT NOT NULL,             -- org_<ulid>
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    aggregate_version INTEGER NOT NULL,
    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'completed' CHECK(status IN ('completed', 'failed')),
    UNIQUE(consumer_name, event_id)           -- Provides deduplication for effectively-once application semantics
);
```

---

## 5. Immutable Compliance Audit Schema (`audit_events`)

```sql
CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,                       -- aud_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid> (NEVER cascade-deleted)
    actor_type TEXT NOT NULL CHECK(actor_type IN ('human', 'service', 'system', 'ai')),
    actor_principal_id TEXT,                   -- NULL for automated system-internal events
    actor_membership_id TEXT,                  -- NULL for service/system actors
    actor_metadata TEXT NOT NULL,              -- JSON: { display_name, service_name, system_worker_id }
    action TEXT NOT NULL,                      -- Canonical 3-part name e.g. 'core.members.invite'
    target_type TEXT NOT NULL,                 -- 'mailbox', 'organization', 'api_credential'
    target_id TEXT NOT NULL,
    event_metadata TEXT NOT NULL,              -- Sanitized JSON before/after state
    ip_address TEXT,
    user_agent TEXT,
    occurred_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Audit Invariants & Redaction:
1. **Append-Only Engine Enforcement**: D1 tables reject `UPDATE` and `DELETE` queries on `audit_events`. Corrections are recorded as new compensating audit events.
2. **System Actor Handling**: `actor_principal_id` is nullable for system actors; structured `actor_metadata` captures worker identity and job run details.
3. **Redaction / Pseudonymization**: Historical audit action meaning is never altered. User PII is pseudonymized (`Deleted User <prn_id>`) upon verified erasure requests without deleting audit records.
