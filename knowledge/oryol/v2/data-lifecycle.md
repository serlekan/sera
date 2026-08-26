# Oryol Data Lifecycle & Deletion Architecture v2.1

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Honest Deletion Mechanics, D1 Time Travel Reality & Multi-Storage Propagation

---

## 1. Canonical Deletion Pipeline

Architecture v2.1 eliminates inaccurate claims of instant cryptographic erasure, replacing them with a realistic, multi-phase deletion pipeline:

```text
[Created] ──► active ──► soft_deleted (Logical Deletion) ──► retention_grace ──► purge_eligible ──► physical_purge (D1/R2)
```

| Lifecycle State | DB Representation | Query Filtering | SLA / Retention |
|---|---|---|---|
| `active` | Normal row | Included by default | Indefinite |
| `soft_deleted` | `deleted_at = CURRENT_TIMESTAMP` | Excluded from standard views | 30-day grace window |
| `archived` | `archived_at = CURRENT_TIMESTAMP` | Cold storage read-only | Compliance retention |
| `purged` | Row deleted (`DELETE FROM ...`) | Irreversible | D1 Time Travel 7-30d |

---

## 2. Multi-Storage Propagation Reality

| Storage Subsystem | Deletion Method | Propagation SLA | Backup / Time Travel Reality |
|---|---|---|---|
| **Primary D1 Relational** | Physical row `DELETE`. | Immediate upon purge execution. | **D1 Time Travel**: Point-in-time recovery remains active in Cloudflare infrastructure for 7 to 30 days until natural log rotation. |
| **Cloudflare R2 Objects** | Hard object deletion via R2 API (`DELETE /objects/...`). | Synchronous or asynchronous batch purge (< 24 hours). | R2 versioning lifecycle rules purge non-current versions. |
| **Search Projections** | Tombstone event (`*.deleted`) consumed by search worker. | Near real-time (< 5 minutes). | Index inverted lists remove document references. |
| **Outbox & Queues** | Outbox rows cleared post-publish; queues process messages. | Natural queue consumption / TTL drain. | Messages in-flight expire within standard queue retention. |
| **Audit Logs** | Pseudonymization / Legal Hold. | **Preserved**. PII masked (`Deleted User`), security audit records retained for compliance. | Never cascade-deleted with organization purge. |
| **Third-Party AI Providers** | Zero Data Retention. | Zero persistence. | Enterprise API contracts enforce 0-day retention on Google/Anthropic/OpenAI edge endpoints. |

---

## 3. Organization Lifecycle & Purge Governance

```text
active ──► suspended ──► archived ──► deletion_pending (30-day grace) ──► physical_purge
```

1. **`deletion_pending`**:
   - Organization status is set to `deletion_pending`.
   - All active user sessions are terminated; inbound email MX relays return SMTP 550 reject.
2. **Physical Purge Execution**:
   - Automated cleanup worker deletes domain records, mailboxes, threads, messages, and memberships from D1.
   - Deletes all R2 objects under prefix `/org_{org_id}/`.
   - Emits final audit tombstone `core.organization.purged`.
