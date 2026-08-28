# Oryol Session Security & Token Family Architecture v2.3

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.3) — Subject to Independent Architecture Review  
**Revision Scope**: Organization Security Policy Integration & Dynamic Timeout Enforcement (ADR-001)

---

## 1. Authoritative Session Entities (D1 Relational)

```sql
-- 1. Account Sessions (Authoritative Base)
CREATE TABLE account_sessions (
    id TEXT PRIMARY KEY,                       -- ses_<ulid>
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    device_fingerprint TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'revoked', 'expired')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_active_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL
);

-- 2. Refresh Token Families (Rotation State Machine)
CREATE TABLE refresh_token_families (
    family_id TEXT PRIMARY KEY,                -- fam_<ulid>
    session_id TEXT NOT NULL REFERENCES account_sessions(id) ON DELETE CASCADE,
    current_generation INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'revoked')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_rotated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    revoked_at DATETIME,
    revocation_reason TEXT                     -- e.g. 'token_reuse_detected', 'user_logout', 'admin_revocation'
);

-- 3. Refresh Tokens (Generational Chain)
CREATE TABLE refresh_tokens (
    token_id TEXT PRIMARY KEY,                 -- rtk_<ulid>
    family_id TEXT NOT NULL REFERENCES refresh_token_families(family_id) ON DELETE CASCADE,
    generation INTEGER NOT NULL,
    token_hash TEXT NOT NULL,                  -- SHA-256 hash of secret token S
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'consumed', 'revoked')),
    issued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    consumed_at DATETIME,
    successor_token_id TEXT,                   -- rtk_<ulid> of next generation
    expires_at DATETIME NOT NULL,
    UNIQUE(family_id, generation)
);

-- 4. Principal Security Versions (Instant Account-Wide Invalidation)
CREATE TABLE principal_security_versions (
    principal_id TEXT PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
    security_version INTEGER NOT NULL DEFAULT 1,
    last_incremented_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 2. Compare-and-Swap (CAS) Refresh Token Rotation & Atomic Successor

Refresh token rotation is modeled as an atomic state machine in D1.

```sql
-- 1. CAS: Consume current token S_n ONLY IF it is active and matching hash
UPDATE refresh_tokens
SET status = 'consumed',
    consumed_at = CURRENT_TIMESTAMP,
    successor_token_id = :new_token_id
WHERE token_id = :presented_token_id
  AND token_hash = :presented_hash
  AND status = 'active'
  AND expires_at > datetime('now');

-- 2. Insert successor token S_n+1 into family
INSERT INTO refresh_tokens (token_id, family_id, generation, token_hash, expires_at)
VALUES (:new_token_id, :family_id, :next_gen, :new_hash, datetime('now', '+30 days'));

-- 3. Advance family generation counter
UPDATE refresh_token_families
SET current_generation = :next_gen,
    last_rotated_at = CURRENT_TIMESTAMP
WHERE family_id = :family_id;
```

---

## 3. Replay Breach Defenses & Account-Level Compromise Scope

If a consumed or revoked refresh token is presented:
1. The CAS predicate returns 0 affected rows.
2. The engine immediately revokes the entire `refresh_token_families` row.
3. If token theft is confirmed, `principal_security_versions.security_version` is incremented, instantly invalidating all active edge tokens for the principal.
4. An immutable security audit log `core.auth.token_family_breach` is emitted.

---

## 4. Organization Security Policy Dynamic Enforcement (ADR-001 — F-4)

Session validation and token minting dynamically enforce policies configured in `organization_security_policies`:

### 4.1 MFA Policy Enforcement
- **`mfa_enforcement = 'required_all'`**: All authenticated sessions for organization members must satisfy multi-factor authentication. Access tokens carry `mfaVerified: true`. If MFA has not been completed, tokens are minted with restricted scope (`mfaVerified: false`), causing Step 8.4 to return `DENY(CONTEXT_MFA_REQUIRED)`.
- **`mfa_enforcement = 'required_admins'`**: Applied specifically to members holding `template_key IN ('owner', 'admin')`.

### 4.2 Dynamic Session Timeout Policies
- **Idle Timeout**: On every session activity refresh, `account_sessions.last_active_at` is evaluated against `organization_security_policies.session_idle_timeout_seconds` (default: 86,400s / 24h; min: 300s). If `datetime('now') > datetime(last_active_at, '+' || session_idle_timeout_seconds || ' seconds')`, the session is marked `status = 'expired'`.
- **Absolute Timeout**: Evaluated against `account_sessions.created_at` and `organization_security_policies.session_absolute_timeout_seconds` (default: 604,800s / 7d; min: 3,600s). Once exceeded, re-authentication is mandatory.

---

## 5. JWKS Key Lifecycle & Unknown `kid` Handling

- **Key Rotation Schedule**: Signing keys rotate every 90 days. Previous verification keys remain active in the JWKS for a 14-day overlap grace period.
- **Edge Cache TTL**: Edge workers cache the JWKS in Cloudflare KV / in-memory for a maximum TTL of **1 hour**.
- **Unknown `kid` On-Demand Resolution Flow**:
  1. If an incoming token specifies an unknown `kid`:
  2. Edge worker executes **one on-demand refresh** from authoritative `https://auth.oryol.com/.well-known/jwks.json`.
  3. If the `kid` is present in the refreshed keyset: Verify signature and proceed.
  4. If the `kid` remains unknown after refresh: **Immediately reject token with `401 Unauthorized (UNKNOWN_KEY_IDENTIFIER)`**.

---

## 6. Browser Cookie Transport (`__Host-` Standard) & CSRF Protection

Per RFC 6265bis, any cookie using the `__Host-` prefix MUST be sent with `Secure`, `Path=/`, and **MUST NOT contain a `Domain` attribute**:

```http
Set-Cookie: __Host-Oryol-Refresh=<secret_token>; HttpOnly; Secure; SameSite=Strict; Path=/
```

- **Path Scoping**: Path authorization is enforced by server endpoint logic (e.g. `/v1/auth/refresh`), not by invalid cookie `Path` parameters.
- **CSRF Mitigation**:
  - API endpoints enforce custom header verification: `X-Oryol-Request: true`.
  - Mutating cookie-authenticated endpoints validate double-submit anti-CSRF tokens.

---

## 7. Dual Verification Revocation SLA & Step-Up Proofs

- **Standard API Endpoints**: Edge-verified via Ed25519 with a maximum revocation window of **10 minutes** (natural TTL).
- **High-Risk Sensitive Endpoints**: High-risk operations (e.g. deleting mailboxes, verifying domains, transferring ownership) perform an authoritative current session, membership, and security-version check in D1 and **do not rely on the access-token TTL window for revocation**.
- **Cryptographic Step-Up Proof Binding**:

```sql
CREATE TABLE step_up_proofs (
    id TEXT PRIMARY KEY,                       -- sup_<ulid>
    session_id TEXT NOT NULL REFERENCES account_sessions(id) ON DELETE CASCADE,
    principal_id TEXT NOT NULL REFERENCES principals(id) ON DELETE CASCADE,
    operation_risk_class TEXT NOT NULL,        -- 'org_ownership_transfer', 'mailbox_purge', 'dkim_rotate'
    verified_factor TEXT NOT NULL,             -- 'passkey_webauthn', 'totp'
    issued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,              -- Max 5-minute validity window
    consumed_at DATETIME,
    UNIQUE(session_id, operation_risk_class, issued_at)
);
```
