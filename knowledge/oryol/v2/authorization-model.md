# Oryol Authorization Policy Algebra v2.1

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Strict 8-Step Evaluation Algebra, Coarse-to-Fine Capability Invariants & Canonical 3-Part Namespace

---

## 1. Canonical Authorization Contract

All authorization decisions in Oryol Workspace are executed through the standard `authorize({ principal, membership, organization, action, resource, context })` interface:

```typescript
export function authorize(req: AuthorizationRequest): Promise<AuthorizationResult>;
```
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
    ownerOrganizationId: string;
    attributes?: Record<string, unknown>;
  };
  context: {
    ipAddress: string;         // Trusted edge context
    clientType: 'web' | 'mobile' | 'api' | 'automation';
    timestamp: string;         // ISO-8601 UTC
    untrustedHeaders?: Record<string, string>; // Sanitized & separated
  };
}

export interface AuthorizationResult {
  decision: 'allow' | 'deny';
  reasonCode: string;          // e.g. "ALLOWED_RBAC_AND_ACL", "DENIED_LACKING_CAPABILITY"
  permissionRegistryVersion: string; // e.g. "2.1.0"
  decisionVersion: string;           // e.g. "2.1.0"
  matchedGrants?: string[];
}
```

---

## 2. Mandatory 8-Step Evaluation Algebra

Every evaluation executes in strictly deterministic, non-reorderable sequence:

```text
 1. Validate Principal Active
       │ (if principal.status !== 'active' ──► DENY: "PRINCIPAL_INACTIVE")
       ▼
 2. Validate Membership Active & Bound
       │ (if membership.status !== 'active' OR membership.orgId !== organization.id ──► DENY: "MEMBERSHIP_INVALID")
       ▼
 3. Validate Application Entitlement
       │ (if app for action not in organization.entitledApps ──► DENY: "APP_NOT_ENTITLED")
       ▼
 4. Apply Explicit Deny Rules
       │ (if explicit deny rule matches ──► DENY: "EXPLICIT_DENY_PRECEDENCE")
       ▼
 5. Resolve RBAC Coarse Permission Capability
       │ (if principal/membership lacks coarse capability scope ──► DENY: "CAPABILITY_UNAUTHORIZED")
       ▼
 6. Resolve Resource ACL / ReBAC Grants
       │ (if coarse allowed, check resource-specific ACL/assignment/team grant ──► if no grant ──► DENY: "RESOURCE_ACCESS_DENIED")
       ▼
 7. Apply Constrained Contextual Conditions (ABAC)
       │ (evaluate IP allowlists, organization status lockouts, time bounds ──► if violated ──► DENY: "CONTEXT_VIOLATION")
       ▼
 8. Default Deny Fallback
         (if no explicit grant path completed ──► DENY: "DEFAULT_DENY")
```

---

## 3. Coarse Capability vs. Resource Access Invariant

> [!IMPORTANT]
> **Fundamental Access Control Invariant**:  
> **RBAC** defines the coarse capability (e.g. `mail.messages.read`).  
> **ACL / ReBAC** defines specific resource access (e.g. `mailbox:mbx_support`).  
> **ABAC** applies contextual restrictions (e.g. `ip in allowlist`).  
> 
> **An ACL or resource grant must NEVER create a capability that the principal lacks at the RBAC capability level.**
> 
> *Example*: If a user does not possess the `mail.messages.read` permission in their role, being added to a shared mailbox ACL grants zero access.

---

## 4. Canonical 3-Part Permission Namespaces

All permissions use the standardized three-part format: `<service>.<resource>.<action>`

### Core Platform (`core.*`)
- `core.members.invite` — Invite users or provision service accounts.
- `core.members.manage` — Change roles, edit titles, or remove memberships.
- `core.roles.manage` — Create, edit, or delete custom organization roles.
- `core.domains.manage` — Add, configure, or delete organization domain claims.
- `core.audit.read` — View organization audit records.

### OryolMail (`mail.*`)
- `mail.messages.read` — View email content in assigned/accessible mailboxes.
- `mail.messages.send` — Dispatch outbound emails from authorized aliases.
- `mail.messages.delete` — Purge or trash email messages.
- `mail.shared.assign` — Assign support tickets and post internal discussion notes.
- `mail.domains.manage` — Configure DKIM, SPF, and MX routing rules.

### Oryol CRM (`crm.*`)
- `crm.deals.read` / `crm.deals.manage` — View and edit sales opportunities.
- `crm.contacts.read` / `crm.contacts.manage` — View and edit customer directories.
- `crm.exports.create` — Export CRM records (sensitive capability).

### Finance & Billing (`finance.*`)
- `finance.invoices.read` / `finance.invoices.manage` — View and manage billing invoices.

---

## 5. Policy Rules & Constraints

1. **Wildcard Policy**:
   - Unrestricted global `*` is strictly **forbidden**.
   - Subsystem wildcards are bounded to a single namespace (e.g. `mail.messages.*` or `mail.*`), and are restricted to system `owner` / `admin` roles.
2. **Context Attributes (Trusted vs. Untrusted)**:
   - **Trusted Context** (`ipAddress`, `tokenSessionId`, `activeOrgId`) is extracted directly by Cloudflare edge middleware from verified connection metadata and cryptographically signed JWT claims.
   - **Untrusted Context** (user-submitted query params or client body attributes) cannot be used for coarse permission bypass.
