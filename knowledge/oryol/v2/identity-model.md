# Oryol Identity Model v2.1 — Canonical Principal & Identity Architecture

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Identity Model Consistency & Principal Taxonomy Fix

---

## 1. Canonical Principal Taxonomy

In Oryol Architecture v2.1, global principal types are strictly partitioned into two fundamental variants: **Human Principal** and **Service Principal**.

```text
                                 ┌─────────────────────────────┐
                                 │          Principal          │
                                 │   (prn_01H8Z7A2B3C4D5...)   │
                                 └──────────────┬──────────────┘
                                                │
                 ┌──────────────────────────────┴──────────────────────────────┐
                 ▼                                                             ▼
  ┌─────────────────────────────┐                               ┌─────────────────────────────┐
  │       Human Principal       │                               │      Service Principal      │
  │     type: "human"           │                               │     type: "service"         │
  └──────────────┬──────────────┘                               └──────────────┬──────────────┘
                 │                                                             │
   ┌─────────────┼─────────────┐                                 ┌─────────────┼─────────────┐
   ▼             ▼             ▼                                 ▼             ▼             ▼
┌───────┐ ┌─────────────┐ ┌─────────┐                         ┌─────────┐ ┌─────────┐ ┌───────────┐
│ User  │ │   Auth      │ │  IdP    │                         │ Service │ │  API    │ │Automation │
│Profile│ │  Factors    │ │Bindings │                         │ Account │ │ Client  │ │  Agent    │
└───────┘ └─────────────┘ └─────────┘                         └─────────┘ └─────────┘ └───────────┘
```

> [!IMPORTANT]
> **Strict Principal Typing Rule**:  
> Roles and affiliations such as `enterprise_user`, `employee`, `contractor`, `guest`, and `external collaborator` are **NEVER** global principal types.  
> They are strictly **Organization Membership attributes, scopes, and Identity Provider Bindings**.

---

## 2. Core Identity Entities & Schemas

### 2.1 Principals Table (`principals`)
```sql
CREATE TABLE principals (
    id TEXT PRIMARY KEY,                       -- prn_<ulid>
    type TEXT NOT NULL CHECK(type IN ('human', 'service')),
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'deactivated')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 2.2 Human Principal Components

#### User Profile (`users`)
```sql
CREATE TABLE users (
    principal_id TEXT PRIMARY KEY,             -- prn_<ulid>
    primary_email TEXT UNIQUE NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    phone_number TEXT,
    timezone TEXT NOT NULL DEFAULT 'UTC',
    locale TEXT NOT NULL DEFAULT 'en-US',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE
);
```

#### Authentication Factors & Credentials (`credentials`)
```sql
CREATE TABLE credentials (
    id TEXT PRIMARY KEY,                       -- cred_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    factor_type TEXT NOT NULL CHECK(factor_type IN ('passkey', 'password', 'totp', 'backup_code')),
    credential_data TEXT NOT NULL,             -- JSON: FIDO2 public key, Argon2id hash, or encrypted secret
    name TEXT NOT NULL,                        -- e.g. "MacBook Touch ID", "YubiKey 5C"
    last_used_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE
);
```

#### Identity Provider Bindings (`identity_provider_bindings`)
```sql
CREATE TABLE identity_provider_bindings (
    id TEXT PRIMARY KEY,                       -- idp_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    organization_id TEXT,                      -- Nullable for global auth; set if binding is enterprise-tenant specific
    provider_type TEXT NOT NULL CHECK(provider_type IN ('oidc', 'saml', 'google', 'github', 'microsoft', 'okta')),
    provider_subject TEXT NOT NULL,            -- IdP unique immutable subject ID
    provider_email TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',       -- JSON claims from IdP
    linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_login_at DATETIME,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    UNIQUE(provider_type, provider_subject)
);
```

#### Recovery Methods (`recovery_methods`)
```sql
CREATE TABLE recovery_methods (
    id TEXT PRIMARY KEY,                       -- rec_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    method_type TEXT NOT NULL CHECK(method_type IN ('recovery_email', 'phone_sms', 'security_questions')),
    destination TEXT NOT NULL,                 -- Encrypted email/phone
    is_verified INTEGER NOT NULL DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE
);
```

### 2.3 Service Principal Components

#### Service Accounts & API Clients (`service_accounts`, `api_clients`)
```sql
CREATE TABLE service_accounts (
    principal_id TEXT PRIMARY KEY,             -- prn_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    service_type TEXT NOT NULL CHECK(service_type IN ('api_client', 'automation_agent', 'integration_sync')),
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE TABLE api_credentials (
    id TEXT PRIMARY KEY,                       -- apikey_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    key_prefix TEXT NOT NULL,                  -- e.g. 'oryol_live_...' (public lookup prefix)
    key_hash TEXT NOT NULL,                    -- Argon2id hash of raw secret token
    name TEXT NOT NULL,                        -- e.g. "Zapier Sync Key"
    allowed_ips TEXT NOT NULL DEFAULT '[]',    -- JSON array of CIDRs
    expires_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE
);
```

#### Delegated Authority (`delegated_authority`)
```sql
CREATE TABLE delegated_authority (
    id TEXT PRIMARY KEY,                       -- del_<ulid>
    grantor_principal_id TEXT NOT NULL,        -- prn_<ulid> (User delegating access)
    grantee_principal_id TEXT NOT NULL,        -- prn_<ulid> (Agent or Service account receiving delegation)
    organization_id TEXT NOT NULL,             -- org_<ulid>
    allowed_scopes TEXT NOT NULL,              -- JSON array of scopes e.g. ["mail.messages.send"]
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (grantor_principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    FOREIGN KEY (grantee_principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);
```

---

## 3. Memberships vs. Invitations

```sql
-- Organization Memberships (Expresses Employee vs Contractor vs Guest)
CREATE TABLE memberships (
    id TEXT PRIMARY KEY,                       -- mem_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    member_type TEXT NOT NULL DEFAULT 'employee' CHECK(member_type IN ('employee', 'contractor', 'guest', 'bot')),
    role TEXT NOT NULL DEFAULT 'member',       -- 'owner', 'admin', 'member', 'guest' or custom role
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'suspended', 'left')),
    custom_title TEXT,
    expires_at DATETIME,                       -- Set for time-bounded contractors/guests
    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    UNIQUE(organization_id, principal_id)
);

-- Separate Invitations Entity
CREATE TABLE invitations (
    id TEXT PRIMARY KEY,                       -- inv_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    email TEXT NOT NULL,
    invited_role TEXT NOT NULL DEFAULT 'member',
    member_type TEXT NOT NULL DEFAULT 'employee' CHECK(member_type IN ('employee', 'contractor', 'guest')),
    invited_by_principal_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'accepted', 'declined', 'expired', 'revoked')),
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (invited_by_principal_id) REFERENCES principals(id) ON DELETE CASCADE
);

CREATE INDEX idx_invitations_org ON invitations(organization_id, email, status);
```

---

## 4. Lifecycle Invariants & Invariant Rules

1. **Principal Creation**:
   - Creating a human principal creates a record in `principals` (`type='human'`) and an initial profile in `users`.
   - Creating a service principal creates a record in `principals` (`type='service'`) and a record in `service_accounts`.
2. **Principal Activation & Suspension**:
   - `status='suspended'` immediately halts all session refresh attempts and API token validations for that principal across all organizations.
3. **Principal Deactivation**:
   - Soft-deactivates the principal, revokes all active credentials and sessions, and marks memberships as `left`.
4. **Credential Revocation & IdP Unlinking**:
   - A principal must always maintain at least one valid authentication factor or IdP binding. Unlinking the final authentication method is rejected unless accompanied by account deletion.
5. **Last-Owner Protection**:
   - An organization must have at least one active membership with the `owner` role.
   - Any operation (role demotion, membership deletion, principal deactivation) that would reduce the count of active owners in an active organization to zero is **strictly rejected** with `LAST_OWNER_PROTECTION_VIOLATION`.
