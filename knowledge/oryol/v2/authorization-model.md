# Oryol Authorization Policy Algebra v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Executable 8-Step Algebra, Published Permission Registry, Tenant-Safe Roles & Explicit Deny Subjects

---

## 1. Canonical Authorization Contract

All authorization decisions in Oryol Workspace are executed through the standard `authorize({ principal, membership, organization, action, resource, context })` interface:

```typescript
export interface AuthorizationRequest {
  principal: {
    id: string;                // prn_<ulid>
    type: 'human' | 'service';
    status: 'active' | 'suspended' | 'deactivated';
  };
  membership: {
    id: string;                // mem_<ulid>
    principalId: string;
    organizationId: string;
    role: string;
    status: 'active' | 'suspended' | 'left';
    customPermissions?: string[];
  };
  organization: {
    id: string;                // org_<ulid>
    status: 'active' | 'suspended' | 'archived' | 'deletion_pending';
    entitledApps: string[];    // ['oryol-mail', 'oryol-crm', ...]
  };
  action: string;              // Canonical 3-part name e.g. "mail.messages.send"
  resource: {
    type: string;              // "mailbox", "thread", "deal", "document"
    id: string;                // "mbx_123", "deal_456"
    organizationId: string;
    attributes?: Record<string, unknown>;
  };
  context: {
    ipAddress: string;         // Trusted edge context
    clientType: 'web' | 'mobile' | 'api' | 'automation';
    timestamp: string;         // ISO-8601 UTC
    untrustedHeaders?: Record<string, string>; // Sanitized & separated
  };
}

export function authorize(req: AuthorizationRequest): Promise<AuthorizationResult>;
```

---

## 2. Authoritative Permission Registry & Security Entities

```sql
-- 1. Immutable Published Permission Registry Versions
CREATE TABLE permission_registry_versions (
    version INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('draft', 'active', 'deprecated')),
    published_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    software_min_version TEXT NOT NULL
);

-- 2. Published Permission Vocabulary (Bound to Immutable Registry Version)
CREATE TABLE permission_definitions (
    registry_version INTEGER NOT NULL REFERENCES permission_registry_versions(version) ON DELETE RESTRICT,
    name TEXT NOT NULL,                        -- e.g. 'mail.messages.send'
    service TEXT NOT NULL,                     -- 'core', 'mail', 'crm', 'calendar', 'drive', 'virel'
    description TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low', 'medium', 'high', 'critical')),
    is_inheritable BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(registry_version, name)
);

-- 3. Role Definitions: Organization-Scoped with System Templates
CREATE TABLE role_definitions (
    id TEXT PRIMARY KEY,                       -- rol_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name TEXT NOT NULL,                        -- 'Owner', 'Admin', 'Member', 'CustomRole'
    description TEXT NOT NULL,
    is_system_template BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, id),
    UNIQUE(organization_id, name)
);

-- 4. Role Permissions Mapping (Flat, Non-Recursive in Phase 1)
CREATE TABLE role_permissions (
    organization_id TEXT NOT NULL,
    role_id TEXT NOT NULL,
    permission_name TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(organization_id, role_id, permission_name),
    FOREIGN KEY (organization_id, role_id) REFERENCES role_definitions(organization_id, id) ON DELETE CASCADE
);

-- 5. Canonical Typed Authorization Subjects for Explicit Deny
CREATE TABLE authorization_subjects (
    id TEXT PRIMARY KEY,                       -- asb_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('membership', 'team', 'principal')),
    membership_id TEXT,
    team_id TEXT,
    principal_id TEXT REFERENCES principals(id),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, team_id) REFERENCES teams(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, id)
);

-- 6. Explicit Deny Rules (Precedence over All Grants; Structurally Bound to Organization)
CREATE TABLE explicit_denies (
    id TEXT PRIMARY KEY,                       -- dny_<ulid>
    organization_id TEXT NOT NULL,
    authorization_subject_id TEXT NOT NULL,
    action_pattern TEXT NOT NULL,              -- e.g. 'mail.*', 'core.domains.manage'
    resource_type TEXT,                        -- NULL for all resources in org
    resource_id TEXT,                          -- NULL for all instances
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, authorization_subject_id) REFERENCES authorization_subjects(organization_id, id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id, resource_type, resource_id) REFERENCES resource_registry(organization_id, resource_type, resource_id) ON DELETE CASCADE
);

-- 7. Monotonic Authorization Version Tracking
CREATE TABLE authorization_versions (
    organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 1,        -- Monotonically incremented on membership/role changes
    last_invalidated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Permission Registry Version vs. Authorization Version
- **Permission Registry Version**: The static, immutable schema version of the published permission dictionary/vocabulary supported by the software release.
- **Authorization Version**: A runtime monotonic integer incremented whenever an organization's memberships, role bindings, or security policies mutate, allowing Edge tokens and high-risk checks to detect stale cached privileges.

---

## 3. Service-to-Application Entitlement Mapping

When evaluating Step 3 (`Validate Application Entitlement`), the `action.service` is mapped deterministically to application installations:

| Action Service | Required Application Installation | Entitlement Semantics |
|---|---|---|
| `core` | *None (Always Entitled)* | Platform capabilities (identity, org settings, memberships, audit) are **always entitled** for active organizations, subject to permission checks. Core actions never fail `APP_NOT_ENTITLED`. |
| `mail` | `oryol-mail` | Requires active `application_installations` record for `oryol-mail`. |
| `crm` | `oryol-crm` | Requires active `application_installations` record for `oryol-crm`. |
| `drive` | `oryol-drive` | Requires active `application_installations` record for `oryol-drive`. |
| `virel` | `virel` | Requires active `application_installations` record for `virel`. |
| `calendar` | `oryol-calendar` | Requires active `application_installations` record for `oryol-calendar`. |

---

## 4. Mandatory 8-Step Evaluation Algebra

Every evaluation executes in strict linear order:

```text
1. Validate Principal Active
   └─► If principal.status != 'active' ──► DENY(PRINCIPAL_INACTIVE)

2. Validate Membership Binding & State
   └─► If membership.principal_id != principal.id ──► DENY(MEMBERSHIP_PRINCIPAL_MISMATCH)
   └─► If membership.organization_id != organization.id ──► DENY(MEMBERSHIP_ORG_MISMATCH)
   └─► If membership.status != 'active' ──► DENY(MEMBERSHIP_INACTIVE)

3. Validate Application Entitlement & Organization State
   └─► If organization.status != 'active' ──► DENY(ORGANIZATION_INACTIVE)
   └─► If action.service == 'core': PROCEED (Always entitled for active org)
   └─► If mapped application_id NOT IN organization.entitledApps ──► DENY(APP_NOT_ENTITLED)

4. Resource Tenant Alignment & Brokered Grants
   └─► If resource.organizationId == organization.id: PROCEED
   └─► If resource.organizationId != organization.id:
         └─► Query authoritative `cross_org_grants` for (source_org=resource.organizationId, target_membership=membership.id, resource, permission).
         └─► If valid grant found: PROCEED to Step 5 (tenant exception satisfied).
         └─► If absent or expired ──► DENY(CROSS_TENANT_VIOLATION)

5. Apply Explicit Deny Rules
   └─► If matching `explicit_denies` rule exists for subject ──► DENY(EXPLICIT_DENY)

6. Resolve Coarse RBAC Capability
   └─► Resolve assigned roles for membership from `role_permissions`.
   └─► Note: Brokered cross-org grants require the user to hold the coarse capability (e.g. `drive.documents.read`) in their own organization.
   └─► If permission is NOT present in any active role ──► DENY(RBAC_DENIED)

7. Resolve Fine-Grained Resource ACL / ReBAC Grant
   └─► If resource requires specific ACL and no matching `resource_grants`, `cross_org_grants`, or hierarchy inheritance exists ──► DENY(ACL_DENIED)

8. Apply Contextual ABAC & Fallback
   └─► Validate trusted edge context (IP allowlist, device posture).
   └─► If all checks pass ──► ALLOW; otherwise ──► DEFAULT_DENY
```

---

## 5. Hierarchy, Inheritance & Context Invariants

1. **Role Inheritance (Phase 1 Invariant)**:
   Arbitrary recursive role inheritance is **disallowed** in Phase 1. Role-permission mappings are flat and direct.
2. **Resource Hierarchies & Inheritance**:
   - *Mail*: `Domain ──► Mailbox ──► Thread ──► Message`
   - *Drive*: `Drive/Space ──► Folder ──► Document`
   - *CRM*: `Account ──► Deal / Contact`
   Permissions propagate down a hierarchy only when the permission definition explicitly specifies `is_inheritable = TRUE`.
3. **Trusted vs. Untrusted Contextual Attributes**:
   - **Trusted**: Server-resolved organization, server-resolved membership, authenticated session ID, authoritative resource metadata from D1, Cloudflare connecting IP.
   - **Untrusted**: Arbitrary client request headers, client-supplied organization IDs in body, unverified client ACL assertions.
