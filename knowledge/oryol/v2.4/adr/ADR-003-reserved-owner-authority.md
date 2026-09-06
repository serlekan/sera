# ADR-003: Reserved Organization Ownership Authority

**Status**: PROPOSED (Target: Architecture v2.4)
**Date**: 2026-09-06
**Author**: Deep Builder (`anthropic/claude-sonnet-5`)
**Scope**: Oryol Core Identity, Tenancy, and Authorization Engine — Organization Ownership Semantics
**Affected Documents**: `identity-model.md`, `authorization-model.md`, `audit-and-events.md`, `multi-tenancy.md` (unchanged; referenced)
**Predecessor Baseline**: Oryol Architecture v2.3 (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Originating Finding**: Independent principal review (`docs/reviews/ASTRA-PRINCIPAL-REVIEW-2026-09-06.md`, `serlekan/oryol-core` @ `docs/oryol-product-foundation-v1` HEAD `993a66aeb529c5bec4c115cc4aa3991bfd0f462e`), Oryol Core frozen candidate `932957c9176b1b2082d0fa62401c101f1f394c17`

---

## 1. Context

Oryol Architecture v2.3 establishes two independently correct rules that, taken together, are contradictory:

- **A — Privilege Escalation Ceiling (`authorization-model.md §6.3`, ADR-002 §3.3)**:
  > *"An actor cannot assign roles conferring permissions that the actor does not actively hold, unless the actor holds the immutable system template `Owner` role."*
  The general ceiling test is permission-set containment: an actor may assign a target role when the actor holds every permission conferred by that target role.

- **B — Owner as System Template with Intrinsic Powers (`authorization-model.md §5` Step 7, ADR-001 §3.4 Step 8.4/8.5, ADR-002 §3.4)**:
  Owner and Admin system templates receive *intrinsic* evaluation-time powers that are **not** enumerated as ordinary `permission_definitions` rows: Step 7 private-ACL bypass, `required_admins` MFA/IP applicability, and (for Owner) privilege-ceiling bypass itself.

### 1.1 The Contradiction (`OWNER_AUTHORITY_AMBIGUITY`)

If Owner's authority were fully expressible as an ordinary permission set `P_owner`, then by rule **A** any non-Owner actor who *holds* `P_owner` (for example, an Admin whose role was granted every currently-defined permission, or a custom role a prior Owner over-provisioned) could assign the Owner role to themselves or others. That actor would thereby acquire privilege-ceiling bypass, Step 7 ACL bypass, and last-Owner authority **without ever having been made an Owner through a reserved act**. The permission-set model cannot, by construction, gate a capability that is defined as "the absence of the gate".

Additionally, v2.3 `identity-model.md §3` invariant 1 (Last-Owner Protection) enumerates only *role removal, deactivation, downgrade*. It does not bind organization-ownership transfer, membership removal, bulk operations, or concurrent Owner changes into one atomic guarantee, leaving a race window where the last Owner can be removed.

---

## 2. Problem Statement

1. Permission-set equivalence must not be a path to acquiring or assigning `Owner`.
2. The set of actors who may assign, transfer, or remove `Owner` must be enumerated exactly.
3. `Admin` must be disambiguated: reserved authority, or a pure permission-defined template?
4. Last-Owner protection must be a single atomic invariant across every operation class that can reduce the active human Owner count.
5. An executable "active organization must have a human Owner" invariant must be stated, and service principals must be excluded from satisfying it.
6. The ownership-transfer transaction must be specified end to end.
7. Canonical error codes and adversarial concurrency requirements must be defined.

---

## 3. Decision

### 3.1 Owner is RESERVED ORGANIZATION AUTHORITY

`Owner` is a **reserved authority**, not a permission bundle. Its defining powers are **structural properties of the authorization engine and the ownership state machine**, not rows in `role_permissions`:

| Owner structural power | Where enforced in v2.3 (unchanged) |
|---|---|
| Privilege-ceiling bypass | `authorization-model.md §6.3` invariant 3; ADR-002 §3.3 |
| Step 7 private-resource ACL bypass | `authorization-model.md §5` Step 7 |
| `required_admins` MFA/IP applicability | ADR-001 §3.4 Sub-steps 8.4 / 8.5 |
| Reserved: assign / transfer / remove `Owner` | **This ADR §3.3** |
| Counts toward the last-human-Owner invariant | **This ADR §3.5 / §3.6** |

> [!IMPORTANT]
> **Reserved Authority Invariant (`RESERVED_OWNER_AUTHORITY`)**:
> The `Owner` reserved authority is conferred **only** by holding an active `membership_role_assignments` row to the organization's role whose `role_definitions` row has `is_system_template = TRUE AND template_key = 'owner'`. It is conferred by **no other means** — not by permission-set equivalence, not by explicit grant, not by delegation, not by cross-org grant, not by a custom role, and not by any `Admin` template.

### 3.2 Permission-Set Equivalence MUST NOT Confer or Assign Owner

Amends the v2.3 Privilege Escalation Ceiling (`authorization-model.md §6.3` invariant 3) with a **reserved-role carve-out** evaluated **before** the permission-set containment test:

```text
assignRole(actor, targetSubject, targetRoleId):
  targetRole := role_definitions[targetRoleId]

  # Reserved-authority gate (NEW — evaluated first, fail-closed)
  IF targetRole.is_system_template = TRUE AND targetRole.template_key = 'owner':
      IF NOT actorHoldsReservedOwnerAuthority(actor, organization):
          DENY(OWNER_ASSIGNMENT_REQUIRES_OWNER)
      # Owner→Owner assignment is permitted only via the §3.4 ownership-transfer transaction
      # or the additive co-Owner path in §3.3; never via the generic assignRole path.
      ROUTE to ownership-transfer / co-owner transaction (§3.3, §3.4)

  IF targetRole.is_system_template = TRUE AND targetRole.template_key = 'admin':
      # Admin is permission-defined (§3.4). Falls through to the ordinary ceiling test.

  # Ordinary privilege-escalation ceiling (v2.3 semantics, unchanged)
  IF actorHoldsReservedOwnerAuthority(actor, organization):
      ALLOW            # Owner bypasses the ceiling (v2.3)
  IF permissionSet(actor) ⊇ permissionSet(targetRole) resolved at the active registry version:
      ALLOW
  DENY(ERR_PRIVILEGE_ESCALATION_CEILING)
```

`actorHoldsReservedOwnerAuthority` is a direct D1 lookup of an active Owner-template `membership_role_assignments` row; it never inspects permission sets.

**Consequence**: An `Admin` (or any actor) holding the union of every currently-defined permission still receives `DENY(OWNER_ASSIGNMENT_REQUIRES_OWNER)` when attempting to assign `Owner`. The only actors who can produce a new Owner are existing Owners.

### 3.3 Who May Assign / Transfer / Remove Owner

| Operation | Authorized actor | Mechanism | Notes |
|---|---|---|---|
| **Assign `Owner`** (add a co-Owner) | An actor holding active reserved `Owner` authority in the same organization | Additive co-Owner transaction (§3.7) | Target MUST be an existing **active human** membership. Increases the active human Owner count; never decreases it. |
| **Transfer `Owner`** (move sole/primary ownership) | An actor holding active reserved `Owner` authority in the same organization | Ownership-transfer transaction (§3.4) | Source may relinquish only if the post-state proves ≥ 1 active human Owner remains. |
| **Remove `Owner`** (from a membership) | (a) An actor holding active reserved `Owner` authority removing **another** Owner, or (b) an Owner voluntarily relinquishing their **own** Owner authority | Owner-removal transaction (§3.7) | Rejected atomically if it would leave zero active human Owners (`LAST_OWNER_PROTECTION_VIOLATION`). |
| **Assign / remove `Owner` for a service principal** | Nobody | — | `service_principal_role_assignments` MUST reject any `role_id` whose `role_definitions` row is `template_key = 'owner'` (`OWNER_ASSIGNMENT_REQUIRES_OWNER`; a service principal can never be routed to the transfer/co-owner transactions). See §3.6. |

- **No platform / support / Anthropic actor** may assign, transfer, or remove `Owner` through the authorization engine. Break-glass tenant recovery (e.g. all Owners lost) is an **out-of-band operational runbook** governed by ADR-005 §Recovery and incident escalation, executed against Core D1 with dual-control and a mandatory immutable audit event `core.organization.ownership.break_glass`; it is explicitly **not** an `authorize()` path and is out of scope for Phase 1 automation.

### 3.4 Admin Has No Intrinsic Reserved Authority Beyond Enumerated Permissions

> [!IMPORTANT]
> **Model adopted (preferred model, no stronger reason found to deviate)**:
> - **`Owner` = reserved authority** (structural; §3.1).
> - **`Admin` = permission-defined system template** (a curated default bundle of `permission_definitions`; its powers are exactly its `role_permissions` rows at the active registry version).

Clarifications to v2.3 (no semantic change, made executable):

1. The v2.3 evaluation-time predicate `template_key IN ('owner', 'admin')` used in Step 7 ACL bypass and `required_admins` applicability is retained **verbatim**. It is a convenience recognition of the two default administrative templates for *contextual policy applicability and private-ACL bypass* — it is **not** a grant of reserved authority and confers **none** of the §3.1 reserved powers (ceiling bypass, ownership mutation, last-Owner standing).
2. `Admin` does **not** bypass the privilege-escalation ceiling. Only reserved `Owner` authority does (v2.3 §6.3, unchanged).
3. `Admin` does **not** count toward the last-human-Owner invariant (§3.5).
4. `Admin` cannot assign, transfer, or remove `Owner` (§3.2, §3.3).
5. Removing the last `Admin` is permitted (it is not a protected invariant); removing the last `Owner` is not.

### 3.5 Active Organization Invariant (Executable)

> [!IMPORTANT]
> **Last-Human-Owner Invariant (`ORG_MUST_HAVE_ACTIVE_HUMAN_OWNER`)**:
> For every organization whose `organizations.status = 'active'`, there MUST exist **at least one** membership `m` such that **all** of the following hold, evaluated against committed D1 state:
> 1. `m.status = 'active'`
> 2. `m.principal_id` references a `principals` row with `type = 'human'` and `status = 'active'`
> 3. there exists an active `membership_role_assignments` row `(m.organization_id, m.id, r.id)` where `role_definitions r` has `r.organization_id = m.organization_id AND r.is_system_template = TRUE AND r.template_key = 'owner'`

Formal count predicate used by every mutation in §3.7:

```sql
SELECT COUNT(*) AS active_human_owner_count
FROM membership_role_assignments mra
JOIN memberships m
  ON m.organization_id = mra.organization_id AND m.id = mra.membership_id
JOIN principals p
  ON p.id = m.principal_id
JOIN role_definitions r
  ON r.organization_id = mra.organization_id AND r.id = mra.role_id
WHERE mra.organization_id = :org
  AND m.status = 'active'
  AND p.type = 'human'
  AND p.status = 'active'
  AND r.is_system_template = 1
  AND r.template_key = 'owner';
```

The invariant holds **iff `active_human_owner_count >= 1`**. An organization transitioning `active -> suspended/archived/deletion_pending` is exempt while non-active; re-activation MUST re-prove the invariant in the same transaction.

### 3.6 Service Principals Never Satisfy the Invariant

1. `membership_role_assignments` binds only human principals (v2.3 `identity-model.md` trigger `trg_memberships_human_principal_insert_check`). Owner authority is therefore structurally unreachable for services already.
2. **New guard (belt-and-braces)**: `service_principal_role_assignments` MUST reject, at the mutation boundary and via a `BEFORE INSERT` trigger, any `role_id` whose `role_definitions` row has `template_key = 'owner'`:

```sql
CREATE TRIGGER trg_sra_reject_owner_template BEFORE INSERT ON service_principal_role_assignments
BEGIN
    SELECT RAISE(FAIL, 'OWNER_ASSIGNMENT_REQUIRES_OWNER: service principals may never hold the reserved Owner authority')
    FROM role_definitions r
    WHERE r.organization_id = NEW.organization_id
      AND r.id = NEW.role_id
      AND r.is_system_template = 1
      AND r.template_key = 'owner';
END;
```

3. The §3.5 count predicate filters `p.type = 'human'`, so even a hypothetical mis-bound service row cannot raise `active_human_owner_count`.

### 3.7 Owner-Class Mutations — Atomic Last-Owner Protection

Every operation below is evaluated inside **one** `db.batch([...])` D1 transaction. Each computes `active_human_owner_count` **as it would be after the mutation is applied** (post-image), asserts it via an in-batch `_owner_assert` checkpoint table (`CHECK(value = 1)`, identical mechanism to ADR-002 §7.4 Phase D), and rolls the whole transaction back on violation.

| Operation class | Post-image assertion | Canonical error on failure |
|---|---|---|
| Owner role removal (`core.rbac.role_removed` on an Owner-template role) | `post_count >= 1` | `LAST_OWNER_PROTECTION_VIOLATION` |
| Membership deactivation (`m.status -> 'suspended'`) of an Owner | `post_count >= 1` | `LAST_OWNER_PROTECTION_VIOLATION` |
| Membership removal (`m.status -> 'left'` / row delete) of an Owner | `post_count >= 1` | `LAST_OWNER_PROTECTION_VIOLATION` |
| Membership downgrade (replace Owner role assignment with a non-Owner role) | `post_count >= 1` | `LAST_OWNER_PROTECTION_VIOLATION` |
| Organization ownership transfer (§3.4) | `post_count >= 1` **and** destination is an active human Owner | `LAST_OWNER_PROTECTION_VIOLATION` / `OWNER_TRANSFER_TARGET_INVALID` |
| Bulk membership operation touching ≥ 1 Owner | `post_count >= 1` computed **once over the fully-applied batch** (not per row) | `LAST_OWNER_PROTECTION_VIOLATION` |
| Principal deactivation (`principals.status -> 'suspended'/'deactivated'`) of a human who is an Owner | `post_count >= 1` | `LAST_OWNER_PROTECTION_VIOLATION` |
| Concurrent Owner changes | serialization guard, §3.9 | `OWNER_MUTATION_CONFLICT` |

Co-Owner **addition** and ownership **transfer to a new Owner** are the only operations that may increase the count; they never need the `>= 1` guard to *block*, but they still run inside the same transaction with the audit + `authorization_versions` increment + outbox emission (v2.3 `audit-and-events.md §6.4`).

### 3.8 Ownership Transfer Transaction (Canonical)

`transferOwnership(actor, sourceMembershipId, destinationMembershipId, { relinquishSource: boolean })` — one atomic `db.batch`:

1. **Validate actor**: `actorHoldsReservedOwnerAuthority(actor, org)` — else `OWNER_ASSIGNMENT_REQUIRES_OWNER`.
2. **Validate source Owner**: `sourceMembershipId` is an active human membership currently holding active Owner authority in `org` — else `OWNER_TRANSFER_TARGET_INVALID`.
3. **Validate destination**: `destinationMembershipId` is an **active** membership in `org`, bound to an **active human** principal, `destination != source` — else `OWNER_TRANSFER_TARGET_INVALID`. Service-principal or suspended/left destinations are rejected here.
4. **Assign / confirm destination Owner**: upsert the active `membership_role_assignments` row binding the destination membership to the Owner-template role (idempotent; no-op with zero side effects if it already exists).
5. **Relinquish source (conditional)**: only if `relinquishSource = true`, remove the source membership's Owner-template `membership_role_assignments` row. If `false`, the result is an additional co-Owner and the source is retained.
6. **Prove invariant**: compute post-image `active_human_owner_count`; assert `>= 1` via `_owner_assert`. (With step 4 always adding/confirming an active human Owner, this is guaranteed unless step 4 was somehow a no-op against an inactive destination — which steps 3 rules out.)
7. **Authorization-version increment**: `authorization_versions.version` increment for `org` (invalidates cached tokens for both principals).
8. **Immutable audit event** `core.organization.ownership.transferred` (actor context, source, destination, `relinquishSource`) — v2.3 append-only semantics; transaction fails closed if the insert fails.
9. **Transactional outbox event** `core.organization.ownership.transferred` (v2.3 `aggregate_type = 'organization'`, monotonic `aggregate_version` per ADR-006).

All nine steps commit together or not at all. A partial transfer (destination assigned but source not relinquished, or version not bumped) is impossible.

### 3.9 Adversarial Concurrency Requirements

1. **Serialized Owner mutations per organization**: Every Owner-class mutation (§3.7) MUST take a per-organization logical lock before evaluating the post-image count. Implementation: a conditional `UPDATE organizations SET owner_mutation_seq = owner_mutation_seq + 1 WHERE id = :org AND owner_mutation_seq = :expected_seq` as the first batch statement; `affected_rows != 1` -> `OWNER_MUTATION_CONFLICT` and the batch aborts. This turns two concurrent "remove the other Owner" requests into one winner and one conflict — never both succeeding.
2. **Post-image, not pre-image**: The `>= 1` check is computed on state *after* every mutation in the batch is applied, so a batch that removes two Owners at once is evaluated as removing both.
3. **No TOCTOU between check and commit**: The count query, the `_owner_assert` insert, and the DDL/DML all execute inside the same `db.batch`; D1 commits the batch atomically, so no other writer can interleave.
4. **Bulk operations**: A bulk membership operation is a single batch; the invariant is proven once over the final applied state. Partial application of a bulk batch is not possible.
5. **Interaction with replay defense / security_version**: Owner mutation increments `authorization_versions` (authorization state), not `principal_security_versions` (credential/session state) — consistent with v2.3 separation. Session revocation of a removed Owner follows the normal `authorization_version` staleness path at high-risk endpoints and refresh.
6. **Idempotency**: Co-Owner add and destination-Owner assignment are idempotent (upsert semantics, zero side effects on no-op — mirrors ADR-002 §4 "No-Op Atomicity"). Owner removal of an already-removed assignment is a strict no-op: 0 rows, 0 version increment, 0 audit, 0 outbox.

---

## 4. Canonical Errors

| Code | Raised when |
|---|---|
| `LAST_OWNER_PROTECTION_VIOLATION` | Any §3.7 operation whose post-image `active_human_owner_count` would be `0` for an active organization. |
| `OWNER_ASSIGNMENT_REQUIRES_OWNER` | An actor lacking active reserved `Owner` authority attempts to assign the Owner-template role (including any service-principal Owner assignment). |
| `OWNER_TRANSFER_TARGET_INVALID` | Ownership transfer where the source is not a current active human Owner, or the destination is not an active human membership in the organization. |
| `OWNER_MUTATION_CONFLICT` | Concurrent Owner-class mutation lost the per-organization serialization race (`owner_mutation_seq` CAS failed). |

These codes are additive to the v2.3 authorization/identity error vocabulary; no v2.3 code is renamed or removed.

---

## 5. Schema & Migration Impact

- One new column: `organizations.owner_mutation_seq INTEGER NOT NULL DEFAULT 0` (serialization counter, §3.9).
- One new trigger: `trg_sra_reject_owner_template` on `service_principal_role_assignments` (§3.6).
- No change to `role_definitions`, `membership_role_assignments`, `service_accounts`, or any v2.3 table shape.
- Accepted migrations `0001`–`0005` remain **sealed and immutable**. The above is delivered in a new forward migration `0006_reserved_owner_authority.sql` (Phase 1 Slice 4+; **not implemented by this task**), governed by ADR-005 verification and quarantine rules.
- Backfill: `owner_mutation_seq` defaults to `0` for all existing organizations; no data transformation required. Migration 0006 preflight MUST assert `ORG_MUST_HAVE_ACTIVE_HUMAN_OWNER` for every active organization and abort with `ERR_MIGRATION_ORG_WITHOUT_HUMAN_OWNER` if any active organization already violates it (surfacing pre-existing corruption rather than sealing over it).

---

## 6. Alternatives Considered & Rejected

1. **Model Owner as an ordinary permission `core.organization.own`**: *Rejected* — it re-creates the contradiction (any holder of that permission set could self-assign), and it cannot express "bypass the assignment gate".
2. **Make Admin also a reserved authority**: *Rejected* — no finding requires it; it would break the v2.3 permission-defined Admin template and the ability to tailor Admin per organization. The review disposition explicitly prefers `Owner = reserved`, `Admin = template`.
3. **Last-Owner check as a `BEFORE DELETE` trigger only**: *Rejected* — triggers cannot see the full post-image of a multi-statement batch (downgrade + deactivate in one batch), and cannot express the human/active/status join cleanly across the transfer case. The in-batch `_owner_assert` checkpoint (ADR-002 precedent) evaluates the true post-image.
4. **Optimistic retry on `OWNER_MUTATION_CONFLICT` inside Core**: *Deferred* — Phase 1 returns the conflict to the caller; automatic retry risks masking a genuine adversarial double-remove. Clients may retry after re-reading state.

---

## 7. Decisions / Invariants / Open Questions

### Decisions
- D1: `Owner` is reserved authority conferred solely by an active Owner-template `membership_role_assignments` row (§3.1).
- D2: Permission-set equivalence never confers or assigns `Owner`; a reserved-authority gate precedes the ceiling test (§3.2).
- D3: Only existing Owners may assign/transfer/remove `Owner`; no platform actor may, except an out-of-band dual-control break-glass runbook (§3.3).
- D4: `Admin` = permission-defined template with zero intrinsic reserved authority; the v2.3 `template_key IN ('owner','admin')` contextual predicate is retained but grants no reserved power (§3.4).
- D5: Ownership transfer is the canonical 9-step atomic transaction of §3.8.

### Invariants
- I1 `RESERVED_OWNER_AUTHORITY` (§3.1).
- I2 `ORG_MUST_HAVE_ACTIVE_HUMAN_OWNER` — `active_human_owner_count >= 1` for every active organization, by the §3.5 predicate.
- I3 Service principals never raise `active_human_owner_count`; `trg_sra_reject_owner_template` + human-only filter (§3.6).
- I4 Last-Owner protection is atomic and post-image across removal, deactivation, removal, downgrade, transfer, bulk ops, principal deactivation, and concurrent changes (§3.7).
- I5 Per-organization Owner-mutation serialization via `owner_mutation_seq` CAS (§3.9).
- I6 Every Owner-class mutation emits, in the same transaction: `authorization_versions` increment + immutable audit event + outbox event (§3.7–3.8).

### Open Questions
- OQ1: Break-glass Owner recovery automation (currently runbook-only) — defer to a dedicated operational ADR once ADR-005 recovery tooling exists.
- OQ2: Whether co-Owner count should have an upper bound per organization (Phase 1: unbounded).
- OQ3: Cross-organization ownership (holding company / org groups) — explicitly out of scope for Phase 1; revisit with `organization_placement` work.
