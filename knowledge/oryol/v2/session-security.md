# Oryol Session Security & Token Family Architecture v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: CAS Refresh Concurrency, Account-Level Compromise Scope, JWKS Key Rotation & Cookie Transport

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
    token_hash TEXT NOT NULL,                  -- SHA-256 hash of secret token
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

## 2. Compare-and-Swap (CAS) Refresh Token Rotation

When a client presents a refresh token `rtk_curr` with secret `S`:

```sql
-- Step 1: Atomic Compare-and-Swap Consumption
UPDATE refresh_tokens
SET
    status = 'consumed',
    consumed_at = CURRENT_TIMESTAMP,
    successor_token_id = :successor_token_id
WHERE
    token_id = :token_id
    AND family_id = :family_id
    AND generation = :generation
    AND status = 'active';
```

### Invariants for CAS Rotation:
1. **Single Rotation Winner**: `affected_rows` **MUST equal 1**. Only one concurrent worker can successfully rotate the generation.
2. **Replay Trigger on 0 Rows**: If `affected_rows == 0`, reload authoritative token state:
   - If token status is `'consumed'` or `'revoked'`: **Trigger Account-Level Replay Defense**.
   - If token hash does not match: **Reject with 401 Unauthorized**.

### Account-Level Replay Defense:
When token reuse is detected:
1. Mark `refresh_token_families.status = 'revoked'` (`revocation_reason = 'token_reuse_detected'`).
2. Mark all active `account_sessions` for that principal as `'revoked'`.
3. Increment `principal_security_versions.security_version` in D1.
4. Record high-severity security audit event `core.session.security_breach`.
5. Require full re-authentication across **all user devices**.

---

## 3. Membership Revalidation on Token Issuance

Every organization access-token issuance or refresh-derived token exchange must revalidate against authoritative D1 state:
- `account_sessions.status == 'active'`
- `principals.status == 'active'`
- `memberships.status == 'active'` AND `memberships.principal_id == principal_id` AND `memberships.organization_id == target_organization_id`
- `organizations.status == 'active'`
- `authorization_versions` current

A refresh token alone can **never** mint an access token into a membership that has been revoked or suspended.

---

## 4. Protected JOSE Header & JWT Structure

```json
// Protected JOSE Header
{
  "alg": "EdDSA",
  "typ": "JWT",
  "kid": "k_2026_q3_ed25519_01"
}

// Payload Claims (RFC7519 + Oryol Private Claims)
{
  "iss": "https://auth.oryol.com",
  "aud": "https://api.oryol.com",
  "sub": "prn_01H8Z7A2B3C4D5E6F7G8H9J0K1",
  "exp": 1756150200,
  "iat": 1756149600,
  "jti": "jwt_01H8Z7E4F5G6H7J8K9L0M1N2P3",
  "token_type": "org_access",
  "session_id": "ses_01H8Z7B5C6D7E8F9G0H1J2K3L4",
  "organization_id": "org_01H8Z7C8D9E0F1G2H3J4K5L6M7",
  "membership_id": "mem_01H8Z7D1E2F3G4H5J6K7L8M9N0",
  "security_version": 1,
  "authorization_version": 2,
  "perms": ["mail.messages.read", "mail.messages.send", "core.domains.manage"]
}
```

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

## 6. Browser Cookie Transport & CSRF Protection

- **Refresh Cookie**: `__Host-Oryol-Refresh` (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/v1/auth`).
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
