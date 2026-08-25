# Oryol Audit, Events & Outbox Architecture v2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Supersedes**: `knowledge/oryol/security.md` (v1 Audit section)

---

## 1. Separation of Concerns

Architecture v2 strictly distinguishes three distinct observability and event streams:

```
┌───────────────────────────────┐  ┌───────────────────────────────┐  ┌───────────────────────────────┐
│         Audit Events          │  │         Domain Events         │  │         Observability         │
│     "What happened?"          │  │"What should others know?"     │  │ "How is system behaving?"     │
│ (Compliance / Security / Forensics) (Decoupled Cross-App Sync)   │  │   (Metrics / Logs / Traces)   │
└───────────────────────────────┘  └───────────────────────────────┘  └───────────────────────────────┘
```

---

## 2. Standard Universal Event Envelope

All domain and audit events conform to the standardized Oryol Event Envelope:

```typescript
export interface OryolEventEnvelope<T = Record<string, unknown>> {
  eventId: string;             // evt_<ulid> or aud_<ulid>
  schemaVersion: number;       // e.g. 2
  organizationId: string;      // org_<ulid>
  producer: string;            // e.g. "oryol-mail-worker", "oryol-core-auth"
  aggregateType: string;       // "mailbox", "deal", "member", "domain"
  aggregateId: string;         // e.g. "dom_01H8Z...", "msg_01H8Z..."
  eventType: string;           // "mail.message.received", "core.member.invited"
  timestamp: string;           // ISO-8601 UTC
  actorContext: {
    principalId: string;       // prn_<ulid>
    membershipId?: string;     // mem_<ulid>
    ipAddress?: string;
    userAgent?: string;
  };
  correlationId: string;       // trace correlation ID across workers
  idempotencyKey: string;      // deduplication key
  payload: T;
}
```

---

## 3. Transactional Outbox Pattern

To prevent dual-write inconsistencies and phantom events, all domain mutations utilize the **Transactional Outbox Pattern**:

```
                    ┌────────────────────────────────────────────────────────┐
                    │                 Single D1 Transaction                  │
                    │                                                        │
                    │   1. Apply Business Mutation (e.g. Insert Message)     │
                    │   2. Insert Outbox Event into `outbox_events` Table    │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │             Outbox Consumer / Edge Queue               │
                    │ (Pulls pending outbox rows ──► Dispatches to Queues)   │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │             Subscribers (CRM, Virel, etc.)             │
                    └────────────────────────────────────────────────────────┘
```

```sql
CREATE TABLE outbox_events (
    id TEXT PRIMARY KEY,                       -- out_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,                     -- JSON serialized EventEnvelope
    status TEXT NOT NULL DEFAULT 'pending',    -- 'pending', 'published', 'failed'
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE INDEX idx_outbox_pending ON outbox_events(status, created_at);
```
