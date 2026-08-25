# Oryol Session Security v2 — Authoritative Session Architecture

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Correction**: Cloudflare KV is NOT an authoritative security store; D1 is authoritative.

---

## 1. The Session Security Invariant

> [!CAUTION]
> **Architectural Correction**:  
> Cloudflare KV is an **eventually consistent** edge cache. It must **never** be used as the authoritative source of truth for immediate session revocation, token reuse detection, or security locks.  
> **D1 (Relational Edge DB)** is the single **authoritative store** for session lifecycle, token families, and device history.

| Storage Layer | Allowed Usage | FORBIDDEN Usage |
|---|---|---|
| **Cloudflare D1 (Relational)** | Authoritative session records, refresh token hashes, token family rotation trees, device tracking history, audit links. | High-frequency read caching on every single API hit. |
| **Cloudflare KV (Key-Value)** | Short-lived cached JWT public keys, fast rate-limiting buckets, temporary OTP verification nonces (TTL < 5m). | Authoritative security state, primary refresh token storage, final revocation decisions. |

---

## 2. Dual-Token Rotation Architecture

```text
                                 ┌─────────────────────────────┐
                                 │       Client Device         │
                                 └──────────────┬──────────────┘
                                                │
                 ┌──────────────────────────────┴──────────────────────────────┐
                 ▼                                                             ▼
  ┌─────────────────────────────┐                               ┌─────────────────────────────┐
  │     Scoped Access Token     │                               │        Refresh Token        │
  │     (15-Minute Short JWT)   │                               │     (Rotating Token Family) │
  └──────────────┬──────────────┘                               └──────────────┬──────────────┘
                 │                                                             │
                 ▼                                                             ▼
  ┌─────────────────────────────┐                               ┌─────────────────────────────┐
  │     Edge JWT Validation     │                               │      D1 Session Refresh     │
  │ (Validated locally in <1ms  │                               │ (Atomic token rotation &    │
  │  via Ed25519 public key)    │                               │  reuse detection in D1 DB)  │
  └─────────────────────────────┘                               └─────────────────────────────┘
```

### 2.1 Token Rotation & Reuse Detection
1. **Token Family**: Each login creates a `session_family_id`. Every refresh generates a new refresh token and invalidates the previous one in the same D1 transaction.
2. **Automatic Breach Defense**: If an already-invalidated refresh token is presented again (indicating token theft), the entire session family is instantly revoked across all devices, triggering an urgent security audit event (`sec.token_reuse_detected`).

---

## 3. Authoritative Session Schema (D1)

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,                       -- ses_<ulid>
    session_family_id TEXT NOT NULL,           -- fam_<ulid>
    principal_id TEXT NOT NULL,                -- prn_<ulid>
    active_organization_id TEXT NOT NULL,      -- org_<ulid>
    refresh_token_hash TEXT NOT NULL,          -- SHA-256 hash of active token
    device_fingerprint TEXT NOT NULL,
    ip_address TEXT NOT NULL,
    user_agent TEXT NOT NULL,
    is_revoked INTEGER NOT NULL DEFAULT 0,
    revoked_at DATETIME,
    revocation_reason TEXT,
    last_active_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (principal_id) REFERENCES principals(id) ON DELETE CASCADE,
    FOREIGN KEY (active_organization_id) REFERENCES organizations(id) ON DELETE CASCADE
);

CREATE INDEX idx_sessions_family ON sessions(session_family_id, is_revoked);
CREATE INDEX idx_sessions_principal ON sessions(principal_id, active_organization_id);
```

---

## 4. Immediate Revocation Workflow

1. User clicks **"Logout All Devices"** or Admin revokes Membership:
   - D1 transaction updates `sessions SET is_revoked = 1, revoked_at = CURRENT_TIMESTAMP WHERE principal_id = ?`.
2. Active 15-minute Access Tokens expire naturally within 15 minutes; subsequent refresh attempts hit D1 and fail immediately.
3. High-security actions (e.g. password change, domain deletion) require an explicit D1 session verification check rather than relying solely on access token claims.
