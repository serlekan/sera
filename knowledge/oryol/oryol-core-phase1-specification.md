# Oryol Core — Phase 1 Engineering Specification (v1)

> [!WARNING]
> **Status: SUPERSEDED**  
> This document describes Oryol Core Phase 1 Engineering Specification v1. For the current canonical architecture baseline, see [`knowledge/oryol/v2/identity-model.md`](v2/identity-model.md), [`knowledge/oryol/v2/authorization-model.md`](v2/authorization-model.md), and [`knowledge/oryol/v2/session-security.md`](v2/session-security.md).

**Target Repository**: `serlekan/oryol-core`  
**Ecosystem**: Oryol Workspace Platform  
**Target Runtime**: Cloudflare Edge Infrastructure (Workers + D1 + KV)  
**Specification Stage**: Phase 1 MVP Baseline (v1)  
**Executor**: Google Antigravity & Pair Engineering Agents  

---

## 1. Repository Initialization Steps

Execute the following commands to initialize the `serlekan/oryol-core` repository:

```bash
# 1. Initialize repository directory
mkdir oryol-core
cd oryol-core
git init -b main

# 2. Initialize Node package and TypeScript config
npm init -y
npm install -D typescript @types/node @cloudflare/workers-types@^4.20241230.0 wrangler@^3.100.0 vitest@^3.0.0 @cloudflare/vitest-pool-workers@^0.6.0 eslint @eslint/js typescript-eslint

# 3. Initialize Cloudflare Wrangler configuration
npx wrangler init --yes

# 4. Create directory structure
mkdir -p src/config src/db src/lib src/middleware src/modules/identity src/modules/organizations src/modules/memberships src/modules/audit src/types migrations tests/unit tests/integration tests/fixtures .sera .github/workflows
```

---

## 2. Exact Package Dependencies

### `package.json` Specification

```json
{
  "name": "oryol-core",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "wrangler dev",
    "deploy": "wrangler deploy",
    "typecheck": "tsc --noEmit",
    "lint": "eslint .",
    "test": "vitest run",
    "test:watch": "vitest",
    "db:migrate:local": "wrangler d1 migrations apply DB --local",
    "db:migrate:prod": "wrangler d1 migrations apply DB --remote"
  },
  "dependencies": {
    "hono": "^4.6.14",
    "jose": "^5.9.6",
    "@oslojs/crypto": "^1.0.1",
    "@oslojs/encoding": "^1.1.0",
    "ulid": "^2.3.0"
  },
  "devDependencies": {
    "@cloudflare/vitest-pool-workers": "^0.6.4",
    "@cloudflare/workers-types": "^4.20241230.0",
    "@eslint/js": "^9.21.0",
    "@types/node": "^22.14.0",
    "eslint": "^9.21.0",
    "typescript": "~5.8.2",
    "typescript-eslint": "^8.24.1",
    "vitest": "^3.0.7",
    "wrangler": "^3.107.3"
  }
}
```

### TypeScript Configuration (`tsconfig.json`)

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "types": ["@cloudflare/workers-types", "@cloudflare/vitest-pool-workers"],
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitOverride": true,
    "skipLibCheck": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"]
    }
  },
  "include": ["src/**/*", "tests/**/*", "migrations/**/*"]
}
```

---

## 3. Cloudflare Configuration (`wrangler.toml`)

```toml
name = "oryol-core"
main = "src/index.ts"
compatibility_date = "2026-08-20"
compatibility_flags = ["nodejs_compat"]

[vars]
ENVIRONMENT = "development"
TOKEN_ISSUER = "https://auth.oryol.com"
SESSION_TTL_SECONDS = "1209600" # 14 days
JWT_EXPIRATION_SECONDS = "900"   # 15 minutes

# Relational Database (Cloudflare D1)
[[d1_databases]]
binding = "DB"
database_name = "oryol-core-db"
database_id = "local-d1-instance"
migrations_dir = "migrations"

# Key-Value Storage for Session Revocation & Caching
[[kv_namespaces]]
binding = "SESSIONS_KV"
id = "local-kv-sessions"

# Local Development Settings
[dev]
port = 8787
local_protocol = "http"
```

---

## 4. Database Migration Tasks

### Task M1: Global Identity & Session Store
- **Objective**: Create foundational tables for user accounts, credentials, and session tracking.
- **File**: `migrations/0001_create_identity.sql`
- **Dependencies**: None
- **Verification**: `npx wrangler d1 migrations apply DB --local` executes successfully.

```sql
-- 0001_create_identity.sql

CREATE TABLE users (
    id TEXT PRIMARY KEY,                       -- usr_<ulid>
    email TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE user_credentials (
    user_id TEXT PRIMARY KEY,                  -- usr_<ulid>
    password_hash TEXT,                        -- Argon2id or Scrypt hash
    webauthn_credentials TEXT DEFAULT '[]',    -- JSON array of FIDO2 public keys
    mfa_secret TEXT,
    mfa_enabled INTEGER NOT NULL DEFAULT 0,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE user_sessions (
    id TEXT PRIMARY KEY,                       -- ses_<ulid>
    user_id TEXT NOT NULL,
    active_organization_id TEXT,
    refresh_token_hash TEXT NOT NULL,
    user_agent TEXT,
    ip_address TEXT,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX idx_sessions_user ON user_sessions(user_id, expires_at);
```

---

### Task M2: Organizations & Custom Domains
- **Objective**: Create multi-tenant organization boundaries and custom domain registries.
- **File**: `migrations/0002_create_orgs.sql`
- **Dependencies**: Task M1
- **Verification**: `npx wrangler d1 migrations apply DB --local`

```sql
-- 0002_create_orgs.sql

CREATE TABLE organizations (
    id TEXT PRIMARY KEY,                       -- org_<ulid>
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL DEFAULT 'starter',       -- 'free', 'starter', 'business', 'enterprise'
    settings TEXT NOT NULL DEFAULT '{}',       -- JSON: MFA enforcement, IP allowlist, retention
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE organization_domains (
    id TEXT PRIMARY KEY,                       -- dom_<ulid>
    organization_id TEXT NOT NULL,
    domain TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',     -- 'verified', 'pending', 'failed'
    dns_records TEXT NOT NULL DEFAULT '[]',    -- JSON array of required DNS records
    dkim_selector TEXT,
    verified_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE INDEX idx_domains_org ON organization_domains(organization_id);
```

---

### Task M3: Memberships & RBAC Roles
- **Objective**: Create organization memberships, system roles, and custom permission bindings.
- **File**: `migrations/0003_create_rbac.sql`
- **Dependencies**: Task M1, Task M2
- **Verification**: `npx wrangler d1 migrations apply DB --local`

```sql
-- 0003_create_rbac.sql

CREATE TABLE roles (
    id TEXT PRIMARY KEY,                       -- rol_<ulid>
    organization_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    permissions TEXT NOT NULL DEFAULT '[]',    -- JSON array of scoped strings
    is_system_role INTEGER NOT NULL DEFAULT 0, -- 1 for built-in Owner/Admin/Member
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    UNIQUE(organization_id, name)
);

CREATE TABLE memberships (
    id TEXT PRIMARY KEY,                       -- mem_<ulid>
    organization_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',       -- 'owner', 'admin', 'member', 'guest'
    status TEXT NOT NULL DEFAULT 'active',     -- 'active', 'pending_invite', 'suspended'
    permissions_override TEXT DEFAULT '[]',    -- JSON array of explicit grants/revocations
    custom_title TEXT,
    joined_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE(organization_id, user_id)
);

CREATE INDEX idx_memberships_user ON memberships(user_id);
CREATE INDEX idx_memberships_org ON memberships(organization_id, status);
```

---

### Task M4: Audit Event Stream
- **Objective**: Create immutable, append-only audit ledger table.
- **File**: `migrations/0004_create_audit.sql`
- **Dependencies**: Task M2
- **Verification**: `npx wrangler d1 migrations apply DB --local`

```sql
-- 0004_create_audit.sql

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY,                       -- aud_<ulid>
    organization_id TEXT NOT NULL,
    actor_user_id TEXT NOT NULL,
    actor_membership_id TEXT,
    actor_ip TEXT,
    actor_user_agent TEXT,
    action TEXT NOT NULL,                      -- e.g. 'domain.verify', 'member.invite'
    resource_type TEXT NOT NULL,               -- 'domain', 'mailbox', 'membership'
    resource_id TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',        -- JSON diff or metadata snapshot
    status TEXT NOT NULL DEFAULT 'success',    -- 'success', 'denied', 'error'
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE INDEX idx_audit_org_time ON audit_events(organization_id, created_at DESC);
CREATE INDEX idx_audit_resource ON audit_events(organization_id, resource_type, resource_id);
```

---

## 5. API Endpoint Tasks

### Task API-1: Authentication & Session Endpoints
- **Objective**: Implement user registration, login, token refresh, org switching, and logout.
- **Files**: `src/modules/identity/identity.routes.ts`, `src/modules/identity/identity.service.ts`, `src/modules/identity/session.service.ts`
- **Endpoints**:
  - `POST /v1/auth/register` — Request: `{ email, password, displayName }` ➔ Returns User + Refresh Token.
  - `POST /v1/auth/login` — Request: `{ email, password }` ➔ Returns User, Memberships list, Refresh Token.
  - `POST /v1/auth/switch-org` — Request: `{ organizationId }` ➔ Validates membership and returns scoped Session JWT (`exp: 15m`).
  - `GET /v1/auth/session` — Returns active user profile, membership details, and permission bitmap.
  - `POST /v1/auth/logout` — Invalidate refresh token in KV and delete session cookie.
- **Dependencies**: Tasks M1, S1, S2, S3
- **Verification**: Integration test verifies token issue, organization switch, and KV revocation.

---

### Task API-2: Organization Management Endpoints
- **Objective**: Implement organization creation, retrieval, and settings updates.
- **Files**: `src/modules/organizations/org.routes.ts`, `src/modules/organizations/org.service.ts`
- **Endpoints**:
  - `GET /v1/orgs` — Returns array of organizations where user holds an active membership.
  - `POST /v1/orgs` — Request: `{ name, slug }` ➔ Creates org and assigns caller as `Owner`.
  - `GET /v1/orgs/:orgId` — Returns organization profile, tier, and policy settings.
  - `PATCH /v1/orgs/:orgId` — Request: `{ name?, settings? }` ➔ Requires `core.org.edit`.
- **Dependencies**: Tasks API-1, M2, S4
- **Verification**: Integration test creates org, verifies Owner role auto-assignment, and tests permission gates.

---

### Task API-3: Custom Domain Endpoints
- **Objective**: Implement custom domain registration, DNS record generation, and deletion.
- **Files**: `src/modules/organizations/org.routes.ts`, `src/modules/organizations/org.service.ts`
- **Endpoints**:
  - `GET /v1/domains` — List all domains registered to active organization.
  - `POST /v1/domains` — Request: `{ domain }` ➔ Validates domain format and returns required MX, SPF, DKIM records. Requires `domain.manage`.
  - `DELETE /v1/domains/:domainId` — Removes custom domain. Requires `domain.manage`.
- **Dependencies**: Tasks API-2, M2
- **Verification**: Integration test registers domain, checks DNS record payload, and verifies deletion.

---

### Task API-4: Membership & Role Endpoints
- **Objective**: Implement member invitation, role modification, membership revocation, and custom RBAC.
- **Files**: `src/modules/memberships/member.routes.ts`, `src/modules/memberships/member.service.ts`, `src/modules/memberships/rbac.service.ts`
- **Endpoints**:
  - `GET /v1/orgs/:orgId/members` — List members with roles, titles, and status.
  - `POST /v1/orgs/:orgId/members/invite` — Request: `{ email, role, customTitle? }` ➔ Requires `core.members.invite`.
  - `PATCH /v1/orgs/:orgId/members/:memberId` — Request: `{ role?, permissionsOverride?, customTitle? }` ➔ Requires `core.members.edit`.
  - `DELETE /v1/orgs/:orgId/members/:memberId` — Revokes membership and terminates sessions. Requires `core.members.remove`.
  - `GET /v1/orgs/:orgId/roles` & `POST /v1/orgs/:orgId/roles` — Custom RBAC role definitions.
- **Dependencies**: Tasks API-2, M3, S5
- **Verification**: Integration test verifies role updates, permission inheritance, and membership revocation.

---

### Task API-5: Audit Trail Query Endpoint
- **Objective**: Implement organization-scoped audit log query endpoint.
- **Files**: `src/modules/audit/audit.routes.ts`, `src/modules/audit/audit.service.ts`
- **Endpoints**:
  - `GET /v1/audit/events` — Query parameters: `?limit=50&cursor=...&action=...&resourceType=...`. Requires `core.audit.view`.
- **Dependencies**: Tasks API-1, M4, S6
- **Verification**: Integration test performs actions (invite member, add domain) and verifies they appear in `/v1/audit/events`.

---

## 6. Service Implementation Order

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. Foundation: ULID, Crypto (Ed25519, Argon2), Standard Error & Envelopes   │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. D1 Client, Migrations & Database Table Schemas                           │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. Auth Middleware & Edge JWT Verification (`middleware/auth.ts`)            │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. Identity & Session Service (`modules/identity/`)                         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. Organization & Custom Domain Service (`modules/organizations/`)          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 6. Membership & RBAC Evaluation Engine (`modules/memberships/`)             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 7. Audit Middleware Interceptor & Event Logger (`modules/audit/`)           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Task S1: Foundation Libraries
- **Objective**: Create ID generation, cryptographic signing, error classes, and response envelopes.
- **Files**: `src/lib/ulid.ts`, `src/lib/crypto.ts`, `src/lib/errors.ts`, `src/lib/response.ts`
- **Dependencies**: Package installations
- **Verification**: Unit tests in `tests/unit/crypto.test.ts` and `tests/unit/ulid.test.ts` pass.

### Task S2: Database Client & Schema
- **Objective**: Wrap Cloudflare D1 with prepared statement helpers and export TypeScript schema interfaces.
- **Files**: `src/db/client.ts`, `src/db/schema.ts`
- **Dependencies**: Task S1, Migrations M1–M4
- **Verification**: Typecheck passes with strict schema types.

### Task S3: Edge Auth & Tenant Middleware
- **Objective**: Verify Session JWTs in `< 1ms` at edge and bind `ctx.var.user`, `ctx.var.orgId`, `ctx.var.membershipId`.
- **Files**: `src/middleware/auth.ts`, `src/middleware/org-context.ts`, `src/middleware/rbac.ts`
- **Dependencies**: Tasks S1, S2
- **Verification**: Unit tests verify valid JWT acceptance, expired JWT rejection, and permission evaluation.

### Task S4: Identity & Session Service
- **Objective**: Implement user account creation, password hashing, organization switching, and token issuance.
- **Files**: `src/modules/identity/identity.service.ts`, `src/modules/identity/session.service.ts`
- **Dependencies**: Tasks S1, S2, S3
- **Verification**: Full register, login, org-switch test flow passes.

### Task S5: Organization & Membership Service
- **Objective**: Implement tenant boundaries, domain handling, membership management, and role resolution.
- **Files**: `src/modules/organizations/org.service.ts`, `src/modules/memberships/member.service.ts`, `src/modules/memberships/rbac.service.ts`
- **Dependencies**: Tasks S3, S4
- **Verification**: Integration tests verify org creation and member invitation flows.

### Task S6: Audit Middleware & Logging
- **Objective**: Implement non-blocking audit logging interceptor on all state-mutating endpoints.
- **Files**: `src/middleware/audit.ts`, `src/modules/audit/audit.service.ts`
- **Dependencies**: Tasks S2, S5
- **Verification**: Audit events are recorded in D1 upon domain/member mutation.

### Task S7: App Composition & Gateway Router
- **Objective**: Mount all route modules onto Hono application and attach global error handler.
- **Files**: `src/index.ts`, `src/middleware/error-handler.ts`
- **Dependencies**: Tasks S1–S6
- **Verification**: Full end-to-end integration test suite passes.

---

## 7. Security Acceptance Criteria

Every PR and task implementation must satisfy the following **7 Non-Bypassable Security Criteria**:

- [ ] **SEC-1 (Parameterized Queries)**: 100% of D1 queries use parameterized bindings (`.prepare('...').bind(...)`). Zero dynamic SQL string concatenation.
- [ ] **SEC-2 (Tenant Scoping)**: Every query on organization-owned tables (`organization_domains`, `memberships`, `roles`, `audit_events`) explicitly filters by `organization_id = ?`.
- [ ] **SEC-3 (Edge Asymmetric JWT)**: Session JWTs are signed with Ed25519 and verified cryptographically at the edge before any route handler logic executes.
- [ ] **SEC-4 (Strict Tenant Assertion)**: `middleware/org-context.ts` validates that the `X-Oryol-Org-Id` request header exactly matches the `org_id` claim in the verified JWT.
- [ ] **SEC-5 (Scope Guarding)**: Sensitive routes are protected by `requirePermission('<scope>')`. Missing permissions immediately return HTTP 403 `PERMISSION_DENIED`.
- [ ] **SEC-6 (Immediate Revocation)**: Logging out or revoking a membership writes to Cloudflare KV, instantly rejecting any active sessions for that token.
- [ ] **SEC-7 (Mandatory Audit Logging)**: All mutations (create org, add domain, invite member, update role, delete member) generate an immutable record in `audit_events`.

---

## 8. Test Cases & Test Matrix

| ID | Test Category | Scenario | Expected Outcome |
|---|---|---|---|
| **TC-01** | Identity | Register new user with email & password | User created in D1, password hashed with Argon2id, refresh token returned |
| **TC-02** | Identity | Login with invalid credentials | HTTP 401 `INVALID_CREDENTIALS`, no session token issued |
| **TC-03** | Org Context | Switch to Organization A | Issues 15-minute Session JWT with `org_id: org_A` and active permissions |
| **TC-04** | Security | Request Org B data using Org A Session JWT | HTTP 403 `TENANT_MISMATCH`, query aborted |
| **TC-05** | RBAC | Member with `mail.read` attempts to invite new member (`core.members.invite`) | HTTP 403 `PERMISSION_DENIED`, action blocked |
| **TC-06** | RBAC | Admin with `core.members.invite` invites new user | Member created with `pending_invite` status, audit event recorded |
| **TC-07** | Domains | Register new custom domain `oryolhq.com` | Domain created with `pending` status, DNS record specification generated |
| **TC-08** | Session | User calls `/v1/auth/logout` | Session revoked in KV, subsequent calls with same refresh token return HTTP 401 |
| **TC-09** | Audit | Member role updated by Admin | Audit log contains record with actor, action `member.role.updated`, and before/after diff |
| **TC-10** | Typecheck & Lint | Run `npm run typecheck` and `npm run lint` | Zero TypeScript errors (`strict: true`), zero ESLint errors |

---

## 9. Definition of Done (DoD)

A task in Phase 1 is marked **COMPLETE** only when:

1. **TypeScript Typecheck**: `npm run typecheck` passes with **0 errors** under `strict: true` and `noUncheckedIndexedAccess: true`.
2. **ESLint**: `npm run lint` passes with **0 errors** and zero unused code.
3. **Automated Test Suite**: 100% of unit and integration tests in `tests/` pass under Vitest.
4. **Multi-Tenant Boundary Verification**: Cross-tenant data isolation and permission denial test cases pass.
5. **Database Migrations**: All 4 D1 migration scripts apply cleanly against local Miniflare D1.
6. **Production Build**: `npx wrangler deploy --dry-run` compiles the Worker bundle with zero packaging errors.
7. **SERA Audit & Seal**: Exact git `HEAD` commit is sealed via `sera seal` with complete test evidence.
