# Oryol Session Security & Token Family Architecture v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Refresh Token Family Entities, Atomic Rotation State Machine, Replay Breaches & Step-Up Proof Binding

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

-- 4. Session Security Versions (Instant Invalidation)
CREATE TABLE session_security_versions (
    principal_id TEXT PRIMARY KEY REFERENCES principals(id) ON DELETE CASCADE,
    security_version INTEGER NOT NULL DEFAULT 1,
    last_incremented_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 2. Atomic Refresh State Machine & Concurrency Control

When an client presents a refresh token `rtk_curr` with secret `S`:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Atomic D1 Refresh Transaction                         │
│                                                                             │
│ 1. Verify `refresh_token_families` WHERE family_id = ? AND status = 'active'│
│ 2. Verify `refresh_tokens` WHERE token_id = ? AND token_hash = SHA256(S)    │
│    └─► IF status == 'consumed' OR status == 'revoked':                      │
│          ──► TRIGGER REPLAY DEFENSE (Revoke Family, Terminate Session)      │
│ 3. UPDATE `refresh_tokens` SET status = 'consumed', consumed_at = NOW(),    │
│    successor_token_id = ? WHERE token_id = ?                                │
│ 4. INSERT INTO `refresh_tokens` (new token_id, generation + 1, new_hash)     │
│ 5. UPDATE `refresh_token_families` SET current_generation = gen + 1,        │
│    last_rotated_at = NOW() WHERE family_id = ?                              │
│ 6. Commit transaction atomically                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Concurrency & Replay Attack Defense
1. **Single Rotation Winner**: If two concurrent requests present the same generation token, only one transaction succeeds in updating the row from `active` to `consumed`.
2. **Replay Detection & Family Revocation**: The competing request sees `status = 'consumed'`. It immediately:
   - Sets `refresh_token_families.status = 'revoked'` (`revocation_reason = 'token_reuse_detected'`).
   - Sets `account_sessions.status = 'revoked'`.
   - Increments `session_security_versions.security_version`.
   - Emits a high-severity security audit event (`core.session.security_breach`).
   - Requires full re-authentication for all user devices.

---

## 3. Membership Revocation & Organization Switching

- **Membership Revocation**: When a membership is suspended or removed, the organization's `authorization_versions` is incremented. High-risk endpoints check D1 and reject stale tokens immediately.
- **Organization Switching**: Switching active organizations (`POST /v1/auth/switch-org`) verifies target membership status directly against D1 before issuing a new organization access token.

---

## 4. Protected JOSE Header & JWT Claim Structure

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
  "authorization_version": 2,
  "perms": ["mail.messages.read", "mail.messages.send", "core.domains.manage"]
}
```

---

## 5. Dual Verification Revocation SLA

- **Standard API Endpoints**: Edge-verified via Ed25519 with a maximum revocation window of **10 minutes** (natural TTL).
- **High-Risk Sensitive Endpoints**: High-risk operations (e.g. deleting mailboxes, verifying domains, transferring ownership) perform an authoritative current session, membership, and security-version check in D1 and **do not rely on the access-token TTL window for revocation**.

---

## 6. Step-Up Authentication Proof Binding Contract

Step-up authentication is bound cryptographically to specific context and cannot be satisfied by an unbound boolean flag:

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
