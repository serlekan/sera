# Oryol Identity Architecture v2.4 — Canonical Principal Model (Delta over v2.3)

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.4) — Subject to Independent Architecture Review
**Predecessor**: [`../v2.3/identity-model.md`](../v2.3/identity-model.md) (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Revision Scope**: Reserved Organization Ownership Authority (ADR-003). No other identity semantics change.

---

## 1. Carry-Forward Declaration

All of [`../v2.3/identity-model.md`](../v2.3/identity-model.md) is carried forward **unchanged** for v2.4 except where §2 below amends it. The v2.3 relational schema (`principals`, `users`, `credentials`, `identity_provider_bindings`, `recovery_methods`, `service_accounts`, `api_credentials`, `invitations`, `organization_service_principals`, `service_principal_role_assignments`), the strict binary principal taxonomy, all triggers, and all six Core Identity Invariants remain authoritative verbatim.

---

## 2. v2.4 Amendments

### 2.1 Invariant 1 (Last-Owner Protection) — superseded by ADR-003

v2.3 Invariant 1 read:

> *"An organization membership holding the `Owner` role cannot be removed, deactivated, or downgraded if it is the sole remaining active Owner of an active Organization (`LAST_OWNER_PROTECTION_VIOLATION`)."*

**v2.4 replaces this with the atomic, post-image invariant defined in [ADR-003 §3.5, §3.7](adr/ADR-003-reserved-owner-authority.md):**

> [!IMPORTANT]
> **`ORG_MUST_HAVE_ACTIVE_HUMAN_OWNER`**: For every organization with `organizations.status = 'active'`, the count `active_human_owner_count` (ADR-003 §3.5 predicate — active membership, active human principal, active assignment to the `is_system_template = TRUE AND template_key = 'owner'` role) MUST be `>= 1`.
>
> Last-Owner protection is enforced **atomically and on the post-image** across **all** of: role removal, membership deactivation, membership removal, membership downgrade, organization ownership transfer, bulk membership operations, human-principal deactivation, and concurrent Owner changes (serialized via `organizations.owner_mutation_seq`). Canonical errors: `LAST_OWNER_PROTECTION_VIOLATION`, `OWNER_ASSIGNMENT_REQUIRES_OWNER`, `OWNER_TRANSFER_TARGET_INVALID`, `OWNER_MUTATION_CONFLICT`.

### 2.2 New Invariant 7 — Reserved Owner Authority

> [!IMPORTANT]
> **Invariant 7 — `RESERVED_OWNER_AUTHORITY` (ADR-003 §3.1–3.3)**:
> The `Owner` role is **reserved organization authority**, not a permission bundle. It is conferred **only** by an active `membership_role_assignments` row to the organization's `template_key = 'owner'` system-template role — never by permission-set equivalence, explicit grant, delegation, cross-org grant, custom role, or the `Admin` template. Permission-set equivalence MUST NOT permit a non-Owner to acquire or assign `Owner`. Only an existing Owner may assign, transfer, or remove `Owner`.

### 2.3 New Invariant 8 — Service Principals Never Own

> [!IMPORTANT]
> **Invariant 8 (ADR-003 §3.6)**: Service principals MUST NEVER satisfy the organization's last-human-Owner invariant. `service_principal_role_assignments` rejects any `role_id` whose `role_definitions` row is `template_key = 'owner'` (trigger `trg_sra_reject_owner_template` + mutation-boundary check, error `OWNER_ASSIGNMENT_REQUIRES_OWNER`). The `active_human_owner_count` predicate filters `principals.type = 'human'`.

### 2.4 Schema delta (delivered by forward migration `0006_reserved_owner_authority.sql`; not implemented by this task)

- `organizations.owner_mutation_seq INTEGER NOT NULL DEFAULT 0` — per-organization Owner-mutation serialization counter (ADR-003 §3.9).
- `trg_sra_reject_owner_template` `BEFORE INSERT` trigger on `service_principal_role_assignments` (ADR-003 §3.6).
- No change to any v2.3 identity table shape. Migrations `0001`–`0005` remain sealed.

---

## 3. Ownership Transfer

The canonical ownership-transfer transaction (validate source Owner → validate destination active human membership → assign/confirm destination Owner → conditionally relinquish source → prove ≥ 1 active human Owner remains → `authorization_versions` increment → immutable audit → outbox, all atomically) is specified in [ADR-003 §3.8](adr/ADR-003-reserved-owner-authority.md). The audit action is `core.organization.ownership.transferred`.
