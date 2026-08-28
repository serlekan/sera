# ADR-001: Core-Owned Organization Security Policy & Trusted Edge Step 8 Contextual ABAC

**Status**: PROPOSED (Target: Architecture v2.3)  
**Date**: 2026-08-28  
**Author**: Deep Builder (`anthropic/claude-sonnet-5`)  
**Scope**: Oryol Core Authorization Engine, Step 8 Contextual ABAC, Organization Security Policy Model  
**Affected Documents**: `authorization-model.md`, `core-boundaries.md`, `multi-tenancy.md`, `audit-and-events.md`

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

### 3.1 Policy Ownership & Persistence Model

Oryol Core stores authoritative organization security policies and IP allowlist entries in Cloudflare D1 with full multi-tenant compound foreign key isolation.

Two normalized tables are introduced:

#### Table 1: `organization_security_policies`
Defines tenant-level security policy configuration. Exactly one row per organization.

```sql
CREATE TABLE organization_security_policies (
    organization_id TEXT PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    mfa_enforcement TEXT NOT NULL DEFAULT 'optional' CHECK(mfa_enforcement IN ('optional', 'required_all', 'required_admins')),
    ip_allowlist_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(ip_allowlist_mode IN ('disabled', 'enforced_all', 'enforced_admins')),
    device_posture_mode TEXT NOT NULL DEFAULT 'disabled' CHECK(device_posture_mode IN ('disabled', 'compliant_only', 'managed_only')),
    session_idle_timeout_seconds INTEGER NOT NULL DEFAULT 86400 CHECK(session_idle_timeout_seconds >= 300),
    session_absolute_timeout_seconds INTEGER NOT NULL DEFAULT 604800 CHECK(session_absolute_timeout_seconds >= 3600),
    version INTEGER NOT NULL DEFAULT 1,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_by_membership_id TEXT,
    FOREIGN KEY (organization_id, updated_by_membership_id) REFERENCES memberships(organization_id, id)
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
    FOREIGN KEY (organization_id, created_by_membership_id) REFERENCES memberships(organization_id, id),
    UNIQUE(organization_id, cidr_block),
    UNIQUE(organization_id, id)
);

CREATE INDEX idx_ip_allowlist_org_status ON organization_ip_allowlist_entries(organization_id, status);
```

---

### 3.2 Trusted Edge Context Contract

The Cloudflare Edge Worker API Gateway extracts and supplies trusted, server-derived contextual attributes into the `AuthorizationContext`:

```typescript
export interface TrustedDevicePosture {
  state: 'compliant' | 'managed' | 'non_compliant' | 'unknown';
  source: 'cloudflare_zero_trust' | 'managed_client_cert' | 'unverified';
  verifiedAt: string; // ISO-8601 UTC timestamp of edge attestation
}

export interface AuthorizationContext {
  ipAddress: string;                   // Derived from Cloudflare CF-Connecting-IP, never client headers
  clientType?: 'web' | 'mobile' | 'api' | 'automation';
  timestamp: string;                   // ISO-8601 UTC from Worker runtime clock
  tokenAuthorizationVersion?: number;  // From verified JWT claim
  devicePosture?: TrustedDevicePosture;
}
```

#### Trust Boundary Rules:
1. **Client Headers Untrusted**: Request headers such as `X-Forwarded-For`, `Client-IP`, `X-Device-Status`, or client request body properties are **strictly untrusted** and MUST NEVER establish `ipAddress` or `devicePosture`.
2. **Edge Attestation**: `ipAddress` is populated exclusively from Cloudflare Worker request metadata (`request.headers.get('CF-Connecting-IP')` or runtime socket properties).
3. **Posture Attestation**: `devicePosture` is populated exclusively when attested by a cryptographically verified edge integration (e.g., Cloudflare Zero Trust WARP posture headers signed by edge mTLS or verified client certificates).

---

### 3.3 Device Posture Trust Contract & States

- **Supported Posture States**:
  - `'compliant'`: Device satisfies all organization security requirements (e.g. disk encryption active, endpoint protection running, OS updated).
  - `'managed'`: Device is enrolled in organization MDM/Zero Trust fleet, but does not meet all granular compliance checks.
  - `'non_compliant'`: Device failed one or more compliance checks.
  - `'unknown'`: Posture signal is absent, unverified, or cannot be determined.

- **Fail-Closed Signal Requirement**:
  - If `device_posture_mode = 'compliant_only'`: Caller MUST have `devicePosture.state === 'compliant'`. If posture is `'managed'`, `'non_compliant'`, `'unknown'`, or absent $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
  - If `device_posture_mode = 'managed_only'`: Caller MUST have `devicePosture.state IN ('compliant', 'managed')`. If posture is `'non_compliant'`, `'unknown'`, or absent $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
  - If `device_posture_mode = 'disabled'`: Posture is not required for authorization.

---

### 3.4 Canonical Step 8 Evaluation Algorithm

Step 8 executes deterministically in linear sequence:

```mermaid
graph TD
    Start[Step 8: Apply Contextual ABAC & Fallback] --> V1[8.1 Validate Token Authorization Version]
    V1 -->|tokenVersion < dbVersion| DenyAuthVer[DENY: AUTHORIZATION_VERSION_STALE]
    V1 -->|Valid or omitted| V2[8.2 Validate Trusted Edge Context Structure]
    
    V2 -->|ipAddress empty or invalid format| DenyDefault[DENY: DEFAULT_DENY]
    V2 -->|Valid IP| V3[8.3 Load Organization Security Policy from D1]
    
    V3 --> V4{8.4 IP Allowlist Enforced?}
    V4 -->|Disabled| V5{8.5 Device Posture Enforced?}
    V4 -->|Enforced| CheckIP[Match IP against active organization_ip_allowlist_entries]
    CheckIP -->|No match or empty allowlist| DenyIP[DENY: CONTEXT_IP_ALLOWLIST_DENIED]
    CheckIP -->|IP Matches CIDR| V5
    
    V5 -->|Disabled| V6[8.6 Decision Finalization]
    V5 -->|Enforced| CheckPosture[Validate context.devicePosture against policy]
    CheckPosture -->|Failed, Unknown, or Absent| DenyPosture[DENY: CONTEXT_DEVICE_POSTURE_DENIED]
    CheckPosture -->|Compliant/Managed| V6
    
    V6 -->|Steps 1-7 yielded Allow| Allow[ALLOW: ALLOW_ROLE | ALLOW_RESOURCE_GRANT | ALLOW_DELEGATION | ALLOW_CROSS_ORG_GRANT]
    V6 -->|No previous Allow| DenyFallback[DENY: DEFAULT_DENY]
```

1. **Sub-step 8.1 (Token Invalidation)**:
   If `context.tokenAuthorizationVersion` is provided:
   - Read authoritative `authorization_versions.version` for `organization_id`.
   - If `context.tokenAuthorizationVersion < db.version` (or is non-integer/NaN) $\to$ **`DENY(AUTHORIZATION_VERSION_STALE)`**.
2. **Sub-step 8.2 (Edge Context Structural Validation)**:
   - Validate that `context.ipAddress` is a non-empty, well-formed IPv4 or IPv6 address.
   - If invalid or empty $\to$ **`DENY(DEFAULT_DENY)`**.
3. **Sub-step 8.3 (Load Organization Security Policy)**:
   - Query `organization_security_policies` for `organization_id`.
   - If no record exists, apply default policy (`ip_allowlist_mode = 'disabled'`, `device_posture_mode = 'disabled'`).
4. **Sub-step 8.4 (IP Allowlist Policy Evaluation)**:
   - If `policy.ip_allowlist_mode != 'disabled'`:
     - Determine applicability:
       - `enforced_all`: Applies to all requests.
       - `enforced_admins`: Applies if caller holds a system template Admin/Owner role (`is_system_template = 1 AND name IN ('Owner', 'Admin')`).
     - If applicable:
       - Query active entries: `SELECT cidr_block, ip_version FROM organization_ip_allowlist_entries WHERE organization_id = ? AND status = 'active'`.
       - If active entries list is empty $\to$ **`DENY(CONTEXT_IP_ALLOWLIST_DENIED)`** (fail-closed: policy requires allowlist but no active ranges configured).
       - Evaluate `context.ipAddress` against each CIDR block using exact bitwise subnet matching (supporting IPv4 `/0`–`/32` and IPv6 `/0`–`/128`).
       - If no active CIDR matches $\to$ **`DENY(CONTEXT_IP_ALLOWLIST_DENIED)`**.
5. **Sub-step 8.5 (Device Posture Policy Evaluation)**:
   - If `policy.device_posture_mode != 'disabled'`:
     - If `context.devicePosture` is absent, unverified, or posture state is `'unknown'` or `'non_compliant'` $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
     - If `policy.device_posture_mode = 'compliant_only'` and `context.devicePosture.state != 'compliant'` $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
     - If `policy.device_posture_mode = 'managed_only'` and `context.devicePosture.state NOT IN ('compliant', 'managed')` $\to$ **`DENY(CONTEXT_DEVICE_POSTURE_DENIED)`**.
6. **Sub-step 8.6 (Decision Finalization)**:
   - If all Step 8 checks pass AND preceding Steps 1–7 proved an allow $\to$ **`ALLOW`** with corresponding reason code (`ALLOW_ROLE`, `ALLOW_RESOURCE_GRANT`, `ALLOW_DELEGATION`, `ALLOW_CROSS_ORG_GRANT`).
   - Otherwise $\to$ **`DENY(DEFAULT_DENY)`**.

---

## 4. Policy Mutation & Version Invalidation

To maintain zero-trust freshness across edge caches and sessions, all policy mutations must be atomic:

1. **Monotonic Version Invalidation**:
   Any of the following operations MUST increment `authorization_versions.version`:
   - Updating `organization_security_policies` (MFA, IP allowlist mode, device posture mode, timeouts).
   - Adding, modifying, or removing an `organization_ip_allowlist_entries` record.
2. **Atomic Batch Guarantee**:
   The policy mutation, version bump, append-only audit event (`core.security_policy.updated`, `core.ip_allowlist.created`, `core.ip_allowlist.deleted`), and transactional outbox event MUST execute inside a single atomic D1 transaction (`db.batch()`).
3. **No-Op Atomicity**:
   If an IP allowlist entry deletion targets a non-existent ID, the conditional SQL pattern (`WHERE EXISTS (SELECT 1 FROM organization_ip_allowlist_entries WHERE organization_id = ? AND id = ?)`) ensures 0 version bumps, 0 audit logs, and 0 outbox events are emitted.

---

## 5. Alternatives Considered

1. **Edge-Only Policy Storage (Cloudflare KV/Zero Trust APIs)**:
   - *Rejected*: Cloudflare KV is eventually consistent and explicitly restricted by `cloudflare-platform.md §2` (*"Never authoritative security or session state"*). Storing tenant policies in KV risks authorization evaluation against stale policy. Core D1 must remain the authoritative relational source of truth.
2. **Arbitrary ABAC Scripting / Policy DSL in Step 8**:
   - *Rejected*: User-defined scripting or regex engines introduce non-deterministic execution, ReDoS vulnerabilities, and high latency budgets. Phase 1 requires deterministic, normalized relational policies.
3. **Storing IP Allowlist as a JSON Array in `organizations` Table**:
   - *Rejected*: JSON columns prevent atomic row-level audit, lack foreign keys for `created_by_membership_id`, and prevent efficient indexing. Normalized `organization_ip_allowlist_entries` is required.

---

## 6. Security Consequences

- **Fail-Closed Guarantee**: Missing posture signals or empty allowlists when policies are active immediately deny access.
- **Spoofing Resistance**: Untrusted HTTP request headers are completely ignored; only Worker runtime socket context is evaluated.
- **Tenant Isolation**: Universal compound keys `(organization_id, id)` and `(organization_id, cidr_block)` guarantee cross-tenant policy leakage is impossible.
- **Deterministic Auditing**: All policy changes produce structured audit events and invalidate cached authorization versions immediately.

---

## 7. Schema & Migration Impact

- **Migration**: Implemented in Migration `0005_core_security_policies_and_service_rbac.sql`.
- **Accepted Migrations `0001`–`0004`**: Remain **sealed and unmodified**.
- **Backward Compatibility**: Existing organizations default to `ip_allowlist_mode = 'disabled'`, `device_posture_mode = 'disabled'`, and `mfa_enforcement = 'optional'`. No existing tenant access is disrupted upon migration execution.
