# Oryol Authorization Policy Algebra v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Executable 8-Step Algebra, Conceptual Registry Entities, Role Inheritance Rules & Trusted Context

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
-- 1. Published Permission Vocabulary (Permission Registry Version)
CREATE TABLE permission_definitions (
    name TEXT PRIMARY KEY,                     -- e.g. 'mail.messages.send'
    service TEXT NOT NULL,                     -- 'core', 'mail', 'crm', 'calendar', 'drive', 'virel'
    description TEXT NOT NULL,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('low', 'medium', 'high', 'critical')),
    is_inheritable BOOLEAN NOT NULL DEFAULT FALSE,
    registry_version INTEGER NOT NULL DEFAULT 1 -- Global permission schema vocabulary version
);

-- 2. Role Definitions
CREATE TABLE role_definitions (
    id TEXT PRIMARY KEY,                       -- rol_<ulid>
    organization_id TEXT,                      -- NULL for global platform roles; org_<ulid> for custom roles
    name TEXT NOT NULL,                        -- 'Owner', 'Admin', 'Member', 'SupportAgent'
    description TEXT,
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3. Role Permissions Mapping (Flat in Phase 1)
CREATE TABLE role_permissions (
    role_id TEXT NOT NULL REFERENCES role_definitions(id) ON DELETE CASCADE,
    permission_name TEXT NOT NULL REFERENCES permission_definitions(name) ON DELETE RESTRICT,
    PRIMARY KEY(role_id, permission_name)
);

-- 4. Explicit Deny Rules (Precedence over All Grants)
CREATE TABLE explicit_denies (
    id TEXT PRIMARY KEY,                       -- dny_<ulid>
    organization_id TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK(subject_type IN ('principal', 'membership', 'team')),
    subject_id TEXT NOT NULL,
    action_pattern TEXT NOT NULL,              -- e.g. 'mail.*', 'core.domains.manage'
    resource_type TEXT,                        -- NULL for all resources in org
    resource_id TEXT,                          -- NULL for all instances
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 5. Monotonic Authorization Version Tracking
CREATE TABLE authorization_versions (
    organization_id TEXT PRIMARY KEY,          -- org_<ulid>
    version INTEGER NOT NULL DEFAULT 1,        -- Monotonically incremented on membership/role changes
    last_invalidated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Permission Registry Version vs. Authorization Version
- **Permission Registry Version**: The static schema version of the published permission dictionary/vocabulary supported by the software release.
- **Authorization Version**: A runtime monotonic integer incremented whenever an organization's memberships, role bindings, or security policies mutate, allowing Edge tokens to detect stale cached privileges.

---

## 3. Mandatory 8-Step Evaluation Algebra

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
   └─► If action.service NOT IN organization.entitledApps ──► DENY(APP_NOT_ENTITLED)

4. Resource Tenant Alignment
   └─► If resource.organizationId != organization.id:
         └─► Verify active `cross_org_grants` record; if absent ──► DENY(CROSS_TENANT_VIOLATION)

5. Apply Explicit Deny Rules
   └─► If matching `explicit_denies` rule exists for principal, membership, or team ──► DENY(EXPLICIT_DENY)

6. Resolve Coarse RBAC Capability
   └─► Resolve assigned roles for membership. If permission is NOT present in any active role ──► DENY(RBAC_DENIED)

7. Resolve Fine-Grained Resource ACL / ReBAC Grant
   └─► If resource requires specific ACL and no matching `resource_grants` or hierarchy inheritance exists ──► DENY(ACL_DENIED)

8. Apply Contextual ABAC & Fallback
   └─► Validate trusted edge context (IP allowlist, device posture).
   └─► If all checks pass ──► ALLOW; otherwise ──► DEFAULT_DENY
```

---

## 4. Hierarchy, Inheritance & Context Invariants

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
