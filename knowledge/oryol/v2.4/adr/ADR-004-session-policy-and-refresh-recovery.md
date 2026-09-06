# ADR-004: Session Policy Enforcement & Refresh Recovery

**Status**: PROPOSED (Target: Architecture v2.4)
**Date**: 2026-09-06
**Author**: Deep Builder (`anthropic/claude-sonnet-5`)
**Scope**: Oryol Core Session Security, Token Issuance, Refresh Rotation, Organization Security Policy Session Semantics
**Affected Documents**: `session-security.md`, `authorization-model.md` (unchanged; referenced), `audit-and-events.md`
**Predecessor Baseline**: Oryol Architecture v2.3 (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Originating Finding**: Independent principal review (`docs/reviews/ASTRA-PRINCIPAL-REVIEW-2026-09-06.md`) — validated ambiguity in `session-security.md §8` and refresh delivery-failure handling.

---

## 1. Context

v2.3 `session-security.md §8` ("Organization Security Policy Dynamic Enforcement") and ADR-001 §3.1 introduced four policy columns on `organization_security_policies`:

```
session_idle_timeout_seconds     INTEGER NOT NULL DEFAULT 86400  CHECK(>= 300)
session_absolute_timeout_seconds INTEGER NOT NULL DEFAULT 604800 CHECK(>= 3600)
```

v2.3 states timeouts "directly govern token TTL", references `datetime('now') - account_sessions.last_active_at` for idle, and resolves multi-org via "strictest tenant enforcement" and "the minimum of +30 days and the most restrictive active organization `session_absolute_timeout_seconds`". The principal review found this **under-specified and operationally hazardous**:

- No rule for *when* `last_active_at` is written → unbounded write amplification, or dead policy.
- Absolute timeout anchor unspecified (creation vs. first-auth vs. last rotation) → refresh could extend a session forever.
- "Strictest policy" lets one tenant silently shorten another tenant's usable identity session (cross-tenant coupling / denial vector).
- Refresh rotation + optional org-token issuance is a single coupled step: if org-token issuance fails *after* the refresh row is consumed, the client is left with a dead successor it never received, and replay defense (correctly) refuses to re-serve it.

This ADR makes all of it executable and decouples the failure modes.

---

## 2. Decision — Session Model: Three Distinct Layers

v2.4 names three layers that v2.3 conflated. **No v2.3 table changes**; this is a semantic contract over existing entities.

| Layer | Backing entity | Lifetime governed by | Purpose |
|---|---|---|---|
| **Global identity session** | `account_sessions` + `refresh_token_families` (the refresh family) | `account_sessions.created_at` + **global absolute cap** (§4) | "This human authenticated on this device." One per device login. |
| **Organization authorization session** | An issued `token_type = 'org_access'` JWT (§5) + the per-request idle check | Target organization's `session_idle_timeout_seconds` / `session_absolute_timeout_seconds` (§3, §4) | "This principal is currently acting inside org X with these perms." Ephemeral, re-mintable. |
| **Refresh family** | `refresh_token_families` / `refresh_tokens` generational chain | Global absolute cap (§4); rotation state machine (v2.3 §2, unchanged) | Long-lived credential that mints org-authorization sessions. Exactly one live generation. |

> [!IMPORTANT]
> **Decoupling Invariant (`SESSION_LAYER_SEPARATION`)**:
> An organization's tightened timeout policy shortens **only** the organization-authorization session for *that* organization. It MUST NOT shorten the global identity session, the refresh family lifetime, or any other organization's authorization session. The only cross-org effect permitted is the **global absolute cap** of §4, which is a fixed platform constant, not a tenant-controlled value.

---

## 3. Idle Timeout — Authoritative Rule

### 3.1 Timestamps

- **Idle reference**: `account_sessions.last_active_at` (existing v2.3 column).
- **Idle test** (evaluated when validating or minting an `org_access` token for organization `O`):

```
idle_seconds := strftime('%s','now') - strftime('%s', account_sessions.last_active_at)
IF idle_seconds > policy(O).session_idle_timeout_seconds:
    DENY(SESSION_IDLE_TIMEOUT)   # requires refresh (§5) or re-authentication
```

`policy(O)` is `organization_security_policies` for `O`, or the ADR-001 §3.4 defaults if absent (`session_idle_timeout_seconds = 86400`).

### 3.2 Authoritative Activity-Update Rule (prevents write amplification)

> [!IMPORTANT]
> **Activity Update Rule (`SESSION_ACTIVITY_HEARTBEAT`)**:
> `account_sessions.last_active_at` is updated by a **rate-limited heartbeat**, never on every authorized request.
>
> On an authorized request, the edge worker updates `last_active_at` **iff** `strftime('%s','now') - strftime('%s', last_active_at) >= SESSION_ACTIVITY_HEARTBEAT_SECONDS` where `SESSION_ACTIVITY_HEARTBEAT_SECONDS = 300` (fixed platform constant, 5 minutes). The update is a single conditional statement:
>
> ```sql
> UPDATE account_sessions
> SET last_active_at = CURRENT_TIMESTAMP
> WHERE id = :session_id
>   AND status = 'active'
>   AND (strftime('%s','now') - strftime('%s', last_active_at)) >= 300;
> ```
>
> Refresh (§5) always updates `last_active_at` unconditionally (a refresh is definitionally activity). High-risk operations (§7) always update it.

Write amplification is bounded to ≤ 1 write / session / 5 minutes regardless of request volume. `SESSION_ACTIVITY_HEARTBEAT_SECONDS` (300) is strictly less than the minimum allowed `session_idle_timeout_seconds` (300) — equal at the floor; a policy at the 300 s floor is enforced at ≤ 300 s granularity, which is acceptable and documented. For any policy above the floor the heartbeat is strictly finer-grained than the timeout.

### 3.3 Idle enforcement points

Idle is enforced at: (a) `org_access` token **minting** (refresh exchange, §5), and (b) authoritative validation at **high-risk endpoints** (§7) and at **normal endpoints** only on the ≤ 10-minute TTL boundary (v2.3 §7 "Dual Verification Revocation SLA", unchanged). Normal edge-verified requests within the access-token TTL do **not** hit D1 for the idle check; the short TTL bounds staleness (v2.3 semantics).

---

## 4. Absolute Timeout — Authoritative Rule

### 4.1 Anchor

> [!IMPORTANT]
> **Absolute Anchor Invariant (`SESSION_ABSOLUTE_ANCHOR`)**:
> The absolute lifetime of a global identity session and its refresh family is measured **exclusively from `account_sessions.created_at`** — an immutable timestamp written once at interactive authentication and never updated. **No refresh, rotation, activity heartbeat, or policy change may move this anchor.**

```
absolute_age_seconds := strftime('%s','now') - strftime('%s', account_sessions.created_at)
```

### 4.2 Two ceilings

1. **Global absolute cap (platform constant)**: `GLOBAL_SESSION_ABSOLUTE_CAP_SECONDS = 2592000` (30 days). If `absolute_age_seconds > GLOBAL_SESSION_ABSOLUTE_CAP_SECONDS` → the family is expired, `account_sessions.status -> 'expired'`, full re-authentication required. This replaces the v2.3 phrase "minimum of +30 days and …" — the `+30 days` half is now this fixed constant, tenant-independent.

2. **Per-organization absolute ceiling (tenant policy)**: When minting or validating an `org_access` token for organization `O`:

```
IF absolute_age_seconds > policy(O).session_absolute_timeout_seconds:
    DENY(SESSION_ABSOLUTE_TIMEOUT)   # for organization O only; re-authentication required to act in O
```

This denies **acting in `O`**. It does **not** revoke the refresh family, expire `account_sessions`, or affect other organizations. The principal can still refresh and act in organization `O'` whose policy is more permissive, using the same global identity session, until the global cap.

### 4.3 No refresh extension past a ceiling

`refresh_tokens.expires_at` for every generation is set to `min(issued_at + REFRESH_TOKEN_GENERATION_TTL, account_sessions.created_at + GLOBAL_SESSION_ABSOLUTE_CAP_SECONDS)` where `REFRESH_TOKEN_GENERATION_TTL = 1209600` (14 days). Because the second term is anchored to the immutable `created_at`, successive rotations produce a **non-increasing** tail bound and can never push the family past the global cap. Per-organization absolute ceilings are enforced at `org_access` mint time (§4.2), not on the refresh token itself, so a tight org policy never shortens the shared refresh credential.

---

## 5. Organization Token Issuance & Validation

### 5.1 Issuance (org_access mint)

`org_access` tokens are minted **only** by:
1. Interactive authentication completing (first org selection), or
2. The **organization-token exchange** (§6.8) — a step that consumes a *valid, already-rotated* refresh token's session context.

Mint procedure for organization `O`, session `S`, principal `P` (authoritative D1 reads, v2.3 §3 revalidation retained verbatim):

```
1. account_sessions[S].status = 'active'                       else DENY(SESSION_INVALID)
2. principals[P].status = 'active'                             else DENY(PRINCIPAL_INACTIVE)
3. memberships(O,P).status = 'active'                          else DENY(MEMBERSHIP_INACTIVE)
4. organizations[O].status = 'active'                          else DENY(ORGANIZATION_INACTIVE)
5. Idle check (§3.1) against policy(O)                          else DENY(SESSION_IDLE_TIMEOUT)
6. Absolute check (§4.2) against policy(O)                      else DENY(SESSION_ABSOLUTE_TIMEOUT)
7. Resolve authorization_versions[O].version -> claim
8. Resolve perms at active registry (v2.3 Step 6 semantics)
9. Sign JWT: token_type='org_access', exp = now + ACCESS_TOKEN_TTL_SECONDS
```

`ACCESS_TOKEN_TTL_SECONDS = 600` (10 minutes) — bounded by, and never exceeding, `min(policy(O).session_idle_timeout_seconds, policy(O).session_absolute_timeout_seconds - absolute_age_seconds, 600)`. If that minimum is ≤ 0 the mint fails with the corresponding timeout denial rather than issuing a zero/negative-life token.

### 5.2 Validation

- **Edge (fast path)**: verify Ed25519 signature + `exp` + `kid` (v2.3 §5). `authorization_version` claim compared to a KV-cached org version with ≤ 60 s staleness; mismatch → forward to authoritative path.
- **Authoritative path (high-risk, §7; and on TTL expiry)**: full §5.1 re-check including idle/absolute.

### 5.3 Multi-organization principal

A principal in orgs `{A, B, C}` holds **one** global identity session and **one** refresh family, and up to **three** independent `org_access` tokens. Tightening `policy(B)` affects only B-scoped tokens and B mint attempts. There is **no** "strictest across all memberships" reduction of the refresh family or the identity session. This deletes the v2.3 cross-tenant coupling.

### 5.4 Membership added / removed

| Event | Effect |
|---|---|
| Membership **added** (`O_new`) | No change to existing tokens. Principal may now exchange (§6.8) for an `O_new` `org_access` token. `authorization_versions[O_new]` unaffected. |
| Membership **removed / suspended** (`O_x`) | The mutation increments `authorization_versions[O_x]` (v2.3 `audit-and-events.md §6.4`). Existing `O_x` `org_access` tokens fail the version check within ≤ 60 s at edge, immediately at high-risk endpoints, and cannot be re-minted (§5.1 step 3). Other orgs' tokens and the refresh family are untouched. |
| **Last** membership removed | Refresh family remains valid (global identity session still valid); the principal simply has no org to exchange into until re-invited. The family still expires at the global cap. |

---

## 6. Refresh Rotation & Delivery Failure

### 6.1 Principle

> [!IMPORTANT]
> **Rotation ≠ exchange ≠ delivery (`REFRESH_EVENT_SEPARATION`)**:
> Three distinct events, never conflated:
> 1. **Credential rotation success** — the old `refresh_tokens` row is `consumed`, the successor row is `active` and committed in D1.
> 2. **Organization-token exchange success** — a new `org_access` JWT was successfully minted for a requested org.
> 3. **HTTP response delivery success** — the successor refresh cookie + `org_access` token reached the client.
> Replay defense keys on event (1). Recovery keys on the client not having observed event (3).

### 6.2 Replay defense stays strict

No permissive replay grace window is introduced. v2.3 §2 Case B (Account-Level Replay Defense on reuse of a consumed/revoked token with valid historical identity) is retained **verbatim**. A client that lost the response and re-presents the **old** (now `consumed`) token still triggers full replay defense. Recovery does **not** relax this — it prevents the client from ever needing to re-present the old token (§6.6).

### 6.3 Predictable failures occur BEFORE consuming the refresh credential

The refresh endpoint performs, **before** the CAS consumption in v2.3 §2 Step 1, a pre-validation of any requested organization exchange:

```
PRE-ROTATION VALIDATION (no refresh row mutated yet):
  IF request includes organization_id X:
    - X is a well-formed org id            else 400 REFRESH_ORG_IDENTIFIER_INVALID   (no consume)
    - organizations[X] exists               else 404 REFRESH_ORG_NOT_FOUND            (no consume)
    - memberships(X, P).status = 'active'   else 403 REFRESH_ORG_MEMBERSHIP_MISSING   (no consume)
    - organizations[X].status = 'active'    else 403 REFRESH_ORG_SUSPENDED            (no consume)
```

If pre-rotation validation fails, the refresh token is **not** consumed, the client keeps its still-valid current credential, and may retry with a corrected request. These are exactly the failure classes that are knowable without side effects.

### 6.4 Rotation

Only after §6.3 passes (or the request carries no org exchange) does v2.3 §2 CAS rotation execute: consume old row, insert successor, advance family generation, in the same atomic transaction. `affected_rows` invariants unchanged.

### 6.5 Failures that can only occur AFTER successful rotation

| Post-rotation failure | Behavior |
|---|---|
| JWT signing failure (KMS/JWKS unavailable) for the org_access token | Rotation is **already committed and durable**. The endpoint returns `200` with the **successor refresh cookie set** and an `org_access_token: null` + `org_exchange_status: "deferred"` body. The client holds a valid successor and MUST call the exchange endpoint (§6.8) to obtain the org token. No rollback of rotation (rolling back a committed rotation would orphan the successor the client may already have). |
| Response transport loss (client never receives the 200) | Client still holds the **old** token. On next use it presents the old token → replay defense fires (§6.2). To avoid this, the successor is also **recoverable** (§6.6): the client's next request presenting the old token within `SUCCESSOR_RECOVERY_WINDOW_SECONDS` receives, instead of an immediate breach, a `409 REFRESH_SUCCESSOR_PENDING` **only if** the family is still `active` and exactly one unconsumed successor exists — directing the client to the recovery endpoint. If the family is already `revoked` (real reuse detected elsewhere) → breach response, no recovery. |
| Dependent service failure (e.g. audit insert fails) | Audit insert is **inside** the rotation transaction (v2.3 §6.4). Its failure rolls back the rotation entirely → old token remains `active`, client retries cleanly. This is the one case where rotation is *not* yet committed, and it is safe precisely because nothing was consumed. |

### 6.6 Successor recovery

- `SUCCESSOR_RECOVERY_WINDOW_SECONDS = 120`.
- New endpoint `POST /v1/auth/refresh/recover` (cookie-authenticated by the **old** token secret): if `refresh_token_families[fam].status = 'active'` AND the presented token is generation `g` with `status = 'consumed'` AND `successor_token_id` points to a generation `g+1` row with `status = 'active'` AND `now - consumed_at <= 120s` → re-serve the successor cookie (idempotent; does not rotate again, does not consume anything new) and record a low-severity audit event `core.session.successor_recovered`.
- Outside the window, or family revoked, or successor already consumed → `401` + replay defense per v2.3 Case B. Recovery is a **narrow, bounded, non-replay** re-delivery of an already-minted successor, not a grace period for arbitrary token reuse.

### 6.7 Idempotency of retried business mutations

Refresh/exchange endpoints are idempotent by the family generation counter, not by random ids. A retried refresh that finds its requested rotation already applied (successor exists, old consumed, within recovery window) returns the successor via the recovery path rather than rotating again. See ADR-006 §Idempotency for the general rule that idempotency binds to the authoritative command, not `new-random-id + now`.

### 6.8 Organization-token exchange as a SEPARATE step

> [!IMPORTANT]
> **Adopted**: Organization-token issuance is a **separate endpoint** `POST /v1/auth/org-token`, distinct from `POST /v1/auth/refresh`.

Rationale (the review asked this be evaluated seriously; it is adopted):
- Removes org-token issuance from the refresh transaction → refresh rotation has one job and one failure mode.
- `refresh` rotates the credential and (optionally, best-effort) returns an initial `org_access` token; if that best-effort mint fails, `org_exchange_status: "deferred"` and the client calls `org-token`.
- `org-token` takes the **current valid refresh token** (not consumed — it is *presented and validated*, not rotated) OR an existing valid `org_access` token for a different org, plus `organization_id`, and runs §5.1. It never mutates the refresh family. It can be retried freely and is safe under concurrency (it only reads session state and signs).
- Switching active organization = call `org-token` with a new `organization_id`. No refresh rotation needed to change orgs.

`org-token` failure classes are all pre-checkable (§6.3 list) plus signing failure (`503`, retryable). None consume or rotate anything.

---

## 7. Sensitive Session Operations — Complete Authoritative Check

The following operations MUST NOT be authorized on edge signature verification alone. Each performs the full authoritative check below against committed D1 state, in addition to the normal `authorize(...)` call:

**Operations**: list sessions; revoke another session; logout-all; credential add/remove/change; recovery-method change; security-policy read/manage (`core.security_policy.*`, `core.ip_allowlist.*`); IP allowlist mutation; organization ownership changes (ADR-003 §3.7); legal-hold placement/release; service-account/API-credential revocation; registry activation.

**Authoritative check (`SENSITIVE_OP_AUTHORITATIVE_VALIDATION`)** — all must pass:

| # | Check | Failure |
|---|---|---|
| 1 | A presenting session id is present in the verified token/cookie | `SESSION_CONTEXT_MISSING` |
| 2 | `account_sessions[presenting].principal_id == token.principal_id` | `SESSION_PRINCIPAL_MISMATCH` |
| 3 | For "revoke another session" / "list": the **target** session's `principal_id == presenting principal_id` (a principal may only enumerate/revoke their own sessions; cross-principal session admin is not a Phase 1 capability) | `SESSION_OWNERSHIP_VIOLATION` |
| 4 | `account_sessions[presenting].status == 'active'` | `SESSION_REVOKED` |
| 5 | `strftime('%s','now') < strftime('%s', account_sessions[presenting].expires_at)` AND absolute cap not exceeded (§4.2) | `SESSION_EXPIRED` |
| 6 | `principal_security_versions[principal].security_version == token.security_version` | `SECURITY_VERSION_STALE` |
| 7 | For organization-scoped operations: `memberships(org, principal).status == 'active'` AND `authorization_versions[org].version == token.authorization_version` | `MEMBERSHIP_INACTIVE` / `AUTHORIZATION_VERSION_STALE` |
| 8 | Step-up proof present and unconsumed for the operation risk class where v2.3 §7 requires it (`org_ownership_transfer`, `mailbox_purge`, `dkim_rotate`, credential change, security policy change) | `STEP_UP_REQUIRED` |
| 9 | The `authorize(...)` decision for the specific action is `ALLOW` | per authorization engine |

Checks 1–7 map directly to v2.3 entities (`account_sessions`, `principals`, `principal_security_versions`, `memberships`, `authorization_versions`) — no schema change. This makes concrete the v2.3 §7 statement that high-risk endpoints "perform an authoritative current session, membership, and security-version check in D1".

---

## 8. Constants (platform-fixed, not tenant-controlled)

| Constant | Value | Meaning |
|---|---|---|
| `SESSION_ACTIVITY_HEARTBEAT_SECONDS` | 300 | Min interval between `last_active_at` writes |
| `GLOBAL_SESSION_ABSOLUTE_CAP_SECONDS` | 2592000 (30 d) | Hard cap on any identity session / refresh family, anchored to `created_at` |
| `REFRESH_TOKEN_GENERATION_TTL` | 1209600 (14 d) | Per-generation refresh token life, clamped to the global cap |
| `ACCESS_TOKEN_TTL_SECONDS` | 600 (10 min) | `org_access` JWT life, further clamped by policy(O) |
| `SUCCESSOR_RECOVERY_WINDOW_SECONDS` | 120 | Window for `/refresh/recover` re-delivery of an already-minted successor |
| `SESSION_VERSION_KV_STALENESS_SECONDS` | 60 | Max edge cache staleness for `authorization_version` |

Tenant-controlled values remain exactly the two v2.3 columns (`session_idle_timeout_seconds`, `session_absolute_timeout_seconds`), and they now govern **only** the organization-authorization session for their own organization.

---

## 9. Canonical Errors (additive)

`SESSION_IDLE_TIMEOUT`, `SESSION_ABSOLUTE_TIMEOUT`, `SESSION_INVALID`, `SESSION_CONTEXT_MISSING`, `SESSION_PRINCIPAL_MISMATCH`, `SESSION_OWNERSHIP_VIOLATION`, `SESSION_REVOKED`, `SESSION_EXPIRED`, `SECURITY_VERSION_STALE`, `STEP_UP_REQUIRED`, `REFRESH_ORG_IDENTIFIER_INVALID`, `REFRESH_ORG_NOT_FOUND`, `REFRESH_ORG_MEMBERSHIP_MISSING`, `REFRESH_ORG_SUSPENDED`, `REFRESH_SUCCESSOR_PENDING`, `org_exchange_status: "deferred"` (body field, not an error).

No v2.3 code renamed or removed. v2.3 `UNKNOWN_KEY_IDENTIFIER`, replay-defense behavior, `__Host-Oryol-Refresh` cookie contract all retained verbatim.

---

## 10. Schema & Migration Impact

- **Zero table changes.** All rules operate over existing v2.3 columns (`account_sessions.created_at`, `.last_active_at`, `.expires_at`, `.status`; `refresh_tokens.expires_at`, `.status`, `.successor_token_id`, `.consumed_at`; `principal_security_versions`; `organization_security_policies.session_*`).
- Two new endpoints (`/v1/auth/org-token`, `/v1/auth/refresh/recover`) — application surface, delivered in Phase 1 Slice 4+ (**not implemented by this task**).
- Migrations `0001`–`0005` unaffected and remain sealed.

---

## 11. Alternatives Considered & Rejected

1. **"Strictest policy across all memberships" (v2.3 literal reading)**: *Rejected* — lets a low-trust tenant DoS a user's session everywhere; couples unrelated tenants. Replaced by per-org authorization sessions + a fixed global cap.
2. **Update `last_active_at` on every request**: *Rejected* — write amplification on D1; a hot session could issue thousands of writes/minute. Rate-limited heartbeat bounds it.
3. **Anchor absolute timeout to `last_rotated_at`**: *Rejected* — refresh would extend the session indefinitely, defeating the absolute bound. Anchored to immutable `created_at`.
4. **Roll back a committed rotation when org-token signing fails**: *Rejected* — the client may already hold the successor; rolling back orphans it and forces replay defense. Deferred exchange + bounded recovery is safe.
5. **Permissive replay grace window**: *Rejected* explicitly — weakens the core security property. Narrow successor **recovery** (re-deliver an already-minted successor, family still active, 120 s) is not a grace window for reuse.
6. **Keep org-token issuance inside `/refresh`**: *Rejected* — transactional coupling is the root cause of the delivery-failure ambiguity. Separated per the review's suggestion.

---

## 12. Decisions / Invariants / Open Questions

### Decisions
- D1: Three session layers named and separated (§2); tenant policy governs only the org-authorization session for that org.
- D2: Idle = `now - last_active_at` vs `policy(O).session_idle_timeout_seconds`; `last_active_at` updated by a 300 s rate-limited heartbeat + on every refresh/high-risk op (§3).
- D3: Absolute timeout anchored to immutable `account_sessions.created_at`; global 30 d platform cap + per-org ceiling enforced at mint time; no refresh extends past either (§4).
- D4: Rotation, org-token exchange, and response delivery are three separate events; predictable failures happen before consuming the refresh row; org-token exchange is a separate endpoint (§6).
- D5: Successor recovery endpoint re-delivers an already-minted successor within 120 s without relaxing replay defense (§6.6).
- D6: Nine-point authoritative check for sensitive session operations (§7).

### Invariants
- I1 `SESSION_LAYER_SEPARATION` — a tenant's tighter timeout never shortens another tenant's or the global session (§2).
- I2 `SESSION_ACTIVITY_HEARTBEAT` — ≤ 1 `last_active_at` write / session / 300 s (§3.2).
- I3 `SESSION_ABSOLUTE_ANCHOR` — absolute age measured only from `created_at`; immutable (§4.1).
- I4 Refresh generation `expires_at` is non-increasing toward the global cap; no rotation crosses it (§4.3).
- I5 `REFRESH_EVENT_SEPARATION` — replay defense keys on rotation-commit; recovery keys on non-delivery; never conflated (§6.1).
- I6 Replay defense unchanged from v2.3; no permissive grace window (§6.2).
- I7 `org-token` / `/refresh/recover` never rotate or consume the refresh family (§6.6, §6.8).
- I8 `SENSITIVE_OP_AUTHORITATIVE_VALIDATION` — all 9 checks pass before any high-risk identity operation (§7).

### Open Questions
- OQ1: Whether `SESSION_ACTIVITY_HEARTBEAT_SECONDS` should be lowered below the `session_idle_timeout_seconds` floor (300) so a floor-value policy gets sub-timeout granularity — deferred; the floor case is documented as coarse-but-acceptable.
- OQ2: Cross-device "logout-all" latency vs. edge cache staleness (`SESSION_VERSION_KV_STALENESS_SECONDS`) — Phase 1 accepts ≤ 60 s; a Durable Object push channel is a Phase 2 option.
- OQ3: Whether `org-token` should accept device-posture re-attestation inline for `device_posture_mode` orgs, or require a fresh interactive auth — Phase 1: posture is re-evaluated at `authorize()` Step 8.6 per request; `org-token` mint does not itself re-attest.
