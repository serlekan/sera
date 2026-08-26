# Oryol Core — System Boundaries & Service Contracts v2.1

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Domain Verification Ownership, AI Context-Provider Inversion & Search Post-Filtering

---

## 1. Responsibilities of Oryol Core

Oryol Core provides the single pane of glass for all cross-cutting infrastructure, identity, and governance concerns.

### 1.1 Identity & Principal Registry
- Global Principal management (`prn_...`) representing human users (`type='human'`) and service principals (`type='service'`).
- WebAuthn Passkeys, Magic Links, MFA verification, and Enterprise IdP mappings.
- Authoritative session store with cryptographic token rotation and instant revocation.

### 1.2 Multi-Tenant Hierarchy & Organization Governance
- Organization (`org_...`) lifecycle management: `active`, `suspended`, `archived`, `deletion_pending`.
- Organization Memberships (`mem_...`), Invitations (`inv_...`), and Team structures (`team_...`).
- Global and custom Role definitions (`rol_...`) and permission bindings.
- Organization-level security policies (MFA enforcement, IP allowlisting, session timeouts).
- Generic Organization Domain Claims (`dom_...`) for cross-application routing.

### 1.3 Authorization Engine
- Uniform evaluation contract: `authorize({ principal, membership, organization, action, resource, context })`.
- Standard 3-part permission registry (`core.*`, `mail.*`, `crm.*`, `calendar.*`, `drive.*`, `finance.*`).
- Deny-precedence rule resolution and scope validation.

### 1.4 Central Platform Pipelines
- **Audit Logging**: Immutable, append-only security logs.
- **Domain Outbox Bus**: Outbox-backed event dispatching for cross-application sync.
- **AI Gateway**: Model provider routing, PII sanitization, and provider-retention compliance.
- **Search Contracts**: Indexing protocols and permission-aware search query contracts.
- **Application Entitlements**: Managing licensed application modules per organization.

---

## 2. Boundary Clarifications & Ownership Contracts

### 2.1 Domain Verification Ownership
- **Oryol Core Owns**: Generic organization custom domain registration and high-level domain ownership claims (`dom_...`).
- **OryolMail Owns**: Email-specific mail routing records, MX verification, SPF record validation, 2048-bit DKIM selector key generation, DMARC policy validation, and inbound relay bindings.

### 2.2 AI Context-Provider Contract (Inversion of Control)
> [!IMPORTANT]
> **Strict AI Boundary**:  
> The Core AI Gateway must **NEVER directly query** OryolMail, CRM, or Drive databases.  
> Context retrieval is strictly **application-owned via a Context-Provider Contract**:
> 
> 1. Application worker receives client request.
> 2. Application worker retrieves and packages target domain context (e.g. email thread messages) from its own database.
> 3. Application invokes Core AI Gateway passing the sanitized context payload.
> 4. Core AI Gateway verifies the caller's permissions, sanitizes secrets, dispatches to the approved model provider matching the retention policy, and returns structured JSON.

### 2.3 Search Authorization Contract
- Search read models perform **live authorization checks or safe post-filtering** against the active `authorize()` engine before results are returned to the client, preventing stale search index ACL leakage.
