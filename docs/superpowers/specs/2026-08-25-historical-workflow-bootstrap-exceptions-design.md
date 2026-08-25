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

## Historical Eligibility Registry

The exception is evidence about a missing handoff, not authority to skip one.
Authority comes only from the tracked project policy at `.sera/config.json`.
The optional `historical_bootstrap_eligibility` setting is a list and defaults
to an empty list when omitted. SERA never adds or updates registry entries.

Each preauthorized entry binds the complete historical task identity:

```json
{
  "task_id": "<historical-task-id>",
  "created_at": "<task.json created_at>",
  "baseline_head_sha": "<task baseline_repository_identity.head_sha>",
  "baseline_tree_sha": "<task baseline_repository_identity.head_tree_sha>",
  "eligibility_type": "historical_builder_handoff_gap",
  "schema_version": 1
}
```

Acceptance requires exactly one entry whose `task_id`, `created_at`, baseline
HEAD, and baseline tree equal the task record and whose eligibility type and
integer schema version equal the values above. Missing, empty, malformed,
duplicate, or mismatched registry data fails closed. A
`bootstrap-exception.json` file cannot authorize its own task. Current and
future tasks remain on the native builder workflow unless project policy
already contains an exact historical authorization.

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

1. `.sera/config.json` contains exactly one matching historical eligibility
   entry.
2. `packet-build.md` does not exist.
3. `packet-build.provenance.json` does not exist.
4. A review packet exists and is current under the existing packet-integrity
   checks.
5. Review coverage recomputes with `coverage_complete == true`.
6. The exception identity matches the review packet's recorded implementation
   HEAD and tree.

The evaluator creates and modifies nothing. In particular, it never creates a
builder packet, provenance, timestamp, context record, ledger entry, or other
synthetic log.

The accepted audit message is:

> Historical eligibility confirmed by policy registry. Bootstrap exception
> records missing historical builder evidence. No builder stage is claimed to
> have occurred.

## Controller Integration

`sera next` evaluates every present exception. Only an accepted exception with
both builder artifacts missing may satisfy the historical handoff gap. Its
precedence is:

```text
builder packet missing
  -> accepted exception: preserve audit state and continue normal progression
  -> present invalid exception: bootstrap_exception_invalid
  -> no exception: existing build_packet / packet_missing behavior
builder packet or provenance present beside exception
  -> bootstrap_exception_invalid
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

Any builder packet or builder provenance beside an exception makes the
exception invalid. This contradictory state returns
`bootstrap_exception_invalid`; it never dispatches the builder and never lets
the exception override builder evidence. With no exception file, valid, stale,
unbound, and missing builder packets retain their existing behavior.

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
6. A valid exception without an exact policy-registry entry fails closed.
7. Registry task ID, creation timestamp, baseline HEAD, baseline tree,
   eligibility type, and schema mismatches each fail closed.

The complete existing test suite must remain green. Final verification runs:

```text
pytest
sera check
sera next
```

## Backward Compatibility

Repositories without `bootstrap-exception.json` behave exactly as before.
The historical eligibility registry defaults empty and is never populated by
SERA. Normal native builder-to-packet workflow remains mandatory for current
and future tasks. Review packet freshness, complete coverage, exact Git
identity, independent-review precedence, release gating, and sealing are
unchanged.

The mechanism preserves the fact that history is missing; it never claims the
missing stage occurred.
