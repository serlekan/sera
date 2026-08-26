# Oryol Identity Architecture v2.2 — Canonical Principal Model

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Strict Binary Principal Taxonomy, IdP Uniqueness, Factor Separation & Last-Owner Protection

---

## 1. Canonical Principal Taxonomy

In Oryol Workspace, the identity layer enforces a strict top-level abstraction:

```text
Principal (prn_<ulid>)
├── Human Principal (`type = 'human'`)  ──► Backed by `users` record
└── Service Principal (`type = 'service'`) ──► Backed by `service_accounts` record
```

> [!IMPORTANT]
> **Strict Taxonomy Rule**:  
> Concepts such as `enterprise_user`, `employee`, `contractor`, `guest`, and `external collaborator` are **NOT** global principal types.  
> They are strictly modeled as **Organization Membership attributes** (`memberships.member_type`) and **Identity Provider Bindings** (`identity_provider_bindings`).

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

-- 6. Service Accounts (Machine & Automation Identities)
CREATE TABLE service_accounts (
    id TEXT PRIMARY KEY,                       -- svc_<ulid>
    principal_id TEXT UNIQUE NOT NULL REFERENCES principals(id) ON DELETE RESTRICT,
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT,
    system_managed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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

-- 8. Organization Invitations
CREATE TABLE invitations (
    id TEXT PRIMARY KEY,                       -- inv_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    inviter_principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL,                  -- SHA-256 hash of random invite token
    role_id TEXT NOT NULL REFERENCES role_definitions(id) ON DELETE RESTRICT,
    member_type TEXT NOT NULL DEFAULT 'employee' CHECK(member_type IN ('employee', 'contractor', 'guest')),
    expires_at DATETIME NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'revoked', 'expired')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(organization_id, email, status)
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
