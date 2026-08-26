# Oryol Audit, Events & Outbox Architecture v2.1

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Reliable Outbox Lifecycle, Consumer Inbox Deduplication & Compliance Audit Guarantees

---

## 1. Distinct Roles: Audit vs. Outbox vs. Telemetry

| Stream | Primary Purpose | Retention & Mutability | Delivery Guarantees |
|---|---|---|---|
| **Audit Events** | "What happened?" Legal, security, and compliance forensics. | Append-only, legally protected, non-cascading. | Synchronous or transactional atomic insert. |
| **Domain Outbox Events** | "What should other systems know?" Cross-application data synchronization. | Transactional outbox table, cleared or archived post-publish. | At-least-once with idempotent consumer deduplication. |
| **Observability** | "How is the system behaving?" Metrics, distributed tracing, health. | Ephemeral rolling telemetry (30–90 days). | Best-effort sampling. |

---

## 2. Canonical Transactional Outbox Schema (`outbox_events`)

```sql
CREATE TABLE outbox_events (
    id TEXT PRIMARY KEY,                       -- out_<ulid>
    event_id TEXT UNIQUE NOT NULL,             -- evt_<ulid>
    schema_version INTEGER NOT NULL DEFAULT 2,
    organization_id TEXT NOT NULL,             -- org_<ulid>
    producer TEXT NOT NULL,                    -- e.g. 'oryol-mail-worker'
    aggregate_type TEXT NOT NULL,              -- 'mailbox', 'deal', 'member'
    aggregate_id TEXT NOT NULL,                -- 'mbx_123', 'dom_456'
    aggregate_version INTEGER NOT NULL,        -- Sequence counter for strict aggregate ordering
    event_type TEXT NOT NULL,                  -- 'mail.messages.created'
    occurred_at DATETIME NOT NULL,
    actor_context TEXT NOT NULL,               -- JSON: { principalId, membershipId, ip, client }
    correlation_id TEXT NOT NULL,              -- Distributed trace correlation ID
    causation_id TEXT,                         -- ID of command or event that caused this event
    idempotency_key TEXT UNIQUE NOT NULL,      -- Prevents duplicate event generation
    payload TEXT NOT NULL,                     -- JSON serialized business payload
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'published', 'dead_letter')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,                          -- Worker instance ID holding delivery lease
    lease_expires_at DATETIME,                 -- Lease expiration for crash recovery
    last_error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE INDEX idx_outbox_dispatch ON outbox_events(status, next_attempt_at) WHERE status IN ('pending', 'processing');
CREATE INDEX idx_outbox_aggregate ON outbox_events(aggregate_type, aggregate_id, aggregate_version);
```

---

## 3. Consumer Inbox & Deduplication Schema (`inbox_events`)

Downstream consumers (e.g. Oryol CRM consuming OryolMail messages) implement the **Idempotent Inbox Pattern**:

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
    UNIQUE(consumer_name, event_id)           -- Guarantees exactly-once execution per consumer
);
```

### 3.1 Delivery & Ingestion Guarantees
- **At-Least-Once Delivery**: Producers retry until acknowledged.
- **Idempotent Consumers**: `UNIQUE(consumer_name, event_id)` rejects re-delivered duplicate payloads instantly.
- **Lease Recovery**: If a worker crashes while holding `lease_owner`, `next_attempt_at` and `lease_expires_at` allow new workers to claim and resume delivery.
- **Dead Letter Queue (DLQ)**: Events exceeding 10 delivery attempts transition to `dead_letter` status for manual inspection.
- **Tombstones & Deletions**: Deletion events (`*.deleted`) carry full tombstones indicating entity deletion and version.

---

## 4. Immutable Compliance Audit Schema (`audit_events`)

```sql
CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,                       -- aud_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid> (Retained even if org is deleted)
    actor_type TEXT NOT NULL CHECK(actor_type IN ('human', 'service', 'system', 'ai')),
    actor_principal_id TEXT NOT NULL,          -- prn_<ulid>
    actor_membership_id TEXT,                  -- mem_<ulid>
    actor_ip TEXT,
    actor_user_agent TEXT,
    correlation_id TEXT NOT NULL,
    request_id TEXT,
    action TEXT NOT NULL,                      -- e.g. 'core.members.invite'
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',        -- JSON: { before, after, metadata }
    status TEXT NOT NULL DEFAULT 'success' CHECK(status IN ('success', 'denied', 'error')),
    legal_hold INTEGER NOT NULL DEFAULT 0,     -- 1 prevents automated compliance purge
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    -- NO foreign key cascade: Organization deletion must NOT delete historical audit logs
);

CREATE INDEX idx_audit_org_time ON audit_events(organization_id, created_at DESC);
CREATE INDEX idx_audit_actor ON audit_events(actor_principal_id, created_at DESC);
```

### 4.1 Audit Governance Rules
1. **Non-Cascading Retention**: When an organization is deleted, audit logs are preserved for the statutory compliance window (e.g. 7 years).
2. **Actor Diversity**: Seamlessly captures human users, service accounts, automated background tasks, and AI Gateway operations.
3. **Atomic Security Recording**: High-risk security mutations (e.g. role demotion, domain deletion) write to `audit_events` in the **same atomic D1 transaction** as the mutation.
