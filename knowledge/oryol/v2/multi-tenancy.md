# Oryol Multi-Tenancy & Structural Isolation Architecture v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Universal Compound Tenant Integrity, Brokered Cross-Org Grants, Platform-Scoped Records & Pilot D1 SLOs

---

## 1. Universal Compound Tenant Foreign Keys

Architecture v2.2 mandates that **every organization-scoped relation** in the Oryol relational schema must enforce tenant integrity via compound foreign keys `(organization_id, <entity>_id)`. This guarantees at the database engine level that an entity belonging to Organization A can never be attached to a parent or relation belonging to Organization B.

```sql
-- 1. Canonical Organization Boundary
CREATE TABLE organizations (
    id TEXT PRIMARY KEY,                       -- org_<ulid>
    slug TEXT UNIQUE NOT NULL,                 -- e.g. 'acme-corp'
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'archived', 'deletion_pending')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 2. Organization Memberships (Compound Unique Target)
CREATE TABLE memberships (
    id TEXT PRIMARY KEY,                       -- mem_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
    member_type TEXT NOT NULL DEFAULT 'employee' CHECK(member_type IN ('employee', 'contractor', 'guest', 'system')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'left')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, id),
    UNIQUE(organization_id, principal_id)
);

-- 3. Teams with Compound Organization Key
CREATE TABLE teams (
    id TEXT PRIMARY KEY,                       -- team_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, id),
    UNIQUE(organization_id, slug)
);

-- 4. Team Memberships: Strictly Enforcing Tenant Integrity on Both Sides
CREATE TABLE team_memberships (
    id TEXT PRIMARY KEY,                       -- tmem_<ulid>
    organization_id TEXT NOT NULL,
    team_id TEXT NOT NULL,
    membership_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, team_id) REFERENCES teams(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    UNIQUE(team_id, membership_id)
);

-- 5. Membership Role Assignments: Structurally Bound to Organization
CREATE TABLE membership_role_assignments (
    id TEXT PRIMARY KEY,                       -- mra_<ulid>
    organization_id TEXT NOT NULL,
    membership_id TEXT NOT NULL,
    role_id TEXT NOT NULL REFERENCES role_definitions(id) ON DELETE RESTRICT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, membership_id, role_id)
);

-- 6. Resource ACL Grants: Structurally Bound to Organization
CREATE TABLE resource_grants (
    id TEXT PRIMARY KEY,                       -- rgr_<ulid>
    organization_id TEXT NOT NULL,
    subject_membership_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,               -- 'mailbox', 'thread', 'document', 'deal'
    resource_id TEXT NOT NULL,
    permission TEXT NOT NULL,                  -- e.g. 'mail.messages.read'
    granted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, subject_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, subject_membership_id, resource_type, resource_id, permission)
);

-- 7. Application Installations: Organization-Scoped Entitlements
CREATE TABLE application_installations (
    id TEXT PRIMARY KEY,                       -- appi_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    application_id TEXT NOT NULL,              -- 'oryol-mail', 'oryol-crm', 'oryol-drive', 'virel'
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'disabled')),
    installed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, application_id)
);

-- 8. Delegated Authority: Both Grantor and Delegate Must Belong to Organization
CREATE TABLE delegated_authority (
    id TEXT PRIMARY KEY,                       -- del_<ulid>
    organization_id TEXT NOT NULL,
    grantor_membership_id TEXT NOT NULL,
    delegate_membership_id TEXT NOT NULL,
    scope TEXT NOT NULL,                       -- e.g. 'mail.send_on_behalf'
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, grantor_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, delegate_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    CHECK (grantor_membership_id != delegate_membership_id)
);
```

---

## 2. Brokered Cross-Organization Collaboration

Local organization ACL tables (`resource_grants`) must **never** directly reference arbitrary foreign-tenant resources or membership IDs. All cross-tenant access is brokered explicitly through `cross_org_grants`:

```sql
CREATE TABLE cross_org_grants (
    id TEXT PRIMARY KEY,                       -- cog_<ulid>
    source_organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    target_organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    granting_principal_id TEXT NOT NULL REFERENCES principals(id),
    granting_membership_id TEXT NOT NULL,
    receiving_principal_id TEXT NOT NULL REFERENCES principals(id),
    receiving_membership_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,               -- 'document', 'calendar_event', 'deal'
    resource_id TEXT NOT NULL,
    permission TEXT NOT NULL,                  -- Canonical 3-part name
    expires_at DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'revoked', 'expired')),
    audit_metadata TEXT NOT NULL,              -- JSON context: reason, approved_by
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_organization_id, granting_membership_id) REFERENCES memberships(organization_id, id),
    FOREIGN KEY (target_organization_id, receiving_membership_id) REFERENCES memberships(organization_id, id)
);
```

### Invariants for Brokered Grants:
1. **Explicit Bilateral Identification**: Both source (owner) and target (guest) organizations are recorded.
2. **Authoritative Revocation**: Source organization administrators can revoke cross-org grants instantly.
3. **No Direct ACL Mixing**: Foreign subjects never appear in the source organization's internal `memberships` or `resource_grants`.

---

## 3. Platform-Scoped vs. Organization-Scoped Entity Separation

| Entity Scope | Conceptual Tables | Access & Governance Rules |
|---|---|---|
| **Platform-Scoped** | `principals`, `users`, `credentials`, `identity_provider_bindings`, `recovery_methods`, `organizations`, `organization_placement` | Owned solely by Oryol Identity Core. Cannot be queried by product applications directly. |
| **Organization-Scoped** | `memberships`, `teams`, `team_memberships`, `membership_role_assignments`, `resource_grants`, `explicit_denies`, `application_installations`, `delegated_authority` | Strictly isolated per tenant. All queries include `WHERE organization_id = ?`. |
| **Product Domain Scoped** | Mailboxes (`oryol-mail`), Contacts (`oryol-crm`), Assets (`oryol-drive`), Wallets (`virel`) | Owned by product D1 database, partitioned by `organization_id`. |

---

## 4. Organization Placement & Sharding Abstraction

Core exposes the `getDbForOrganization(organizationId)` abstraction:

```sql
CREATE TABLE organization_placement (
    organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE RESTRICT,
    logical_shard TEXT NOT NULL,               -- e.g. 'shard_us_east_01'
    jurisdiction TEXT NOT NULL,                -- 'us', 'eu', 'apac'
    database_identifier TEXT NOT NULL,         -- Cloudflare D1 binding name or ID
    migration_state TEXT NOT NULL DEFAULT 'steady' CHECK(migration_state IN ('steady', 'migrating', 'read_only')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 5. Controlled Pilot D1 SLOs & Operational Thresholds

During pilot operations and initial production rollouts on Cloudflare D1, systems adhere to the following target operational metrics:

| Metric Dimension | Target SLO / Threshold | Operational Action on Breach |
|---|---|---|
| **Read Latency (p50 / p95 / p99)** | `< 5ms` (p50) / `< 25ms` (p95) / `< 100ms` (p99) | Cache frequent reads in Cloudflare KV / optimize indexes. |
| **Write Contention / Lock Wait** | `< 50ms` lock wait, max 3 retry attempts | Shard write-heavy outbox tables or batch writes. |
| **D1 Error / Overload Rate** | `< 0.01%` query failures | Trigger circuit breaker and fallback retry queue. |
| **Per-Database Storage Capacity** | Threshold: `5 GB` per D1 shard | Trigger `organization_placement` re-sharding pipeline. |
| **Regional Transit Latency** | `< 50ms` global transit to nearest replica | Utilize Cloudflare Smart Placement for Workers. |
