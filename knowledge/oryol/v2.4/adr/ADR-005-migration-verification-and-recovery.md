# ADR-005: Migration Verification, Quarantine & Recovery

**Status**: PROPOSED (Target: Architecture v2.4)
**Date**: 2026-09-06
**Author**: Deep Builder (`anthropic/claude-sonnet-5`)
**Scope**: Oryol Core D1 migration lifecycle, verification durability, production routing eligibility, disaster recovery
**Affected Documents**: `data-lifecycle.md`, `cloudflare-platform.md` (unchanged; referenced), `audit-and-events.md`, ADR-002 §7 (Migration 0005 contract; unchanged, extended)
**Predecessor Baseline**: Oryol Architecture v2.3 (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Originating Finding**: Independent principal review (`docs/reviews/ASTRA-PRINCIPAL-REVIEW-2026-09-06.md`) — v2.3 correctly blocks routing on postcondition failure but does not make routing eligibility **durable**, does not bind verification evidence to a concrete database identity, and does not address preflight→DDL TOCTOU or post-migration recovery.

---

## 1. Context

ADR-002 §7.1 defines a three-phase Migration 0005 boundary (host preflight → atomic `db.batch` → post-batch confirmation) and states:

> *"Post-batch validation confirms committed state; any unexpected failure is treated as a fatal incident condition (`MIGRATION_POSTCONDITION_FAILURE`)."*

The principal review validated three gaps:

1. **No durable routing-eligibility state.** "Postcondition failure blocks production routing" is a runtime assertion in the migration process. If the process exits (crash, redeploy, new `wrangler deploy`) the block evaporates. A migration that *committed its DDL* but *failed its postconditions* leaves the database structurally changed and unverified, yet a fresh Worker rollout has nothing that tells it "do not route to this DB".
2. **Verification evidence is not bound to a database identity.** "Verification passed" is meaningless without *which* D1 database, which binding, which environment, which migration file + hash, which schema fingerprint, and which application release.
3. **Preflight assumes predecessor data is frozen through DDL.** ADR-002 §7 preflight reads rows, then the batch mutates them. Nothing fences concurrent writers between the two.

---

## 2. Decision — Persistent Migration Verification State Machine

Every Core D1 database carries exactly one row in a new **local** control table `schema_migration_state` (created by the earliest migration; for the accepted `0001`–`0005` line it is introduced by forward migration `0006_migration_verification_state.sql`, **not implemented by this task**). The row's `state` is one of:

| State | Meaning | Production routing |
|---|---|---|
| `PRE_MIGRATION` | No migration has been applied to this database yet (fresh binding). | **BLOCKED** |
| `MIGRATING` | A migration batch is in progress (lease held, §6). | **BLOCKED** |
| `MIGRATED_UNVERIFIED` | A migration batch committed its DDL, but structural + semantic verification has not completed successfully. | **BLOCKED** |
| `VERIFIED` | The migration at `verified_migration_id` committed **and** all structural + semantic postconditions passed, bound to the evidence in §3. | **ELIGIBLE** |
| `VERIFICATION_FAILED` | A verification run executed and at least one postcondition failed. Structural change may be fully or partially present. | **BLOCKED** (quarantine) |
| `RECOVERY_REQUIRED` | An operator or automated check has declared this database in a state requiring forward repair / restore / reconciliation before any further migration or routing. | **BLOCKED** (quarantine) |

### 2.1 Transitions

```
PRE_MIGRATION ──lease acquired──► MIGRATING
MIGRATING ──batch committed──► MIGRATED_UNVERIFIED
MIGRATING ──batch rolled back / lease lost──► PRE_MIGRATION (if first) | last VERIFIED-or-FAILED state (if re-migration)
MIGRATED_UNVERIFIED ──verification pass──► VERIFIED
MIGRATED_UNVERIFIED ──verification fail──► VERIFICATION_FAILED
VERIFIED ──new migration lease──► MIGRATING
VERIFICATION_FAILED ──verification re-run pass──► VERIFIED
VERIFICATION_FAILED ──operator escalation──► RECOVERY_REQUIRED
RECOVERY_REQUIRED ──operator clears after repair + successful verification──► VERIFIED
```

> [!IMPORTANT]
> **Durable Quarantine Invariant (`MIGRATION_ROUTING_ELIGIBILITY`)**:
> Production request routing to a Core D1 database is permitted **iff** its `schema_migration_state.state = 'VERIFIED'` **and** `schema_migration_state.verified_evidence_hash` matches the evidence recomputed for the currently deployed application release (§3.4). This check is performed by the Worker **at cold start and cached for ≤ 60 s**, and by the migration orchestrator before hand-off. A process restart re-reads the row; it **cannot** synthesize eligibility. There is no in-memory-only "verified" flag anywhere in the architecture.

### 2.2 Table shape (informative; delivered by migration 0006)

```sql
CREATE TABLE schema_migration_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),           -- singleton
    state TEXT NOT NULL CHECK (state IN (
        'PRE_MIGRATION','MIGRATING','MIGRATED_UNVERIFIED',
        'VERIFIED','VERIFICATION_FAILED','RECOVERY_REQUIRED')),
    applied_migration_id TEXT,                       -- exact canonical id, e.g. '0005_core_security_policies_and_service_rbac'
    verified_migration_id TEXT,                      -- exact canonical id last VERIFIED
    migration_sha256 TEXT,                           -- sha256 of the exact migration file bytes
    schema_fingerprint TEXT,                         -- canonical schema identity hash (§3.2)
    application_release_id TEXT,                     -- release identity that performed/verified (§3.3)
    verified_evidence_hash TEXT,                     -- sha256 over the full §3 evidence tuple
    cf_account_id TEXT,
    cf_environment TEXT,                             -- 'production' | 'staging' | 'pilot-<name>'
    d1_database_id TEXT,                             -- Cloudflare D1 database UUID
    d1_binding_name TEXT,                            -- wrangler binding, e.g. 'CORE_DB'
    verification_version INTEGER NOT NULL DEFAULT 1, -- version of the verification suite that ran
    lease_owner TEXT,                                -- migration lease (§6)
    lease_expires_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## 3. Database Identity — Verification Evidence Binding

> [!IMPORTANT]
> **Evidence Binding Invariant (`VERIFICATION_EVIDENCE_BINDING`)**:
> A `VERIFIED` transition is valid only if the verification run recorded, and `verified_evidence_hash` is computed over, **all** of the following, and every field is non-null:

| Evidence field | Source |
|---|---|
| Cloudflare account id | `cf_account_id` (deploy env) |
| Cloudflare environment | `cf_environment` (`production` / `staging` / `pilot-*`) |
| D1 database identifier | `d1_database_id` (Cloudflare D1 UUID) |
| D1 binding name | `d1_binding_name` (wrangler binding) |
| Migration filename | exact canonical id (e.g. `0005_core_security_policies_and_service_rbac.sql`) |
| Migration SHA-256 | `sha256(migration file bytes)` — no glob, no `LIKE` (§4) |
| Schema identity / fingerprint | `schema_fingerprint` (§3.2) |
| Application release identity | `application_release_id` (§3.3) |
| Timestamp | UTC ISO-8601 of the verification run |
| Verification suite version | `verification_version` |

`verified_evidence_hash = sha256(canonical_json(evidence_tuple))`.

### 3.1 Purpose

Prevents "verified on staging → routed on production", "verified for release N → running release N+2 with a new required migration", and "verified against database A → binding now points at database B".

### 3.2 Schema fingerprint

`schema_fingerprint` is a deterministic hash over the **canonical** schema, not `sqlite_master` text verbatim (which carries formatting noise). It is computed as `sha256` over a sorted, normalized enumeration of: table names + column names + declared types + NOT NULL + DEFAULT + PK; every foreign key (child cols → parent table(parent cols) + on-delete action); every unique index (cols + partial predicate, normalized); every non-unique index (cols); every trigger (name + normalized SQL body). This is the same set §5 verifies semantically; the fingerprint is its identity.

### 3.3 Application release identity

`application_release_id` = the Worker script version/deploy id (Cloudflare `version_metadata.id`) plus the source commit SHA embedded at build time. The routing check (§2.1) recomputes the *required migration set* for the running release and compares `verified_migration_id` ≥ the max required id **and** `verified_evidence_hash` was produced by this `application_release_id` **or** a release whose required-migration set is identical (a pure application change that adds no migration re-uses the existing VERIFIED evidence; a release that adds migration `000N` forces `MIGRATED_UNVERIFIED` until `000N` verifies).

### 3.4 Routing eligibility predicate (exact)

```
routable(db, release) :=
      db.state == 'VERIFIED'
  AND db.verified_migration_id is not null
  AND required_migrations(release) is a subset of applied_migrations(db)
  AND every id in required_migrations(release) has state VERIFIED in migration_ledger (§5.4)
  AND db.verified_evidence_hash == recompute_evidence_hash(db, release)
```

Any clause false → **not routable** → Worker returns `503 SERVICE_UNAVAILABLE (DB_NOT_VERIFIED)` and emits an operational alert.

---

## 4. Routing Eligibility & Retry Semantics

### 4.1 Committed ≠ eligible

> A migration process exiting `0` is **insufficient** for routing eligibility. Eligibility requires `state = 'VERIFIED'` with matching evidence (§3.4). If Migration 0005's `db.batch` commits but a post-batch check fails, the orchestrator MUST set `state = 'VERIFICATION_FAILED'` (or `MIGRATED_UNVERIFIED` if the failure was inconclusive / the check could not complete) **before** exiting, and routing stays blocked across any number of process restarts.

### 4.2 Verification is independently re-runnable

> [!IMPORTANT]
> **Independent Verification Invariant (`VERIFICATION_INDEPENDENTLY_EXECUTABLE`)**:
> Structural + semantic verification for migration `id` is a standalone command (`sera`/orchestrator subcommand `verify-migration --id <canonical-id>`) that can run at any time against a database, regardless of whether `id` is still "pending". Applying `0005` does not consume the right to verify `0005`. A database in `MIGRATED_UNVERIFIED` or `VERIFICATION_FAILED` MUST be re-verifiable without re-applying DDL.

### 4.3 Subsequent migration command re-runs verification

Running the migration orchestrator against a database whose `state != 'VERIFIED'` MUST, before attempting any new migration, **re-run verification for the last applied migration**. If it now passes → `VERIFIED`, then proceed. If it fails → stay quarantined, do not apply the next migration. This closes the "0005 applied but unverified, then someone runs 0006" hole.

### 4.4 Exact migration identity — no broad matching

> [!IMPORTANT]
> **Exact Identity Invariant (`MIGRATION_EXACT_IDENTITY`)**:
> Verification, ledger lookup, and routing checks MUST key on the **exact canonical migration id** (`0005_core_security_policies_and_service_rbac`) and its `sha256`. Broad matching such as `WHERE name LIKE '%0005%'`, prefix globs, or substring scans are **prohibited** anywhere an exact id is available. The canonical id is the migration filename without extension; the `sha256` is over the exact file bytes as committed to `serlekan/oryol-core`.

---

## 5. Schema Semantic Verification (postconditions)

Postconditions MUST verify more than `sqlite_master` object *names*. For each migration, the verification suite (versioned; `verification_version`) asserts, using exact identities:

### 5.1 Structural (per object, canonical form)

| Aspect | Assertion |
|---|---|
| Foreign keys | Every expected FK exists with exact child columns → parent table(parent columns) and exact `ON DELETE` action (`PRAGMA foreign_key_list(<table>)` parsed, not string-matched). Compound FKs verified as ordered column tuples. |
| Compound / primary keys | `PRAGMA table_info` + `PRAGMA index_list` confirm exact PK column ordering. |
| Unique indexes | Exact column set **and** partial-index predicate (e.g. `uq_role_definitions_org_template ... WHERE template_key IS NOT NULL`) present and normalized-equal to expected. |
| Partial-index predicates | The `WHERE` clause is parsed and compared as a normalized expression, not as raw text. |
| Triggers | Trigger SQL is normalized (whitespace, quoting) and compared to a canonical fingerprint; presence of the trigger *name* alone is insufficient. Both `trg_role_definitions_immutable_template`, `trg_service_accounts_org_immutable`, `trg_audit_no_update/delete`, ADR-003 `trg_sra_reject_owner_template`, etc. |
| `PRAGMA foreign_key_check` | Returns zero rows over the whole database. |
| `PRAGMA integrity_check` | Returns `ok`. |

### 5.2 Semantic invariants over EXISTING rows

| Invariant | Assertion |
|---|---|
| Role-template taxonomy | Zero `role_definitions` rows violating `(is_system_template=0 AND template_key IS NULL) OR (is_system_template=1 AND template_key IN ('owner','admin','member'))`; ≤ 1 of each `template_key` per organization. |
| Service-account tenant ownership | Zero `service_accounts` with `organization_id IS NULL`; every `service_accounts.organization_id` equals its authoritative OSP binding org; no principal mapped to > 1 org. |
| Authorization subject relationships | Every `authorization_subjects` row's referenced membership/team/OSP exists in the same org and matches `subject_type` (the ADR-002 §7.4 Phase A checks, re-asserted post-commit). |
| Explicit-deny grammar & resource shape | Every `explicit_denies.action_pattern` matches the canonical Phase-1 grammar (`^[a-z0-9_-]+(\.[a-z0-9_-]+)+$` or `^[a-z0-9_-]+\.\*$`); zero rows with `resource_type IS NULL AND resource_id IS NOT NULL`. |
| Membership human-principal taxonomy | Every `memberships.principal_id` → `principals.type = 'human'`. |
| OSP service-principal taxonomy | Every `organization_service_principals.principal_id` → `principals.type = 'service'`. |
| Invitation tenant relationships | Every `invitations` row's `(organization_id, role_id)` and `(organization_id, invited_by_membership_id)` resolve; `UNIQUE(organization_id, email, status)` index present; no duplicate active invitation. |
| Required row / tuple preservation | Row-count parity + full ID-set preservation for any shadow-reconstructed table (ADR-002 §7.4 Phase D parity, re-asserted). For 0005: `organization_service_principals`, `authorization_subjects`, `explicit_denies` ID sets unchanged vs. a pre-migration baseline snapshot captured by the orchestrator. |
| ADR-003 org invariant | Every `organizations.status='active'` satisfies `active_human_owner_count >= 1`. |
| Registry integrity | Every `role_permissions.(registry_version, permission_name)` resolves in `permission_definitions`; every org's bound registry is `status='active'`. |

### 5.3 Defensive behavioral validation via isolated fixtures

> Where a postcondition needs to prove a **trigger actually fires** (not merely that its SQL text is present), verification runs the probe against an **isolated in-memory fixture database** constructed from the same canonical schema — never against the production database. Example: create a fixture, insert a service account, attempt `UPDATE service_accounts SET organization_id = <other>` and assert `SERVICE_ACCOUNT_ORG_IMMUTABLE` is raised. Production data is never mutated to test behavior. (This is the pattern already used by the SERA governance test `test_migration_0005_sqlite_simulation_fixture`.)

### 5.4 Migration ledger

A local append-only `migration_ledger` table (id, `sha256`, applied_at, `verification_version`, `verify_result` ∈ {`VERIFIED`,`FAILED`,`PENDING`}, `evidence_hash`) records every apply and every verification run. `schema_migration_state` is the current pointer; `migration_ledger` is the history. Ledger rows are never updated in place — a re-verification appends a new row.

---

## 6. Migration Fence — Preflight ↔ DDL TOCTOU

> [!IMPORTANT]
> **Migration Fence Invariant (`MIGRATION_WRITE_FENCE`)**:
> A migration MUST NOT assume predecessor data is unchanged between host preflight (ADR-002 §7 read-only assertions) and the DDL batch. The architecture mandates an explicit fence combining **all three** of:
>
> 1. **Deployment lock / migration lease**: The orchestrator acquires the `schema_migration_state` lease via CAS: `UPDATE schema_migration_state SET lease_owner=:id, lease_expires_at=datetime('now','+600 seconds'), state='MIGRATING' WHERE id=1 AND (lease_owner IS NULL OR lease_expires_at < datetime('now')) AND state IN ('PRE_MIGRATION','VERIFIED')`. `affected_rows != 1` → abort (`ERR_MIGRATION_LEASE_UNAVAILABLE`); another migration or a quarantine is active.
> 2. **Write quiescence via maintenance mode**: Core sets `organizations`-independent global maintenance flag in KV (`core:maintenance = migrating`), read by every Worker cold start and per-request middleware; mutating endpoints return `503 MAINTENANCE_MODE`. Read endpoints may continue against the last VERIFIED state. Maintenance mode is engaged **before** preflight and released only after the state reaches `VERIFIED` or a quarantine state.
> 3. **Preflight re-assertion inside the batch**: Every read-only preflight assertion in ADR-002 §7 that gates a destructive step is **re-expressed as an in-batch `_migration_assert` checkpoint** (ADR-002 §7.4 Phase D mechanism) so that if a write slipped through between preflight and DDL, the batch rolls back. Preflight becomes an early-exit optimization; the in-batch assertion is the authority.

The combination means: concurrent writers are blocked (2), a second orchestrator cannot race (1), and any residual drift is caught atomically (3).

---

## 7. Recovery

### 7.1 Failure taxonomy and response

| Failure | State outcome | Recovery path |
|---|---|---|
| **Migration fails before commit** (preflight abort, in-batch assertion, lease lost) | No DDL committed. State returns to prior (`PRE_MIGRATION` or last `VERIFIED`). Maintenance mode released. | Fix the flagged condition (e.g. duplicate template, orphan subject), re-run the orchestrator. No data recovery needed — nothing changed. |
| **Migration commits but verification fails** | `VERIFICATION_FAILED` (or `MIGRATED_UNVERIFIED` if inconclusive). Routing blocked durably. | (a) Re-run `verify-migration` (§4.2) — transient infra failures clear this. (b) **Forward repair**: author a corrective forward migration `000N+1` that fixes the specific defect, apply + verify. (c) If structural damage is severe → `RECOVERY_REQUIRED` + §7.2. |
| **Application rollout fails after a VERIFIED migration** | DB stays `VERIFIED`. The app release is rolled back to the previous release. | Routing predicate (§3.4): if the previous release's required-migration set ⊆ applied and VERIFIED, it routes normally. If the new migration is **not** backward compatible with the previous release, the previous release's required set differs → previous release also blocked → must roll forward with a fixed app build. (This is why migrations in this architecture are additive/backward-compatible within a slice.) |
| **Core D1 restored from Time Travel / bookmark** | Orchestrator MUST run `verify-migration` for the restored point; `schema_migration_state` is part of the restored snapshot, so it reflects the restored moment. If the restore predates a migration that product databases already consumed events from → `RECOVERY_REQUIRED` + §7.3 reconciliation. | §7.2, §7.3. |

### 7.2 D1 Time Travel / bookmark

- Core D1 Time Travel provides point-in-time restore for **7–30 days** (v2.3 `data-lifecycle.md §2`, unchanged). Before any restore, the orchestrator captures a bookmark of the *current* (damaged) state for forensics.
- Restore is an **operator action under dual control**, recorded as an immutable audit event `core.db.time_travel_restore` (account, environment, source bookmark/timestamp, target bookmark).
- After restore: mandatory `verify-migration` for the latest applied id; state transitions per §2.1. Routing stays blocked until `VERIFIED`.

### 7.3 External side effects are NOT reversed by database restoration

> [!IMPORTANT]
> **External Effects Invariant (`RESTORE_DOES_NOT_REVERSE_EXTERNAL_EFFECTS`)**:
> Restoring Core D1 to an earlier point does **not** undo: emails already sent via the mail provider, DNS records already created/verified at the registrar, Virel invoices/payments already transmitted to a payment provider, webhook deliveries already made to tenant endpoints, or events already consumed by product databases (`oryol-mail`, `oryol-crm`, `oryol-calendar`, `oryol-drive`, `virel`). The architecture MUST NOT claim otherwise anywhere.

**Reconciliation procedure after a Core restore that rewinds past consumed events:**

1. **Routing quarantine**: Every product database whose `inbox_events` high-water mark for a Core aggregate exceeds Core's post-restore `outbox_events` / aggregate-version position is placed in `RECOVERY_REQUIRED` for the affected aggregates (product-side equivalent of this state machine).
2. **Event reconciliation**: The orchestrator computes, per aggregate, the set of `(aggregate_id, aggregate_version)` that products consumed but Core no longer has emitted (the "phantom" set), and the set Core will now re-emit after catching back up (the "replay" set). ADR-006 aggregate-version semantics make both sets computable exactly.
3. **Forward repair, not rollback**: Core re-derives current domain state and emits **compensating** or **corrected** events (new event ids, causation-linked to the restore incident) rather than attempting to "un-send" phantom events. Products apply them idempotently (ADR-006 idempotency).
4. **Financial / provider effects**: Handed to the domain's own compensation flow (e.g. Virel issues a credit note; mail cannot be recalled and is logged as an incident artifact). The database layer never asserts these are reversed.
5. **Incident escalation**: A Core restore that rewinds past consumed events is a **Sev-1 incident**. It requires an incident commander, a written reconciliation plan approved by dual control, and a post-incident review. `RECOVERY_REQUIRED` is cleared per database only after its reconciliation plan completes and `verify-migration` passes.
6. **Rollback eligibility**: A migration is "rollback eligible" only if (a) no product database has advanced its inbox high-water mark past events that depend on the migration's schema, and (b) the migration's DDL is reversible without data loss. Migration 0005 (shadow reconstruction, NOT NULL backfill) is **not** rollback eligible once any tenant traffic has run against it; recovery is forward-only.

---

## 8. Canonical Errors (additive)

`DB_NOT_VERIFIED`, `MIGRATION_POSTCONDITION_FAILURE` (v2.3, retained), `ERR_MIGRATION_LEASE_UNAVAILABLE`, `MAINTENANCE_MODE`, `ERR_MIGRATION_ORG_WITHOUT_HUMAN_OWNER`, `ERR_MIGRATION_EVIDENCE_INCOMPLETE`, `ERR_VERIFICATION_SUITE_VERSION_MISMATCH`.

---

## 9. Schema & Migration Impact

- New local control tables `schema_migration_state` (singleton) and `migration_ledger` (append-only), plus a KV maintenance flag. Delivered by forward migration `0006_migration_verification_state.sql` (Phase 1 Slice 4+; **not implemented by this task**).
- Migrations `0001`–`0005` remain **sealed and immutable**. ADR-002 §7 Migration 0005 contract is unchanged; this ADR adds the *durable* state + evidence + fence + recovery layer around it and requires that 0005's post-batch checks write `VERIFICATION_FAILED`/`VERIFIED` durably rather than only asserting in-process.
- Retroactive note: because `0005` shipped before `schema_migration_state` exists, migration `0006` MUST, as its first act, create the table, back-fill `state` by running the full §5 verification for `0005` against the live database, and set `VERIFIED` only if it passes (else `VERIFICATION_FAILED` and the deployment halts).

---

## 10. Alternatives Considered & Rejected

1. **In-memory "verified" flag / env var**: *Rejected* — evaporates on restart; the entire finding.
2. **Verify by `sqlite_master` name presence only**: *Rejected* — a renamed-but-wrong FK, a dropped partial predicate, or a stubbed trigger all pass. Semantic + canonical-form verification required.
3. **`LIKE '%0005%'` migration matching**: *Rejected* explicitly — collides with `10005`, `0005a`, comments; exact canonical id + sha256 mandated.
4. **Optimistic migration without write fence** ("tenants rarely write during deploy"): *Rejected* — TOCTOU between preflight and DDL is a correctness bug, not a probability. Lease + maintenance mode + in-batch re-assertion.
5. **Claiming D1 restore reverts external effects**: *Rejected* — false; forward reconciliation + incident escalation instead.
6. **Automatic rollback of any failed migration**: *Rejected* — 0005-class migrations are not reversible post-traffic; forward repair is the only safe path.

---

## 11. Decisions / Invariants / Open Questions

### Decisions
- D1: Six-state persistent `schema_migration_state` machine; routing eligible only in `VERIFIED` (§2).
- D2: `VERIFIED` requires the full 10-field evidence tuple bound and hashed (§3).
- D3: Verification is an independently executable command; a subsequent migration re-runs verification for the last applied id first (§4.2–4.3).
- D4: Exact canonical id + sha256 everywhere; no `LIKE`/glob/substring matching (§4.4).
- D5: Postconditions verify FKs, compound keys, unique + partial indexes, trigger fingerprints, and semantic row invariants; trigger behavior probed on isolated fixtures only (§5).
- D6: Migration fence = lease + maintenance-mode write quiescence + in-batch re-assertion of every gating preflight (§6).
- D7: External side effects are never reversed by DB restore; forward reconciliation + Sev-1 escalation (§7.3).

### Invariants
- I1 `MIGRATION_ROUTING_ELIGIBILITY` — route iff `VERIFIED` + evidence hash matches running release; re-checked at cold start; never synthesized (§2.1).
- I2 `VERIFICATION_EVIDENCE_BINDING` — all 10 evidence fields non-null and hashed (§3).
- I3 `VERIFICATION_INDEPENDENTLY_EXECUTABLE` — applying a migration never consumes the right to verify it (§4.2).
- I4 `MIGRATION_EXACT_IDENTITY` — exact id + sha256; broad matching prohibited (§4.4).
- I5 `MIGRATION_WRITE_FENCE` — no migration assumes frozen predecessor data without lease + quiescence + in-batch re-assertion (§6).
- I6 `RESTORE_DOES_NOT_REVERSE_EXTERNAL_EFFECTS` (§7.3).
- I7 A committed-but-unverified migration is durably `MIGRATED_UNVERIFIED`/`VERIFICATION_FAILED` and survives process restart (§4.1).

### Open Questions
- OQ1: Whether maintenance-mode write quiescence can be scoped per-organization (rolling migration) rather than global — Phase 1: global; pilot D1 volumes make the global stall acceptable.
- OQ2: Retention alignment — Time Travel is 7–30 d but a reconciliation incident may exceed 30 d; whether Core needs periodic R2 logical snapshots as a longer restore floor. Defer to a capacity ADR.
- OQ3: Automated product-side `RECOVERY_REQUIRED` propagation vs. operator-initiated — Phase 1 leaves product quarantine operator-initiated with tooling support.
