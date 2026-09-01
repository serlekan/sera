# ADR-002: Service Principal Role-Based Access Control (RBAC) & Step 6 Evaluation

**Status**: PROPOSED (Target: Architecture v2.3)  
**Date**: 2026-08-28  
**Author**: Deep Builder (`anthropic/claude-sonnet-5`)  
**Scope**: Oryol Core Identity, Tenancy, and Authorization Engine  
**Affected Documents**: `authorization-model.md`, `multi-tenancy.md`, `identity-model.md`, `audit-and-events.md`

---

## 1. Context

Oryol Architecture v2.2 establishes a strict binary principal taxonomy in `identity-model.md §2`:
1. **Human Principals**: Authenticated individuals bound to organizations via `memberships`.
2. **Service Principals**: Automated workloads, backend daemons, and programmatic integrations bound to organizations via `organization_service_principals`.

In the authorization algebra (`authorization-model.md §5`):
- **Step 1** validates `principals.status = 'active'`.
- **Step 2** validates `memberships.status = 'active'` (for humans) or `organization_service_principals.status = 'active'` (for service principals).
- **Step 5** evaluates `explicit_denies` matching `authorization_subjects` (which explicitly supports `subject_type = 'service_principal'`).
- **Step 6** resolves coarse RBAC capability against the organization's active `permission_registry_versions`.

---

## 2. Problem Statement (`ARCHITECTURE_SCHEMA_CONTRADICTION #2`)

During Phase 1 — Slice 3 implementation of the authorization engine, a schema contradiction was discovered:
1. In canonical Phase 1 migrations `0001` through `0004`, coarse-RBAC role assignments exist **exclusively** for human memberships via `membership_role_assignments (organization_id, membership_id, role_id)`.
2. The schema contains **no table** for assigning roles to service principals (e.g. `service_principal_role_assignments`).
3. In Step 6 coarse-RBAC evaluation, when a request is made by a service principal, the engine queries role assignments for `resolvedMembershipId`. Because a service principal has no membership record (`resolvedMembershipId === null`), no roles can be resolved.
4. Consequently, every service principal authorization request inevitably terminates at Step 6 with **`DENY(RBAC_DENIED)`**. While fail-closed, this made service principals structurally incapable of holding permissions or performing authorized actions in Phase 1.

---

## 3. Decision

We introduce explicit, tenant-bound service principal role assignments in D1, establishing unified coarse-RBAC capability resolution across both human and service principal identities.

### 3.1 Persistence Model, Immutable System Templates & Taxonomy Invariants (F-6, R-7)

#### Table 1: `principals` Enhancement (R-7)
To ensure the principal taxonomy is permanently immutable and cannot be flipped post-creation:
```sql
CREATE TRIGGER trg_principals_immutable_type BEFORE UPDATE OF type ON principals
BEGIN
    SELECT RAISE(FAIL, 'PRINCIPAL_TYPE_IMMUTABLE: principal type cannot be modified');
END;
```

#### Table 2: `role_definitions` Enhancement & Template Invariant (F-6, P0-1)
To prevent role renaming, template flag mutability, and custom role template forgery from creating administrative privilege escalation vectors, `role_definitions` incorporates a strict template invariant, uniqueness constraint, and immutable trigger:

```sql
CREATE TABLE role_definitions (
    id TEXT PRIMARY KEY,                       -- rol_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,                        -- 'Owner', 'Admin', 'Member', 'CustomRole'
    description TEXT NOT NULL,
    is_system_template BOOLEAN NOT NULL DEFAULT FALSE,
    template_key TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (is_system_template = 0 AND template_key IS NULL) OR
        (is_system_template = 1 AND template_key IN ('owner', 'admin', 'member'))
    ),
    UNIQUE(organization_id, id),
    UNIQUE(organization_id, name)
);

-- Exactly one system template per template_key per organization (P0-1)
CREATE UNIQUE INDEX uq_role_definitions_org_template 
    ON role_definitions(organization_id, template_key) 
    WHERE template_key IS NOT NULL;

CREATE TRIGGER trg_role_definitions_immutable_template BEFORE UPDATE OF is_system_template, template_key ON role_definitions
BEGIN
    SELECT RAISE(FAIL, 'ROLE_TEMPLATE_IMMUTABLE: cannot modify system template designation');
END;
```

**Template Creation Contract**:
- Tenant-created custom roles MUST force `is_system_template = FALSE` and `template_key = NULL`. Client API requests attempting to provide `is_system_template` or `template_key` are rejected at the edge gateway.
- System templates (`Owner`, `Admin`, `Member`) can ONLY be created through the trusted organization bootstrap/provisioning routine (`provisionOrganization()` / Core tenant provisioning service) during initial tenant initialization.

#### Table 2b: `service_accounts` Tenant Ownership (P0-2)
A service account belongs to exactly one tenant. To structurally prevent cross-tenant service principal binding at the database engine layer, `service_accounts` records its authoritative `organization_id` and exposes a compound key:

```sql
CREATE TABLE service_accounts (
    id TEXT PRIMARY KEY,                       -- svc_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT,
    system_managed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(principal_id),
    UNIQUE(organization_id, principal_id),
    UNIQUE(organization_id, id)
);
```

#### Table 2c: `organization_service_principals` Compound Tenant Binding (P0-2)
`organization_service_principals` enforces tenant confinement through a compound foreign key referencing `service_accounts(organization_id, principal_id)`. An attempt to bind a service account owned by Organization A into Organization B fails with a database foreign key constraint violation:

```sql
CREATE TABLE organization_service_principals (
    id TEXT PRIMARY KEY,                       -- osp_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'revoked')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, principal_id)
        REFERENCES service_accounts(organization_id, principal_id) ON DELETE CASCADE,
    UNIQUE(organization_id, principal_id),
    UNIQUE(organization_id, id)
);
```

#### Table 3: `service_principal_role_assignments`
```sql
CREATE TABLE service_principal_role_assignments (
    id TEXT PRIMARY KEY,                       -- sra_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    organization_service_principal_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, organization_service_principal_id)
        REFERENCES organization_service_principals(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, role_id)
        REFERENCES role_definitions(organization_id, id) ON DELETE RESTRICT,
    UNIQUE(organization_id, organization_service_principal_id, role_id),
    UNIQUE(organization_id, id)
);

CREATE INDEX idx_sra_osp ON service_principal_role_assignments(organization_id, organization_service_principal_id);
CREATE INDEX idx_sra_role ON service_principal_role_assignments(organization_id, role_id);
```

#### Database-Enforced Principal Taxonomy Triggers (F-8, R-7):
```sql
-- Enforce human principal type on memberships INSERT and UPDATE
CREATE TRIGGER trg_memberships_human_principal_insert_check BEFORE INSERT ON memberships
BEGIN
    SELECT RAISE(FAIL, 'MEMBERSHIP_PRINCIPAL_TYPE_INVALID: memberships may only bind human principals')
    FROM principals
    WHERE id = NEW.principal_id AND type != 'human';
END;

CREATE TRIGGER trg_memberships_human_principal_update_check BEFORE UPDATE OF principal_id ON memberships
BEGIN
    SELECT RAISE(FAIL, 'MEMBERSHIP_PRINCIPAL_TYPE_INVALID: memberships may only bind human principals')
    FROM principals
    WHERE id = NEW.principal_id AND type != 'human';
END;

-- Enforce service principal type on organization_service_principals INSERT and UPDATE
CREATE TRIGGER trg_osp_service_principal_insert_check BEFORE INSERT ON organization_service_principals
BEGIN
    SELECT RAISE(FAIL, 'OSP_PRINCIPAL_TYPE_INVALID: organization_service_principals may only bind service principals')
    FROM principals
    WHERE id = NEW.principal_id AND type != 'service';
END;

CREATE TRIGGER trg_osp_service_principal_update_check BEFORE UPDATE OF principal_id ON organization_service_principals
BEGIN
    SELECT RAISE(FAIL, 'OSP_PRINCIPAL_TYPE_INVALID: organization_service_principals may only bind service principals')
    FROM principals
    WHERE id = NEW.principal_id AND type != 'service';
END;
```

---

### 3.2 Canonical Step 6 Unified RBAC Evaluation Algebra

Step 6 resolves coarse capabilities uniformly based on principal type:

```mermaid
graph TD
    Step6[Step 6: Resolve Coarse RBAC Capability] --> RegCheck[Verify Organization Active Registry Version prv.status = 'active']
    RegCheck -->|Inactive or Unbound| DenyReg[DENY: REGISTRY_NOT_BOUND | REGISTRY_DEPRECATED]
    RegCheck --> ActionCheck[Verify Action exists in permission_definitions]
    ActionCheck -->|Unknown Action| DenyPerm[DENY: PERMISSION_UNKNOWN]
    ActionCheck --> TypeBranch{Principal Type?}
    
    TypeBranch -->|Human| QueryMem[Query membership_role_assignments + role_permissions]
    TypeBranch -->|Service| QuerySvc[Query service_principal_role_assignments + role_permissions]
    
    QueryMem --> MatchPerm{Permission held in active registry?}
    QuerySvc --> MatchPerm
    
    MatchPerm -->|Yes| Step7[Proceed to Step 7 Fine-Grained ACL]
    MatchPerm -->|No roles or missing permission| DenyRBAC[DENY: RBAC_DENIED]
```

#### Resolution Algorithm:
1. Query `organization_permission_registries` for `organization_id` joined with `permission_registry_versions prv`.
   - If no registry is bound $\to$ **`DENY(REGISTRY_NOT_BOUND)`**.
   - If bound registry has `prv.status != 'active'` $\to$ **`DENY(REGISTRY_DEPRECATED)`**.
2. Query `permission_definitions` for `(registry_version, action)`.
   - If action does not exist in registry $\to$ **`DENY(PERMISSION_UNKNOWN)`**.
3. Resolve assigned roles based on principal taxonomy:
   - **For `principal.type = 'human'`**:
     ```sql
     SELECT rp.permission_name, rd.name as role_name, rd.is_system_template, rd.template_key
     FROM membership_role_assignments mra
     JOIN role_definitions rd ON rd.organization_id = mra.organization_id AND rd.id = mra.role_id
     JOIN role_permissions rp ON rp.organization_id = rd.organization_id AND rp.role_id = rd.id
     WHERE mra.organization_id = ?
       AND mra.membership_id = ?
       AND rp.registry_version = ?
       AND rp.permission_name = ?;
     ```
   - **For `principal.type = 'service'`**:
     ```sql
     SELECT rp.permission_name, rd.name as role_name, rd.is_system_template, rd.template_key
     FROM service_principal_role_assignments sra
     JOIN role_definitions rd ON rd.organization_id = sra.organization_id AND rd.id = sra.role_id
     JOIN role_permissions rp ON rp.organization_id = rd.organization_id AND rp.role_id = rd.id
     WHERE sra.organization_id = ?
       AND sra.organization_service_principal_id = ?
       AND rp.registry_version = ?
       AND rp.permission_name = ?;
     ```
4. If no matching role permission exists $\to$ **`DENY(RBAC_DENIED)`**.
5. If matching role permission exists $\to$ PROCEED to Step 7 with resolved role capabilities.

---

### 3.3 Privilege Escalation Ceiling & Mutation Permissions (F-5)

1. **Required Permission Definitions**:
   The following permissions are defined in the active registry:

   | Permission Name | Service | Risk Level | Description |
   |---|---|---|---|
   | `core.rbac.service_principal_role.assign` | `core` | `critical` | Assign a role to an organization service principal |
   | `core.rbac.service_principal_role.revoke` | `core` | `critical` | Remove a role from an organization service principal |

2. **Privilege Escalation Ceiling Invariant (F-5, P0-1)**:
   When an actor assigns a role to a service principal (or another human), the authorization engine enforces that:
   - The mutating actor MUST actively hold all permissions conferred by the target role within that organization, OR
   - The mutating actor MUST hold the immutable system template `Owner` role (`role.is_system_template = TRUE AND role.template_key = 'owner'`).
   An actor holding only `core.rbac.service_principal_role.assign` cannot grant roles containing permissions they themselves do not possess (`ERR_PRIVILEGE_ESCALATION_CEILING`).

---

### 3.4 Strict Evaluation Invariants

1. **Shared Registry & Role Definitions**:
   Service principals evaluate against the **exact same** `permission_definitions`, `role_definitions`, and `role_permissions` as human members. No bespoke "service-only" permission registry is introduced.
2. **Explicit Deny Precedence (Step 5)**:
   Explicit denies configured on service principals (`authorization_subjects WHERE subject_type = 'service_principal'`) execute at Step 5 and unconditionally override any role permission granted via `service_principal_role_assignments`.
3. **Step 7 Fine-Grained Resource Evaluation & Immutable Template Matching (F-6, P0-1)**:
   - Organization-wide non-resource actions succeed upon passing Step 6 coarse-RBAC and Step 8 contextual ABAC.
   - Privately owned resources require explicit resource grants (`resource_grants`) or unowned resource status. Service principals holding system template Admin/Owner roles (`rd.is_system_template = TRUE AND rd.template_key IN ('owner', 'admin')`) bypass private ACLs identically to human administrators.
4. **Authentication vs. Authorization Separation**:
   This ADR defines authorization state and evaluation only. How a service principal authenticates (API keys, mTLS, client credentials) is managed by dedicated authentication slices. The authorization engine receives an already-authenticated principal and verifies rights authoritatively in D1.

---

## 4. Policy Mutation & Version Invalidation

1. **Monotonic Version Invalidation**:
   - Assigning a role to a service principal (`assignServicePrincipalRole`) MUST increment `authorization_versions.version`.
   - Removing a role from a service principal (`removeServicePrincipalRole`) MUST increment `authorization_versions.version`.
2. **Atomic Batch Guarantee**:
   All service principal role mutations execute in a single atomic D1 transaction (`db.batch()`) containing:
   - The role assignment `INSERT` or conditional `DELETE`.
   - The conditional `authorization_versions` increment.
   - The immutable audit event (`core.rbac.service_principal_role_assigned` / `core.rbac.service_principal_role_removed`).
   - The transactional outbox event.
3. **No-Op Atomicity**:
   Removing a non-existent service principal role assignment is a strict no-op: 0 rows deleted, 0 version increments, 0 audit logs, and 0 outbox events.

---

## 5. Alternatives Considered & Rejected

1. **Synthesizing Fake Human Memberships for Service Principals**:
   - *Rejected*: Violates binary principal taxonomy in `identity-model.md §2`. Memberships represent human employment/invitation lifecycles and require user profiles. Conflating services with human memberships creates security audit confusion and breaks GDPR/privacy redaction pipelines.
2. **Hardcoded Administrative Bypass for Service Principals**:
   - *Rejected*: Severe violation of least privilege. Service principals must be restricted to explicitly assigned roles.
3. **Embedding Role Claims Inside Service Principal API Tokens / JWTs**:
   - *Rejected*: Violates `authorization-model.md §6.3` (*"Privileges resolved strictly server-side"*). Token-embedded roles cannot be invalidated instantaneously upon role revocation. D1 role assignments are authoritative.
4. **Polymorphic `role_assignments` Table with Subject Type Discrimination**:
   - *Rejected*: SQLite/D1 does not support conditional foreign keys on a single polymorphic column. Separate tables `membership_role_assignments` and `service_principal_role_assignments` maintain strict database-enforced relational integrity via compound foreign keys.

---

## 6. Security Consequences

- **Fail-Closed Default**: A service principal without an explicit role assignment continues to be denied access with `DENY_RBAC_DENIED`.
- **Tenant Confinement**: Service principals can only be assigned roles within their own organization; compound foreign keys prevent cross-tenant role binding.
- **Audit Traceability**: All service principal role assignments and mutations are recorded with structured actor context and aggregate ordering.

---

## 7. Migration 0005 Upgrade Contract: `0005_core_security_policies_and_service_rbac.sql` (P0-3)

### 7.1 Preamble & Immutability Boundary
- Accepted migrations `0001` through `0004` are **immutable, sealed, and must never be edited**.
- Migration `0005_core_security_policies_and_service_rbac.sql` defines the authoritative, deterministic transition from the v2.2 schema to the v2.3 schema.
- The entire migration executes within a single atomic D1 transaction. Any constraint violation, ambiguity, or unexpected legacy state MUST immediately abort and roll back the entire migration.

### 7.2 Step 1: `role_definitions` Table Reconstruction & Backfill
Existing `role_definitions` (from Migration 0002) lacks `template_key`. Because SQLite/D1 does not support adding complex CHECK constraints and partial indexes via simple `ALTER TABLE`, `role_definitions` is upgraded via table reconstruction:

1. **Create Staging Table**:
   ```sql
   CREATE TABLE new_role_definitions (
       id TEXT PRIMARY KEY,                       -- rol_<ulid>
       organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
       name TEXT NOT NULL,
       description TEXT NOT NULL,
       is_system_template BOOLEAN NOT NULL DEFAULT FALSE,
       template_key TEXT,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       CHECK (
           (is_system_template = 0 AND template_key IS NULL) OR
           (is_system_template = 1 AND template_key IN ('owner', 'admin', 'member'))
       ),
       UNIQUE(organization_id, id),
       UNIQUE(organization_id, name)
   );
   ```

2. **Deterministic Data Backfill**:
   Every existing role row is migrated using deterministic template mapping:
   ```sql
   INSERT INTO new_role_definitions (id, organization_id, name, description, is_system_template, template_key, created_at)
   SELECT
       id,
       organization_id,
       name,
       description,
       is_system_template,
       CASE
           WHEN is_system_template = 0 THEN NULL
           WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'owner' THEN 'owner'
           WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'admin' THEN 'admin'
           WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'member' THEN 'member'
           ELSE RAISE(FAIL, 'ERR_MIGRATION_UNMAPPABLE_SYSTEM_TEMPLATE: cannot map is_system_template=1 role with unexpected name')
       END,
       created_at
   FROM role_definitions;
   ```
   *Fail-Closed Guarantee*: If any existing row has `is_system_template = 1` but does not match one of the canonical names (`Owner`, `Admin`, `Member`), the migration fails closed immediately. No unmappable system templates are ever silently converted to custom roles or assigned null keys.

3. **Swap Tables & Create Indices / Triggers**:
   ```sql
   DROP TABLE role_definitions;
   ALTER TABLE new_role_definitions RENAME TO role_definitions;

   CREATE UNIQUE INDEX uq_role_definitions_org_template 
       ON role_definitions(organization_id, template_key) 
       WHERE template_key IS NOT NULL;

   CREATE TRIGGER trg_role_definitions_immutable_template 
       BEFORE UPDATE OF is_system_template, template_key ON role_definitions
   BEGIN
       SELECT RAISE(FAIL, 'ROLE_TEMPLATE_IMMUTABLE: cannot modify system template designation');
   END;
   ```

### 7.3 Step 2: `service_accounts` & `organization_service_principals` Compound Tenant Binding Upgrade (P0-2)
Migration 0001 created `service_accounts` without `organization_id`. In Migration 0005, `service_accounts` is upgraded to be explicitly tenant-owned, and `organization_service_principals` is upgraded with a compound foreign key:

1. **Preflight Ownership Validation**:
   Before reconstructing tables, preflight assertions run:
   - **Orphan Check**: `SELECT COUNT(*) FROM service_accounts sa WHERE NOT EXISTS (SELECT 1 FROM organization_service_principals osp WHERE osp.principal_id = sa.principal_id)`. If `> 0` $\to$ abort with `ERR_MIGRATION_ORPHAN_SERVICE_ACCOUNT`.
   - **Ambiguity Check**: `SELECT principal_id, COUNT(DISTINCT organization_id) FROM organization_service_principals GROUP BY principal_id HAVING COUNT(DISTINCT organization_id) > 1`. If any row returned $\to$ abort with `ERR_MIGRATION_AMBIGUOUS_SERVICE_ACCOUNT_OWNERSHIP`.

2. **Reconstruct `service_accounts`**:
   ```sql
   CREATE TABLE new_service_accounts (
       id TEXT PRIMARY KEY,                       -- svc_<ulid>
       organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
       principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
       name TEXT NOT NULL,
       description TEXT,
       system_managed BOOLEAN NOT NULL DEFAULT FALSE,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       UNIQUE(principal_id),
       UNIQUE(organization_id, principal_id),
       UNIQUE(organization_id, id)
   );

   INSERT INTO new_service_accounts (id, organization_id, principal_id, name, description, system_managed, created_at)
   SELECT sa.id, osp.organization_id, sa.principal_id, sa.name, sa.description, sa.system_managed, sa.created_at
   FROM service_accounts sa
   JOIN organization_service_principals osp ON osp.principal_id = sa.principal_id;

   DROP TABLE service_accounts;
   ALTER TABLE new_service_accounts RENAME TO service_accounts;
   ```

3. **Reconstruct `organization_service_principals`**:
   ```sql
   CREATE TABLE new_organization_service_principals (
       id TEXT PRIMARY KEY,                       -- osp_<ulid>
       organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
       principal_id TEXT NOT NULL,
       name TEXT NOT NULL,
       status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'revoked')),
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       FOREIGN KEY (organization_id, principal_id)
           REFERENCES service_accounts(organization_id, principal_id) ON DELETE CASCADE,
       UNIQUE(organization_id, principal_id),
       UNIQUE(organization_id, id)
   );

   INSERT INTO new_organization_service_principals (id, organization_id, principal_id, name, status, created_at)
   SELECT id, organization_id, principal_id, name, status, created_at
   FROM organization_service_principals;

   DROP TABLE organization_service_principals;
   ALTER TABLE new_organization_service_principals RENAME TO organization_service_principals;

   CREATE TRIGGER trg_osp_service_principal_insert_check BEFORE INSERT ON organization_service_principals
   BEGIN
       SELECT RAISE(FAIL, 'OSP_PRINCIPAL_TYPE_INVALID: organization_service_principals may only bind service principals')
       FROM principals
       WHERE id = NEW.principal_id AND type != 'service';
   END;

   CREATE TRIGGER trg_osp_service_principal_update_check BEFORE UPDATE OF principal_id ON organization_service_principals
   BEGIN
       SELECT RAISE(FAIL, 'OSP_PRINCIPAL_TYPE_INVALID: organization_service_principals may only bind service principals')
       FROM principals
       WHERE id = NEW.principal_id AND type != 'service';
   END;
   ```

### 7.4 Step 3: Creation of New Tables, Indexes, and Global Triggers
1. **`service_principal_role_assignments`**:
   ```sql
   CREATE TABLE service_principal_role_assignments (
       id TEXT PRIMARY KEY,                       -- sra_<ulid>
       organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
       organization_service_principal_id TEXT NOT NULL,
       role_id TEXT NOT NULL,
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       FOREIGN KEY (organization_id, organization_service_principal_id)
           REFERENCES organization_service_principals(organization_id, id) ON DELETE CASCADE,
       FOREIGN KEY (organization_id, role_id)
           REFERENCES role_definitions(organization_id, id) ON DELETE RESTRICT,
       UNIQUE(organization_id, organization_service_principal_id, role_id),
       UNIQUE(organization_id, id)
   );

   CREATE INDEX idx_sra_osp ON service_principal_role_assignments(organization_id, organization_service_principal_id);
   CREATE INDEX idx_sra_role ON service_principal_role_assignments(organization_id, role_id);
   ```

2. **`organization_security_policies`**:
   ```sql
   CREATE TABLE organization_security_policies (
       organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
       ip_allowlist_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(ip_allowlist_mode IN ('disabled', 'enforced_all', 'enforced_admins')),
       device_posture_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(device_posture_mode IN ('disabled', 'compliant_only', 'managed_only')),
       mfa_enforcement TEXT NOT NULL DEFAULT 'optional' CHECK(mfa_enforcement IN ('optional', 'required_all', 'required_admins')),
       allow_internal_dispatch BOOLEAN NOT NULL DEFAULT TRUE,
       session_idle_timeout_seconds INTEGER NOT NULL DEFAULT 86400 CHECK(session_idle_timeout_seconds >= 300),
       session_absolute_timeout_seconds INTEGER NOT NULL DEFAULT 604800 CHECK(session_absolute_timeout_seconds >= 3600),
       updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       updated_by_membership_id TEXT,
       FOREIGN KEY (organization_id, updated_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE SET NULL
   );
   ```

3. **`organization_ip_allowlist_entries`**:
   ```sql
   CREATE TABLE organization_ip_allowlist_entries (
       id TEXT PRIMARY KEY,                       -- ipa_<ulid>
       organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
       cidr_block TEXT NOT NULL,
       ip_version INTEGER NOT NULL CHECK(ip_version IN (4, 6)),
       description TEXT NOT NULL,
       status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       created_by_membership_id TEXT,
       FOREIGN KEY (organization_id, created_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE SET NULL,
       UNIQUE(organization_id, cidr_block),
       UNIQUE(organization_id, id)
   );

   CREATE INDEX idx_oiae_org_active ON organization_ip_allowlist_entries(organization_id, status);
   ```

4. **Principal Type Immutability Trigger**:
   ```sql
   CREATE TRIGGER trg_principals_immutable_type BEFORE UPDATE OF type ON principals
   BEGIN
       SELECT RAISE(FAIL, 'PRINCIPAL_TYPE_IMMUTABLE: principal type cannot be modified');
   END;
   ```

### 7.5 Atomicity & Fail-Closed Guarantee
If any preflight validation fails, any SQL statement fails, or any constraint is violated, Cloudflare D1 rolls back the entire batch. No intermediate or inconsistent state is ever persisted. Existing production applications and databases remain in their valid Migration 0004 state.
