# Oryol Audit, Outbox & Event Ingestion Architecture v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Append-Only Privacy Overlays, Non-Blocking Legal Holds, Expired Lease Recovery & Atomic Security Mutations

---

## 1. Distinct Roles: Audit vs. Outbox vs. Observability

| Subsystem | Primary Purpose | Storage | Cascade on Org Delete | Mutability |
|---|---|---|---|---|
| **Compliance Audit Log** (`audit_events`) | Forensics, legal compliance, "Who did what and when?" | Cloudflare D1 + R2 cold archive | **NEVER** (Preserved under permanent Phase 1 retention) | **Strictly Append-Only (No Physical Purge / No In-Place Updates)** |
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
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'published', 'dead_letter', 'retry', 'blocked_on_gap')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    lease_owner TEXT,                          -- Worker instance ID holding claim
    lease_expires_at DATETIME,                 -- Lease expiration timestamp
    last_error TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME
);

CREATE INDEX idx_outbox_eligibility ON outbox_events(status, next_attempt_at, lease_expires_at);
```

---

## 3. Dispatcher Claim Algorithm & Expired Lease Recovery

### 3.1 Canonical Eligibility & Atomic Lease Claim
An outbox dispatcher worker queries and claims eligible events using an atomic lease update:
```sql
UPDATE outbox_events
SET status = 'processing',
    lease_owner = :worker_id,
    lease_expires_at = datetime('now', '+30 seconds'),
    attempt_count = attempt_count + 1
WHERE event_id IN (
    SELECT event_id FROM outbox_events
    WHERE (status IN ('pending', 'retry') AND next_attempt_at <= datetime('now'))
       OR (status = 'processing' AND lease_expires_at <= datetime('now'))
    ORDER BY occurred_at ASC
    LIMIT 50
);
```

### 3.2 Safe Finalize Invariant
Only the current lease owner may finalize publication before lease expiration:
```sql
UPDATE outbox_events
SET status = 'published',
    published_at = datetime('now'),
    lease_owner = NULL,
    lease_expires_at = NULL
WHERE event_id = :event_id
  AND lease_owner = :worker_id
  AND status = 'processing'
  AND lease_expires_at > datetime('now');
```
If the lease expired prior to publication completion, the worker **fails closed** and does not mark the record published blindly.

---

## 4. Aggregate Ordering, Gap Recovery & Schema Evolution

### 4.1 Transport & Ordering Rules
- **Transport Reality**: Cloudflare Queues transport is strictly **at-least-once**. Queue-level ordering is **not** assumed.
- **Aggregate Evaluation**:
  - `received_version == expected_version` ➔ Process normally and advance `expected_version = received_version + 1`.
  - `received_version < expected_version` ➔ Candidate is duplicate or stale ➔ Deduplicate via `inbox_events` or safely ignore.
  - `received_version > expected_version` ➔ Forward gap detected ➔ Set `status = 'blocked_on_gap'`, buffer event, request/replay missing aggregate versions from source. If unresolved within 5 minutes ➔ Send to DLQ and trigger operational alert.

### 4.2 Schema Evolution
- Every event specifies `event_type` and `schema_version`.
- Additive, non-breaking schema additions are backward-compatible.
- Breaking changes require a new `schema_version` or new `event_type`.
- Consumers declare supported version ranges; unsupported versions route to DLQ for triage (never guessed). Replay retains original `schema_version`.

---

## 5. Atomic Inbox Semantics & Deduplication (`inbox_events`)

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
    status TEXT NOT NULL DEFAULT 'completed' CHECK(status IN ('pending', 'completed', 'failed')),
    UNIQUE(consumer_name, event_id)
);
```

### Invariant for Effectively-Once Application Semantics:
- **Atomic Side Effect & Marker**: Effectively-once semantics require the **consumer domain side effect** and the **`inbox_events` completion marker** to commit in the **same atomic D1 transaction**.
- Non-atomic consumers remain at-least-once / idempotent.
- A failed inbox processing attempt records `status = 'failed'` without permanently blocking legitimate retries.

---

## 6. Phase 1 Permanent Audit Retention, Privacy Overlays & Legal Holds

### 6.1 Phase 1 Permanent Retention & Zero In-Place Updates
> [!IMPORTANT]
> **Zero In-Place Updates / No Physical Purge in Phase 1**:  
> In Phase 1, `audit_events` are **permanently immutable**. The database engine strictly prohibits both `UPDATE` and `DELETE` on `audit_events`.  
> When a user exercises GDPR/privacy erasure rights, the audit table is **never updated in place**. Instead, an append-only privacy overlay record is inserted into `audit_redactions`. Read and export queries dynamically apply the overlay to mask PII without altering the stored raw audit event bytes.

```sql
-- 1. Append-Only Privacy Redaction Overlay
CREATE TABLE audit_redactions (
    id TEXT PRIMARY KEY,                       -- red_<ulid>
    organization_id TEXT NOT NULL,
    subject_principal_id TEXT NOT NULL,
    replacement_label TEXT NOT NULL,           -- e.g. 'Deleted User prn_01H8Z7...'
    reason TEXT NOT NULL,                      -- e.g. 'GDPR Right to Erasure'
    effective_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by_principal_id TEXT NOT NULL,
    legal_hold_checked_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Legal Holds Contract (Survives Membership and Organization Deletion)
CREATE TABLE audit_legal_holds (
    id TEXT PRIMARY KEY,                       -- hld_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid> (Preserved permanently)
    scope_type TEXT NOT NULL CHECK(scope_type IN ('organization', 'principal', 'mailbox', 'deal')),
    scope_id TEXT,                             -- NULL for entire org, or specific entity ID
    reason TEXT NOT NULL,
    legal_authority TEXT NOT NULL,             -- Subpoena / court order / regulator ref
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'released')),
    placed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    placed_by_principal_id TEXT NOT NULL,
    placed_by_actor_metadata TEXT NOT NULL,    -- JSON snapshot of placer membership/role/name
    released_at DATETIME,
    released_by_principal_id TEXT,
    released_by_actor_metadata TEXT            -- JSON snapshot of releaser membership/role/name
);

-- 3. Compliance Audit Events (Permanently Immutable)
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

### 6.2 Legal Hold Governance & Required Permissions
Legal holds govern:
- Whether privacy redaction overlays are permitted to mask records.
- Mandatory retention guarantees during legal discovery.
- Cold-archive preservation and eDiscovery export restrictions.

**Required Permissions for Legal Holds**:
- `core.audit.legal_hold.create`: Permission to place an active legal hold.
- `core.audit.legal_hold.release`: Permission to release an active legal hold.
- `core.audit.legal_hold.read`: Permission to view active and released legal holds.

Hold creation and release require active authorized membership, step-up authentication, and are themselves recorded as immutable audit events.

### 6.3 Multi-Layer Append-Only Enforcement
1. **Repository Capability Boundary**: The `AuditRepository` interface exposes strictly `append(event: AuditEvent)`—no `update()` or `delete()` methods exist.
2. **D1 SQLite Engine Triggers**:
   ```sql
   CREATE TRIGGER trg_audit_no_update BEFORE UPDATE ON audit_events
   BEGIN SELECT RAISE(FAIL, 'AUDIT_LOG_IMMUTABLE: updates prohibited'); END;

   CREATE TRIGGER trg_audit_no_delete BEFORE DELETE ON audit_events
   BEGIN SELECT RAISE(FAIL, 'AUDIT_LOG_IMMUTABLE: deletes prohibited'); END;
   ```
3. **Compensating Audit Records**: Any correction or retraction is recorded as a new append-only compensating audit event.

### 6.4 Atomic Security Mutations
All security-critical operations require the **business state mutation** and the corresponding **audit event insert** to execute in the **same atomic D1 transaction**:
- Membership revocation / role change
- Organization ownership transfer
- Credential / service account revocation
- Session security breach invalidation
- Cross-org grant creation / revocation
- Legal hold placement / release

If the audit event insertion fails, the entire transaction **fails closed** and rolls back.
