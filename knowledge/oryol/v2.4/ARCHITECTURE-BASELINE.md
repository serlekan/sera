# Oryol Workspace Architecture Baseline v2.4

**Version**: 2.4
**Status**: PROPOSED ARCHITECTURE BASELINE (v2.4) — Awaiting Independent Architecture Review
**Date**: 2026-09-06
**Ecosystem**: Oryol Workspace Platform (`serlekan/sera`, `serlekan/oryol-mail`, `serlekan/oryol-core`)
**SERA Governance Version (unchanged, ACTIVE)**: `0.4.2`
**Previous Baseline Version**: `2.3`
**Previous Baseline Accepted Specification SHA**: `bc3df742d16f3a49b53f417482ae328f8f053264`
**Previous Baseline Activation SHA**: `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`
**Accepted Specification SHA (v2.4)**: Assigned externally after all three review gates approve the same frozen proposal
**Oryol Core frozen candidate exposing the findings**: `932957c9176b1b2082d0fa62401c101f1f394c17`
**Astra principal review disposition**: `serlekan/oryol-core` `docs/reviews/ASTRA-PRINCIPAL-REVIEW-2026-09-06.md` (branch `docs/oryol-product-foundation-v1`, HEAD `993a66aeb529c5bec4c115cc4aa3991bfd0f462e`)

---

## 1. Authoritative Governance Declaration

> [!IMPORTANT]
> **Strict Proposal Isolation**:
> Architecture v2.4 is **PROPOSED**. It has **no effect on SERA runtime behavior**. The active architecture baseline remains **v2.3** (`ORYOL_ARCHITECTURE_BASELINE_VERSION = "2.3"`, `ORYOL_ARCHITECTURE_SPEC_SHA = "bc3df742d16f3a49b53f417482ae328f8f053264"`). Builder and reviewer packets continue to embed the v2.3 provenance triple.
>
> **Strict Implementation Gate**:
> No Oryol Core implementation of v2.4 changes may begin, and no Slice 4 work may start, until the independent architecture review returns `APPROVED FOR IMPLEMENTATION` from **all three** configured gates (GPT-6 Astra principal review, `anthropic/claude-opus-5` independent review, configured OpenAI release gate) against **one exact frozen proposal commit + tree**.
>
> Architecture v2.4 is the **minimum** revision needed to resolve four validated findings from the independent principal review. It does not redesign unrelated Oryol architecture and does not reopen accepted v2.3 decisions without evidence. All unchanged v2.3 semantics are preserved.

### 1.1 Predecessor Immutability

The v2.3 corpus (`knowledge/oryol/v2.3/`) is historical accepted evidence and MUST have **zero diff** versus activation commit `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`. It is not modified by this revision.

---

## 2. Validated Findings Resolved by v2.4

| ADR | Finding | Resolution |
|---|---|---|
| **ADR-003** | Reserved Organization Ownership Authority — contradiction between the privilege-ceiling permission-set equivalence rule and `Owner` as a system-template authority with intrinsic powers (incl. ceiling bypass). Last-Owner protection not atomic across all operation classes. | `Owner` = reserved authority (structural, not a permission bundle); permission-set equivalence never confers/assigns `Owner`; exact enumeration of who may assign/transfer/remove `Owner`; `Admin` = permission-defined template with no intrinsic reserved authority; atomic post-image `ORG_MUST_HAVE_ACTIVE_HUMAN_OWNER` invariant across removal/deactivation/downgrade/transfer/bulk/concurrent; canonical ownership-transfer transaction; canonical errors; adversarial concurrency via `owner_mutation_seq` serialization. |
| **ADR-004** | Session Policy Enforcement & Refresh Recovery — `organization_security_policies` session semantics under-specified: no activity-update rule (write amplification risk), no absolute-timeout anchor, "strictest policy" cross-tenant coupling, refresh-rotation vs. org-token-issuance delivery failure. | Three named session layers (global identity / org authorization / refresh family) with decoupling invariant; idle = `now - last_active_at` with a 300 s rate-limited heartbeat; absolute anchored to immutable `created_at` with a fixed 30-day global cap + per-org ceiling at mint time; multi-org principal resolved without cross-tenant coupling; rotation ≠ exchange ≠ delivery; predictable failures before consuming the refresh row; separate `org-token` endpoint; bounded 120 s successor recovery with replay defense intact; 9-point authoritative check for sensitive operations. |
| **ADR-005** | Migration Verification, Quarantine & Recovery — postcondition failure blocks routing but not durably; verification evidence not bound to a database identity; preflight↔DDL TOCTOU; no post-restore reconciliation. | Persistent 6-state `schema_migration_state` machine; routing eligible only in `VERIFIED` with a matching 10-field evidence hash, re-checked at cold start, never synthesized; exact canonical migration id + `sha256` (no `LIKE`/glob); semantic postconditions over FKs/compound keys/unique+partial indexes/trigger fingerprints/existing-row invariants with isolated-fixture behavioral probes; migration fence = lease + maintenance-mode quiescence + in-batch re-assertion; forward-only recovery; `RESTORE_DOES_NOT_REVERSE_EXTERNAL_EFFECTS`; Sev-1 escalation. |
| **ADR-006** | Event Aggregate Version Semantics — monotonic `aggregate_version` required but source/allocation contract not executable; filtered consumers misread unconsumed versions as transport loss; idempotency tied to `random_id + now()`. | Model B (per-aggregate event-stream sequence), never mixed; authoritative source `aggregate_event_sequences`; CAS allocation inside the mutation transaction; hard-coded versions prohibited (P1-09); `ONE_EVENT_ONE_VERSION`; per-consumer contiguous `consumer_stream_seq` for gap detection; deterministic command-scoped idempotency key + `command_idempotency` table. |

---

## 3. Canonical Architecture v2.4 Document Registry

The following **21** documents constitute the proposed Architecture v2.4 baseline. Documents marked **[carried forward]** contain no v2.4 semantic change and reference the v2.3 predecessor; documents marked **[delta]** amend the v2.3 predecessor and cite the governing ADR; documents marked **[new]** are introduced by v2.4.

### 3.1 Canonical documents

| # | Document | Kind | Notes |
|---|---|---|---|
| 1 | [`workspace-architecture.md`](workspace-architecture.md) | carried forward | ← [`../v2.3/workspace-architecture.md`](../v2.3/workspace-architecture.md) |
| 2 | [`core-boundaries.md`](core-boundaries.md) | carried forward | ← [`../v2.3/core-boundaries.md`](../v2.3/core-boundaries.md) |
| 3 | [`multi-tenancy.md`](multi-tenancy.md) | carried forward | ← [`../v2.3/multi-tenancy.md`](../v2.3/multi-tenancy.md) |
| 4 | [`identity-model.md`](identity-model.md) | delta | ADR-003: reserved Owner authority, last-human-Owner invariant, service-principal Owner prohibition |
| 5 | [`authorization-model.md`](authorization-model.md) | delta | ADR-003: reserved-authority carve-out in the §6 privilege-escalation ceiling; all 8 evaluation steps otherwise verbatim |
| 6 | [`session-security.md`](session-security.md) | delta | ADR-004: executable §8 session-policy enforcement, timeout anchors, refresh recovery, sensitive-op authoritative check; zero table change |
| 7 | [`audit-and-events.md`](audit-and-events.md) | delta | ADR-006: `aggregate_version` Model B + allocation + filtered-consumer gap policy + idempotency; ADR-003/004/005 audit actions; zero table change |
| 8 | [`cloudflare-platform.md`](cloudflare-platform.md) | carried forward | ← [`../v2.3/cloudflare-platform.md`](../v2.3/cloudflare-platform.md) |
| 9 | [`data-lifecycle.md`](data-lifecycle.md) | delta | ADR-005: durable migration verification state + recovery/reconciliation; deletion pipeline verbatim |
| 10 | [`ai-platform.md`](ai-platform.md) | carried forward | ← [`../v2.3/ai-platform.md`](../v2.3/ai-platform.md) |
| 11 | [`search-platform.md`](search-platform.md) | carried forward | ← [`../v2.3/search-platform.md`](../v2.3/search-platform.md) |
| 12 | [`product-integration.md`](product-integration.md) | carried forward | ← [`../v2.3/product-integration.md`](../v2.3/product-integration.md) |
| 13 | [`sera-governance.md`](sera-governance.md) | delta | PROPOSED vs. ACTIVE baseline isolation; `.sera/` layout + detection + fail-closed enforcement verbatim |
| 14 | [`predecessor-schema-manifest.md`](predecessor-schema-manifest.md) | carried forward | ← [`../v2.3/predecessor-schema-manifest.md`](../v2.3/predecessor-schema-manifest.md) |

### 3.2 Architectural Decision Records

| # | ADR | Kind |
|---|---|---|
| 15 | [`adr/ADR-001-step8-security-policy.md`](adr/ADR-001-step8-security-policy.md) | carried forward (accepted v2.3) |
| 16 | [`adr/ADR-002-service-principal-rbac.md`](adr/ADR-002-service-principal-rbac.md) | carried forward (accepted v2.3; incl. Migration 0005 contract §7) |
| 17 | [`adr/ADR-003-reserved-owner-authority.md`](adr/ADR-003-reserved-owner-authority.md) | new |
| 18 | [`adr/ADR-004-session-policy-and-refresh-recovery.md`](adr/ADR-004-session-policy-and-refresh-recovery.md) | new |
| 19 | [`adr/ADR-005-migration-verification-and-recovery.md`](adr/ADR-005-migration-verification-and-recovery.md) | new |
| 20 | [`adr/ADR-006-event-version-semantics.md`](adr/ADR-006-event-version-semantics.md) | new |

### 3.3 Supporting

| # | Document | Kind |
|---|---|---|
| 21 | [`IMPLEMENTATION-OBLIGATIONS.md`](IMPLEMENTATION-OBLIGATIONS.md) | new — records P1-01, P1-02, P1-06, P1-07, P1-09 as implementation obligations without new semantics |

---

## 4. Historical Findings Preserved (No Regression)

Architecture v2.4 does **not** regress any of the following v2.3 / v2.2 decisions:

explicit deny precedence · Step 6 mandatory RBAC · service principal RBAC · resource ACL sequencing · delegation constraints · cross-org constraints · Step 8 contextual policy · trusted-edge semantics · `authorization_version` / `security_version` separation · service-account tenant ownership (compound `(organization_id, principal_id)`, immutable in Phase 1) · deny-pattern grammar (`^[a-z0-9_-]+(\.[a-z0-9_-]+)+$` exact, `^[a-z0-9_-]+\.\*$` wildcard) · Migration 0005 shadow parity (7-phase reconstruction, in-batch assertions) · invitation integrity (`UNIQUE(organization_id, email, status)`, compound tenant FKs, `member_type` preservation) · system-template dual signals (`is_system_template = TRUE AND template_key IN ('owner', 'admin')`) · no client-controlled system templates.

The v2.4 governance tests (`tests/test_oryol_v2_governance.py`, class `TestOryolV24ProposedArchitecture`) assert v2.3 is frozen and the above are not weakened.

---

## 5. Architecture Revision History

- **2026-08-26**: Architecture v2.2 accepted (`e59f28a1abc0392fe3f38ecfe3a3fde8e379c033`).
- **2026-09-04**: Architecture v2.3 accepted (`bc3df742d16f3a49b53f417482ae328f8f053264`) and activated (`78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`) following dual assured reviews.
- **2026-09-06**: Oryol Core frozen candidate `932957c9176b1b2082d0fa62401c101f1f394c17` independent principal review (Astra) recorded validated findings.
- **2026-09-06**: Proposed Architecture v2.4 drafted — ADR-003 (reserved Owner authority), ADR-004 (session policy & refresh recovery), ADR-005 (migration verification & recovery), ADR-006 (event version semantics). Status: **PROPOSED ARCHITECTURE BASELINE — awaiting independent review**. SERA runtime active version unchanged (v2.3). Oryol Core unchanged. Slice 4 not started.

---

## 6. Validation Sequence (post-draft)

1. Run architecture governance tests (`tests/test_oryol_v2_governance.py`).
2. Run the full SERA test suite (`python -m unittest discover -s tests`).
3. Record the exact proposal HEAD and TREE; hard-freeze the proposal.
4. Independent review, in order: (a) GPT-6 Astra principal architecture review; (b) `anthropic/claude-opus-5` independent architecture review; (c) configured OpenAI release gate.
5. Activate **only** if all required gates approve the **same** exact proposal.
