# Oryol Core Executable Predecessor Schema Manifest

**Document Purpose**: Pinned review evidence for the actual executable predecessor database schema of Oryol Core. This document provides an immutable, self-contained reference for independent architectural and security reviews in isolated sandboxes where cross-repository access to `serlekan/oryol-core` may be unavailable.

> [!IMPORTANT]
> **AUTHORITY STATEMENT**: THE ACCEPTED CORE MIGRATIONS REMAIN AUTHORITATIVE FOR EXECUTABLE PREDECESSOR FACTS. This manifest is a pinned review snapshot to make the architecture proposal auditable in isolation. If any statement in this manifest differs from the pinned migrations in `serlekan/oryol-core`, the migration wins and the manifest is erroneous.

---

## 1. Pinned Predecessor Baseline

- **Repository**: `serlekan/oryol-core`
- **Exact Accepted Commit SHA**: `ca3fb9c18e8e061c277a3e2f4f009bbc9b961717`
- **Accepted Executable Migration Files & Blob Hashes**:

| Migration File | Git Blob Hash | Description |
|---|---|---|
| `migrations/0001_core_identity.sql` | `176f5684e75f4f8a5bbd327bf92a3a837441a234` | Core Identity Foundation: principals, users, credentials, service_accounts, api_credentials |
| `migrations/0002_core_tenancy_and_rbac.sql` | `086164b60e8f0920b27751e40801c45813e74e4b` | Multi-Tenancy, Memberships, Teams, Role Definitions, OSP, Invitations |
| `migrations/0003_core_resource_registry_and_apps.sql` | `31a906726cc9def79848ac7b92d21efa5d89587d` | Resource Registry, Authorization Subjects, Explicit Denies, App Entitlements |
| `migrations/0004_core_placement_and_events.sql` | `9755c8bd8b43a3de45c283d25dbec6c910453625` | Organization Placement, Sessions, Security Versions, Outbox/Inbox, Audit Log |

---

## 2. Load-Bearing Predecessor DDL Excerpts

The following verbatim DDL excerpts define the exact executable predecessor tables interacting with Migration 0005.

### 2.1 Identity Foundation (`0001_core_identity.sql`)

```sql
CREATE TABLE principals (
    id TEXT PRIMARY KEY,                       -- prn_<ulid>
    type TEXT NOT NULL CHECK(type IN ('human', 'service')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'deactivated')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE service_accounts (
    id TEXT PRIMARY KEY,                       -- svc_<ulid>
    principal_id TEXT UNIQUE NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT,
    system_managed INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

*Predecessor Audit Note*: `service_accounts` was created without `organization_id`. This historical implementation drift is explicitly reconciled by Migration 0005.

---

### 2.2 Tenancy & RBAC Foundation (`0002_core_tenancy_and_rbac.sql`)

```sql
CREATE TABLE organizations (
    id TEXT PRIMARY KEY,                       -- org_<ulid>
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'archived', 'deletion_pending')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE teams (
    id TEXT PRIMARY KEY,                       -- team_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, id),
    UNIQUE(organization_id, slug)
);

CREATE TABLE role_definitions (
    id TEXT PRIMARY KEY,                       -- rol_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    is_system_template INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, id),
    UNIQUE(organization_id, name)
);

CREATE TABLE organization_service_principals (
    id TEXT PRIMARY KEY,                       -- osp_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'revoked')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, principal_id),
    UNIQUE(organization_id, id)
);

CREATE TABLE invitations (
    id TEXT PRIMARY KEY,                       -- inv_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role_id TEXT NOT NULL,
    invited_by_membership_id TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    member_type TEXT NOT NULL DEFAULT 'employee' CHECK(member_type IN ('employee', 'contractor', 'guest')),
    expires_at DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'revoked', 'expired')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, role_id) REFERENCES role_definitions(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, invited_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, id)
);
```

*Predecessor Audit Notes*:
1. `role_definitions` already carries `UNIQUE(organization_id, id)` and `UNIQUE(organization_id, name)`. It defines `is_system_template` without `template_key`.
2. `invitations` already binds via **compound foreign keys**: `(organization_id, role_id) REFERENCES role_definitions(organization_id, id)` and `(organization_id, invited_by_membership_id) REFERENCES memberships(organization_id, id)`. It preserves `member_type`. Predecessor Migration 0002 omitted `UNIQUE(organization_id, email, status)`, which Migration 0005 restores via preflight duplicate validation and in-place `CREATE UNIQUE INDEX uq_invitations_org_email_status ON invitations(organization_id, email, status);` without table reconstruction.
3. `organization_service_principals` already carries `UNIQUE(organization_id, id)` and `UNIQUE(organization_id, principal_id)`.

---

### 2.3 Resource Registry & Authorization Deny Chain (`0003_core_resource_registry_and_apps.sql`)

```sql
CREATE TABLE resource_registry (
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    resource_type TEXT NOT NULL,               -- 'mailbox', 'thread', 'document', 'deal', 'wallet'
    resource_id TEXT NOT NULL,
    application_id TEXT NOT NULL,              -- 'oryol-mail', 'oryol-crm', 'oryol-drive', 'virel'
    owner_membership_id TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived', 'deleted')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id, resource_type, resource_id),
    FOREIGN KEY (organization_id, owner_membership_id) REFERENCES memberships(organization_id, id)
);

CREATE TABLE authorization_subjects (
    id TEXT PRIMARY KEY,                       -- asb_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('membership', 'team', 'service_principal')),
    membership_id TEXT,
    team_id TEXT,
    organization_service_principal_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, team_id) REFERENCES teams(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, organization_service_principal_id) REFERENCES organization_service_principals(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, id),
    CHECK (
        (subject_type = 'membership' AND membership_id IS NOT NULL AND team_id IS NULL AND organization_service_principal_id IS NULL) OR
        (subject_type = 'team' AND team_id IS NOT NULL AND membership_id IS NULL AND organization_service_principal_id IS NULL) OR
        (subject_type = 'service_principal' AND organization_service_principal_id IS NOT NULL AND membership_id IS NULL AND team_id IS NULL)
    )
);

CREATE TABLE explicit_denies (
    id TEXT PRIMARY KEY,                       -- dny_<ulid>
    organization_id TEXT NOT NULL,
    authorization_subject_id TEXT NOT NULL,
    action_pattern TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, authorization_subject_id) REFERENCES authorization_subjects(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, resource_type, resource_id) REFERENCES resource_registry(organization_id, resource_type, resource_id) ON DELETE CASCADE,
    UNIQUE(organization_id, id)
);
```

---

## 3. The Authorization Deny Chain Evidence

The predecessor DDL establishes the exact cascade dependency graph:

```text
organization_service_principals (Migration 0002)
    ▲
    │ FOREIGN KEY (organization_id, organization_service_principal_id) 
    │     REFERENCES organization_service_principals(organization_id, id) ON DELETE CASCADE
authorization_subjects (Migration 0003)
    ▲
    │ FOREIGN KEY (organization_id, authorization_subject_id) 
    │     REFERENCES authorization_subjects(organization_id, id) ON DELETE CASCADE
explicit_denies (Migration 0003)
```

**Security Vulnerability Prevented**:
Any naive drop of `organization_service_principals` while child records exist would trigger SQLite cascading deletion through `authorization_subjects` down to `explicit_denies`. This would silently erase service-principal explicit-deny security policies. Migration 0005 prevents this via shadow tables, preflight assertions, leaf-to-root retirement, and root-to-leaf promotion.

---

## 4. Compound Parent-Key Eligibility Inventory

SQLite requires that columns referenced by foreign keys must be backed by a `PRIMARY KEY` or `UNIQUE` constraint on the parent table. The following table verifies parent-key validity for all compound foreign keys interacting with Migration 0005:

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
