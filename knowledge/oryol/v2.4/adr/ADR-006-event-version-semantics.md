# ADR-006: Event Aggregate Version Semantics

**Status**: PROPOSED (Target: Architecture v2.4)
**Date**: 2026-09-06
**Author**: Deep Builder (`anthropic/claude-sonnet-5`)
**Scope**: Oryol Core & product event emission, `outbox_events` / `inbox_events`, aggregate versioning, consumer gap detection, idempotency
**Affected Documents**: `audit-and-events.md`, `product-integration.md` (unchanged; referenced)
**Predecessor Baseline**: Oryol Architecture v2.3 (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Originating Finding**: Independent principal review (`docs/reviews/ASTRA-PRINCIPAL-REVIEW-2026-09-06.md`) — `audit-and-events.md` requires a monotonic `aggregate_version` and interprets `received_version > expected_version` as possible transport loss, but does not define where the version comes from, how it is allocated transactionally, or how a **filtered** consumer distinguishes an intentionally-unconsumed version from a lost one. Also flags `P1-09`: hard-coded `aggregate_version` in implementation.

---

## 1. Context

v2.3 `audit-and-events.md`:
- `outbox_events.aggregate_version INTEGER NOT NULL` — "Monotonic entity version for ordering".
- `idempotency_key TEXT UNIQUE NOT NULL` — "Dedup key (e.g. `evt_<agg_id>_<ver>`)".
- §4.1: `received_version == expected_version` → process; `<` → duplicate/stale; `>` → **forward gap → `blocked_on_gap`**, replay missing versions, DLQ after 5 min.
- `inbox_events.aggregate_version INTEGER NOT NULL`, `UNIQUE(consumer_name, event_id)`.

Three under-specifications:

1. **Allocation contract.** What is `aggregate_version` a version *of*? The domain row? The event stream? Who increments it, in which transaction, under what concurrency?
2. **Filtered consumers.** `oryol-crm-sync` subscribes to `mail.message.*` but not `mail.mailbox.*`. If a mailbox aggregate emits versions 1,2,3 (mailbox events) and 4 (a message event), the CRM consumer sees version 4 with `expected_version = 1` → v2.3 says "forward gap" → `blocked_on_gap` forever. That is wrong: versions 1–3 were never meant for this consumer.
3. **Idempotency.** "`evt_<agg_id>_<ver>`" as an *example* is fine, but if implementations fall back to `random_id + now()` for retried mutations, the same business command emitted twice produces two non-equal idempotency keys and double-applies.

---

## 2. Decision — Model B: Per-Aggregate Event Stream Sequence

> [!IMPORTANT]
> **Version Model Invariant (`EVENT_VERSION_MODEL`)** — the architecture adopts **Model B** and MUST NOT mix models:
> `aggregate_version` is the **monotonic sequence number of the event within that aggregate's event stream**. It is allocated by the event store at append time. It is **not** the domain-state row version, and there is no separate global event sequence.

Precisely:

- **Aggregate identity** (`AGGREGATE_IDENTITY`): the pair **`(aggregate_type, aggregate_id)`** scoped within `organization_id`. `aggregate_id` is the stable business key of the domain object (`org_<ulid>`, `mbx_<ulid>`, `msg_<ulid>`, `deal_<ulid>`), assigned at creation and never reused. The canonical aggregate key is `(organization_id, aggregate_type, aggregate_id)`.
- **Authoritative source of aggregate version** (`AGGREGATE_VERSION_SOURCE`): a new local table `aggregate_event_sequences` in the **producing** service's database (Core for `core.*`, mail for `mail.*`, etc.):

```sql
CREATE TABLE aggregate_event_sequences (
    organization_id TEXT NOT NULL,
    aggregate_type  TEXT NOT NULL,
    aggregate_id    TEXT NOT NULL,
    last_version    INTEGER NOT NULL DEFAULT 0,   -- highest emitted event version for this aggregate
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (organization_id, aggregate_type, aggregate_id)
);
```

`last_version` starts at 0; the first event for an aggregate is version 1.

### 2.1 Version increment / event allocation transaction

> [!IMPORTANT]
> **Allocation Invariant (`EVENT_VERSION_ALLOCATION`)**:
> Every emitted event's `aggregate_version` is allocated inside the **same atomic D1 transaction** (`db.batch`) as the domain mutation that caused it, using a compare-and-swap bump of `aggregate_event_sequences`. Hard-coded, client-supplied, or post-hoc `aggregate_version` values are **prohibited** (resolves `P1-09`).

Canonical emit sequence (one `db.batch`):

```
1. Apply the domain-state mutation (INSERT/UPDATE the business row).
2. Bump the sequence with CAS:
     UPDATE aggregate_event_sequences
       SET last_version = last_version + 1, updated_at = CURRENT_TIMESTAMP
       WHERE organization_id = :org AND aggregate_type = :atype AND aggregate_id = :aid
             AND last_version = :expected_last;         -- optimistic
     -- affected_rows != 1  -> whole batch rolls back -> caller retries (§3)
   (For the aggregate's first event: INSERT the row with last_version = 1,
    relying on the PRIMARY KEY to reject a concurrent duplicate INSERT.)
3. allocated_version := :expected_last + 1
4. INSERT INTO outbox_events (... aggregate_version = allocated_version,
        idempotency_key = :command_scoped_key (§4) ...).
5. INSERT the immutable audit event where the mutation is security-critical
   (v2.3 audit-and-events.md §6.4).
```

Steps 1–5 commit together or not at all. If step 2's CAS loses, nothing is applied and the caller re-reads `last_version` and retries the whole command (§3).

### 2.2 Domain-state version vs. emitted-event version

- If a domain table also carries its own `version`/`updated_at` optimistic-concurrency column (some do, e.g. `organization_security_policies.version`), that is **independent** of `aggregate_version`. The domain `version` guards row writes; `aggregate_version` orders the event stream.
- They are **not required to be equal** and MUST NOT be assumed equal. A single domain mutation emits exactly one event and bumps `aggregate_version` by exactly 1; it may bump the domain `version` by 1 as well, but a mutation that emits **no** event (rare, e.g. a pure metadata touch) bumps neither `aggregate_version` nor emits — and a mutation that emits **two** events (not done in Phase 1; disallowed) is prohibited precisely to keep the 1:1 event↔version relationship.

> [!IMPORTANT]
> **1:1 Emission Invariant (`ONE_EVENT_ONE_VERSION`)**: In Phase 1, each authoritative aggregate mutation that is externally observable emits **exactly one** `outbox_events` row and consumes **exactly one** `aggregate_version` increment. Batch domain operations that touch N aggregates emit N events (one per aggregate), each with that aggregate's own next version.

### 2.3 Concurrency behavior

- Two concurrent mutations to the **same** aggregate: the CAS in step 2 serializes them. Winner emits version `k+1`; loser's batch rolls back and retries, reads `last_version = k+1`, emits `k+2`. No gap, no duplicate version, strict monotonic per aggregate.
- Concurrent mutations to **different** aggregates: independent rows, no contention.
- The event stream per aggregate is therefore **gap-free and strictly increasing** at the source. Any gap a consumer observes is either transport loss or filtering (§3).

---

## 3. Filtered Consumers & Gap Policy

> [!IMPORTANT]
> **Consumer-Aware Sequencing Invariant (`CONSUMER_STREAM_SEQUENCE`)**:
> Gap detection is performed on a **per-consumer, per-aggregate delivered sequence**, not on the raw `aggregate_version`. A consumer that does not subscribe to every event type of an aggregate MUST NOT treat skipped `aggregate_version` values as transport loss.

### 3.1 Mechanism: producer-stamped per-subscription sequence

Each `outbox_events` row is fanned out to consumers according to a **static subscription matrix** (`consumer_name → set of event_type globs`), declared in `product-integration.md` and versioned. At dispatch time, for each `(event, consumer)` pair the dispatcher stamps a **`consumer_stream_seq`**: a monotonic per-`(consumer_name, organization_id, aggregate_type, aggregate_id)` counter maintained in the producer's `consumer_delivery_sequences` table, incremented **only for events that match that consumer's subscription**.

```sql
CREATE TABLE consumer_delivery_sequences (
    consumer_name   TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    aggregate_type  TEXT NOT NULL,
    aggregate_id    TEXT NOT NULL,
    last_stream_seq INTEGER NOT NULL DEFAULT 0,
    last_source_version INTEGER NOT NULL DEFAULT 0,  -- highest aggregate_version fanned to this consumer
    PRIMARY KEY (consumer_name, organization_id, aggregate_type, aggregate_id)
);
```

The delivered envelope carries **both**: `aggregate_version` (source stream position, for audit/debug) **and** `consumer_stream_seq` (this consumer's contiguous position) **and** `prev_consumer_stream_seq`.

### 3.2 Consumer evaluation (replaces the raw comparison in v2.3 §4.1 for filtered consumers)

For consumer `C`, aggregate `(org, atype, aid)`, with `expected := inbox high-water consumer_stream_seq + 1`:

| Condition | Meaning | Action |
|---|---|---|
| `received.consumer_stream_seq == expected` | in order | process; advance high-water; atomic with side effect (v2.3 §5) |
| `received.consumer_stream_seq < expected` | duplicate / stale | dedupe via `inbox_events UNIQUE(consumer_name, event_id)`; ack, no side effect |
| `received.consumer_stream_seq > expected` **and** `received.prev_consumer_stream_seq == expected - 1` mismatch | genuine gap in *this consumer's* stream | `status = 'blocked_on_gap'`; request replay of the missing `consumer_stream_seq` range from the producer; DLQ + alert after 5 min (v2.3 timing retained) |
| `received.consumer_stream_seq > expected` **and** producer confirms no intervening matching events | not a gap (should not happen with `prev_` stamping, but defended) | process; advance |

Because `consumer_stream_seq` is contiguous **by construction for events this consumer subscribes to**, skipped source `aggregate_version` values (mailbox events the CRM consumer doesn't want) are invisible to the gap check. A "forward gap" now means *only* that a matching event was lost in transport — exactly the v2.3 intent, made correct for filtered consumers.

### 3.3 Full-fanout consumers

A consumer subscribed to `*` for an aggregate has `consumer_stream_seq == aggregate_version` for that aggregate, so v2.3 §4.1's behavior is preserved exactly as a special case. Products that want the simple model subscribe to all event types.

### 3.4 Subscription matrix changes

Adding an event type to a consumer's subscription starts a **new `consumer_stream_seq` epoch** (`epoch` integer bumped, seq resets) with a documented backfill boundary; the consumer is told "from source `aggregate_version` V onward you now also receive type T" and does not treat pre-V absence as loss. Removing a type is forward-only and does not create gaps.

---

## 4. Idempotency

> [!IMPORTANT]
> **Command-Scoped Idempotency Invariant (`EVENT_IDEMPOTENCY_KEY`)**:
> `outbox_events.idempotency_key` MUST be a **deterministic function of the authoritative command / mutation**, not of `random_id + now()`. For a domain mutation it is:
> ```
> idempotency_key := sha256(organization_id || ':' || aggregate_type || ':' || aggregate_id
>                           || ':' || event_type || ':' || allocated_aggregate_version)
> ```
> Since `allocated_aggregate_version` is itself allocated exactly once per committed mutation (§2.1), the key is stable: retrying the *same* committed mutation's emission produces the *same* key and the `UNIQUE` constraint dedupes it. A retried *command* that has **not** yet committed re-allocates a fresh version and thus a fresh key — which is correct, because no event was emitted for the failed attempt.

For commands that carry a **client-supplied idempotency token** (e.g. "send this email once"), the token is bound into the domain layer: the mutation's first statement is `INSERT INTO command_idempotency(organization_id, command_scope, client_token, aggregate_id, aggregate_version) VALUES (...)` with `UNIQUE(organization_id, command_scope, client_token)`. A duplicate submission fails that insert, the batch rolls back, and the handler returns the **already-recorded** `(aggregate_id, aggregate_version)` result rather than mutating again or emitting a second event. This ties idempotency to the authoritative command as the review requires.

Explicitly **prohibited**: using a freshly generated `event_id` (`evt_<ulid>`) plus `occurred_at = now()` as the *sole* conceptual dedupe guarantee for a retried business mutation. `event_id` remains the transport-level primary key and MAY differ across retries of *delivery*, but it is never the idempotency authority.

---

## 5. Relationship to v2.3 §4.1 (Aggregate Ordering)

- v2.3 §4.1's three-way comparison is **retained** and now operates on `consumer_stream_seq` (per-consumer contiguous) rather than raw `aggregate_version` for filtered consumers, and identically on `aggregate_version` for full-fanout consumers (§3.3).
- v2.3 `blocked_on_gap` status, 5-minute replay window, DLQ, and operational alert are **unchanged**.
- v2.3 `inbox_events` schema is unchanged; `inbox_events.aggregate_version` continues to record the source version, and the consumer high-water is tracked per `(consumer_name, organization_id, aggregate_type, aggregate_id)` — the same key the dispatcher stamps.
- v2.3 transport reality ("Cloudflare Queues at-least-once, no ordering assumed") is unchanged; the source-side monotonic allocation + per-consumer sequencing is what makes deterministic gap detection possible on top of an unordered transport.

---

## 6. Schema & Migration Impact

- New **local** tables in each producing service: `aggregate_event_sequences`, `consumer_delivery_sequences`, `command_idempotency`. Delivered by forward migration `0006`/product-equivalent (Phase 1 Slice 4+; **not implemented by this task**).
- **No change** to `outbox_events` / `inbox_events` table shapes. The `aggregate_version` column semantics are *pinned* (Model B) rather than altered; two envelope fields (`consumer_stream_seq`, `prev_consumer_stream_seq`, `epoch`) are added to the **event payload envelope**, not the table.
- Migrations `0001`–`0005` remain **sealed and immutable**.
- Backfill: `aggregate_event_sequences.last_version` is initialized per aggregate from `MAX(aggregate_version)` over existing `outbox_events` for that aggregate (or 0 if none). This runs under ADR-005 §6 fence and is a verified postcondition (ADR-005 §5.2 "required row/tuple preservation").

---

## 7. Alternatives Considered & Rejected

1. **Model A (domain-state version == event version, every mutation bumps both, every event uses the exact domain version)**: *Rejected* — breaks when a mutation legitimately emits no event or when domain rows have their own optimistic `version` semantics with different increment rules; forces a 1:1 that some domain tables can't honor. Model B keeps event ordering independent and clean.
2. **Global monotonic event sequence (single counter for all aggregates)**: *Rejected* — a single hot counter across all orgs/aggregates is a write bottleneck on D1 and provides ordering guarantees consumers don't need; per-aggregate ordering is sufficient and contention-free.
3. **Let consumers infer filtering from event_type and skip gap detection entirely**: *Rejected* — then real transport loss of a subscribed event is undetectable. Per-consumer contiguous sequencing detects real loss while ignoring intentional skips.
4. **`idempotency_key = random`**: *Rejected* explicitly (`P1-09` / review finding) — not idempotent across retries.
5. **Client-supplied `aggregate_version`**: *Rejected* — trust boundary violation and non-monotonic under concurrency.

---

## 8. Decisions / Invariants / Open Questions

### Decisions
- D1: Model B — `aggregate_version` is the per-aggregate **event-stream** sequence number, allocated by the event store at append (§2). Models are not mixed.
- D2: Aggregate identity = `(organization_id, aggregate_type, aggregate_id)`; `aggregate_id` is the stable business key, never reused (§2).
- D3: Version allocation is a CAS bump of `aggregate_event_sequences` inside the same `db.batch` as the domain mutation and the outbox insert; hard-coded versions prohibited (§2.1, resolves P1-09).
- D4: Domain-state `version` and `aggregate_version` are independent and not assumed equal (§2.2); exactly one event + one version increment per observable mutation (§2.2 `ONE_EVENT_ONE_VERSION`).
- D5: Filtered consumers evaluate gaps on a producer-stamped per-consumer contiguous `consumer_stream_seq`, not raw `aggregate_version` (§3).
- D6: Idempotency key is a deterministic function of `(org, aggregate, event_type, allocated_version)`; client command tokens bind via `command_idempotency` (§4).

### Invariants
- I1 `EVENT_VERSION_MODEL` — Model B only; never mixed (§2).
- I2 `EVENT_VERSION_ALLOCATION` — allocated atomically with the mutation via CAS; no hard-coded/client values (§2.1).
- I3 `ONE_EVENT_ONE_VERSION` — one observable mutation ⇒ one outbox row ⇒ one version increment (§2.2).
- I4 Per-aggregate source stream is gap-free and strictly increasing (§2.3).
- I5 `CONSUMER_STREAM_SEQUENCE` — gap detection on per-consumer contiguous sequence; skipped source versions are never "loss" for a filtered consumer (§3).
- I6 `EVENT_IDEMPOTENCY_KEY` — deterministic, command-scoped; `random_id + now()` prohibited as the dedupe authority (§4).
- I7 v2.3 §4.1 `blocked_on_gap` / replay / DLQ timing and `inbox_events` schema unchanged (§5).

### Open Questions
- OQ1: Whether `consumer_delivery_sequences` should live in the producer (chosen) or be reconstructable purely from `outbox_events` + the subscription matrix at replay time — Phase 1 stores it for O(1) dispatch; a rebuild path from the matrix is the recovery fallback.
- OQ2: Cross-aggregate causal ordering (e.g. "mailbox deleted" then "message deleted" for messages in it) — Phase 1 relies on `causation_id` + consumer-side idempotency, not a global order; a saga/process-manager pattern is deferred.
- OQ3: Envelope size growth from the extra sequence fields — negligible; revisit only if Queues message-size limits bite.
