# Oryol Identity Architecture v2.3 — Canonical Principal Model

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.3) — Subject to Independent Architecture Review  
**Revision Scope**: Service Principal Role Assignments Integration (ADR-002) & Database-Enforced Taxonomy (F-8)

---

## 1. Canonical Principal Taxonomy

In Oryol Workspace, the identity layer enforces a strict top-level abstraction:

```text
Principal (prn_<ulid>)
├── Human Principal (`type = 'human'`)   ──► Backed by `users` record; bound to organizations via `memberships` & `membership_role_assignments`
└── Service Principal (`type = 'service'`) ──► Backed by `service_accounts` record; bound to organizations via `organization_service_principals` & `service_principal_role_assignments`
```

> [!IMPORTANT]
> **Strict Taxonomy Rule**:  
> Concepts such as `enterprise_user`, `employee`, `contractor`, `guest`, and `external collaborator` are **NOT** global principal types.  
> They are strictly modeled as **Organization Membership attributes** (`memberships.member_type`) and **Identity Provider Bindings** (`identity_provider_bindings`).  
> Service accounts are tenant-bound via `organization_service_principals` and assigned coarse capabilities via `service_principal_role_assignments`.

---

## 2. Relational Schema for Identity Core (D1 Relational)

```sql
-- 1. Base Principals Table
CREATE TABLE principals (
    id TEXT PRIMARY KEY,                       -- prn_<ulid>
    type TEXT NOT NULL CHECK(type IN ('human', 'service')),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'deactivated')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trg_principals_immutable_type BEFORE UPDATE OF type ON principals
BEGIN
    SELECT RAISE(FAIL, 'PRINCIPAL_TYPE_IMMUTABLE: principal type cannot be modified');
END;

-- 2. Human Users (Profile and primary identity)
CREATE TABLE users (
    id TEXT PRIMARY KEY,                       -- usr_<ulid>
    principal_id TEXT UNIQUE NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
    primary_email TEXT UNIQUE NOT NULL,
    email_verified BOOLEAN NOT NULL DEFAULT FALSE,
    display_name TEXT NOT NULL,
    locale TEXT NOT NULL DEFAULT 'en-US',
    timezone TEXT NOT NULL DEFAULT 'UTC',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 3. Authentication Factor Credentials
CREATE TABLE credentials (
    id TEXT PRIMARY KEY,                       -- crd_<ulid>
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    factor_type TEXT NOT NULL CHECK(factor_type IN ('passkey_webauthn', 'password_argon2id', 'totp', 'magic_link')),
    public_key_or_hash TEXT NOT NULL,          -- WebAuthn COSE public key or Argon2id hash
    algorithm TEXT NOT NULL,                   -- 'ES256', 'RS256', 'argon2id', 'totp_sha1'
    counter INTEGER DEFAULT 0,                 -- Sign counter for WebAuthn replay defense
    aaguid TEXT,                               -- Authenticator Attestation GUID
    name TEXT NOT NULL,                        -- e.g. "MacBook TouchID", "YubiKey 5C"
    last_used_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 4. Identity Provider Bindings (Enterprise SSO, Google Workspace, Microsoft Entra)
CREATE TABLE identity_provider_bindings (
    id TEXT PRIMARY KEY,                       -- idp_<ulid>
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    provider_type TEXT NOT NULL,               -- 'google_oidc', 'microsoft_entra', 'okta_saml', 'custom_oidc'
    provider_issuer TEXT NOT NULL,             -- e.g. 'https://accounts.google.com', 'https://login.microsoftonline.com/{tenant}'
    provider_subject TEXT NOT NULL,            -- Unique immutable subject claim (sub/NameID)
    email_at_provider TEXT NOT NULL,
    raw_claims TEXT,                           -- Encrypted JSON attributes
    bound_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_authenticated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider_type, provider_issuer, provider_subject)
);

-- 5. Account Recovery Methods
CREATE TABLE recovery_methods (
    id TEXT PRIMARY KEY,                       -- rcv_<ulid>
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    method_type TEXT NOT NULL CHECK(method_type IN ('recovery_email', 'backup_codes', 'security_questions_tier2')),
    destination_or_hash TEXT NOT NULL,         -- Verified secondary email or hashed backup codes
    is_primary_factor BOOLEAN NOT NULL DEFAULT FALSE, -- Security questions are never primary
    verified_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 6. Service Accounts: Explicit Tenant-Bound Machine & Automation Identities (P0-2)
-- Note: A service account has exactly one owning organization (organization_id), which is required, tenant-authoritative, and immutable in Phase 1 (cross-organization transfer is NOT supported).
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

-- 7. Service Account API Credentials (Hashed)
CREATE TABLE api_credentials (
    id TEXT PRIMARY KEY,                       -- apic_<ulid>
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    key_prefix TEXT NOT NULL,                  -- First 8 chars of key for lookup (e.g. 'ory_live_')
    key_hash TEXT NOT NULL,                    -- Argon2id hash of full secret key
    name TEXT NOT NULL,
    scopes TEXT NOT NULL,                      -- Comma-separated canonical permissions
    expires_at DATETIME,
    last_used_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 8. Organization Invitations (Restored — F-9)
CREATE TABLE invitations (
    id TEXT PRIMARY KEY,                       -- inv_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    role_id TEXT NOT NULL,
    invited_by_membership_id TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,           -- SHA-256 hash of random invite token
    member_type TEXT NOT NULL DEFAULT 'employee' CHECK(member_type IN ('employee', 'contractor', 'guest')),
    expires_at DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'revoked', 'expired')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, role_id) REFERENCES role_definitions(organization_id, id) ON DELETE RESTRICT,
    FOREIGN KEY (organization_id, invited_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, id)
);

-- 9. Organization Service Principals: Explicit Tenant-Bound Service Accounts (P0-2)
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

-- 10. Service Principal Role Assignments (ADR-002)
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
```

---

## 3. Core Identity Invariants

1. **Last-Owner Protection Invariant**:
   An organization membership holding the `Owner` role cannot be removed, deactivated, or downgraded if it is the sole remaining active Owner of an active Organization (`LAST_OWNER_PROTECTION_VIOLATION`).
2. **Strict Factor Verification**:
   Passkey/WebAuthn is the primary passwordless standard. Security questions are restricted to secondary/tier-2 recovery verification and are **never** permitted as a standalone primary recovery factor.
3. **Enterprise IdP Uniqueness**:
   IdP subject bindings enforce global uniqueness across `(provider_type, provider_issuer, provider_subject)` to prevent tenant-spoofing across multi-tenant IdPs.
4. **Service Principal Role Confinement & Taxonomy**:
   Service principals receive permissions strictly through `service_principal_role_assignments` evaluated against the active registry version. Human memberships and service principals are strictly segregated in the schema.
5. **Service Account Tenant Confinement & Ownership Immutability (P0-2)**:
   A service account has exactly one authoritative owning organization in Phase 1 (`service_accounts.organization_id`). Once Migration 0005 has backfilled the organization owner, or once a new service account is created, its organization ownership is immutable during Phase 1 (`organization_id` is required, tenant-authoritative, and immutable after creation/backfill). Cross-organization transfer of a service account is NOT supported in Phase 1; any future transfer capability requires a separate architecture decision. Both creation (INSERT) and update (UPDATE) operations are protected by database triggers: `organization_id` cannot be set to NULL on INSERT or UPDATE, and cannot be changed from one organization to another on UPDATE. `organization_service_principals` enforces a compound foreign key referencing `service_accounts(organization_id, principal_id)`, structurally preventing a service account owned by Organization A from being bound into Organization B.

