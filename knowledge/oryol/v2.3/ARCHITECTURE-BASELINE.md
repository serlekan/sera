# Oryol Workspace Architecture Baseline v2.3

**Version**: 2.3  
**Status**: PROPOSED ARCHITECTURE BASELINE (v2.3) — Subject to Independent Architecture Review  
**Date**: 2026-08-28  
**Ecosystem**: Oryol Workspace Platform (`serlekan/serlekan-sera`, `serlekan/oryol-mail`, `serlekan/oryol-core`)  
**SERA Governance Version**: `0.4.2`  
**Previous Specification Commit**: `e59f28a1abc0392fe3f38ecfe3a3fde8e379c033` (Architecture v2.2)  
**Architecture Specification Commit**: `e72228553fb315d1f4f2ae53130659d710d830b3`  

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

The following 15 canonical documents constitute the proposed Architecture v2.3 baseline:

1. [`workspace-architecture.md`](workspace-architecture.md) — Two-tier platform hierarchy (`Platform -> Core -> Applications`), 7 permanent architecture rules, and Virel financial domain ownership.
2. [`core-boundaries.md`](core-boundaries.md) — Core platform capabilities vs. product business domains; authoritative security policy ownership (`organization_security_policies`, `organization_ip_allowlist_entries`), email DNS verification, and attachment persistence.
3. [`multi-tenancy.md`](multi-tenancy.md) — Universal compound foreign key isolation, brokered cross-org grants (`cross_org_grants`), service principal role assignments (`service_principal_role_assignments`), and controlled pilot D1 SLOs.
4. [`identity-model.md`](identity-model.md) — Strict binary Principal taxonomy (`human`, `service`), authentication factors, IdP global uniqueness, recovery methods, and service principal role assignment bindings.
5. [`authorization-model.md`](authorization-model.md) — Executable 8-step `authorize({ principal, membership, organization, action, resource, context })` algebra, unified Step 6 coarse-RBAC resolution for humans and service principals, and deterministic Step 8 contextual ABAC algorithm.
6. [`session-security.md`](session-security.md) — D1 authoritative session entities, atomic refresh token family rotation state machine, replay breach defenses, dual-verification revocation SLA, and step-up proof binding.
7. [`audit-and-events.md`](audit-and-events.md) — Separate Audit vs. Outbox vs. Observability; worker lease locking dispatcher, retry backoffs, aggregate ordering via `aggregate_version`, non-cascading audit logs, append-only privacy overlays, and policy mutation audit actions.
8. [`cloudflare-platform.md`](cloudflare-platform.md) — Cloudflare edge storage topology mapping Workers, D1, KV, Queues, R2, Vectorize, and Durable Objects phased by rollout.
9. [`data-lifecycle.md`](data-lifecycle.md) — Multi-phase deletion pipeline (`active` ➔ `soft_deleted` ➔ `retention_grace` ➔ `physical_purge`), D1 Time Travel reality (7-30d), and multi-storage propagation.
10. [`ai-platform.md`](ai-platform.md) — Centralized Oryol AI Gateway, permission-checked application context providers, and verified provider-retention policy compliance.
11. [`search-platform.md`](search-platform.md) — Search contract: derived read model, live authorization post-filtering, sensitive snippet protection, and RAG retrieval alignment.
12. [`product-integration.md`](product-integration.md) — Outbox-driven integration topology across OryolMail, CRM, Calendar, Drive, and Virel.
13. [`sera-governance.md`](sera-governance.md) — Standardized 5-file `.sera/` repository layout, multi-signal detection outside `.sera/`, and deterministic fail-closed policy enforcement.
14. [`adr/ADR-001-step8-security-policy.md`](adr/ADR-001-step8-security-policy.md) — Architectural Decision Record for Core-Owned Security Policy, IP CIDR Allowlisting, and Trusted Device Posture.
15. [`adr/ADR-002-service-principal-rbac.md`](adr/ADR-002-service-principal-rbac.md) — Architectural Decision Record for Service Principal RBAC, Step 6 Evaluation, and Tenant Role Assignments.

---

## 3. Architecture Revision History

- **2026-08-25**: Architecture v1 drafted.
- **2026-08-25**: First Independent Architecture Review identified 7 core design gaps.
- **2026-08-26**: Architecture v2.1 P0 Remediation completed.
- **2026-08-26**: Final Architecture v2.2 Remediation completed (`e59f28a1abc0392fe3f38ecfe3a3fde8e379c033`).
- **2026-08-28**: Proposed Architecture v2.3 drafted to resolve Step 8 Contextual ABAC persistence contract (ADR-001) and Service Principal RBAC capability bindings (ADR-002).
