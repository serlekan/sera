# Oryol Authorization Model v2 — Canonical Access Control

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Supersedes**: `knowledge/oryol/security.md` (v1 Permission section)

---

## 1. The Four-Tier Authorization Pipeline

Architecture v2 replaces simple flat RBAC with a robust 4-tier evaluation pipeline:

```
                      ┌──────────────────────────────────────────┐
                      │          Incoming Authorization          │
                      │               Request Context            │
                      └────────────────────┬─────────────────────┘
                                           │
                                           ▼
                      ┌──────────────────────────────────────────┐
                      │           1. RBAC Role Scopes            │
                      │ (Owner, Admin, Member, Custom Role)      │
                      └────────────────────┬─────────────────────┘
                                           │
                                           ▼
                      ┌──────────────────────────────────────────┐
                      │          2. Permission Registry          │
                      │  (Static dot-notation definition catalog)│
                      └────────────────────┬─────────────────────┘
                                           │
                                           ▼
                      ┌──────────────────────────────────────────┐
                      │       3. Resource-Level Authority        │
                      │  (Direct ACLs, Mailbox/Folder sharing)   │
                      └────────────────────┬─────────────────────┘
                                           │
                                           ▼
                      ┌──────────────────────────────────────────┐
                      │       4. Limited Context Rules           │
                      │ (IP boundary, Time, Org Status, Deny-All)│
                      └────────────────────┬─────────────────────┘
                                           │
                                           ▼
                      ┌──────────────────────────────────────────┐
                      │             Final Decision               │
                      │            ALLOW   or   DENY             │
                      └──────────────────────────────────────────┘
```

---

## 2. Universal Authorization Contract

All services, middleware, and edge handlers invoke authorization via a unified interface:

```typescript
export interface AuthorizationRequest {
  principalId: string;         // prn_<ulid>
  organizationId: string;      // org_<ulid>
  membershipId: string;        // mem_<ulid>
  action: string;              // e.g. "mail.send", "crm.deal.edit"
  resource: {
    type: string;              // "mailbox", "thread", "deal", "document"
    id: string;                // "mbx_123", "deal_456"
    ownerOrganizationId: string;
    attributes?: Record<string, unknown>;
  };
  context: {
    ipAddress: string;
    clientType: 'web' | 'mobile' | 'api' | 'automation';
    organizationStatus: 'active' | 'suspended' | 'archived';
    timestamp: string;
  };
}

export interface AuthorizationResult {
  decision: 'allow' | 'deny';
  reasonCode: string;          // e.g. "ALLOWED_BY_ROLE", "DENIED_ORG_SUSPENDED", "DENIED_MISSING_SCOPE"
  matchedPolicy?: string;
}
```

---

## 3. Core Permission Namespace Catalog

Permissions follow strict dot-notation: `<namespace>.<resource>.<action>`

### Core Platform (`core.*`)
- `core.members.invite` — Invite new human users or service accounts.
- `core.roles.manage` — Create, edit, or delete custom organization roles.
- `core.audit.view` — Read organization compliance and security logs.
- `core.domains.manage` — Add, configure, and remove custom domains.

### OryolMail (`mail.*`)
- `mail.read` — View email messages in personal mailboxes.
- `mail.send` — Dispatch outbound emails from authorized sender aliases.
- `mail.shared.assign` — Assign support tickets and author internal discussion notes.
- `mail.domain.manage` — Configure DKIM, SPF, and MX routing rules.
- `mail.delete` — Permanently purge email threads.

### Oryol CRM (`crm.*`)
- `crm.deal.view`, `crm.deal.edit`, `crm.deal.manage` — Pipeline and opportunity governance.
- `crm.contacts.manage` — Create and update customer accounts and contacts.
- `crm.export` — Export customer directories (sensitive high-risk gate).

### Oryol Drive & Documents (`drive.*`)
- `drive.read`, `drive.write`, `drive.share`, `drive.admin`.

---

## 4. Deny-Precedence & Scope Rules

1. **Explicit Deny Overrides All**: If any policy, explicit revocation, or context rule returns `deny`, the final decision is strictly `DENY` regardless of role.
2. **Organization Status Gate**: If `organization.status !== 'active'`, all mutating actions (`*.edit`, `*.create`, `*.delete`, `*.send`) are automatically denied with `DENIED_ORG_SUSPENDED`.
3. **Cross-Tenant Guard**: If `resource.ownerOrganizationId !== context.organizationId`, access is immediately denied with `DENIED_CROSS_TENANT`.
4. **Versioned Registry**: The Permission Registry is versioned (`schema_version: 2`) to ensure newly introduced permissions default to closed.
