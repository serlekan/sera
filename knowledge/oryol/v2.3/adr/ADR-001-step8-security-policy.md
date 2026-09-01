# ADR-001: Core-Owned Organization Security Policy & Trusted Edge Step 8 Contextual ABAC

**Status**: PROPOSED (Target: Architecture v2.3)  
**Date**: 2026-08-28  
**Author**: Deep Builder (`anthropic/claude-sonnet-5`)  
**Scope**: Oryol Core Authorization Engine, Step 8 Contextual ABAC, Organization Security Policy Model  
**Affected Documents**: `authorization-model.md`, `core-boundaries.md`, `multi-tenancy.md`, `session-security.md`, `audit-and-events.md`

---

## 1. Context

Oryol Architecture v2.2 establishes an executable 8-step authorization algebra `authorize({ principal, membership, organization, action, resource, context })` implemented across Cloudflare Workers and Cloudflare D1.

In frozen Architecture v2.2:
- `authorization-model.md §5 Step 8` requires: *"Apply Contextual ABAC & Fallback: Validate trusted edge context (IP allowlist, device posture). If all checks pass -> ALLOW; otherwise -> DEFAULT_DENY"*.
- `core-boundaries.md §1.2` declares that Oryol Core owns *"Organization-level security policies (MFA enforcement, IP allowlisting, session timeouts)"*.
- `authorization-model.md §6.3` establishes that contextual attributes are partitioned into **Trusted** (server-resolved organization, server-resolved membership, authenticated session ID, authoritative D1 metadata, Cloudflare connecting IP) and **Untrusted** (arbitrary client request headers, client-supplied organization IDs in body, unverified client ACL assertions).

---

## 2. Problem Statement (`ARCHITECTURE_SCHEMA_CONTRADICTION #1`)

During Phase 1 — Slice 3 implementation of the authorization engine, an architecture-to-schema contradiction was discovered:
1. Canonical Phase 1 D1 migrations `0001` through `0004` (totaling 37 tables) contain **zero schema definitions, tables, or columns** for persisting organization-level security policies, IP CIDR allowlists, or device posture requirements.
2. In the absence of a persistence contract in D1, the Step 8 evaluator could only verify that the runtime `context.ipAddress` string was non-empty. This structural check prevented completely absent IP strings, but could not enforce actual tenant IP allowlisting or device posture rules.
3. Attempting to evaluate IP allowlists or device posture policies without a canonical database model created an architectural contradiction between the specification's claims and the database capabilities.

---

## 3. Decision

We establish the canonical principle:
> **CORE OWNS POLICY STATE. EDGE PROVIDES TRUSTED CONTEXT.**

### 3.1 Authoritative Relational Schema for Security Policies

Oryol Core stores authoritative organization security policies and IP allowlist entries in Cloudflare D1 with full multi-tenant compound foreign key isolation.

#### Table 1: `organization_security_policies`
Defines tenant-level security policy configuration. Exactly one row per organization.

```sql
CREATE TABLE organization_security_policies (
    organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    mfa_enforcement TEXT NOT NULL DEFAULT 'optional' CHECK(mfa_enforcement IN ('optional', 'required_all', 'required_admins')),
    ip_allowlist_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(ip_allowlist_mode IN ('disabled', 'enforced_all', 'enforced_admins')),
    allow_internal_dispatch BOOLEAN NOT NULL DEFAULT TRUE,
    device_posture_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(device_posture_mode IN ('disabled', 'compliant_only', 'managed_only')),
    session_idle_timeout_seconds INTEGER NOT NULL DEFAULT 86400 CHECK(session_idle_timeout_seconds >= 300),
    session_absolute_timeout_seconds INTEGER NOT NULL DEFAULT 604800 CHECK(session_absolute_timeout_seconds >= 3600),
    version INTEGER NOT NULL DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by_membership_id TEXT,
    FOREIGN KEY (organization_id, updated_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE SET NULL
);
```

#### Table 2: `organization_ip_allowlist_entries`
Stores individual CIDR blocks for organizations with active IP allowlisting.

```sql
CREATE TABLE organization_ip_allowlist_entries (
    id TEXT PRIMARY KEY,                       -- ipl_<ulid>
    organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    cidr_block TEXT NOT NULL,                  -- Valid IPv4 (e.g. '198.51.100.0/24') or IPv6 (e.g. '2001:db8::/32')
    ip_version INTEGER NOT NULL CHECK(ip_version IN (4, 6)),
    label TEXT NOT NULL,                       -- Human readable identifier (e.g. 'Corporate VPN', 'HQ Office')
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by_membership_id TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (organization_id, created_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE,
    UNIQUE(organization_id, cidr_block),
    UNIQUE(organization_id, id)
);

CREATE INDEX idx_ip_allowlist_org_status ON organization_ip_allowlist_entries(organization_id, status);
```

---

### 3.2 Deterministic CIDR Ingestion & IP Normalization Specification (R-6)

To eliminate subnet ambiguity, representation spoofing, and parsing drift:

1. **Deterministic IPv4 Ingestion**:
   - Representation: Canonical dot-decimal format `a.b.c.d/prefix` where `prefix` $\in [0, 32]$. Single IPs must be formatted as `/32` (e.g. `203.0.113.50/32`).
   - Host Bit Invariant: All host bits outside the subnet mask MUST be zero. Ingestion strictly **rejects** inputs with non-zero host bits (e.g. `192.168.1.135/24` is rejected with `ERR_CIDR_NON_ZERO_HOST_BITS`). Operators must submit the canonical network address `192.168.1.0/24`.
   - `ip_version` must be `4`.
2. **Deterministic IPv6 Ingestion**:
   - Representation: Canonical RFC 5952 lowercase, zero-compressed format `[addr]/prefix` where `prefix` $\in [0, 128]$. Single IPs must be formatted as `/128`.
   - Host Bit Invariant: All host bits outside the subnet mask MUST be zero. Ingestion strictly **rejects** inputs with non-zero host bits.
   - `ip_version` must be `6`.
3. **IPv4-Mapped IPv6 Unwrapping**:
   - Inbound socket addresses formatted as IPv4-mapped IPv6 (e.g. `::ffff:198.51.100.1`) MUST be unwrapped to standard canonical IPv4 (`198.51.100.1`) before matching.
4. **Evaluation Disambiguation**:
   - In Step 8 evaluation, candidate `organization_ip_allowlist_entries` rows are filtered by `ip_version == incoming_ip_version`. Mismatched IP versions are skipped without coercion. Subnet containment is evaluated via exact bitwise masking.

---

### 3.3 Trusted Edge Context Contract

The Cloudflare Edge Worker API Gateway extracts and supplies trusted, server-derived contextual attributes into the `AuthorizationContext`:

```typescript
export interface TrustedDevicePosture {
  state: 'compliant' | 'managed' | 'non_compliant' | 'unknown';
  source: 'cloudflare_zero_trust' | 'managed_client_cert' | 'unverified';
  verifiedAt: string; // ISO-8601 UTC timestamp of edge attestation
}

export interface AuthorizationContext {
  ipAddress: string;                   // Derived from Cloudflare CF-Connecting-IP, or 'internal:worker_runtime'
  clientType: 'web' | 'mobile' | 'api' | 'automation' | 'internal_execution';
  timestamp: string;                   // ISO-8601 UTC from Worker runtime clock
  tokenAuthorizationVersion: number;   // Required: From cryptographically verified JWT / session claim
  mfaVerified?: boolean;               // True if authenticated session satisfies MFA requirements
  devicePosture?: TrustedDevicePosture;
}
```

#### Trust Boundary Invariants:
1. **Client Headers Untrusted**: Request headers such as `X-Forwarded-For`, `Client-IP`, `X-Device-Status`, or client request body properties are **strictly untrusted** and MUST NEVER establish `ipAddress`, `mfaVerified`, or `devicePosture`.
2. **Mandatory Token Authorization Version (F-1)**: `tokenAuthorizationVersion` is **required**. Requests lacking a valid integer `tokenAuthorizationVersion` fail closed immediately with `DENY(AUTHORIZATION_VERSION_STALE)`.
3. **Edge Attestation & Internal Execution Trust Boundary (F-7, P1)**:
   - `ipAddress` is populated exclusively from Cloudflare Worker request metadata (`request.headers.get('CF-Connecting-IP')` or runtime socket properties).
   - `clientType: 'internal_execution'` and `ipAddress: 'internal:worker_runtime'` are constructed **server-side exclusively** by trusted runtime handlers (cron triggers, queue consumers, outbox dispatchers).
   - Client requests can **never** declare themselves `clientType = 'internal_execution'`. Any external HTTP request carrying headers or payload fields asserting `internal_execution` or `internal:worker_runtime` is rejected and can never acquire internal trust.
4. **Posture Attestation & Freshness Invariant (F-2, R-10)**:
   - Posture is valid ONLY when `source IN ('cloudflare_zero_trust', 'managed_client_cert')`. A posture with `source = 'unverified'` is treated as invalid.
   - The edge attestation timestamp `verifiedAt` MUST satisfy: $(t_{\text{current}} - t_{\text{verifiedAt}}) \in [0, 300\text{ seconds}]$ (freshness $\le 5$ minutes, non-negative). If future-dated, expired, missing, or unparseable $\to$ posture is treated as `'unknown'`.

---

### 3.4 Canonical Step 8 Evaluation Algorithm

Step 8 executes deterministically in the following exact linear sequence:

```mermaid
graph TD
    Start[Step 8: Apply Contextual ABAC & Fallback] --> V1[8.1 Validate Token Authorization Version]
    V1 -->|tokenAuthorizationVersion != db.version| DenyAuthVer[DENY: AUTHORIZATION_VERSION_STALE]
    V1 -->|Matches db.version| V2[8.2 Validate Trusted Edge Context Structure]
    
    V2 -->|Invalid format or empty IP| DenyDefault[DENY: DEFAULT_DENY]
    V2 -->|Valid structure| V3[8.3 Load Organization Security Policy from D1]
    
    V3 --> V4{8.4 Human MFA Enforcement Check}
    V4 -->|Human caller & MFA required but context.mfaVerified != true| DenyMFA[DENY: CONTEXT_MFA_REQUIRED]
    V4 -->|Service principal or MFA satisfied or optional| V5{8.5 IP Allowlist Policy}
    
    V5 -->|Internal Execution & allow_internal_dispatch=true| V7[8.7 Decision Finalization]
    V5 -->|Internal Execution & allow_internal_dispatch=false| DenyInternal[DENY: CONTEXT_INTERNAL_DISPATCH_DENIED]
    V5 -->|Disabled| V6{8.6 Device Posture Policy}
    V5 -->|Enforced on Caller| CheckIP[Match normalized IP against active organization_ip_allowlist_entries]
    CheckIP -->|No match or empty allowlist| DenyIP[DENY: CONTEXT_IP_ALLOWLIST_DENIED]
    CheckIP -->|IP Matches active CIDR| V6
    
    V6 -->|Disabled| V7
    V6 -->|Enforced| CheckPosture[Validate devicePosture state, source, and freshness in 0-300s]
    CheckPosture -->|Failed, Non-compliant, Stale, or Unknown| DenyPosture[DENY: CONTEXT_DEVICE_POSTURE_DENIED]
    CheckPosture -->|Compliant / Managed Valid| V7
    
    V7 -->|Steps 1-7 yielded Allow| Allow[ALLOW: ALLOW_ROLE | ALLOW_RESOURCE_GRANT | ALLOW_DELEGATION | ALLOW_CROSS_ORG_GRANT]
    V7 -->|No previous Allow| DenyFallback[DENY: DEFAULT_DENY]
```

1. **Sub-step 8.1 (Token Invalidation — F-1)**:
   - Validate that `context.tokenAuthorizationVersion` is a valid integer. If missing, null, or NaN $\to$ **`DENY(AUTHORIZATION_VERSION_STALE)`**.
   - Query `authorization_versions.version` for `organization_id`.
   - If `context.tokenAuthorizationVersion != db.version` $\to$ **`DENY(AUTHORIZATION_VERSION_STALE)`**.
2. **Sub-step 8.2 (Edge Context Structural Validation — F-7)**:
   - If `context.clientType === 'internal_execution'`: Validate `context.ipAddress === 'internal:worker_runtime'`.
   - Otherwise: Validate that `context.ipAddress` is a non-empty, well-formed IPv4 or IPv6 address string. If invalid, empty, or unparseable $\to$ **`DENY(DEFAULT_DENY)`**.
3. **Sub-step 8.3 (Load Organization Security Policy)**:
   - Query `organization_security_policies` for `organization_id`.
   - If no record exists, apply default policy (`ip_allowlist_mode = 'disabled'`, `device_posture_mode = 'disabled'`, `mfa_enforcement = 'optional'`, `allow_internal_dispatch = true`).
4. **Sub-step 8.4 (MFA Policy Evaluation — F-4, R-4, P0-1)**:
   - MFA enforcement applies **exclusively to Human Principals** (`principal.type === 'human'`). Service principals and internal dispatchers authenticate via cryptographic machine credentials and are not subject to interactive human MFA.
   - If `principal.type === 'human'`:
     - If `policy.mfa_enforcement === 'required_all'`:
       If `context.mfaVerified !== true` $\to$ **`DENY(CONTEXT_MFA_REQUIRED)`**.
     - If `policy.mfa_enforcement === 'required_admins'`:
       If caller holds an immutable system template Admin/Owner role (`role.is_system_template === true AND role.template_key IN ('owner', 'admin')`) AND `context.mfaVerified !== true` $\to$ **`DENY(CONTEXT_MFA_REQUIRED)`**.
5. **Sub-step 8.5 (IP Allowlist Policy Evaluation — F-3, F-7, P1)**:
   - If `context.clientType === 'internal_execution'`:
     - If `policy.allow_internal_dispatch === true` $\to$ IP allowlist and device posture checks are bypassed for internal worker runtime; proceed directly to Sub-step 8.7 (Decision Finalization).
     - If `policy.allow_internal_dispatch === false` $\to$ **`DENY(CONTEXT_INTERNAL_DISPATCH_DENIED)`**. (Internal dispatch sentinels are not IP addresses and are never evaluated against CIDR blocks).
   - If `policy.ip_allowlist_mode != 'disabled'`:
     - Determine applicability:
       - `enforced_all`: Applies to all human and external service requests.
       - `enforced_admins`: Applies if caller holds an immutable system template Admin/Owner role (`role.is_system_template === true AND role.template_key IN ('owner', 'admin')`).
     - If applicable:
       - Query active entries: `SELECT cidr_block, ip_version FROM organization_ip_allowlist_entries WHERE organization_id = ? AND status = 'active'`.
       - If active entries list is empty $\to$ **`DENY(CONTEXT_IP_ALLOWLIST_DENIED)`** (fail-closed).
       - Evaluate `context.ipAddress` against each CIDR block with matching `ip_version` using exact bitwise subnet masking.
       - If no active CIDR matches $\to$ **`DENY(CONTEXT_IP_ALLOWLIST_DENIED)`**.
6. **Sub-step 8.6 (Device Posture Policy Evaluation — F-2, R-5, R-10, A2-1)**:
   - Device posture evaluation applies **exclusively to interactive Human Principals** (`principal.type === 'human'`). Service principals (`principal.type === 'service'`) authenticate via cryptographic machine credentials and do not carry client device posture; posture evaluation is bypassed for service principals.
   - If `context.clientType === 'internal_execution'`: Handled in Sub-step 8.5 (bypassed if `allow_internal_dispatch === true`, denied if `false`).
   - If `principal.type === 'human'` AND `policy.device_posture_mode != 'disabled'`:
     - If `context.devicePosture` is absent, or `source NOT IN ('cloudflare_zero_trust', 'managed_client_cert')`, or $(t_{\text{now}} - t_{\text{verifiedAt}}) \notin [0, 300\text{s}]$ $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
     - If `policy.device_posture_mode = 'compliant_only'` and `context.devicePosture.state != 'compliant'` $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
     - If `policy.device_posture_mode = 'managed_only'` and `context.devicePosture.state NOT IN ('compliant', 'managed')` $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
7. **Sub-step 8.7 (Decision Finalization)**:
   - If all Step 8 checks pass AND preceding Steps 1–7 proved an allow $\to$ **`ALLOW`** with corresponding reason code (`ALLOW_ROLE`, `ALLOW_RESOURCE_GRANT`, `ALLOW_DELEGATION`, `ALLOW_CROSS_ORG_GRANT`).
   - Otherwise $\to$ **`DENY(DEFAULT_DENY)`**.

---

## 4. Policy Mutation Permissions & Version Invalidation

### 4.1 Canonical Permission Definitions (F-5)
The following canonical permission definitions are defined in the active registry version for policy operations:

| Permission Name | Service | Risk Level | Description |
|---|---|---|---|
| `core.security_policy.read` | `core` | `low` | View organization security policies and IP allowlists |
| `core.security_policy.manage` | `core` | `critical` | Update organization MFA, IP allowlist mode, posture mode, and session timeout policies |
| `core.ip_allowlist.read` | `core` | `low` | List IP allowlist CIDR entries |
| `core.ip_allowlist.manage` | `core` | `critical` | Create, update, or remove IP allowlist CIDR entries |

### 4.2 Comprehensive Self-Lockout Prevention Invariant (F-10.e, R-8, P0-1)
When an administrator (holding an active role with `is_system_template = TRUE AND template_key IN ('owner', 'admin')`) modifies `organization_security_policies` or `organization_ip_allowlist_entries`, the policy mutation service MUST verify that the mutating administrator's active session satisfies the proposed policy before committing:
1. **IP Allowlist Self-Lockout**: If enabling IP allowlist (`enforced_all` or `enforced_admins`), the admin's `context.ipAddress` must match at least one active allowlist CIDR entry.
2. **MFA Self-Lockout**: If enabling mandatory MFA (`required_all` or `required_admins`), the admin's current session must have `mfaVerified === true`.
3. **Device Posture Self-Lockout**: If enabling mandatory posture (`compliant_only` or `managed_only`), the admin's current session must possess verified compliant/managed posture.
Any mutation violating self-lockout validation is rejected with `ERR_SECURITY_POLICY_SELF_LOCKOUT`.

### 4.3 Atomic Batch Guarantee & Version Invalidation
Any update to `organization_security_policies` or insert/update/delete on `organization_ip_allowlist_entries` MUST execute inside a single atomic `db.batch()` transaction:
1. Mutate policy / allowlist row.
2. Increment `authorization_versions.version` for the organization.
3. Emit append-only audit event (`core.security_policy.updated`, `core.ip_allowlist.entry_added`, `core.ip_allowlist.entry_updated`, `core.ip_allowlist.entry_removed`).
4. Emit transactional outbox event.
5. Conditional `WHERE EXISTS (...)` ensures strict zero-side-effect no-ops on duplicate or non-existent target mutations.

---

## 5. Alternatives Considered & Rejected

1. **Edge-Only Policy Storage (Cloudflare KV/Zero Trust APIs)**:
   - *Rejected*: Cloudflare KV is eventually consistent and explicitly restricted by `cloudflare-platform.md §2` (*"Never authoritative security or session state"*). Storing tenant policies in KV risks authorization evaluation against stale policy. Core D1 must remain the authoritative relational source of truth.
2. **Arbitrary ABAC Scripting / Policy DSL in Step 8**:
   - *Rejected*: User-defined scripting or regex engines introduce non-deterministic execution, ReDoS vulnerabilities, and high latency budgets. Phase 1 requires deterministic, normalized relational policies.
3. **Storing IP Allowlist as a JSON Array in `organizations` Table**:
   - *Rejected*: JSON columns prevent atomic row-level audit, lack foreign keys for `created_by_membership_id`, and prevent efficient indexing. Normalized `organization_ip_allowlist_entries` is required.

---

## 6. Security Consequences

- **Fail-Closed Guarantee**: Missing posture signals, expired attestations, or empty allowlists when policies are active immediately deny access.
- **Spoofing Resistance**: Untrusted HTTP request headers are completely ignored; only Worker runtime socket context is evaluated.
- **Tenant Isolation**: Universal compound keys `(organization_id, id)` and `(organization_id, cidr_block)` guarantee cross-tenant policy leakage is impossible.
- **Deterministic Auditing**: All policy changes produce structured audit events and invalidate cached authorization versions immediately.

---

## 7. Schema & Migration Impact

- **Migration**: Implemented in Migration `0005_core_security_policies_and_service_rbac.sql`.
- **Accepted Migrations `0001`–`0004`**: Remain **sealed and unmodified**.
- **Backward Compatibility**: Existing organizations default to `ip_allowlist_mode = 'disabled'`, `device_posture_mode = 'disabled'`, and `mfa_enforcement = 'optional'`. No existing tenant access is disrupted upon migration execution.
