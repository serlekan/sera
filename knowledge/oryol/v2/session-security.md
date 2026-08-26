# Oryol Session Security & Token State Machine v2.1

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Token Family State Machine, Honest Revocation SLA & Cryptographic JWKS Claims

---

## 1. Three-Tier Session & Token Hierarchy

Architecture v2.1 clearly distinguishes between Account Sessions, Organization Access Tokens, and Refresh Token Families:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       1. Account Session (ses_...)                          │
│     Represents authenticated human principal device login in D1 DB          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                 ┌─────────────────────┴─────────────────────┐
                 ▼                                           ▼
┌─────────────────────────────────┐         ┌─────────────────────────────────┐
│ 2. Refresh Token Family         │         │ 3. Organization Access Token    │
│    (fam_... with Generations)   │         │    (Short-lived 10m JWT at Edge)│
│ Atomic rotation in D1 database  │         │ Cryptographically self-contained│
└─────────────────────────────────┘         └─────────────────────────────────┘
```

---

## 2. Refresh Token Family State Machine

Refresh tokens use strict **Generation Chains** with atomic rotation in Cloudflare D1:

```sql
CREATE TABLE refresh_tokens (
    id TEXT PRIMARY KEY,                       -- rtok_<ulid>
    family_id TEXT NOT NULL,                   -- fam_<ulid>
    session_id TEXT NOT NULL,                  -- ses_<ulid>
    generation INTEGER NOT NULL,               -- 1, 2, 3...
    token_hash TEXT NOT NULL,                  -- SHA-256 hash of plaintext secret
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'consumed', 'revoked')),
    issued_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    consumed_at DATETIME,
    successor_generation INTEGER,
    revoked_at DATETIME,
    revocation_reason TEXT,
    expires_at DATETIME NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
    UNIQUE(family_id, generation)
);

CREATE INDEX idx_refresh_token_lookup ON refresh_tokens(family_id, token_hash);
```

### 2.1 Atomic Rotation & Automatic Breach Defense
1. **Normal Refresh (Atomic)**:
   - Within a single D1 transaction: active token is marked `consumed` (`consumed_at = CURRENT_TIMESTAMP`, `successor_generation = N + 1`), and new generation `N + 1` token is inserted.
2. **Reuse Detection (Token Theft Defense)**:
   - If an already-`consumed` refresh token is presented again:
     1. The entire `family_id` is immediately marked `revoked`.
     2. The associated `session_id` is terminated.
     3. An urgent security audit event (`sec.token_family_breach_detected`) is emitted.
     4. All active sessions on the device are invalidated, forcing complete re-authentication.

---

## 3. Honest Revocation SLA & Dual-Verification Model

Because stateless edge JWTs cannot be immediately revoked globally without introducing a centralized database read on every edge hit, Oryol enforces an **explicit two-tier revocation SLA**:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Standard API Endpoints                           │
│  (e.g. List Mail Messages, View Deal, Search Calendar)                      │
│                                                                             │
│  - Verifies Ed25519 JWT locally at Edge in <1ms without DB lookup.          │
│  - Standard endpoints accept the documented maximum 10-minute JWT window.   │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                         High-Risk Sensitive Endpoints                       │
│  (e.g. Delete Mailbox, Verify Domain, Export Data, Transfer Ownership)      │
│                                                                             │
│  - High-risk operations perform an authoritative current session,           │
│    membership, and security-version check in D1 and therefore do not        │
│    rely on the access-token TTL window for revocation.                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Canonical Organization Access JWT & JOSE Structure

Access tokens are signed using asymmetric `EdDSA` (Ed25519) and structured into protected JOSE headers, standard RFC7519 claims, and Oryol private claims:

### 4.1 Protected JOSE Header
```json
{
  "alg": "EdDSA",
  "typ": "JWT",
  "kid": "k_2026_q3_ed25519_01"
}
```

### 4.2 Standard RFC7519 Payload Claims
- `iss`: `"https://auth.oryol.com"`
- `aud`: `"https://api.oryol.com"`
- `sub`: `"prn_01H8Z7A2B3C4D5E6F7G8H9J0K1"` (Principal ID)
- `exp`: `1756150200` (10 minutes after issuance)
- `iat`: `1756149600`
- `jti`: `"jwt_01H8Z7E4F5G6H7J8K9L0M1N2P3"`

### 4.3 Oryol Private Claims
```json
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

## 5. Cookie, CSRF & Signing Key Management

1. **HttpOnly Cookie Strategy**:
   - Refresh tokens are stored in `__Host-Oryol-Refresh` cookies (`HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/v1/auth`).
2. **CSRF Defense**:
   - API mutations require `X-Oryol-Request: true` header; cookie-authenticated endpoints validate cryptographic double-submit anti-CSRF tokens.
3. **Key Rotation & JWKS**:
   - Asymmetric Ed25519 signing keys are rotated every 90 days.
   - Public keys are exposed via standard JWKS endpoint: `GET /.well-known/jwks.json`. Edge workers cache JWKS in Cloudflare KV with a 1-hour TTL.
4. **Step-Up Authentication Contract**:
   - Destructive endpoints (e.g. delete organization, purge mailbox) require step-up re-authentication within `< 5 minutes` (`POST /v1/auth/step-up`).
