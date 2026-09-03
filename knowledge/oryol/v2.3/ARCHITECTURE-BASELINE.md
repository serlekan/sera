# Oryol Workspace Architecture Baseline v2.3

**Version**: 2.3  
**Status**: PROPOSED ARCHITECTURE BASELINE (v2.3) — Subject to Independent Architecture Review  
**Date**: 2026-08-28  
**Ecosystem**: Oryol Workspace Platform (`serlekan/sera`, `serlekan/oryol-mail`, `serlekan/oryol-core`)  
**SERA Governance Version**: `0.4.2`  
**Previous Baseline Version**: `2.2`  
**Previous Baseline SHA**: `e59f28a1abc0392fe3f38ecfe3a3fde8e379c033`  
**Accepted Specification SHA**: Assigned externally after approval  

---

## 1. Authoritative Governance Declaration

> [!IMPORTANT]
> **Strict Implementation Gate**:  
> **No Oryol Core implementation of v2.3 changes may begin until the independent architecture review returns `APPROVED FOR IMPLEMENTATION`.**  
> Architecture v2.3 resolves two blocking contradictions discovered during Phase 1 Slice 3:
> 1. **ADR-001**: Core-Owned Organization Security Policy & Trusted Edge Step 8 Contextual ABAC.
> 2. **ADR-002**: Service Principal Role-Based Access Control (RBAC) & Step 6 Evaluation.
> Historical v2.2 architecture remains frozen as the baseline for accepted Slices 1 and 2.

---

## 2. Canonical Architecture v2.3 Document Registry

The following 16 canonical documents constitute the proposed Architecture v2.3 baseline:

1. [`workspace-architecture.md`](workspace-architecture.md) — Two-tier platform hierarchy (`Platform -> Core -> Applications`), 7 permanent architecture rules, and Virel financial domain ownership.
2. [`core-boundaries.md`](core-boundaries.md) — Core platform capabilities vs. product business domains; authoritative security policy ownership (`organization_security_policies`, `organization_ip_allowlist_entries`), email DNS verification, and attachment persistence.
3. [`multi-tenancy.md`](multi-tenancy.md) — Universal compound foreign key isolation, brokered cross-org grants (`cross_org_grants`), compound service account tenant ownership (`(organization_id, principal_id)`), service principal role assignments (`service_principal_role_assignments`), and controlled pilot D1 SLOs.
4. [`identity-model.md`](identity-model.md) — Strict binary Principal taxonomy (`human`, `service`), authentication factors, IdP global uniqueness, recovery methods, single-tenant service account ownership, and service principal role assignment bindings.
5. [`authorization-model.md`](authorization-model.md) — Executable 8-step `authorize({ principal, membership, organization, action, resource, context })` algebra, unified Step 6 coarse-RBAC resolution for humans and service principals, dual-signal template validation (`is_system_template = TRUE AND template_key IN ('owner', 'admin')`), and deterministic Step 8 contextual ABAC algorithm.
6. [`session-security.md`](session-security.md) — D1 authoritative session entities, atomic refresh token family rotation state machine, replay breach defenses, dual-verification revocation SLA, and step-up proof binding.
7. [`audit-and-events.md`](audit-and-events.md) — Separate Audit vs. Outbox vs. Observability; worker lease locking dispatcher, retry backoffs, aggregate ordering via `aggregate_version`, non-cascading audit logs, append-only privacy overlays, and policy mutation audit actions.
8. [`cloudflare-platform.md`](cloudflare-platform.md) — Cloudflare edge storage topology mapping Workers, D1, KV, Queues, R2, Vectorize, and Durable Objects phased by rollout.
9. [`data-lifecycle.md`](data-lifecycle.md) — Multi-phase deletion pipeline (`active` ➔ `soft_deleted` ➔ `retention_grace` ➔ `physical_purge`), D1 Time Travel reality (7-30d), and multi-storage propagation.
10. [`ai-platform.md`](ai-platform.md) — Centralized Oryol AI Gateway, permission-checked application context providers, and verified provider-retention policy compliance.
11. [`search-platform.md`](search-platform.md) — Search contract: derived read model, live authorization post-filtering, sensitive snippet protection, and RAG retrieval alignment.
12. [`product-integration.md`](product-integration.md) — Outbox-driven integration topology across OryolMail, CRM, Calendar, Drive, and Virel.
13. [`sera-governance.md`](sera-governance.md) — Standardized 5-file `.sera/` repository layout, multi-signal detection outside `.sera/`, and deterministic fail-closed policy enforcement.
14. [`adr/ADR-001-step8-security-policy.md`](adr/ADR-001-step8-security-policy.md) — Architectural Decision Record for Core-Owned Security Policy, IP CIDR Allowlisting, Trusted Device Posture, and deterministic internal dispatch denial.
15. [`adr/ADR-002-service-principal-rbac.md`](adr/ADR-002-service-principal-rbac.md) — Architectural Decision Record for Service Principal RBAC, Step 6 Evaluation, Role Template Invariants, Compound Tenant Ownership, and Migration 0005 Upgrade Contract.
16. [`predecessor-schema-manifest.md`](predecessor-schema-manifest.md) — Self-contained pinned manifest of the actual accepted executable predecessor database schema (`serlekan/oryol-core` @ `ca3fb9c18e8e061c277a3e2f4f009bbc9b961717`), verbatim DDL excerpts, blob hashes, and compound parent-key inventory.

---

## 3. Architecture Revision History

- **2026-08-25**: Architecture v1 drafted.
- **2026-08-25**: First Independent Architecture Review identified 7 core design gaps.
- **2026-08-26**: Architecture v2.1 P0 Remediation completed.
- **2026-08-26**: Final Architecture v2.2 Remediation completed (`e59f28a1abc0392fe3f38ecfe3a3fde8e379c033`).
- **2026-08-28**: Proposed Architecture v2.3 drafted to resolve Step 8 Contextual ABAC persistence contract (ADR-001) and Service Principal RBAC capability bindings (ADR-002).
- **2026-08-30**: Adversarial Architecture Review remediations F-1 through F-10 and R-1 through R-10 applied.
- **2026-09-01**: Service principal role foreign key deletion semantics aligned to `ON DELETE RESTRICT`.
- **2026-09-01**: Security gate remediation: closed P0-1 system role template forgery, P0-2 service-principal tenant escape, P0-3 Migration 0005 deterministic upgrade contract, and P1 internal execution CIDR contradiction.
- **2026-09-02**: Migration 0005 dependency-chain and transaction-semantics closure: established 7-phase shadow reconstruction algorithm safeguarding authorization subjects and explicit-deny policies from cascading deletion (Finding A); reconciled historical service account tenant ownership drift against accepted Migration 0001 (Finding B); established immutable service account tenant ownership triggers; corrected predecessor invitations schema facts and preserved invitations without destructive reconstruction; restored Frozen v2.2 invitation uniqueness `UNIQUE(organization_id, email, status)` via preflight duplicate detection and in-place unique index; implemented in-batch assertion failure mechanics (`_migration_assert`) guaranteeing rollback before table retirement; clarified post-batch confirmation semantics without false rollback claims; inventoried compound foreign key parent-key eligibility; added self-contained predecessor schema manifest; documented fresh-install vs. migrated schema equivalence.
- **2026-09-03**: Final Security Closure: upgraded service account ownership immutability triggers to SQLite null-safe identity semantics (`WHERE NEW.organization_id IS NOT OLD.organization_id;`) on both fresh-install and migrated paths (P0); added in-batch service account ownership assertions (`_migration_assert_ownership`); added in-batch role template backfill assertions (`_migration_assert_templates`); formally bounded tenant role creation against client-supplied system template fields; upgraded Phase D deny-chain shadow parity to bidirectional full-tuple equality via symmetric `EXCEPT` across all columns of OSP, authorization subjects, and explicit denies; established deterministic Phase-1 action-pattern grammar (exact and `<service>.*`) with fail-closed malformed pattern and invalid resource scope handling; hardened Step 8.2 with immediate terminal denial `DENY(CONTEXT_INTERNAL_CONTEXT_INVALID)` for invalid internal execution sentinels; audited predecessor secondary indexes confirming zero lost indexes.
