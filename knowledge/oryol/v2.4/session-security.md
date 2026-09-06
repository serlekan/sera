# Oryol Session Security & Token Family Architecture v2.4 (Delta over v2.3)

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.4) — Subject to Independent Architecture Review
**Predecessor**: [`../v2.3/session-security.md`](../v2.3/session-security.md) (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Revision Scope**: Executable session-policy enforcement, absolute/idle timeout anchors, activity-update rule, multi-organization decoupling, refresh rotation / delivery-failure recovery, and the authoritative check for sensitive session operations (ADR-004). **Zero table changes.**

---

## 1. Carry-Forward Declaration

All of [`../v2.3/session-security.md`](../v2.3/session-security.md) is carried forward **unchanged** except where §2 amends the interpretation of §8 ("Organization Security Policy Dynamic Enforcement"). Specifically retained verbatim:

- §1 Authoritative Session Entities (`account_sessions`, `refresh_token_families`, `refresh_tokens`, `principal_security_versions`) — **unchanged schema**.
- §2 Compare-and-Swap Refresh Token Rotation, all CAS invariants, **Account-Level Replay Defense** (Cases A/B/C) — **unchanged**. No permissive replay grace window is introduced.
- §3 Membership Revalidation on Token Issuance — **unchanged**.
- §4 Protected JOSE Header & JWT Structure — **unchanged**.
- §5 JWKS Key Lifecycle & Unknown `kid` Handling (`UNKNOWN_KEY_IDENTIFIER`) — **unchanged**.
- §6 Browser Cookie Transport (`__Host-Oryol-Refresh`, `Path=/`, CSRF) — **unchanged**.
- §7 Dual Verification Revocation SLA & Step-Up Proofs (`step_up_proofs`) — **unchanged**.

---

## 2. v2.4 Amendment — §8 becomes executable (ADR-004)

v2.3 §8 established the intent (policy columns govern token TTL, strictest tenant enforcement, idle vs `last_active_at`) but did not specify the activity-update rule, the absolute-timeout anchor, or how multi-organization membership is resolved without cross-tenant coupling. **[ADR-004](adr/ADR-004-session-policy-and-refresh-recovery.md) supersedes v2.3 §8.2 with the following authoritative rules.**

### 2.1 Three session layers (ADR-004 §2)

| Layer | Backing entity | Governed by |
|---|---|---|
| Global identity session | `account_sessions` + refresh family | `account_sessions.created_at` + fixed 30-day platform cap |
| Organization authorization session | issued `org_access` JWT + per-request idle check | that organization's `session_idle_timeout_seconds` / `session_absolute_timeout_seconds` |
| Refresh family | `refresh_token_families` / `refresh_tokens` | 30-day platform cap; v2.3 rotation state machine |

> [!IMPORTANT]
> **`SESSION_LAYER_SEPARATION`**: A tenant's tightened timeout shortens **only** that organization's authorization session. It MUST NOT shorten the global identity session, the refresh family, or another organization's authorization session. This **removes** the v2.3 "strictest across all memberships" coupling — one tenant can no longer shorten another tenant's usable identity session.

### 2.2 Idle timeout (ADR-004 §3)

- Idle test: `now - account_sessions.last_active_at` vs `policy(O).session_idle_timeout_seconds` → `DENY(SESSION_IDLE_TIMEOUT)`.
- **`SESSION_ACTIVITY_HEARTBEAT`**: `last_active_at` is updated by a **rate-limited heartbeat** — at most once per `SESSION_ACTIVITY_HEARTBEAT_SECONDS = 300` per session (single conditional `UPDATE`), plus unconditionally on every refresh and every high-risk operation. **Not** on every authorized request. Write amplification is bounded to ≤ 1 write / session / 5 min.

### 2.3 Absolute timeout (ADR-004 §4)

> [!IMPORTANT]
> **`SESSION_ABSOLUTE_ANCHOR`**: Absolute session age is measured **exclusively from the immutable `account_sessions.created_at`**. No refresh, rotation, heartbeat, or policy change moves this anchor.

- Global cap: `GLOBAL_SESSION_ABSOLUTE_CAP_SECONDS = 2592000` (30 d) — fixed, tenant-independent. Exceeded → `account_sessions.status = 'expired'`, full re-auth.
- Per-organization ceiling: `now - created_at > policy(O).session_absolute_timeout_seconds` → `DENY(SESSION_ABSOLUTE_TIMEOUT)` **for organization O only**; the refresh family and other orgs are untouched.
- `refresh_tokens.expires_at` per generation `= min(issued_at + 14d, created_at + 30d)` → non-increasing tail; **no rotation extends a session past either ceiling**.

### 2.4 Multi-organization principal (ADR-004 §5.3–5.4)

One global identity session + one refresh family + up to N independent `org_access` tokens. Membership add: no effect on existing tokens; principal may exchange for the new org's token. Membership remove/suspend: increments only that org's `authorization_versions`; other orgs and the refresh family untouched.

---

## 3. Refresh Rotation & Delivery Failure (ADR-004 §6)

> [!IMPORTANT]
> **`REFRESH_EVENT_SEPARATION`**: credential rotation success ≠ organization-token exchange success ≠ HTTP response delivery success. Replay defense keys on rotation-commit; recovery keys on non-delivery.

- **Predictable exchange failures happen BEFORE consuming the refresh token** (ADR-004 §6.3): invalid org id, org not found, no active membership, org suspended → the refresh row is **not** consumed; client keeps its valid credential and retries.
- **Post-rotation signing failure**: rotation is committed; response returns the successor cookie + `org_exchange_status: "deferred"`; client calls the separate org-token endpoint. Rotation is never rolled back.
- **Response transport loss**: successor is recoverable within `SUCCESSOR_RECOVERY_WINDOW_SECONDS = 120` via `POST /v1/auth/refresh/recover` — a bounded re-delivery of the **already-minted** successor while the family is still `active`. Outside the window / family revoked → v2.3 Case B replay defense. This is **not** a grace window for token reuse.
- **Dependent (audit) failure**: audit insert is inside the rotation transaction (v2.3 §6.4) → its failure rolls back rotation → old token stays `active` → clean retry.
- **Organization-token exchange is a SEPARATE endpoint** `POST /v1/auth/org-token` (ADR-004 §6.8): it presents (does not rotate) the current valid refresh token + `organization_id`, runs the mint procedure, and never mutates the refresh family. Switching active organization = call `org-token` with a new `organization_id`; no refresh rotation required.

---

## 4. Sensitive Session Operations — Authoritative Check (ADR-004 §7)

> [!IMPORTANT]
> **`SENSITIVE_OP_AUTHORITATIVE_VALIDATION`**: list sessions, revoke another session, logout-all, credential changes, recovery-method changes, security-policy changes, IP-allowlist changes, ownership changes, legal-hold placement/release, service-account/API-credential revocation, and registry activation MUST NOT be authorized on edge signature verification alone. Each validates, against committed D1 state:
> 1. presenting session id present; 2. `account_sessions[presenting].principal_id == token.principal_id`; 3. target session ownership (`target.principal_id == presenting principal_id`); 4. `account_sessions[presenting].status == 'active'`; 5. session not expired and absolute cap not exceeded; 6. `principal_security_versions.security_version == token.security_version`; 7. for org-scoped ops: `memberships(org, principal).status == 'active'` and `authorization_versions[org].version == token.authorization_version`; 8. step-up proof present + unconsumed where v2.3 §7 requires it; 9. `authorize(...)` = ALLOW.
>
> Failure codes: `SESSION_CONTEXT_MISSING`, `SESSION_PRINCIPAL_MISMATCH`, `SESSION_OWNERSHIP_VIOLATION`, `SESSION_REVOKED`, `SESSION_EXPIRED`, `SECURITY_VERSION_STALE`, `MEMBERSHIP_INACTIVE`, `AUTHORIZATION_VERSION_STALE`, `STEP_UP_REQUIRED`.

---

## 5. Platform Constants (ADR-004 §8)

`SESSION_ACTIVITY_HEARTBEAT_SECONDS = 300`, `GLOBAL_SESSION_ABSOLUTE_CAP_SECONDS = 2592000`, `REFRESH_TOKEN_GENERATION_TTL = 1209600`, `ACCESS_TOKEN_TTL_SECONDS = 600`, `SUCCESSOR_RECOVERY_WINDOW_SECONDS = 120`, `SESSION_VERSION_KV_STALENESS_SECONDS = 60`. Tenant-controlled values remain exactly the two v2.3 columns and govern only their own organization's authorization session.

---

## 6. Schema & Migration Impact

**Zero table changes.** All rules operate over existing v2.3 columns. Two new application endpoints (`/v1/auth/org-token`, `/v1/auth/refresh/recover`) delivered in Phase 1 Slice 4+ (not implemented by this task). Migrations `0001`–`0005` unaffected and sealed.
