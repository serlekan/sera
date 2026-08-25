# Oryol Identity Model v2 — Principal & Membership Architecture

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Supersedes**: `knowledge/oryol/identity.md` (v1)

---

## 1. The Principal Concept

In Architecture v2, the identity model is expanded beyond simple human users to encompass all actors in the workspace under the unified concept of a **Principal (`prn_...`)**.

```text
                                 ┌─────────────────────────────┐
                                 │          Principal          │
                                 │    (prn_01H8Z7A2B3C4...)    │
                                 └──────────────┬──────────────┘
                                                │
                 ┌──────────────────────────────┼──────────────────────────────┐
                 ▼                              ▼                              ▼
  ┌─────────────────────────────┐┌─────────────────────────────┐┌─────────────────────────────┐
  │        User Identity        ││      Service Identity       ││      External Identity      │
  │   (Human Employees/Users)   ││   (API Keys/Automations)    ││   (SAML/OIDC Federations)   │
  └─────────────────────────────┘└─────────────────────────────┘└─────────────────────────────┘
                 │                              │                              │
                 └──────────────────────────────┼──────────────────────────────┘
                                                │
                                                ▼
                                 ┌─────────────────────────────┐
                                 │         Membership          │
                                 │    (mem_01H8Z7B5C6D7...)    │
                                 └──────────────┬──────────────┘
                                                │
                                                ▼
                                 ┌─────────────────────────────┐
                                 │        Organization         │
                                 │    (org_01H8Z7C8D9E0...)    │
                                 └─────────────────────────────┘
```

---

## 2. Principal Types Supported

1. **Normal Users (`user`)**: Standard human employees authenticated via FIDO2 passkeys, magic links, or password+MFA.
2. **Enterprise Users (`enterprise_user`)**: Corporate employees authenticated via SAML 2.0 / OIDC IdP federations with automated JIT (Just-In-Time) provisioning.
3. **Contractors & External Collaborators (`guest`)**: Time-bounded or asset-scoped external members without full directory access.
4. **Service Accounts (`service_account`)**: Non-human machine principals for backend integrations, sync workers, and CI/CD pipelines.
5. **Automation Accounts (`bot`)**: Workspace automation bots (e.g. Virel automated agent accounts) executing tasks on behalf of workflows.

---

## 3. Canonical Relational Entities (D1)

```sql
-- Unified Principal Registry
CREATE TABLE principals (
    id TEXT PRIMARY KEY,                       -- prn_<ulid>
    type TEXT NOT NULL,                        -- 'user', 'service_account', 'external'
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Human User Extensions
CREATE TABLE users (
    principal_id TEXT PRIMARY KEY,             -- prn_<ulid>
    email TEXT UNIQUE NOT NULL,
    email_verified INTEGER NOT NULL DEFAULT 0,
    phone_number TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE
);

-- External Identity Provider Bindings
CREATE TABLE external_identities (
    id TEXT PRIMARY KEY,                       -- ext_id_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    provider TEXT NOT NULL,                    -- 'google', 'okta', 'azure_ad', 'github'
    provider_subject TEXT NOT NULL,            -- IdP unique user ID
    provider_email TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',       -- JSON claims from IdP
    linked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    UNIQUE(provider, provider_subject)
);

-- Service Account Credentials & Scopes
CREATE TABLE service_accounts (
    principal_id TEXT PRIMARY KEY,             -- prn_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    key_hash TEXT NOT NULL,                    -- Argon2id hash of API Secret Token
    key_prefix TEXT NOT NULL,                  -- 'oryol_live_...'
    allowed_ips TEXT DEFAULT '[]',             -- JSON array of CIDR strings
    expires_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

-- Organization Memberships
CREATE TABLE memberships (
    id TEXT PRIMARY KEY,                       -- mem_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    role TEXT NOT NULL DEFAULT 'member',       -- 'owner', 'admin', 'member', 'guest'
    status TEXT NOT NULL DEFAULT 'active',     -- 'active', 'suspended', 'invited'
    custom_title TEXT,
    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    UNIQUE(organization_id, principal_id)
);

-- Invitations
CREATE TABLE invitations (
    id TEXT PRIMARY KEY,                       -- inv_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    email TEXT NOT NULL,
    invited_role TEXT NOT NULL DEFAULT 'member',
    invited_by_principal_id TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (invited_by_principal_id) REFERENCES principals(id) ON DELETE CASCADE
);

-- Teams (Functional Sub-groups)
CREATE TABLE teams (
    id TEXT PRIMARY KEY,                       -- team_<ulid>
    organization_id TEXT NOT NULL,             -- org_<ulid>
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    description TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, slug)
);

-- Team Memberships
CREATE TABLE team_memberships (
    id TEXT PRIMARY KEY,                       -- tmem_<ulid>
    team_id TEXT NOT NULL,                     -- team_<ulid>
    membership_id TEXT NOT NULL,               -- mem_<ulid>
    team_role TEXT NOT NULL DEFAULT 'member',  -- 'lead', 'member'
    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (team_id) REFERENCES teams(id) ON DELETE CASCADE,
    FOREIGN KEY (membership_id) REFERENCES memberships(id) ON DELETE CASCADE,
    UNIQUE(team_id, membership_id)
);
```
