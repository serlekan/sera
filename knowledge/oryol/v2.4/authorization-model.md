# Oryol Authorization Policy Algebra v2.4 (Delta over v2.3)

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.4) — Subject to Independent Architecture Review
**Predecessor**: [`../v2.3/authorization-model.md`](../v2.3/authorization-model.md) (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Revision Scope**: Reserved Owner authority carve-out in the privilege-escalation ceiling (ADR-003). All eight evaluation steps, entity schemas, deny grammar, and Step 8 contextual ABAC are otherwise carried forward verbatim.

---

## 1. Carry-Forward Declaration

The entire v2.3 authorization model is carried forward **unchanged** except §2 below:

- The `authorize({ principal, membership, organization, action, resource, context })` contract and all TypeScript interfaces — **unchanged**.
- §2 Authoritative Security & Policy Entities (all 13 table definitions) — **unchanged**.
- §3 Active Registry Invariant & Migration Semantics — **unchanged**.
- §4 Service-to-Application Entitlement Mapping — **unchanged**.
- §5 Mandatory 8-Step Evaluation Algebra, Steps 1–8 including every Step 8 sub-step (8.1–8.7), all `DENY(...)` codes, explicit-deny grammar, internal-execution sentinel handling — **unchanged**.
- §6 Hierarchy, Inheritance & Context Invariants 1, 2, 4 — **unchanged**.

> [!IMPORTANT]
> **No regression**: v2.4 preserves explicit-deny precedence, mandatory Step 6 RBAC, service-principal RBAC, resource-ACL sequencing, delegation constraints, cross-org constraints, Step 8 contextual policy, trusted-edge semantics, `authorization_version` / `security_version` separation, deny-pattern grammar, and dual-signal system-template validation (`is_system_template = TRUE AND template_key IN ('owner', 'admin')`) exactly as accepted in v2.3.

---

## 2. v2.4 Amendment — §6 Invariant 3 (Privilege Escalation Ceiling)

v2.3 §6 Invariant 3 read:

> *"An actor cannot assign roles conferring permissions that the actor does not actively hold, unless the actor holds the immutable system template `Owner` role (`rd.is_system_template = TRUE AND rd.template_key = 'owner'`)."*

**v2.4 prepends a reserved-authority gate, evaluated BEFORE the permission-set containment test** ([ADR-003 §3.2](adr/ADR-003-reserved-owner-authority.md)):

```text
assignRole(actor, targetSubject, targetRoleId):
  targetRole := role_definitions[targetRoleId]

  # (NEW v2.4) Reserved-authority gate — fail-closed, evaluated first:
  IF targetRole.is_system_template = TRUE AND targetRole.template_key = 'owner':
      IF NOT actorHoldsReservedOwnerAuthority(actor, organization):   # direct D1 lookup; never inspects permission sets
          DENY(OWNER_ASSIGNMENT_REQUIRES_OWNER)
      ROUTE to the ownership-transfer / co-Owner transaction (ADR-003 §3.7, §3.8)
      # the generic assignRole path never produces a new Owner

  # (v2.3, unchanged) ordinary privilege-escalation ceiling:
  IF actorHoldsReservedOwnerAuthority(actor, organization):
      ALLOW                                   # Owner bypasses the ceiling
  IF permissionSet(actor) ⊇ permissionSet(targetRole) @ active registry version:
      ALLOW
  DENY(ERR_PRIVILEGE_ESCALATION_CEILING)
```

### 2.1 Consequences

1. An `Admin` — or any actor — holding the union of every currently-defined permission still receives `DENY(OWNER_ASSIGNMENT_REQUIRES_OWNER)` when attempting to assign the `Owner` role. **Permission-set equivalence is never a path to `Owner`.**
2. `Admin` (`template_key = 'admin'`) continues to fall through to the ordinary ceiling test — it has **no intrinsic reserved authority beyond its enumerated permissions** (ADR-003 §3.4). `Admin` does **not** bypass the ceiling; only reserved `Owner` authority does (v2.3 semantics, retained).
3. The v2.3 evaluation-time predicate `template_key IN ('owner', 'admin')` used in **Step 7** (private-resource ACL bypass) and **Step 8.4 / 8.5** (`required_admins` MFA / IP applicability) is retained **verbatim**. It recognizes the two default administrative templates for contextual applicability and ACL bypass; it confers **none** of the reserved powers (ceiling bypass, Owner mutation, last-Owner standing).

### 2.2 New canonical error codes (additive)

`OWNER_ASSIGNMENT_REQUIRES_OWNER`, `OWNER_TRANSFER_TARGET_INVALID`, `LAST_OWNER_PROTECTION_VIOLATION`, `OWNER_MUTATION_CONFLICT` — see [ADR-003 §4](adr/ADR-003-reserved-owner-authority.md). No v2.3 code is renamed or removed.

---

## 3. Interaction with Session & Event Semantics

- Owner-class mutations increment `authorization_versions.version` for the organization (v2.3 `audit-and-events.md §6.4`), which propagates to session validation exactly as any other authorization change ([ADR-004 §5.4](adr/ADR-004-session-policy-and-refresh-recovery.md)).
- The ownership-transfer audit + outbox emission follows [ADR-006](adr/ADR-006-event-version-semantics.md) aggregate-version allocation (`aggregate_type = 'organization'`).
