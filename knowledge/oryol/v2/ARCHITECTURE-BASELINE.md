# Oryol Workspace Architecture Baseline v2.2

**Version**: 2.2  
**Status**: CANDIDATE FOR FINAL FOUR-ITEM DELTA REVIEW  
**Date**: 2026-08-26  
**Ecosystem**: Oryol Workspace Platform (`serlekan/serlekan-sera`, `serlekan/oryol-mail`, `serlekan/oryol-core`)  
**SERA Governance Version**: `0.4.2`  
**Previous Specification Commit**: `7939b9737a0e2ea7bffac0de29f5b4529a17e4c4`  
**Architecture Specification Commit**: `pending_delta_commit`  

---

## 1. Authoritative Governance Declaration

> [!IMPORTANT]
> **Strict Implementation Gate**:  
> **No Oryol Core implementation may begin until the independent GPT final architecture review returns `APPROVED FOR IMPLEMENTATION`.**  
> Any contradictory non-canonical, legacy v1, or interim v2.0/v2.1 document is formally **SUPERSEDED**. Architecture v2.2 is the sole authoritative specification across the Oryol Workspace ecosystem.

---

## 2. Canonical Architecture v2.2 Document Registry

The following 13 canonical documents constitute the authoritative Architecture v2.2 baseline:

1. [`workspace-architecture.md`](workspace-architecture.md) — Two-tier platform hierarchy (`Platform -> Core -> Applications`), 7 permanent architecture rules, and Virel financial domain ownership.
2. [`core-boundaries.md`](core-boundaries.md) — Core platform capabilities vs. product business domains; email DNS verification, AI context-provider inversion, search authorization, and attachment persistence.
3. [`multi-tenancy.md`](multi-tenancy.md) — Universal compound foreign key isolation, brokered cross-org grants (`cross_org_grants`), platform-scoped records, and controlled pilot D1 SLOs.
4. [`identity-model.md`](identity-model.md) — Strict binary Principal taxonomy (`human`, `service`), authentication factors, IdP global uniqueness, secondary security questions, and last-owner protection.
5. [`authorization-model.md`](authorization-model.md) — Executable 8-step `authorize({ principal, membership, organization, action, resource, context })` algebra, permission registry entities, flat Phase 1 role mappings, and trusted contextual attributes.
6. [`session-security.md`](session-security.md) — D1 authoritative session entities, atomic refresh token family rotation state machine, replay breach defenses, dual-verification revocation SLA, and step-up proof binding.
7. [`audit-and-events.md`](audit-and-events.md) — Separate Audit vs. Outbox vs. Observability; worker lease locking dispatcher, retry backoffs, aggregate ordering via `aggregate_version`, non-cascading audit logs, and structured system actors.
8. [`cloudflare-platform.md`](cloudflare-platform.md) — Cloudflare edge storage topology mapping Workers, D1, KV, Queues, R2, Vectorize, and Durable Objects phased by rollout.
9. [`data-lifecycle.md`](data-lifecycle.md) — Multi-phase deletion pipeline (`active` ➔ `soft_deleted` ➔ `retention_grace` ➔ `physical_purge`), D1 Time Travel reality (7-30d), and multi-storage propagation.
10. [`ai-platform.md`](ai-platform.md) — Centralized Oryol AI Gateway, permission-checked application context providers, and verified provider-retention policy compliance.
11. [`search-platform.md`](search-platform.md) — Search contract: derived read model, live authorization post-filtering, sensitive snippet protection, and RAG retrieval alignment.
12. [`product-integration.md`](product-integration.md) — Outbox-driven integration topology across OryolMail, CRM, Calendar, Drive, and Virel.
13. [`sera-governance.md`](sera-governance.md) — Standardized 5-file `.sera/` repository layout, multi-signal detection outside `.sera/`, and deterministic fail-closed policy enforcement.

---

## 3. Review History

- **2026-08-25**: Architecture v1 drafted.
- **2026-08-25**: First Independent Architecture Review identified 7 core design gaps.
- **2026-08-26**: Architecture v2.1 P0 Remediation completed.
- **2026-08-26**: Final Architecture v2.2 Remediation completed. Closed all fail-open SERA governance gaps, completed universal compound tenant integrity, codified brokered cross-org grants, executable 8-step authorization algebra, atomic refresh state machine, outbox lease locking, live search authorization, and Virel financial domain ownership.
