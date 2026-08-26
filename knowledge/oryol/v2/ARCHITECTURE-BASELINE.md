# Oryol Workspace Architecture Baseline v2.1

**Version**: 2.1  
**Status**: CANDIDATE FOR FINAL REVIEW  
**Date**: 2026-08-26  
**Ecosystem**: Oryol Workspace Platform (`serlekan/serlekan-sera`, `serlekan/oryol-mail`, `serlekan/oryol-core`)

---

## 1. Governance Declaration

> [!IMPORTANT]
> **Strict Implementation Gate**:  
> **No Oryol Core implementation may begin until the independent GPT final architecture review returns `APPROVED FOR IMPLEMENTATION`.**

---

## 2. Canonical Architecture v2.1 Document Registry

The following documents constitute the authoritative Architecture v2.1 baseline:

1. [`workspace-architecture.md`](workspace-architecture.md) — Two-tier platform hierarchy (`Platform -> Core -> Applications`) and 7 permanent architecture rules.
2. [`core-boundaries.md`](core-boundaries.md) — Core platform capabilities vs. application business domains; domain verification & AI context-provider boundaries.
3. [`multi-tenancy.md`](multi-tenancy.md) — Structural tenant isolation, compound foreign keys, and `organization_placement` sharding abstraction.
4. [`identity-model.md`](identity-model.md) — Canonical Principal taxonomy (`human`, `service`), authentication factors, IdP bindings, and invitation lifecycle.
5. [`authorization-model.md`](authorization-model.md) — 8-step `authorize({ principal, membership, organization, action, resource, context })` algebra and 3-part namespace.
6. [`session-security.md`](session-security.md) — D1 authoritative session store, atomic refresh token family rotation, honest 10-minute access token SLA, and JWKS rotation.
7. [`audit-and-events.md`](audit-and-events.md) — Separate Audit, Domain Events, and Observability; Transactional Outbox pattern and Idempotent Inbox deduplication.
8. [`cloudflare-platform.md`](cloudflare-platform.md) — Cloudflare edge storage topology mapping Workers, D1, KV, R2, Queues, and Durable Objects.
9. [`data-lifecycle.md`](data-lifecycle.md) — Deletion pipeline (`active` ➔ `soft_deleted` ➔ `retention_grace` ➔ `physical_purge`), D1 Time Travel reality, and multi-storage propagation.
10. [`ai-platform.md`](ai-platform.md) — Centralized Oryol AI Gateway, permission-checked context providers, and provider-retention policy compliance.
11. [`search-platform.md`](search-platform.md) — Search contract: derived read model, live authorization post-filtering, and organization isolation.
12. [`product-integration.md`](product-integration.md) — Integration topology and contracts across OryolMail, CRM, Calendar, Drive, and Virel.
13. [`sera-governance.md`](sera-governance.md) — Standardized `.sera/` repository layout and fail-closed policy enforcement pipeline.

---

## 3. Review History

- **2026-08-25**: Architecture v1 drafted.
- **2026-08-25**: Initial Independent GPT Architecture Review identified 7 fundamental design gaps (Principal taxonomy, KV session authority assumption, flat RBAC, unseparated audit/events, AI provider calls, search data assumptions, and SERA policy loading).
- **2026-08-26**: Architecture v2.1 P0 Remediation completed. All seven P0 contracts formally resolved and codified in canonical specifications.
