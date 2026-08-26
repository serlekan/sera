# Oryol Multi-Tenancy Architecture v2.1 — Structural Tenant Isolation

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Structural Tenant Isolation, Placement Routing & Cross-Tenant Constraint Invariants

---

## 1. Canonical Multi-Tenant Rules & Boundaries

1. **Organization as Absolute Tenant Boundary**: The **Organization (`org_...`)** is the sole root tenant and security boundary.
2. **Teams & App Installations are Organization-Scoped**: Teams (`team_...`) and Application Installations (`app_inst_...`) are internal organization-scoped groupings and entitlements, **never** security ancestors or multi-tenant root partitions.
3. **Universal Resource Tuple**: Every business resource in every product belongs to the canonical 3-tuple:
   ```
   (organization_id, application_id, resource_id)
   ```

```text
Platform (Oryol Global)
   │
   └── Organization (org_...) ────────── Absolute Security & Data Partition Boundary
         │
         ├── organization_placement ──── Shard / Jurisdiction / Routing Metadata
         │
         ├── Teams (team_...) ────────── Internal Functional Grouping
         │     └── Team Memberships (tmem_...) [Enforced: team.org_id == member.org_id]
         │
         ├── Application Installations ─ Licensed Modules (oryol-mail, oryol-crm, etc.)
         │
         └── Resources ───────────────── (org_id, app_id, resource_id)
```

---

## 2. Structural Protection Against Cross-Tenant Inconsistencies

To prevent cross-tenant leakage caused by application bugs, relational schemas enforce cross-tenant integrity via **compound foreign keys** and **structural uniqueness constraints**:

### 2.1 Team Membership Compound Foreign Key Invariant
A user cannot be added to a Team in Organization A using a Membership from Organization B:

```sql
-- Teams Table with Compound Key Exposure
CREATE TABLE teams (
    id TEXT PRIMARY KEY,                       -- team_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, id),              -- Enables compound foreign key
    UNIQUE(organization_id, slug)
);

-- Memberships Table with Compound Key Exposure
CREATE TABLE memberships (
    id TEXT PRIMARY KEY,                       -- mem_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    role TEXT NOT NULL DEFAULT 'member',
    status TEXT NOT NULL DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    UNIQUE(organization_id, id),              -- Enables compound foreign key
    UNIQUE(organization_id, principal_id)
);

-- Structurally Isolated Team Memberships
CREATE TABLE team_memberships (
    id TEXT PRIMARY KEY,                       -- tmem_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    team_id TEXT NOT NULL,                     -- team_<ulid>
    membership_id TEXT NOT NULL,               -- mem_<ulid>
    team_role TEXT NOT NULL DEFAULT 'member',
    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    -- Structural Invariant: Both Team and Membership MUST belong to the SAME organization_id
    FOREIGN KEY (organization_id, team_id) REFERENCES teams(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    UNIQUE(team_id, membership_id)
);
```

### 2.2 Record Scoping Categories
- **Platform-Scoped Records**: `principals`, `users`, `credentials`, `identity_provider_bindings`. Contain zero tenant-specific business assets.
- **Organization-Scoped Records**: `organization_domains`, `memberships`, `teams`, `roles`, `audit_events`, `outbox_events`, `mailboxes`, `threads`, `deals`.
- **Cross-Organization Collaboration Grants**: Explicit, time-bounded collaboration records (`guest_grants`, `shared_channels`). Direct foreign-tenant foreign keys or foreign-tenant direct ACL references in domain tables are **strictly forbidden**.

---

## 3. Placement Routing & Horizontal Sharding Strategy

Architecture v2.1 defines a **Placement Routing Abstraction** so that applications and Core never assume one global D1 database is permanent.

### 3.1 Organization Placement Schema (`organization_placement`)

```sql
CREATE TABLE organization_placement (
    organization_id TEXT PRIMARY KEY,          -- org_<ulid>
    logical_shard TEXT NOT NULL DEFAULT 'shard_01', -- Logical partition ID
    jurisdiction TEXT NOT NULL DEFAULT 'global',    -- 'eu', 'us', 'apac', 'fedramp'
    database_identifier TEXT NOT NULL,         -- Cloudflare D1 Database UUID / Binding Name
    migration_state TEXT NOT NULL DEFAULT 'steady' CHECK(migration_state IN ('steady', 'preparing', 'migrating', 'read_only')),
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);
```

### 3.2 Phased Database Sharding Strategy

```text
Phase 1 (MVP Baseline)       Future Phases (Enterprise Scale)
┌────────────────────────┐   ┌────────────────────────────────────────────────────────┐
│ Single Controlled D1   │   │ Placement Router (resolves `org_id` ➔ D1 DB Binding)  │
│ (Shared Core Database) │   └───────────────────────────┬────────────────────────────┘
└────────────────────────┘                               │
                                ┌────────────────────────┼────────────────────────┐
                                ▼                        ▼                        ▼
                     ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
                     │ D1 Shard US-01     │   │ D1 Shard EU-01     │   │ D1 Shard APAC-01   │
                     │ (US Data Residency)│   │ (EU Data Residency)│   │(APAC Residency)    │
                     └────────────────────┘   └────────────────────┘   └────────────────────┘
```

- **Phase 1 Baseline**: Single controlled Cloudflare D1 instance for Core, accessed via the `getDbForOrganization(orgId)` routing helper.
- **Future Scale**: The `getDbForOrganization(orgId)` helper queries `organization_placement` or cached KV shard routes to bind the target regional D1 instance seamlessly without modifying application code.
