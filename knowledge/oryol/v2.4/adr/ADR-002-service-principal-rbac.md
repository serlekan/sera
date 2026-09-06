# ADR-002-service-principal-rbac — v2.4 (Carried Forward Unchanged from v2.3)

**Status**: ACCEPTED under Architecture v2.3; carried forward unchanged into PROPOSED v2.4.
**Authoritative text**: [`../../v2.3/adr/ADR-002-service-principal-rbac.md`](../../v2.3/adr/ADR-002-service-principal-rbac.md)

---

Architecture v2.4 does not reopen this ADR. Its decisions, invariants, schema, and the Migration 0005 contract (ADR-002 §7) remain authoritative exactly as accepted under Architecture v2.3.

v2.4 ADRs that build on it:
- **ADR-003** extends the ADR-002 §3.3 privilege-escalation ceiling with a reserved-`Owner` carve-out and adds `trg_sra_reject_owner_template`.
- **ADR-005** wraps the ADR-002 §7 Migration 0005 three-phase boundary in a durable verification-state machine, evidence binding, and a write fence.
- **ADR-006** pins the `aggregate_version` model referenced by ADR-002 §4 mutation atomicity.
