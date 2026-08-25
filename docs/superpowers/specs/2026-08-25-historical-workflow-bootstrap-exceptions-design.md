# Historical Workflow Bootstrap Exceptions Design

## Goal

Allow a documented historical task to proceed past an unavailable builder
handoff without inventing evidence, while preserving all current packet,
coverage, independent-review, gate, and seal checks.

## Scope

This mechanism applies only to tasks created before native builder-handoff
capture existed. It does not add a workflow stage, reconstruct a missing
handoff, or relax the normal workflow for future tasks.

The exception lives at:

```text
.sera/tasks/<task-id>/bootstrap-exception.json
```

## Schema

The supported schema is version 1:

```json
{
  "schema_version": 1,
  "type": "historical_workflow_bootstrap_exception",
  "reason": "The task predates native builder handoff capture.",
  "missing_stage": "builder_handoff",
  "no_fabricated_evidence": true,
  "implementation_head_sha": "<current Git HEAD>",
  "implementation_tree_sha": "<current Git HEAD tree>",
  "future_workflow_required": [
    "builder",
    "packet",
    "independent_review",
    "gate",
    "seal"
  ]
}
```

Every displayed field is required. Validation is strict:

- `schema_version` must equal the integer `1`.
- `type` must equal `historical_workflow_bootstrap_exception`.
- `reason` must be a non-empty string.
- `missing_stage` must equal `builder_handoff`.
- `no_fabricated_evidence` must be exactly the Boolean `true`; truthy values,
  `false`, and omission fail closed.
- `implementation_head_sha` and `implementation_tree_sha` must exactly match
  the task artifact identity recorded by the current review packet. Existing
  review-packet validation separately proves that identity still matches the
  repository checkout; the exception is never accepted merely because it
  matches whatever HEAD happens to be open.
- `future_workflow_required` must exactly equal the ordered sequence shown
  above.

Unknown additional fields may remain visible for audit purposes, but they do
not affect acceptance.

## Validation and Audit State

The core layer exposes a read-only bootstrap-exception state evaluator. It
loads the JSON defensively and returns machine-readable audit state including:

- whether the file exists;
- whether it is accepted;
- the stable reason `bootstrap_exception_invalid` when a present exception
  fails validation;
- validation details suitable for diagnostics;
- the declared missing stage and implementation identity when readable.

An exception is accepted only when all schema checks pass and all of these
state checks are true:

1. `packet-build.md` does not exist.
2. `packet-build.provenance.json` does not exist.
3. A review packet exists and is current under the existing packet-integrity
   checks.
4. Review coverage recomputes with `coverage_complete == true`.
5. The exception identity matches the review packet's recorded implementation
   HEAD and tree.

The evaluator creates and modifies nothing. In particular, it never creates a
builder packet, provenance, timestamp, context record, ledger entry, or other
synthetic log.

The accepted audit message is:

> Builder handoff history is unavailable and has been explicitly preserved as
> missing. Workflow progression continues under a documented bootstrap
> exception. This does not assert that the builder stage occurred.

## Controller Integration

`sera next` evaluates the exception only when both builder artifacts are
missing. Its precedence is:

```text
builder packet missing
  -> accepted exception: preserve audit state and continue normal progression
  -> present invalid exception: bootstrap_exception_invalid
  -> no exception: existing build_packet / packet_missing behavior
```

An accepted exception satisfies only the historical builder-handoff
requirement. The controller then follows the existing state machine without a
new action type. For an assured task with a current review packet and current
passing independent review but no gate review, the result remains:

```text
state = dispatch_review
next_action = dispatch_review
reason = Required review stage: gate.
```

A present but invalid exception fails closed with:

```text
state = invalid
next_action = bootstrap_exception_invalid
```

The controller response includes bootstrap-exception audit state so consumers
can distinguish a genuine builder packet from an explicitly preserved gap in
historical evidence.

Builder packets that exist but are stale, unbound, or otherwise invalid retain
their existing behavior. A bootstrap exception cannot override them because
the exception requires all builder packet artifacts to be absent. When a valid
builder packet exists, an adjacent bootstrap-exception file is ignored for
progression and the existing builder validation remains authoritative.

## Testing

Regression coverage uses real temporary Git repositories and existing packet,
coverage, and review APIs.

1. A historical assured task with no builder artifacts, a valid exception, a
   current complete review packet, and a current passing independent review
   progresses to `dispatch_review`; its reason names the gate and its audit
   state is accepted.
2. An implementation HEAD or tree mismatch returns
   `bootstrap_exception_invalid` and does not progress.
3. `no_fabricated_evidence: false` returns
   `bootstrap_exception_invalid`; omission and non-Boolean truthy values are
   also rejected by schema validation.
4. No exception preserves `build_packet` with `packet_missing` behavior.
5. Existing builder artifacts make an exception invalid, proving that the
   controller never treats fabricated or contradictory handoff artifacts as a
   historical gap.
6. A valid existing builder packet plus any bootstrap-exception file follows
   ordinary builder-packet progression and does not reinterpret the task as a
   historical exception.

The complete existing test suite must remain green. Final verification runs:

```text
pytest
sera check
sera next
```

## Backward Compatibility

Repositories without `bootstrap-exception.json` behave exactly as before.
Normal native builder-to-packet workflow remains mandatory for current and
future tasks. Review packet freshness, complete coverage, exact Git identity,
independent-review precedence, release gating, and sealing are unchanged.

The mechanism preserves the fact that history is missing; it never claims the
missing stage occurred.
