# Oryol Data Lifecycle & Governance v2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Supersedes**: `knowledge/oryol/data-model.md` (v1 Data Lifecycle section)

---

## 1. Entity Lifecycle States

All primary business records in Oryol Workspace progress through standardized lifecycle states:

```text
[Created] ──► active ──► soft_deleted ──► archived ──► purged (Hard Deleted)
```

| Lifecycle Phase | State | Query Behavior | Recovery Window |
|---|---|---|---|
| **Active** | `active` | Included in standard application queries (`deleted_at IS NULL`). | N/A |
| **Soft Deleted** | `soft_deleted` | Excluded from standard views; accessible via Trash/Undo. | 30 Days |
| **Archived** | `archived` | Read-only compliance state; excluded from active search indexes. | Organization Defined |
| **Purged** | Cryptographically Erased | Permanently wiped from D1 tables, KV keys, and R2 buckets. | Irreversible |

---

## 2. Retention & GDPR / Right-to-be-Forgotten Compliance

1. **Organization Data Retention Policy**:
   - Organizations configure retention periods for emails, audit logs, and CRM records (`settings.retention_days`).
   - Automated nightly scheduled Workers identify and soft-delete expired records.
2. **User Data Erasure (GDPR)**:
   - When a human user exercises right-to-be-forgotten, the `users` row is stripped of PII (name replaced with `Deleted User`, email anonymized to `deleted_<hash>@oryol.internal`).
   - Audit logs retain immutable actor principal IDs (`prn_...`) for security traceability without exposing PII.
