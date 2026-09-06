# Oryol Data Lifecycle & Deletion Architecture v2.4 (Delta over v2.3)

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.4) — Subject to Independent Architecture Review
**Predecessor**: [`../v2.3/data-lifecycle.md`](../v2.3/data-lifecycle.md) (carried forward from the v2.2 canonical text; accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Revision Scope**: Migration verification durability, quarantine state, and post-restore recovery / reconciliation (ADR-005). Deletion pipeline, multi-storage propagation, and organization purge governance are carried forward unchanged.

---

## 1. Carry-Forward Declaration

All of [`../v2.3/data-lifecycle.md`](../v2.3/data-lifecycle.md) is carried forward **unchanged** except §2 below. Retained verbatim:

- §1 Canonical Deletion Pipeline (`active → soft_deleted → retention_grace → purge_eligible → physical_purge`, lifecycle-state table) — **unchanged**.
- §2 Multi-Storage Propagation Reality (D1 / R2 / Search / Outbox / Audit / AI Providers, **D1 Time Travel 7–30 d**, permanent audit retention, no physical purge / zero in-place updates) — **unchanged**.
- §3 Organization Lifecycle & Purge Governance (`deletion_pending` 30-day grace, physical purge execution, `core.organization.purged` tombstone) — **unchanged**.

---

## 2. v2.4 Amendment — Migration verification is durable routing state (ADR-005)

v2.3 correctly requires that a migration postcondition failure block production routing, but does not make that block **survive a process restart**, does not bind verification evidence to a concrete database identity, and does not specify preflight↔DDL fencing or post-restore reconciliation. **[ADR-005](adr/ADR-005-migration-verification-and-recovery.md) adds the durable layer:**

> [!IMPORTANT]
> **`MIGRATION_ROUTING_ELIGIBILITY`**: Production routing to a Core D1 database is permitted **iff** its persistent `schema_migration_state.state = 'VERIFIED'` **and** the bound evidence hash matches the running application release. A process exiting `0` is insufficient. Verification state is one of `PRE_MIGRATION`, `MIGRATING`, `MIGRATED_UNVERIFIED`, `VERIFIED`, `VERIFICATION_FAILED`, `RECOVERY_REQUIRED`. A restart re-reads the row; eligibility is never synthesized in memory. A subsequent migration command re-runs verification for the last applied migration before proceeding.

- **`VERIFICATION_EVIDENCE_BINDING`**: `VERIFIED` requires all of — Cloudflare account/environment, D1 database id, D1 binding name, migration filename, migration SHA-256, canonical schema fingerprint, application release id, UTC timestamp, verification-suite version — bound and hashed.
- **`MIGRATION_EXACT_IDENTITY`**: verification and routing key on the exact canonical migration id + `sha256`; broad matching (`LIKE '%0005%'`, globs, substrings) is prohibited.
- **Semantic postconditions** verify foreign keys, compound keys, unique/partial indexes, trigger fingerprints, and semantic invariants over existing rows (taxonomy, service-account ownership, authorization-subject relationships, deny grammar/resource shape, invitation tenant relationships, required-row preservation, ADR-003 human-Owner invariant). Trigger **behavior** is probed on isolated in-memory fixtures — never by mutating production data.
- **`MIGRATION_WRITE_FENCE`**: migrations acquire a `schema_migration_state` lease (CAS), engage KV maintenance-mode write quiescence, and re-express every gating preflight assertion as an in-batch `_migration_assert` checkpoint — so no migration assumes predecessor data is unchanged between preflight and DDL.

### 2.1 Recovery & reconciliation (ADR-005 §7)

- D1 Time Travel / bookmark restore is an operator action under dual control, audited (`core.db.time_travel_restore`), followed by mandatory re-verification.
- **`RESTORE_DOES_NOT_REVERSE_EXTERNAL_EFFECTS`**: restoring Core D1 does **not** undo sent email, registrar DNS changes, transmitted Virel invoices/payments, delivered webhooks, or events already consumed by product databases. Recovery is **forward**: routing quarantine of affected product databases, exact event reconciliation using ADR-006 aggregate-version semantics, compensating/corrected events (new ids, causation-linked), domain-specific financial compensation, and Sev-1 incident escalation with a dual-control reconciliation plan.
- **Rollback eligibility**: a migration is rollback-eligible only if no product inbox high-water mark depends on its schema and its DDL is losslessly reversible. Migration 0005 (shadow reconstruction, NOT NULL backfill) is **not** rollback-eligible post-traffic; recovery is forward-only.

---

## 3. Schema & Migration Impact

New **local** control tables `schema_migration_state` (singleton) and `migration_ledger` (append-only) plus a KV maintenance flag, delivered by forward migration `0006_migration_verification_state.sql` (Phase 1 Slice 4+; not implemented by this task). Migration `0006` back-fills `schema_migration_state` by running the full ADR-005 §5 verification for the already-shipped `0005` and setting `VERIFIED` only if it passes. Migrations `0001`–`0005` remain sealed and immutable.
