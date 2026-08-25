# Oryol Global Data Model & Conventions (v1)

> [!WARNING]
> **Status: SUPERSEDED**  
> This document describes Oryol Data Model v1. For the current canonical data model, lifecycle, and outbox event schemas, see [`knowledge/oryol/v2/identity-model.md`](v2/identity-model.md), [`knowledge/oryol/v2/data-lifecycle.md`](v2/data-lifecycle.md), and [`knowledge/oryol/v2/audit-and-events.md`](v2/audit-and-events.md).

All Oryol Workspace applications share standard entity conventions, identifier formats, timestamps, and multi-tenant persistence schemas.

---

## 1. Unified ID Prefix Conventions

Every persistent entity in the Oryol ecosystem must use standard, sortable, prefixed identifiers:

| Prefix | Entity | Example |
|---|---|---|
| `usr_` | Global User | `usr_01H8Z7A2...` |
| `org_` | Organization | `org_01H8Z7B5...` |
| `mem_` | Organization Membership | `mem_01H8Z7C8...` |
| `dom_` | Custom Domain Configuration | `dom_01H8Z7D1...` |
| `mbx_` | Mailbox (Personal or Shared) | `mbx_01H8Z7E4...` |
| `thd_` | Email Conversation Thread | `thd_01H8Z7F7...` |
| `msg_` | Email Message | `msg_01H8Z7G0...` |
| `att_` | File / Attachment Asset | `att_01H8Z7H3...` |
| `nt_`  | Internal Team Note | `nt_01H8Z7J6...` |
| `act_` | Action Item / Task Checklist | `act_01H8Z7K9...` |
| `aud_` | Audit Log Record | `aud_01H8Z7M2...` |

---

## 2. Standard Entity Field Contract

All primary business records in Oryol Workspace database schemas must implement the standard platform audit and multi-tenant columns:

```typescript
export interface BaseOryolEntity {
  id: string;              // Prefixed ULID/UUID (e.g. msg_01...)
  organizationId: string;  // Mandatory organization reference
  createdAt: string;       // ISO-8601 UTC timestamp
  updatedAt: string;       // ISO-8601 UTC timestamp
  deletedAt?: string;      // Optional soft-delete timestamp
  createdById?: string;    // Actor User ID
  updatedById?: string;    // Actor User ID
}
```

---

## 3. Relational Schema Blueprint (D1 / SQLite Edge)

```sql
-- Core Organizations
CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Organization Memberships
CREATE TABLE memberships (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL, -- 'owner', 'admin', 'member', 'guest'
    permissions TEXT NOT NULL, -- JSON array of scoped strings
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, user_id)
);

-- Custom Domains
CREATE TABLE domains (
    id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending', -- 'verified', 'pending', 'failed'
    dns_records TEXT NOT NULL, -- JSON array of DNS record specifications
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(domain)
);
```
