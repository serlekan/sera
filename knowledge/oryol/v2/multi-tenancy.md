# Oryol Multi-Tenancy Architecture v2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Supersedes**: `knowledge/oryol/security.md` (v1 Multi-Tenant section)

---

## 1. Canonical Multi-Tenant Hierarchy

In Architecture v2, multi-tenancy encompasses platform governance, organizations, teams, application installations, and resource-level access:

```
Platform (Oryol Global)
   │
   └── Organization (org_...) ────────── Absolute Security & Data Partition Boundary
         │
         ├── Teams (team_...) ────────── Functional Sub-groups (e.g. Sales, Support, Engineering)
         │     └── Team Memberships (tmem_...)
         │
         ├── Application Installations (app_inst_...) ── Entitlements (OryolMail, Oryol CRM, etc.)
         │
         └── Resources ───────────────── Business Objects (Mailboxes, Deals, Calendars, Assets)
               └── Resource Permissions (Resource-level ACLs & Ownership)
```

---

## 2. Organization Lifecycle States

Organizations transition through an explicit lifecycle state machine:

```text
[Provisioned] ──► active ──► suspended ──► archived ──► deletion_pending ──► [Purged]
                   ▲            │
                   └────────────┘ (Reactivated)
```

| State | Allowed Operations | Data Access | Description |
|---|---|---|---|
| `active` | Full read, write, execution | Normal | Organization operates with full platform capabilities. |
| `suspended` | Read-only or blocked | Blocked / Grace | Blocked due to payment delinquency or security lockdown. All write mutations rejected. |
| `archived` | Admin export only | Offline | Cold-stored; active user logins disabled; scheduled for decommissioning. |
| `deletion_pending`| Zero access | Soft-deleted | 30-day grace window before irreversible cryptographic cryptographic erasure across D1, KV, and R2. |

---

## 3. Application Installations & Entitlements

Organizations explicitly install and license applications:

```sql
CREATE TABLE application_installations (
    id TEXT PRIMARY KEY,                       -- app_inst_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    application_id TEXT NOT NULL,              -- 'oryol-mail', 'oryol-crm', etc.
    status TEXT NOT NULL DEFAULT 'active',     -- 'active', 'disabled', 'trial'
    plan_tier TEXT NOT NULL DEFAULT 'standard',-- 'starter', 'professional', 'enterprise'
    entitlements TEXT NOT NULL DEFAULT '{}',   -- JSON: quota limits, custom features
    installed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, application_id)
);
```

If an organization lacks an active installation of `oryol-crm`, all incoming API requests to CRM endpoints are immediately rejected with `HTTP 403 APPLICATION_NOT_ENTITLED` at the edge gateway.

---

## 4. Resource Scoping & Partitioning Guarantees

1. **Relational Storage (Cloudflare D1)**:
   Every business entity table contains `organization_id TEXT NOT NULL`. Queries without `WHERE organization_id = ?` are rejected by code review and query middleware.
2. **Object Storage (Cloudflare R2)**:
   Keys are strictly namespaced: `/org_{organization_id}/{app_id}/{resource_type}/{resource_id}/{file_name}`.
3. **Cache & Ephemeral State (Cloudflare KV)**:
   Keys are strictly prefixed: `org:{organization_id}:{key}`.
