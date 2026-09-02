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

**Canonical Phase-1 Ownership Rule**:
- A service account has exactly one owning organization (`service_accounts.organization_id`).
- Once Migration 0005 has backfilled the organization owner, or once a new service account is created, its organization ownership is immutable during Phase 1 (`organization_id` is required, tenant-authoritative, and immutable after creation/backfill).
- Cross-organization transfer of a service account is NOT supported in Phase 1 (`organization_id` cannot be modified or set to NULL via UPDATE).
- A future transfer capability requires a separate architecture decision.
- Both creation and update operations are protected by database triggers: `organization_id` cannot be set to NULL on INSERT or UPDATE, and cannot be changed from one organization to another on UPDATE.


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

### 7.1 Preamble, Immutability Boundary & Canonical Source-of-Truth Roles
- Accepted migrations `0001` through `0004` are **immutable, sealed, and must never be edited**.
- Migration `0005_core_security_policies_and_service_rbac.sql` defines the authoritative, deterministic transition from the actual executable `0001`–`0004` predecessor schema to the v2.3 target schema.
- **Three-Phase Execution Boundary**:
  1. **Host Semantic Preflight**: Read-only validation queries execute before the batch in trusted migration orchestration. If any preflight assertion fails, deployment immediately aborts and NO DDL batch is issued (no rollback claim is necessary because no database mutation occurred).
  2. **Atomic D1 Batch (`db.batch([...])`)**: Destructive migration statements and rollback-critical assertions execute in a single atomic transaction. Rollback-critical assertions are modeled as statements capable of failing within the batch (via a temporary `_migration_assert` table with `CHECK(value = 1)`). Any constraint violation or assertion failure causes SQLite / Cloudflare D1 to immediately roll back the entire transaction.
  3. **Post-Batch Confirmation**: Post-batch checks (e.g. `PRAGMA foreign_key_check`) run on the committed database for defense-in-depth and observability. Post-batch validation confirms committed state; any unexpected failure is treated as a fatal incident condition (`MIGRATION_POSTCONDITION_FAILURE`).
- **Canonical Migration 0005 Source-of-Truth Roles**:
  - **A. Frozen Architecture v2.2**: Describes the previously approved **INTENDED** architecture semantics.
  - **B. Accepted Oryol Core Migrations 0001–0004**: Describe the **ACTUAL EXECUTABLE predecessor database schema**.
  - **C. Architecture v2.3**: Defines the **TARGET** semantics.
  - **D. Migration 0005**: MUST deterministically transform the actual executable `0001`–`0004` schema into the v2.3 target while restoring any approved v2.2 invariant that implementation previously omitted.
  - **E. Accepted Migrations 0001–0004**: Remain permanently immutable.
- **Historical Architecture / Implementation Drift**:
  Frozen Architecture v2.2 specified `service_accounts.organization_id` as required tenant ownership (`TEXT NOT NULL`). The accepted executable Core Migration 0001 omitted this column (`service_accounts` was created without `organization_id`). Because migrations 0001–0004 are sealed and immutable, Migration 0005 is the explicit reconciliation point that resolves this historical implementation drift by adding, backfilling, validating, and permanently enforcing `organization_id`.
- **Lifecycle Invariant vs Migration Mechanism (Preserve Security State)**:
  `ON DELETE CASCADE` is runtime lifecycle behavior, **NOT a data migration mechanism**. Migration 0005 MUST NEVER intentionally cascade-delete authorization state and then attempt to infer or reconstruct it afterward. Particularly, service-principal explicit denies (`explicit_denies`) and authorization subjects (`authorization_subjects`) are security-critical authorization policy state that must be preserved explicitly before any predecessor table retirement.
- **Foreign Key Execution Safety (A0-1)**: Because D1 enforces foreign keys, existing tables with inbound references (`role_definitions`, `service_accounts`) MUST NOT be dropped or recreated via simple table swap. Tables evolve via non-destructive in-place `ALTER TABLE ADD COLUMN`, authoritative backfill, compound indexes, triggers, or through deterministic shadow-table reconstruction that migrates the complete dependent chain.

### 7.2 Predecessor Executable Schema Audit & Inbound Dependency Inventory
To prevent faulty migration assumptions, Migration 0005 is audited directly against the actual accepted executable migrations in `serlekan/oryol-core` at commit `ca3fb9c18e8e061c277a3e2f4f009bbc9b961717`. A complete, self-contained verbatim excerpt of all load-bearing predecessor tables and their Git blob hashes is pinned in [predecessor-schema-manifest.md](file:///C:/Users/lekan/Documents/sera/repo/knowledge/oryol/v2.3/predecessor-schema-manifest.md).

| Migration 0005 Assumption | Actual Predecessor Schema Evidence (Core 0001–0004) | v2.2 Architectural Intent | v2.3 Target | Required Upgrade Operation |
|---|---|---|---|---|
| `role_definitions.template_key` | Migration 0002 defines `role_definitions` with `is_system_template INTEGER NOT NULL DEFAULT 0` but no `template_key` column. | System role template identification (`Owner`, `Admin`, `Member`). | `template_key TEXT` with partial unique index `uq_role_definitions_org_template`. | In-place `ALTER TABLE ADD COLUMN template_key TEXT;`, deterministic `UPDATE` backfill, triggers. |
| `service_accounts.organization_id` | Migration 0001 defines `service_accounts(id, principal_id, name, description, system_managed, created_at)` omitting `organization_id`. | `organization_id TEXT NOT NULL REFERENCES organizations(id)`. | `organization_id TEXT NOT NULL REFERENCES organizations(id)` immutable in Phase 1. | In-place `ALTER TABLE ADD COLUMN`, backfill from OSP, compound unique indexes, INSERT NOT NULL & UPDATE immutability triggers. |
| `organization_service_principals` compound ownership FK | Migration 0002 defines single-column FK `principal_id REFERENCES principals(id) ON DELETE CASCADE`. | Single-column FK to `principals(id)`. | Compound FK `(organization_id, principal_id) REFERENCES service_accounts(organization_id, principal_id) ON DELETE CASCADE`. | Shadow table reconstruction migrating the full dependent authorization chain. |
| `authorization_subjects` inbound OSP FK | Migration 0003 defines `FOREIGN KEY (organization_id, organization_service_principal_id) REFERENCES organization_service_principals(organization_id, id) ON DELETE CASCADE`. | Compound FK binding service principals to OSP. | Rebound compound FK to reconstructed `organization_service_principals(organization_id, id)`. | Shadow table reconstruction preserving all subject types (`membership`, `team`, `service_principal`) and IDs. |
| `explicit_denies` inbound authorization_subject FK | Migration 0003 defines `FOREIGN KEY (organization_id, authorization_subject_id) REFERENCES authorization_subjects(organization_id, id) ON DELETE CASCADE`. | Compound FK binding denies to `authorization_subjects`. | Rebound compound FK to reconstructed `authorization_subjects(organization_id, id)`. | Shadow table reconstruction preserving all explicit-deny IDs and policy rules. |
| `invitations` tenant role/member integrity & status uniqueness | Migration 0002 defines compound FKs `FOREIGN KEY (organization_id, role_id) REFERENCES role_definitions(organization_id, id) ON DELETE RESTRICT` and `FOREIGN KEY (organization_id, invited_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE`, and preserves `member_type`, but omitted `UNIQUE(organization_id, email, status)`. | Frozen v2.2 specified `UNIQUE(organization_id, email, status)` to prevent duplicate active invitations. | `UNIQUE(organization_id, email, status)` restored via in-place index without table reconstruction. | **No table reconstruction required**. Preflight duplicate detection (`ERR_MIGRATION_DUPLICATE_INVITATION_STATE`) followed by in-place `CREATE UNIQUE INDEX uq_invitations_org_email_status ON invitations(organization_id, email, status);`. |

#### Canonical Compound Parent-Key Eligibility Inventory
SQLite requires that parent columns referenced by compound foreign keys must be backed by an explicit `PRIMARY KEY` or `UNIQUE` constraint on the parent table (otherwise raising `foreign key mismatch` at DML time). The following table inventories and verifies parent-key validity for all compound foreign keys interacting with Migration 0005:

| Referenced Parent Table & Key | Predecessor Source Migration | Constraint Type in Predecessor DDL | Child Tables Referencing This Parent Key |
|---|---|---|---|
| `role_definitions(organization_id, id)` | `0002_core_tenancy_and_rbac.sql:80` | `UNIQUE(organization_id, id)` | `invitations` (predecessor 0002), `membership_role_assignments` (predecessor 0002), `service_principal_role_assignments` (Migration 0005) |
| `memberships(organization_id, id)` | `0002_core_tenancy_and_rbac.sql:20` | `UNIQUE(organization_id, id)` | `invitations` (predecessor 0002), `authorization_subjects` (0003 & shadow), `team_memberships` (0002) |
| `teams(organization_id, id)` | `0002_core_tenancy_and_rbac.sql:30` | `UNIQUE(organization_id, id)` | `authorization_subjects` (0003 & shadow), `team_memberships` (0002) |
| `organization_service_principals(organization_id, id)` | `0002_core_tenancy_and_rbac.sql:115` | `UNIQUE(organization_id, id)` | `authorization_subjects` (0003 & shadow), `service_principal_role_assignments` (Migration 0005) |
| `authorization_subjects(organization_id, id)` | `0003_core_resource_registry_and_apps.sql:28` | `UNIQUE(organization_id, id)` | `explicit_denies` (0003 & shadow) |
| `resource_registry(organization_id, resource_type, resource_id)` | `0003_core_resource_registry_and_apps.sql:13` | `PRIMARY KEY (organization_id, resource_type, resource_id)` | `explicit_denies` (0003 & shadow), `resource_grants` (0003) |
| `service_accounts(organization_id, principal_id)` | `0005_core_security_policies_and_service_rbac.sql` | `CREATE UNIQUE INDEX idx_service_accounts_org_principal ON service_accounts(organization_id, principal_id);` | `new_organization_service_principals` (Migration 0005 target) |

Every compound parent key referenced by Migration 0005 target DDL is physically backed by an explicit `PRIMARY KEY` or `UNIQUE` constraint in the executable predecessor schema or explicitly created in-place by Migration 0005 prior to referencing.

#### Actual Inbound Foreign Key Inventory for OSP & Authorization Deny Chain
`organization_service_principals` **DOES HAVE CHILD DEPENDENCIES** in the accepted predecessor schema:
```text
organization_service_principals (Migration 0002)
    ▲
    │ Inbound FK: (organization_id, organization_service_principal_id) ON DELETE CASCADE
authorization_subjects (Migration 0003)
    ▲
    │ Inbound FK: (organization_id, authorization_subject_id) ON DELETE CASCADE
explicit_denies (Migration 0003)
```

**Fatal Risk of Naive OSP Drop**:
Dropping `organization_service_principals` while child rows exist would either fail closed with `FOREIGN KEY constraint failed` under D1 foreign key enforcement, or, if foreign keys were disabled/cascaded, trigger a cascading deletion: `organization_service_principals → authorization_subjects → explicit_denies`. This would silently erase service-principal explicit-deny policies, creating a catastrophic fail-open vulnerability.

**Hard Security Invariant: Authorization Deny Chain Preservation**:
Migration 0005 MUST unconditionally preserve:
1. All `organization_service_principals` IDs (`osp_...`)
2. All `authorization_subjects` IDs (`asb_...`), including `membership`, `team`, and `service_principal` types
3. All `explicit_denies` IDs (`dny_...`)
4. All existing explicit-deny policies and action patterns
No service-principal deny rule may disappear, change ID, or be weakened during OSP schema evolution.

### 7.3 Step 1: `role_definitions` In-Place Upgrade & Template Integrity (A0-1, A2-3)
Existing `role_definitions` (from Migration 0002) is referenced by `membership_role_assignments`, `role_permissions`, and `invitations`. It is upgraded in-place without table dropping:

1. **Preflight System Template Validations**:
   - **Duplicate Template Check (A2-3)**: Verify that no organization possesses duplicate template names that would collide:
     ```sql
     SELECT organization_id, template_key, COUNT(*)
     FROM (
         SELECT organization_id,
             CASE
                 WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'owner' THEN 'owner'
                 WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'admin' THEN 'admin'
                 WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'member' THEN 'member'
             END as template_key
         FROM role_definitions
         WHERE is_system_template = 1
     )
     GROUP BY organization_id, template_key
     HAVING COUNT(*) > 1;
     ```
     If any row returned $\to$ abort with `ERR_MIGRATION_DUPLICATE_SYSTEM_TEMPLATE`.
   - **Unmappable Template Check**: Verify that all `is_system_template = 1` rows map to valid canonical keys (`owner`, `admin`, `member`):
     ```sql
     SELECT COUNT(*) FROM role_definitions
     WHERE is_system_template = 1 
       AND LOWER(TRIM(name)) NOT IN ('owner', 'admin', 'member');
     ```
     If `> 0` $\to$ abort with `ERR_MIGRATION_UNMAPPABLE_SYSTEM_TEMPLATE`.

2. **In-Place Schema Evolution & Backfill (A0-1)**:
   ```sql
   -- Add nullable template_key column in-place (preserves all child FKs)
   ALTER TABLE role_definitions ADD COLUMN template_key TEXT;

   -- Populate template_key deterministically
   UPDATE role_definitions
   SET template_key = CASE
       WHEN is_system_template = 0 THEN NULL
       WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'owner' THEN 'owner'
       WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'admin' THEN 'admin'
       WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'member' THEN 'member'
       ELSE NULL
   END;

   -- Enforce canonical uniqueness: at most one template_key per org
   CREATE UNIQUE INDEX uq_role_definitions_org_template 
       ON role_definitions(organization_id, template_key) 
       WHERE template_key IS NOT NULL;

   -- Enforce check constraint and template immutability via triggers
   CREATE TRIGGER trg_role_definitions_template_invariant_insert BEFORE INSERT ON role_definitions
   BEGIN
       SELECT RAISE(FAIL, 'ROLE_TEMPLATE_INVALID: template_key must be NULL for custom roles and non-null for system templates')
       WHERE NOT (
           (NEW.is_system_template = 0 AND NEW.template_key IS NULL) OR
           (NEW.is_system_template = 1 AND NEW.template_key IN ('owner', 'admin', 'member'))
       );
   END;

   CREATE TRIGGER trg_role_definitions_immutable_template 
       BEFORE UPDATE OF is_system_template, template_key ON role_definitions
   BEGIN
       SELECT RAISE(FAIL, 'ROLE_TEMPLATE_IMMUTABLE: cannot modify system template designation');
   END;
   ```

### 7.4 Step 2: `service_accounts` In-Place Evolution & Dependent Authorization Deny Chain Reconstruction (P0-2, A0-1)
Migration 0001 omitted `organization_id` on `service_accounts`. In Migration 0005, `service_accounts` evolves in-place to establish permanent tenant ownership, and `organization_service_principals` along with its downstream dependent authorization chain (`authorization_subjects`, `explicit_denies`) is safely reconstructed using a deterministic 7-phase algorithm:

#### Part 1: `service_accounts` In-Place Evolution
1. **Preflight Ownership Validation**:
   - **Orphan Check**:
     ```sql
     SELECT COUNT(*) FROM service_accounts sa 
     WHERE NOT EXISTS (SELECT 1 FROM organization_service_principals osp WHERE osp.principal_id = sa.principal_id);
     ```
     If `> 0` $\to$ abort with `ERR_MIGRATION_ORPHAN_SERVICE_ACCOUNT`.
   - **Ambiguity Check**:
     ```sql
     SELECT principal_id, COUNT(DISTINCT organization_id) 
     FROM organization_service_principals 
     GROUP BY principal_id 
     HAVING COUNT(DISTINCT organization_id) > 1;
     ```
     If any row returned $\to$ abort with `ERR_MIGRATION_AMBIGUOUS_SERVICE_ACCOUNT_OWNERSHIP`.

2. **In-Place Schema Evolution of `service_accounts`**:
   ```sql
   -- Add organization_id column in-place (preserves incoming FKs)
   ALTER TABLE service_accounts ADD COLUMN organization_id TEXT REFERENCES organizations(id) ON DELETE RESTRICT;

   -- Authoritatively backfill organization_id from unique OSP binding
   UPDATE service_accounts
   SET organization_id = (
       SELECT osp.organization_id
       FROM organization_service_principals osp
       WHERE osp.principal_id = service_accounts.principal_id
   );

   -- Create compound unique indexes for tenant isolation and relational targeting
   CREATE UNIQUE INDEX idx_service_accounts_org_principal ON service_accounts(organization_id, principal_id);
   CREATE UNIQUE INDEX idx_service_accounts_org_id ON service_accounts(organization_id, id);

   -- Enforce NOT NULL invariant for all future service accounts
   CREATE TRIGGER trg_service_accounts_org_not_null BEFORE INSERT ON service_accounts
   BEGIN
       SELECT RAISE(FAIL, 'SERVICE_ACCOUNT_ORG_REQUIRED: organization_id must not be null')
       WHERE NEW.organization_id IS NULL;
   END;

   -- Enforce immutable tenant ownership for service accounts (rejects nullification and cross-organization transfer)
   CREATE TRIGGER trg_service_accounts_org_immutable BEFORE UPDATE OF organization_id ON service_accounts
   BEGIN
       SELECT RAISE(FAIL, 'SERVICE_ACCOUNT_ORG_REQUIRED: organization_id must not be null')
       WHERE NEW.organization_id IS NULL;
       SELECT RAISE(FAIL, 'SERVICE_ACCOUNT_ORG_IMMUTABLE: service account organization ownership is immutable in Phase 1')
       WHERE NEW.organization_id != OLD.organization_id;
   END;
   ```

#### Part 2: Canonical OSP & Dependent Authorization Deny Chain Reconstruction Strategy
Because SQLite/D1 cannot alter foreign key constraints in place on `organization_service_principals`, and because dropping OSP directly would trigger catastrophic cascading deletions of authorization subjects and explicit denies, the migration executes a safe shadow-table reconstruction of the complete dependency chain:

##### Phase A — Preflight Validation & Baseline Row Counts
Before executing any DDL:
1. **Validate Principal Taxonomy**:
   ```sql
   SELECT COUNT(*) FROM organization_service_principals osp
   JOIN principals p ON p.id = osp.principal_id
   WHERE p.type != 'service';
   ```
   If `> 0` $\to$ abort with `ERR_MIGRATION_INVALID_OSP_TAXONOMY`.
2. **Validate OSP Service Account Correspondence**:
   ```sql
   SELECT COUNT(*) FROM organization_service_principals osp
   WHERE NOT EXISTS (SELECT 1 FROM service_accounts sa WHERE sa.principal_id = osp.principal_id);
   ```
   If `> 0` $\to$ abort with `ERR_MIGRATION_OSP_MISSING_SERVICE_ACCOUNT`.
3. **Validate All `authorization_subjects` Inbound References**:
   ```sql
   SELECT COUNT(*) FROM authorization_subjects asb
   WHERE (asb.subject_type = 'membership' AND NOT EXISTS (SELECT 1 FROM memberships m WHERE m.id = asb.membership_id AND m.organization_id = asb.organization_id))
      OR (asb.subject_type = 'team' AND NOT EXISTS (SELECT 1 FROM teams t WHERE t.id = asb.team_id AND t.organization_id = asb.organization_id))
      OR (asb.subject_type = 'service_principal' AND NOT EXISTS (SELECT 1 FROM organization_service_principals osp WHERE osp.id = asb.organization_service_principal_id AND osp.organization_id = asb.organization_id));
   ```
   If `> 0` $\to$ abort with `ERR_MIGRATION_ORPHAN_AUTH_SUBJECT`.
4. **Validate All `explicit_denies` References**:
   ```sql
   SELECT COUNT(*) FROM explicit_denies dny
   WHERE NOT EXISTS (
       SELECT 1 FROM authorization_subjects asb 
       WHERE asb.id = dny.authorization_subject_id 
         AND asb.organization_id = dny.organization_id
   );
   ```
   If `> 0` $\to$ abort with `ERR_MIGRATION_ORPHAN_EXPLICIT_DENY`.
5. **Record Preflight Row Count Baseline**:
   Capture expected counts for `organization_service_principals`, `authorization_subjects`, and `explicit_denies`.

##### Phase B — Create Target Shadow Tables
Construct shadow tables equipped with target v2.3 compound foreign keys without modifying the live tables:
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

CREATE TABLE new_authorization_subjects (
    id TEXT PRIMARY KEY,                       -- asb_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('membership', 'team', 'service_principal')),
    membership_id TEXT,
    team_id TEXT,
    organization_service_principal_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, team_id) REFERENCES teams(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, organization_service_principal_id) REFERENCES new_organization_service_principals(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, id),
    CHECK (
        (subject_type = 'membership' AND membership_id IS NOT NULL AND team_id IS NULL AND organization_service_principal_id IS NULL) OR
        (subject_type = 'team' AND team_id IS NOT NULL AND membership_id IS NULL AND organization_service_principal_id IS NULL) OR
        (subject_type = 'service_principal' AND organization_service_principal_id IS NOT NULL AND membership_id IS NULL AND team_id IS NULL)
    )
);

CREATE TABLE new_explicit_denies (
    id TEXT PRIMARY KEY,                       -- dny_<ulid>
    organization_id TEXT NOT NULL,
    authorization_subject_id TEXT NOT NULL,
    action_pattern TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, authorization_subject_id) REFERENCES new_authorization_subjects(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, resource_type, resource_id) REFERENCES resource_registry(organization_id, resource_type, resource_id) ON DELETE CASCADE,
    UNIQUE(organization_id, id)
);
```

##### Phase C — Copy Data Preserving All IDs, Columns, and Subject Types
Explicitly copy all existing state into shadow tables, preserving all primary keys:
```sql
-- 1. Copy OSP rows
INSERT INTO new_organization_service_principals (id, organization_id, principal_id, name, status, created_at)
SELECT id, organization_id, principal_id, name, status, created_at
FROM organization_service_principals;

-- 2. Copy ALL authorization subjects (membership, team, and service_principal)
INSERT INTO new_authorization_subjects (id, organization_id, subject_type, membership_id, team_id, organization_service_principal_id, created_at)
SELECT id, organization_id, subject_type, membership_id, team_id, organization_service_principal_id, created_at
FROM authorization_subjects;

-- 3. Copy ALL explicit deny policies
INSERT INTO new_explicit_denies (id, organization_id, authorization_subject_id, action_pattern, resource_type, resource_id, created_at)
SELECT id, organization_id, authorization_subject_id, action_pattern, resource_type, resource_id, created_at
FROM explicit_denies;
```

##### Phase D — Atomic In-Batch Parity Assertions Before Destructive DDL
Before retiring any legacy table, the atomic batch executes strict in-batch assertions. To ensure failures abort `db.batch([...])` and trigger an immediate transaction rollback inside SQLite/D1, assertions are evaluated via an assertion checkpoint table with `CHECK(value = 1)`:

```sql
-- Create temporary assertion checkpoint table:
CREATE TABLE _migration_assert (
    value INTEGER NOT NULL CHECK(value = 1)
);

-- 1. Assert Exact Row Count Parity:
-- If any count does not match, value becomes 0 -> CHECK fails -> batch rolls back.
INSERT INTO _migration_assert(value)
SELECT CASE
    WHEN (SELECT COUNT(*) FROM organization_service_principals) = (SELECT COUNT(*) FROM new_organization_service_principals)
     AND (SELECT COUNT(*) FROM authorization_subjects) = (SELECT COUNT(*) FROM new_authorization_subjects)
     AND (SELECT COUNT(*) FROM explicit_denies) = (SELECT COUNT(*) FROM new_explicit_denies)
    THEN 1 ELSE 0 END;

-- 2. Assert Complete ID Preservation:
-- All old OSP IDs exist in new OSP:
INSERT INTO _migration_assert(value)
SELECT CASE
    WHEN NOT EXISTS (
        SELECT 1 FROM organization_service_principals old_osp
        WHERE NOT EXISTS (SELECT 1 FROM new_organization_service_principals new_osp WHERE new_osp.id = old_osp.id)
    )
    THEN 1 ELSE 0 END;

-- All old authorization_subject IDs exist in new authorization_subjects:
INSERT INTO _migration_assert(value)
SELECT CASE
    WHEN NOT EXISTS (
        SELECT 1 FROM authorization_subjects old_asb
        WHERE NOT EXISTS (SELECT 1 FROM new_authorization_subjects new_asb WHERE new_asb.id = old_asb.id)
    )
    THEN 1 ELSE 0 END;

-- All old explicit-deny IDs exist in new explicit_denies:
INSERT INTO _migration_assert(value)
SELECT CASE
    WHEN NOT EXISTS (
        SELECT 1 FROM explicit_denies old_dny
        WHERE NOT EXISTS (SELECT 1 FROM new_explicit_denies new_dny WHERE new_dny.id = old_dny.id)
    )
    THEN 1 ELSE 0 END;

-- 3. Assert Relational Targeting Integrity:
-- All service-principal authorization subjects target a valid shadow OSP:
INSERT INTO _migration_assert(value)
SELECT CASE
    WHEN NOT EXISTS (
        SELECT 1 FROM new_authorization_subjects asb
        WHERE asb.subject_type = 'service_principal'
          AND NOT EXISTS (SELECT 1 FROM new_organization_service_principals osp WHERE osp.id = asb.organization_service_principal_id)
    )
    THEN 1 ELSE 0 END;

-- All explicit denies target a valid shadow authorization subject:
INSERT INTO _migration_assert(value)
SELECT CASE
    WHEN NOT EXISTS (
        SELECT 1 FROM new_explicit_denies dny
        WHERE NOT EXISTS (SELECT 1 FROM new_authorization_subjects asb WHERE asb.id = dny.authorization_subject_id)
    )
    THEN 1 ELSE 0 END;

-- Drop temporary assertion table once all assertions pass:
DROP TABLE _migration_assert;
```
*These assertions execute INSIDE `db.batch([...])` strictly BEFORE Phase E drops the predecessor tables. Any mismatch causes an immediate `CHECK constraint failed: value = 1` error, rolling back all uncommitted mutations.*

##### Phase E — Remove Old Chain Leaf-to-Root (Retirement Order)
Only after shadow parity is fully verified, retire the predecessor tables in strict leaf-to-root order:
```sql
-- Leaf-to-root retirement guarantees zero cascading deletion and complies with SQLite FK rules:
DROP TABLE explicit_denies;
DROP TABLE authorization_subjects;
DROP TABLE organization_service_principals;
```
*Under no circumstances may a parent table be dropped while dependent child tables exist.*

##### Phase F — Promote Shadow Tables Root-to-Leaf (Rename Order)
Promote the shadow tables to authoritative tables in root-to-leaf order and recreate triggers:
```sql
-- Root-to-leaf promotion:
ALTER TABLE new_organization_service_principals RENAME TO organization_service_principals;
ALTER TABLE new_authorization_subjects RENAME TO authorization_subjects;
ALTER TABLE new_explicit_denies RENAME TO explicit_denies;

-- Recreate canonical service principal taxonomy triggers
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

##### Phase G — Post-Batch Confirmation (Defense-in-Depth & Observability)
After the atomic `db.batch()` completes and commits, trusted migration orchestration executes post-batch confirmation checks:
1. Re-verify row counts for all three tables match the pre-migration baseline metrics.
2. Execute `PRAGMA foreign_key_check;` to assert zero foreign key constraint violations across the entire database.
3. Validate representative service-principal explicit-deny resolution to confirm policy evaluation executes identically before and after migration.

> [!NOTE]
> **Confirmation vs. Rollback Contract**: Post-batch checks execute on the committed database and cannot roll back the committed transaction. Critical correctness is guaranteed *before* commit by in-batch constraints, foreign keys, triggers, and Phase D assertions. Any post-batch check failure represents an operational incident condition (`MIGRATION_POSTCONDITION_FAILURE`) requiring deployment halt and alert triggering.

### 7.5 Step 3: `invitations` Predecessor Equivalence & In-Place Uniqueness Restoration
Audit of accepted Migration 0002 confirms that `invitations` in the executable predecessor schema already provides:
- Compound tenant-scoped role foreign key: `FOREIGN KEY (organization_id, role_id) REFERENCES role_definitions(organization_id, id) ON DELETE RESTRICT`
- Compound tenant-scoped inviter membership foreign key: `FOREIGN KEY (organization_id, invited_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE`
- Member classification preservation: `member_type TEXT NOT NULL DEFAULT 'employee' CHECK(member_type IN ('employee', 'contractor', 'guest'))`
- Tenant-bound primary identification: `id TEXT PRIMARY KEY` with `UNIQUE(organization_id, id)`
- Cryptographic token hash: `token_hash TEXT UNIQUE NOT NULL`

Predecessor Migration 0002 omitted the Frozen v2.2 invariant `UNIQUE(organization_id, email, status)`.

#### 1. Architectural Invariant: In-Place Restoration (No Table Reconstruction)
Because the accepted predecessor schema already enforces canonical compound tenant isolation and preserves `member_type`, Migration 0005 **MUST NOT reconstruct the `invitations` table**. The table is preserved in-place, and Migration 0005 restores the approved v2.2 uniqueness invariant via an in-place unique index.

#### 2. Host-Language Preflight Integrity Validations
Before executing the structural migration batch, trusted migration orchestration executes the following non-destructive read-only assertions:
```sql
-- 1. Verify all existing invitations reference valid memberships within the same organization:
SELECT COUNT(*) FROM invitations i
WHERE NOT EXISTS (
    SELECT 1 FROM memberships m 
    WHERE m.id = i.invited_by_membership_id 
      AND m.organization_id = i.organization_id
);
-- If > 0 -> abort with ERR_MIGRATION_ORPHAN_INVITATION

-- 2. Verify all existing invitations reference roles defined within the same organization (F1):
SELECT COUNT(*) FROM invitations i
WHERE NOT EXISTS (
    SELECT 1 FROM role_definitions rd
    WHERE rd.id = i.role_id
      AND rd.organization_id = i.organization_id
);
-- If > 0 -> abort with ERR_MIGRATION_CROSS_ORG_INVITATION_ROLE

-- 3. Detect conflicting predecessor duplicate states for (organization_id, email, status):
SELECT organization_id, email, status, COUNT(*)
FROM invitations
GROUP BY organization_id, email, status
HAVING COUNT(*) > 1;
-- If any row exists -> abort before DDL with ERR_MIGRATION_DUPLICATE_INVITATION_STATE
```
*If duplicate invitation states exist, migration orchestration aborts before issuing DDL. Duplicates are never automatically merged or deleted.*

#### 3. In-Batch In-Place Index Creation
Inside the atomic migration batch, Migration 0005 adds the uniqueness constraint non-destructively:
```sql
CREATE UNIQUE INDEX uq_invitations_org_email_status
ON invitations(organization_id, email, status);
```

### 7.6 Step 4: Creation of New Tables, Indexes, and Global Triggers
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
       mfa_enforcement TEXT NOT NULL DEFAULT 'optional' CHECK(mfa_enforcement IN ('optional', 'required_all', 'required_admins')),
       ip_allowlist_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(ip_allowlist_mode IN ('disabled', 'enforced_all', 'enforced_admins')),
       allow_internal_dispatch BOOLEAN NOT NULL DEFAULT TRUE,
       device_posture_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(device_posture_mode IN ('disabled', 'compliant_only', 'managed_only')),
       session_idle_timeout_seconds INTEGER NOT NULL DEFAULT 86400 CHECK(session_idle_timeout_seconds >= 300),
       session_absolute_timeout_seconds INTEGER NOT NULL DEFAULT 604800 CHECK(session_absolute_timeout_seconds >= 3600),
       version INTEGER NOT NULL DEFAULT 1,
       updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       updated_by_membership_id TEXT REFERENCES memberships(id) ON DELETE SET NULL
   );
   ```

3. **`organization_ip_allowlist_entries`**:
   ```sql
   CREATE TABLE organization_ip_allowlist_entries (
       id TEXT PRIMARY KEY,                       -- ipl_<ulid>
       organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
       cidr_block TEXT NOT NULL,                  -- Valid IPv4 (e.g. '198.51.100.0/24') or IPv6 (e.g. '2001:db8::/32')
       ip_version INTEGER NOT NULL CHECK(ip_version IN (4, 6)),
       label TEXT NOT NULL,                       -- Human readable identifier (e.g. 'Corporate VPN', 'HQ Office')
       status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
       created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       created_by_membership_id TEXT REFERENCES memberships(id) ON DELETE SET NULL,
       updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
       UNIQUE(organization_id, cidr_block),
       UNIQUE(organization_id, id)
   );

   CREATE INDEX idx_ip_allowlist_org_status ON organization_ip_allowlist_entries(organization_id, status);
   ```

4. **Principal Type Immutability Trigger**:
   ```sql
   CREATE TRIGGER trg_principals_immutable_type BEFORE UPDATE OF type ON principals
   BEGIN
       SELECT RAISE(FAIL, 'PRINCIPAL_TYPE_IMMUTABLE: principal type cannot be modified');
   END;
   ```

### 7.7 Fresh Install vs Migrated Schema Equivalence
The v2.3 architecture specifications (such as `identity-model.md` and `multi-tenancy.md`) show the canonical target DDL definitions intended for fresh database initialization. For existing production databases, Migration 0005 evolves the actual executable predecessor schema (`0001` through `0004`) to establish 100% equivalent authorization and relational invariants.

Because SQLite / Cloudflare D1 does not allow changing column nullability in place (`ALTER TABLE ALTER COLUMN`) or adding inline table `CHECK` constraints to existing tables, the physical DDL paths differ slightly while runtime security semantics remain strictly identical:

| Feature / Invariant | Fresh Install (Target DDL) | Migrated Schema (Predecessor + Migration 0005) | Invariant Equivalence Guarantee |
|---|---|---|---|
| `service_accounts.organization_id` | Column declared `TEXT NOT NULL REFERENCES organizations(id)` inline in `CREATE TABLE`. | `ALTER TABLE ADD COLUMN organization_id TEXT`, backfilled from OSP, compound unique indexes created, and guarded by `trg_service_accounts_org_not_null` (on INSERT) and `trg_service_accounts_org_immutable` (on UPDATE). | Disallows NULL on INSERT, disallows NULL or modification on UPDATE; strictly enforces immutable single-tenant ownership. |
| `role_definitions` System Template Invariants | Inline table `CHECK ((is_system_template = 0 AND template_key IS NULL) OR (is_system_template = 1 AND template_key IN ('owner', 'admin', 'member')))` in `CREATE TABLE`. | `ALTER TABLE ADD COLUMN template_key TEXT`, backfilled deterministically, unique partial index `uq_role_definitions_org_template`, guarded by `trg_role_definitions_template_invariant_insert` (on INSERT) and `trg_role_definitions_immutable_template` (on UPDATE). | Disallows unmappable templates, disallows custom roles with templates, disallows modifying template designations, enforces at most one of each template key per organization. |
| `role_definitions` Unique Names | `UNIQUE(organization_id, name)` inline in `CREATE TABLE`. | Predecessor Migration 0002 already defined `UNIQUE(organization_id, name)`. | Preserved untouched from predecessor schema. |
| `organization_service_principals` Compound Binding | Inline compound FK `(organization_id, principal_id) REFERENCES service_accounts(organization_id, principal_id)` in `CREATE TABLE`. | Reconstructed via shadow table `new_organization_service_principals` with compound FK, verified via parity assertions, and promoted root-to-leaf with taxonomy triggers. | Enforces strict compound tenant isolation; service principals can only bind service accounts in their own tenant. |
| `invitations` Tenant Binding & Status Uniqueness | Inline compound FKs to `role_definitions` and `memberships`, `member_type`, `token_hash`, and `UNIQUE(organization_id, email, status)`. | Predecessor Migration 0002 already defines compound FKs `(organization_id, role_id) REFERENCES role_definitions(organization_id, id)` and `(organization_id, invited_by_membership_id) REFERENCES memberships(organization_id, id)`, plus `member_type`. Predecessor omitted `UNIQUE(organization_id, email, status)`, which Migration 0005 restores in-place via preflight validation and `CREATE UNIQUE INDEX uq_invitations_org_email_status ON invitations(organization_id, email, status);`. | Zero table reconstruction required. Guarantees 100% equivalent tenant isolation, classification, and status uniqueness. |

There is zero semantic or authorization divergence between a fresh install and a migrated database.

### 7.8 Host-Language Preflight vs D1 Batch Execution Mechanics
Migration 0005 execution separates pre-flight validation, atomic batch execution, and post-batch confirmation:

1. **Host-Language Preflight Phase (Pre-Batch Guards, Outside `db.batch`)**:
   - Executed by the trusted migration orchestrator (deployment runner / Cloudflare Worker) prior to submitting the DDL batch.
   - Evaluates read-only semantic queries (`SELECT COUNT(*) ...`) for orphan service accounts, ambiguous ownership, principal taxonomy, authorization subject references, explicit deny integrity, and duplicate invitation status states.
   - If any query returns a non-zero count, the runner immediately halts deployment with a typed error code (`ERR_MIGRATION_*`) without issuing DDL or mutating database state. No transaction rollback claim is needed because no database mutation occurred.

2. **Atomic D1 Batch Execution Phase (`db.batch([...])`)**:
   - Executes the deterministic DDL and DML sequence (in-place column additions, backfills, indexes, triggers, shadow table creation, data copy, in-batch assertion checkpoint, leaf-to-root drop, and root-to-leaf rename) in a single atomic transaction.
   - Rollback-critical assertions (e.g. shadow count parity, ID preservation, and relational targeting integrity) execute *inside* this batch via an assertion checkpoint table (`_migration_assert` with `CHECK(value = 1)`).
   - If any statement in the batch fails or any assertion fails, SQLite / Cloudflare D1 automatically rolls back the entire batch. Old tables remain untouched.

3. **Post-Batch Confirmation Phase (Post-Batch Verification, After Commit)**:
   - The runner re-verifies row count preservation, executes `PRAGMA foreign_key_check;`, and tests policy resolution against the pre-batch baseline to confirm the committed state.
   - Post-batch checks do NOT claim rollback capability on an already-committed transaction; critical correctness is guaranteed before commit by in-batch constraints and assertions. Any post-batch check failure is treated as a fatal deployment incident (`MIGRATION_POSTCONDITION_FAILURE`).

### 7.9 Atomicity & Fail-Closed Guarantee
The execution contract enforces deterministic fail-closed behavior at every stage:
- If any preflight validation fails, deployment aborts prior to batch submission; database remains untouched.
- If any SQL statement fails or any in-batch assertion in `_migration_assert` evaluates to 0, SQLite / Cloudflare D1 rolls back the entire `db.batch([...])` transaction. No intermediate, corrupted, or inconsistent state is ever committed. Existing production databases remain in their valid Migration 0004 state.
- If any post-batch confirmation check fails, the system triggers `MIGRATION_POSTCONDITION_FAILURE`, blocking production routing to the unverified database state.
