# Oryol Core — Audit, Outbox & Event-Driven Architecture v2.4 (Delta over v2.3)

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.4) — Subject to Independent Architecture Review
**Predecessor**: [`../v2.3/audit-and-events.md`](../v2.3/audit-and-events.md) (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Revision Scope**: Executable `aggregate_version` source/allocation contract, filtered-consumer gap policy, command-scoped idempotency (ADR-006); ownership-transfer and migration-verification audit actions (ADR-003, ADR-005). **No `outbox_events` / `inbox_events` / `audit_events` table shape change.**

---

## 1. Carry-Forward Declaration

All of [`../v2.3/audit-and-events.md`](../v2.3/audit-and-events.md) is carried forward **unchanged** except §2–§4 below. Retained verbatim:

- §1 Distinct Roles: Audit vs. Outbox vs. Observability — **unchanged**.
- §2 Canonical Transactional Outbox Schema (`outbox_events`, `idx_outbox_eligibility`) — **unchanged schema**.
- §3 Dispatcher Claim Algorithm & Expired Lease Recovery — **unchanged**.
- §4.1 Transport & Ordering Rules three-way comparison, `blocked_on_gap`, 5-minute replay window, DLQ, operational alert — **unchanged** (now evaluated on the per-consumer contiguous sequence for filtered consumers, §3).
- §4.2 Schema Evolution — **unchanged**.
- §5 Atomic Inbox Semantics & Deduplication (`inbox_events`, `UNIQUE(consumer_name, event_id)`, effectively-once via atomic side-effect + marker) — **unchanged**.
- §6 Phase 1 Permanent Audit Retention, Privacy Overlays & Legal Holds, all triggers (`trg_audit_no_update`, `trg_audit_no_delete`), §6.3 Multi-Layer Append-Only Enforcement, §6.4 Atomic Security Mutations — **unchanged**.

> [!IMPORTANT]
> **No regression**: zero in-place audit updates, no physical purge in Phase 1, non-cascading audit logs, append-only privacy overlays, legal-hold survival, and atomic security mutations (business mutation + version increment + audit insert + outbox in one transaction) are all preserved exactly as accepted in v2.3.

---

## 2. v2.4 Amendment — `aggregate_version` source & allocation (ADR-006)

v2.3 defines `outbox_events.aggregate_version INTEGER NOT NULL` as "monotonic entity version for ordering" and gives `idempotency_key` the *example* `evt_<agg_id>_<ver>`, but does not pin the model or the allocation transaction. **[ADR-006](adr/ADR-006-event-version-semantics.md) pins Model B:**

> [!IMPORTANT]
> **`EVENT_VERSION_MODEL` (Model B, never mixed)**: `aggregate_version` is the **per-aggregate event-stream sequence number**, allocated by the event store at append time — **not** the domain-state row version, and there is **no** separate global event sequence.
>
> - **Aggregate identity**: `(organization_id, aggregate_type, aggregate_id)`; `aggregate_id` is the stable business key, never reused.
> - **Authoritative source**: new local table `aggregate_event_sequences(organization_id, aggregate_type, aggregate_id, last_version)` in the producing service's database.
> - **`EVENT_VERSION_ALLOCATION`**: every emitted event's `aggregate_version` is allocated inside the **same `db.batch`** as the domain mutation and the `outbox_events` insert, via a CAS bump of `aggregate_event_sequences` (`... SET last_version = last_version + 1 WHERE ... AND last_version = :expected`). CAS loss → whole batch rolls back → caller retries. **Hard-coded / client-supplied `aggregate_version` is prohibited** (resolves implementation finding P1-09).
> - **`ONE_EVENT_ONE_VERSION`**: each externally observable aggregate mutation emits exactly one `outbox_events` row and consumes exactly one version increment. A batch touching N aggregates emits N events, each with that aggregate's own next version.
> - Domain-state `version` columns (e.g. `organization_security_policies.version`) are **independent** of `aggregate_version` and MUST NOT be assumed equal.

Per-aggregate source streams are therefore **gap-free and strictly increasing**; any gap a consumer observes is transport loss or filtering (§3).

---

## 3. v2.4 Amendment — Filtered consumers & gap policy (ADR-006 §3)

> [!IMPORTANT]
> **`CONSUMER_STREAM_SEQUENCE`**: gap detection is performed on a **producer-stamped, per-consumer, per-aggregate contiguous sequence** (`consumer_stream_seq`), not on the raw `aggregate_version`. A consumer that does not subscribe to every event type of an aggregate MUST NOT treat skipped `aggregate_version` values as transport loss.

- The dispatcher maintains `consumer_delivery_sequences(consumer_name, organization_id, aggregate_type, aggregate_id, last_stream_seq, last_source_version)` in the producer and stamps each delivered envelope with `consumer_stream_seq` + `prev_consumer_stream_seq` + `epoch` (payload envelope fields — **not** new table columns on `outbox_events`).
- v2.3 §4.1's three-way comparison now runs on `consumer_stream_seq` for filtered consumers and identically on `aggregate_version` for full-fanout (`*`) consumers (`consumer_stream_seq == aggregate_version` in that case).
- Subscription-matrix changes bump `epoch` with a documented backfill boundary; pre-boundary absence is never "loss".

---

## 4. v2.4 Amendment — Idempotency (ADR-006 §4)

> [!IMPORTANT]
> **`EVENT_IDEMPOTENCY_KEY`**: `outbox_events.idempotency_key` MUST be a deterministic function of the authoritative mutation:
> `sha256(organization_id || ':' || aggregate_type || ':' || aggregate_id || ':' || event_type || ':' || allocated_aggregate_version)`.
> Client-supplied command tokens bind via a domain `command_idempotency(organization_id, command_scope, client_token, aggregate_id, aggregate_version)` table with `UNIQUE(organization_id, command_scope, client_token)`; a duplicate submission rolls the batch back and returns the already-recorded result.
> **Prohibited**: a freshly generated `event_id` + `occurred_at = now()` as the sole conceptual idempotency guarantee for a retried business mutation.

---

## 5. v2.4 Amendment — New audit actions (additive)

| Action | Origin |
|---|---|
| `core.organization.ownership.transferred` | ADR-003 §3.8 ownership-transfer transaction |
| `core.organization.ownership.break_glass` | ADR-003 §3.3 out-of-band dual-control Owner recovery |
| `core.db.time_travel_restore` | ADR-005 §7.2 operator restore under dual control |
| `core.session.successor_recovered` | ADR-004 §6.6 bounded successor re-delivery (low severity) |

All follow v2.3 §6.4 atomicity: the action's business mutation, `authorization_versions` increment where applicable, immutable audit insert, and outbox emission commit in one transaction or the transaction fails closed.

---

## 6. Schema & Migration Impact

New **local** producer tables: `aggregate_event_sequences`, `consumer_delivery_sequences`, `command_idempotency`. **No change** to `outbox_events`, `inbox_events`, `audit_events`, `audit_redactions`, or `audit_legal_holds` table shapes. Backfill of `aggregate_event_sequences.last_version` from `MAX(aggregate_version)` per aggregate over existing `outbox_events`, executed under the ADR-005 §6 migration fence and verified as an ADR-005 §5.2 postcondition. Migrations `0001`–`0005` unaffected and sealed. Delivered in forward migration `0006`/product equivalents (Phase 1 Slice 4+; not implemented by this task).
