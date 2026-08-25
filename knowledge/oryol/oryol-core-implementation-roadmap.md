# Oryol Core — Implementation Roadmap & Engineering Plan

**Repository**: `serlekan/oryol-core`  
**Ecosystem**: Oryol Workspace Platform  
**Target Runtime**: Cloudflare Edge Infrastructure (Workers + D1 + KV + Queues)  
**Status**: Implementation Roadmap (Ready for Antigravity Execution)

---

## 1. Repository Structure

`oryol-core` is organized as a modular, lightweight, edge-native TypeScript service utilizing **Hono** on **Cloudflare Workers**:

```
oryol-core/
├── .github/
│   └── workflows/
│       ├── ci.yml                 # Typecheck, lint, test on every PR
│       └── deploy.yml             # Wrangler deploy on merge to main
├── .sera/
│   ├── config.json                # SERA risk policies, token budgets & tests
│   ├── architecture-rules.md      # Permanent 7 Oryol Workspace rules
│   └── review-rules.md            # Exact-HEAD review checklist
├── migrations/
│   ├── 0001_create_identity.sql   # users, user_credentials, user_sessions
│   ├── 0002_create_orgs.sql       # organizations, organization_domains
│   ├── 0003_create_rbac.sql       # roles, memberships
│   └── 0004_create_audit.sql      # audit_events, notifications
├── src/
│   ├── config/
│   │   ├── bindings.ts            # Cloudflare Worker Env (D1, KV, Secrets)
│   │   └── constants.ts           # ID prefixes, token durations, default roles
│   ├── db/
│   │   ├── client.ts              # D1 client wrapper & transaction helper
│   │   └── schema.ts              # D1 relational TypeScript table interfaces
│   ├── lib/
│   │   ├── crypto.ts              # Argon2id / WebAuthn edge helpers, Ed25519 JWT
│   │   ├── errors.ts              # Standard AppError & HTTP error mapping
│   │   ├── response.ts            # Unified success & error response envelopes
│   │   └── ulid.ts                # Prefixed identifier generator (usr_, org_, etc.)
│   ├── middleware/
│   │   ├── audit.ts               # Non-blocking audit event emitter
│   │   ├── auth.ts                # Edge JWT validation & session verification
│   │   ├── error-handler.ts       # Global exception interceptor
│   │   ├── org-context.ts         # X-Oryol-Org-Id assertion & tenant binding
│   │   └── rbac.ts                # Permission scope guard (requirePermission)
│   ├── modules/
│   │   ├── identity/
│   │   │   ├── identity.service.ts # User creation, password/passkey verification
│   │   │   ├── session.service.ts  # Token issuance, refresh, KV session store
│   │   │   └── identity.routes.ts  # /v1/auth/* endpoints
│   │   ├── organizations/
│   │   │   ├── org.service.ts      # Organization CRUD, settings & domain config
│   │   │   └── org.routes.ts       # /v1/orgs/* and /v1/domains/* endpoints
│   │   ├── memberships/
│   │   │   ├── member.service.ts   # Invites, role assignment, permission bitmap
│   │   │   ├── rbac.service.ts     # Role hierarchy & permission resolution
│   │   │   └── member.routes.ts    # /v1/orgs/:id/members & /v1/orgs/:id/roles
│   │   └── audit/
│   │       ├── audit.service.ts    # Audit ingestion queue producer & query API
│   │       └── audit.routes.ts     # /v1/audit/* endpoints
│   ├── types/
│   │   ├── domain.ts              # Core domain models (User, Org, Membership)
│   │   └── api.ts                 # Request/Response DTO contracts
│   └── index.ts                   # Hono app router & Cloudflare Worker entrypoint
├── tests/
│   ├── unit/                      # Crypto, ULID, Permission engine unit tests
│   ├── integration/               # D1-backed route tests using Miniflare
│   └── fixtures/                  # Deterministic test seeds (Sarah, David, Oryol HQ)
├── wrangler.toml                  # Cloudflare Worker, D1 & KV bindings config
├── package.json                   # Dependencies, scripts (dev, test, deploy)
├── tsconfig.json                  # strict: true, noUncheckedIndexedAccess: true
└── vitest.config.ts               # Vitest + Cloudflare Workers pool configuration
```

---

## 2. Service Boundaries

`oryol-core` is structured into five distinct, decoupled service boundaries:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Edge API Gateway Router                           │
└──────┬──────────────────────┬──────────────────────┬─────────────────┬──────┘
       ▼                      ▼                      ▼                 ▼
┌──────────────┐      ┌──────────────┐      ┌────────────────┐ ┌──────────────┐
│   Identity   │      │ Organization │      │   Membership   │ │    Audit     │
│   Service    │      │   Service    │      │  & RBAC Engine │ │   Service    │
└──────┬───────┘      └──────┬───────┘      └────────┬───────┘ └──────┬───────┘
       ▼                      ▼                      ▼                 ▼
┌──────────────┐      ┌──────────────┐      ┌────────────────┐ ┌──────────────┐
│ users        │      │organizations │      │ memberships    │ │ audit_events │
│ credentials  │      │org_domains   │      │ roles          │ │              │
│ sessions(KV) │      │              │      │                │ │              │
└──────────────┘      └──────────────┘      └────────────────┘ └──────────────┘
```

1. **Identity & Session Service**:
   - Manages global `usr_...` user accounts, WebAuthn passkeys, password hashes, and magic links.
   - Issues short-lived Organization Session JWTs and handles KV session revocation.
2. **Organization & Domain Service**:
   - Manages `org_...` tenants, custom domain registration (`dom_...`), and organization security policies.
3. **Membership & RBAC Service**:
   - Manages `mem_...` organization relationships, invitation lifecycles, and system/custom roles (`rol_...`).
   - Resolves effective permissions into compact bitmaps for sub-millisecond edge evaluation.
4. **Audit Service**:
   - Collects audit logs from middleware interceptors and writes to `audit_events`.
   - Provides organization-scoped query APIs (`/v1/audit/events`).
5. **Edge Security & Context Middleware**:
   - Enforces cryptographic JWT verification, tenant context assertions (`X-Oryol-Org-Id`), and RBAC scope gates across all routes.

---

## 3. Database Migration Order (Cloudflare D1)

Database migrations execute sequentially against Cloudflare D1 via `wrangler d1 migrations apply`:

### Migration 0001: Global Identity & Sessions
- `users`: Core user profile (`id`, `email`, `display_name`, `avatar_url`, `is_active`, `created_at`).
- `user_credentials`: Authentication secrets (`user_id`, `password_hash`, `webauthn_credentials`, `mfa_secret`).
- `user_sessions`: Active session registry (`id`, `user_id`, `active_organization_id`, `refresh_token_hash`, `expires_at`).

### Migration 0002: Organizations & Custom Domains
- `organizations`: Tenant root (`id`, `name`, `slug`, `plan`, `settings`, `created_at`).
- `organization_domains`: Custom domains (`id`, `organization_id`, `domain`, `status`, `dns_records`, `verified_at`).

### Migration 0003: Memberships & Roles
- `roles`: Role definitions (`id`, `organization_id`, `name`, `permissions`, `is_system_role`).
- `memberships`: User-to-organization binding (`id`, `organization_id`, `user_id`, `role`, `status`, `permissions_override`, `custom_title`).

### Migration 0004: Audit Trail
- `audit_events`: Immutable audit records (`id`, `organization_id`, `actor_user_id`, `actor_membership_id`, `action`, `resource_type`, `resource_id`, `details`, `status`, `created_at`).
- Indexes: `idx_audit_org_time` ON `audit_events(organization_id, created_at DESC)`.

---

## 4. API Implementation Order

Implementation proceeds in five strictly sequenced phases:

### Phase 1: Core Foundation & Libraries (Days 1–2)
1. Setup `wrangler.toml`, D1 database, and KV namespaces.
2. Implement `lib/ulid.ts` for standard prefixed IDs (`usr_`, `org_`, `mem_`, `dom_`, `aud_`, `ses_`).
3. Implement `lib/crypto.ts` for Ed25519 JWT signing/verification and Argon2id password hashing.
4. Implement `lib/response.ts` and `lib/errors.ts` for unified response envelopes.

### Phase 2: Identity & Session Management (Days 3–4)
1. `POST /v1/auth/register` — Create user account & credentials.
2. `POST /v1/auth/login` — Password / OTP verification & initial refresh token issuance.
3. `POST /v1/auth/passkey/login-challenge` & `login-verify` — WebAuthn flow.
4. `POST /v1/auth/switch-org` — Issue scoped Session JWT for specified `organization_id`.
5. `POST /v1/auth/logout` — Revoke session in KV.

### Phase 3: Organization & Domain Management (Days 5–6)
1. `GET /v1/orgs` — List organizations for authenticated user.
2. `POST /v1/orgs` — Create new organization (caller automatically assigned `Owner` membership).
3. `GET /v1/orgs/:orgId` — Retrieve organization details and quota state.
4. `PATCH /v1/orgs/:orgId` — Update organization settings (`core.org.edit`).
5. `GET /v1/domains` & `POST /v1/domains` — Register custom domains.

### Phase 4: Memberships & RBAC Engine (Days 7–8)
1. `GET /v1/orgs/:orgId/members` — List members and assigned roles.
2. `POST /v1/orgs/:orgId/members/invite` — Dispatch member invite.
3. `POST /v1/orgs/:orgId/members/accept` — Accept invitation and activate membership.
4. `PATCH /v1/orgs/:orgId/members/:memberId` — Update role or custom title.
5. `DELETE /v1/orgs/:orgId/members/:memberId` — Revoke membership and terminate active sessions.
6. `GET /v1/orgs/:orgId/roles` & `POST /v1/orgs/:orgId/roles` — Custom RBAC role definitions.

### Phase 5: Audit Event Pipeline (Days 9–10)
1. Implement `middleware/audit.ts` interceptor attached to all mutating routes.
2. `GET /v1/audit/events` — Paginated query of organization audit trail.
3. Integration verification across all modules.

---

## 5. Security Implementation Order

1. **Level 1 — SQL Parameterization Guard**: All D1 queries must use prepared statements with bound parameters (`db.prepare('...').bind(...)`). Zero string interpolation.
2. **Level 2 — Edge JWT Verification**: Cloudflare Workers verify Ed25519 token signatures at the edge before route handlers execute.
3. **Level 3 — Tenant Assertion & Context Binding**: `middleware/org-context.ts` validates that the `X-Oryol-Org-Id` header exactly matches the JWT's `org_id` claim and the user holds an `active` membership.
4. **Level 4 — Non-Bypassable RBAC Scope Interceptors**: Endpoints apply `requirePermission('core.members.invite')` before business logic executes.
5. **Level 5 — Immutable Audit Ledger**: Sensitive operations asynchronously emit structured audit logs to D1 before returning the client response.

---

## 6. Testing Strategy

### 6.1 Testing Pyramid

| Test Layer | Framework | Scope |
|---|---|---|
| **Static Typecheck** | `tsc --noEmit` | `strict: true`, `noUncheckedIndexedAccess: true` (0 errors required) |
| **Linting** | ESLint | Zero unused variables, proper error handling |
| **Unit Tests** | Vitest | Crypto utilities, ULID generation, JWT encoding, RBAC permission evaluation |
| **Integration Tests** | Vitest + `@cloudflare/workers-vitest-pool` | Full HTTP lifecycle testing against in-memory Miniflare D1 database |
| **Multi-Tenant Security Tests** | Vitest | Cross-tenant data leakage tests, permission denial assertions, session revocation verification |

### 6.2 Mandatory Multi-Tenant Test Matrix

```typescript
describe('Multi-Tenant Boundary Isolation', () => {
  it('rejects query when user belongs to Org A but requests Org B data', async () => {
    const tokenOrgA = generateTestJwt({ userId: 'usr_1', orgId: 'org_A' });
    const res = await app.request('/v1/orgs/org_B/members', {
      headers: { Authorization: `Bearer ${tokenOrgA}`, 'X-Oryol-Org-Id': 'org_B' }
    });
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({
      success: false,
      error: { code: 'TENANT_MISMATCH' }
    });
  });
});
```

---

## 7. Local Development Setup

### Prerequisites
- Node.js ≥ 20.x
- npm ≥ 10.x
- Cloudflare Wrangler CLI

### Quick Start
```bash
# Clone and install dependencies
git clone https://github.com/serlekan/oryol-core.git
cd oryol-core
npm install

# Initialize local D1 database with migrations
npx wrangler d1 migrations apply DB --local

# Seed initial development fixtures (Sarah Jenkins, David Chen, Oryol HQ)
npm run seed:local

# Start local Cloudflare Workers development server (Miniflare)
npm run dev
```

The API will be available at `http://localhost:8787`.

---

## 8. Cloudflare Deployment Strategy

### Environments

- **Local (`local`)**: Runs locally via Miniflare (`npm run dev`) with in-memory SQLite D1.
- **Staging (`staging`)**: Automated deployment from `develop` branch to Cloudflare Workers staging environment connected to staging D1 database (`oryol-core-staging-db`).
- **Production (`production`)**: Automated deployment from `main` branch to Cloudflare global edge connected to production D1 database (`oryol-core-prod-db`).

### Secret Management
Secrets configured via `wrangler secret put`:
- `JWT_PRIVATE_KEY` (Ed25519 Private Key in PEM format)
- `JWT_PUBLIC_KEY` (Ed25519 Public Key in PEM format)
- `SESSION_SECRET` (Entropy secret for cookie signing)

---

## 9. MVP Scope (Delivered in Phase 1 Baseline)

✅ **User Identity**: Global accounts (`usr_...`), email, credentials, profile metadata.  
✅ **Organization Model**: Organization creation (`org_...`), slug registry, settings.  
✅ **Membership Model**: Organization memberships (`mem_...`), invitation workflow.  
✅ **System Roles**: `Owner`, `Admin`, `Member`, `Guest` role hierarchy.  
✅ **Permissions**: Fine-grained dot-notation scopes with edge bitmap evaluation.  
✅ **Session Management**: Dual-tier tokens (Refresh + Scoped Session JWT) with KV revocation.  
✅ **Audit Trail**: Immutable audit event logging (`aud_...`) on all mutating operations.

---

## 10. Deferred Features (Post-MVP Phase 2+)

The following enterprise and product features are **strictly deferred** to subsequent phases:

- ❌ **Enterprise SAML 2.0 & OIDC SSO**: Deferred to Phase 2.
- ❌ **Stripe Billing & Subscription Management**: Deferred to Phase 2.
- ❌ **AI Provider Orchestration & Gateway**: Deferred to Phase 2 (Oryol AI).
- ❌ **Inbound/Outbound Email Transport (SMTP/IMAP/Resend)**: Belongs to OryolMail backend.
- ❌ **CRM / Calendar / Drive Entity Storage**: Belongs to respective product backends.
