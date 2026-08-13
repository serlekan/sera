# SERA Controller

Version 0.4 adds a controller layer on top of the original relay protocol. Versions
0.4.1 and 0.4.2 harden it for high-assurance repositories.

The controller does **not** call model APIs. It prepares enough deterministic state for ChatGPT, Codex, Claude Code, or another runtime to dispatch the right worker without forwarding a large transcript.

## Controller loop

```text
Natural-language request
  -> delta repository map
  -> context selection with reasons
  -> automatic risk/mode draft
  -> exact ownership confirmation
  -> routed builder packet
  -> verification evidence
  -> fresh review
  -> optional release gate
  -> SERA Seal
```

## `sera run`

`sera run` turns a request into a task draft and route in one command.

```bash
sera run "fix payment reconciliation" --file src/payments.py --verify "python -m unittest"
```

When exact files are supplied, the builder packet is generated immediately. When files are inferred from the repository map, they remain **candidate ownership** until a controller confirms them:

```bash
sera task confirm
```

This is intentional. Context relevance is not the same thing as permission to edit.

## Mode precedence

One resolver decides the effective mode for every task-creation path:

```text
explicit CLI mode  >  configured default_mode  >  built-in fallback (standard)
```

`--mode auto` and an omitted `--mode` both mean "no explicit override", so the
project's `default_mode` is used. An invalid mode — configured or explicit — is
rejected; SERA never falls back to a different mode to keep going.

High risk overrides the resolved mode upward. A high-risk task always routes at
`assured`, even when `--mode fast` was requested explicitly.

## Project risk policy

Built-in risk terminology is deliberately generic. Repositories declare their own:

```json
{
  "risk_policy": {
    "high_risk_terms": ["payment", "authentication", "production deployment"],
    "high_risk_paths": ["src/auth/**", "src/payments/**", "migrations/**"]
  }
}
```

**Terms** match case-insensitively against the objective and owned paths, as
contiguous token runs rather than substrings:

| Term | Matches | Does not match |
| --- | --- | --- |
| `order` | `cancel the order`, `src/order_book.py` | `reorder`, `borders` |
| `production deployment` | `block the production deployment` | `production readiness and deployment` |

No stemming is applied, so `payment` does not match `payments`. List both when
both matter.

**Paths** use a documented glob syntax:

| Pattern | Meaning |
| --- | --- |
| `**` | matches across directory separators, including zero segments |
| `*` | matches any characters within a single path segment |
| `?` | matches one character within a single path segment |

`src/payments/**` matches `src/payments/gateway.py` and `src/payments/eu/sepa.py`
but not `src/payments_legacy.py`.

### Risk composition

Effective risk is the **maximum** severity implied by the built-in classifier,
project terms, project paths, and explicit user risk:

```text
auto = high, user says low  =>  high
```

Explicit input can raise risk. It never silently lowers automatically detected
risk; the rejected downgrade is recorded as `explicit_risk_not_applied` so the
attempt stays auditable.

### Requested inputs versus derived policy

A task stores two different things:

| Field | Meaning |
| --- | --- |
| `requested_mode` / `requested_risk` | what the user explicitly asked for, or `null` |
| `mode` / `risk` / `risk_reasons` | what SERA derived from the current contract |

Policy is always re-derived from the *requested* inputs plus the *current*
objective and ownership — never from a previously derived value. That keeps a
transiently confirmed high-risk path from making risk permanently sticky, while
an explicit `--risk high` remains a floor that survives any later change.

### Ownership confirmation re-runs policy

Confirming ownership changes what a task is authorized to touch, so it re-runs
the same risk and mode resolution used at creation:

```bash
sera task confirm --file critical/secret.py
```

recomputes effective risk, re-escalates the mode, and reroutes the lanes. A task
drafted as low-risk `fast` and then confirmed onto a high-risk path becomes
`assured` with independent review and the release gate required. `risk_reasons`
is rebuilt from the current contract, so reasons from previous ownership do not
linger.

Reverting ownership back to low-risk paths recomputes to low risk again — unless
an explicit risk floor was requested, which persists.

Setting `controller.auto_risk` to `false` disables the built-in classifier only.
Project risk policy is explicit repository configuration and always applies.

### Risk reasons

Every escalation is explained in machine-readable output:

```json
{
  "risk": "high",
  "risk_reasons": [
    {"type": "project_term", "value": "execution"},
    {"type": "project_path", "value": "trading/risk/**", "matched": "trading/risk/limits.py"},
    {"type": "mode_escalation", "value": "assured"}
  ]
}
```

Reason types: `builtin_term`, `project_term`, `project_path`, `explicit_risk`,
`explicit_risk_not_applied`, `auto_risk_disabled`, `mode_escalation`, `no_signal`.

## Ownership is not context

These are separate concepts with separate budgets.

- **Ownership** — the files a task is authorized to modify. Authorization only.
- **Selected context** — the files a single stage needs to read.

A task can own 36 files while a builder packet selects 12 and a reviewer packet
selects a different 8 plus the bounded diff. Excluding a file from context never
removes its ownership.

```json
{
  "ownership": {"file_count": 36, "estimated_tokens": 227307},
  "selected_context": {"file_count": 10, "estimated_tokens": 28140}
}
```

The stage token budget applies to **selected context**, never to total ownership.

### Stage-specific selection

Selection is deterministic — candidates are ranked by score, then size, then path.
The highest-priority candidate is always selected, so a stage never receives empty
context; further files are added while both the file cap and the token budget
allow.

Review context is diff-aware. Rather than rereading every owned file, a reviewer
receives the task contract, the bounded diff, changed files, their imported
dependencies, the verification summary, and relevant tests. Raw logs are never
forwarded. **Every changed file stays represented through the bounded diff even
when its full contents are not selected** — narrowing review context never hides a
change from the reviewer.

### Review packet safety invariant

> Every changed file is represented in the review packet by actual bounded change
> evidence, or by an explicit deterministic non-text/binary representation. If
> the configured budget cannot provide minimum representation for every changed
> file, review packet generation fails closed rather than silently omitting
> changes.

The diff is built **per file**, never as one combined patch truncated at its
head and tail — that older approach could drop every file between the first and
last. Each changed file gets its own block:

```text
### `src/payments/gateway.py`
- status: modified
- location: committed+staged+unstaged
- blobs: `a1b2c3d`..`e4f5a6b`
- content: text
- patch sha256: `9f2c…`
- shown: 512 of 4,096 patch characters
```

`location` names every source the file changed in. Blob identity spans the whole
task range: the oldest source supplies where the file started, the newest where
it stands now. One file is always exactly one block — never several competing
ones — so a reviewer reads its cumulative task-relative change in one place.

Budget allocation runs in two phases:

1. **Guaranteed minimum** — every changed file is reserved real patch body first,
   so no file can reach zero coverage.
2. **Relevance allocation** — the remainder is water-filled smallest-need first,
   so unused headroom flows to the files that need it. The result depends only
   on the change set, never on input order.

#### The budget is exact, not estimated

When `review_diff_coverage` reports success, this holds exactly:

```text
len(rendered_text) <= max_chars
```

There is no estimate, tolerance, or unmodelled overhead. A single canonical
renderer produces each file block, and budgeting measures that renderer's real
output — headers, blob identity, patch hashes, `shown`/`total` counters, omission
markers, and separators all included. The minimum representation for every
changed file is rendered and measured first; if that measured minimum exceeds
`max_chars`, generation fails immediately. Remaining budget is then distributed,
the result re-rendered and re-measured, and shrunk deterministically until the
measured length fits.

`max_chars` counts **Python string characters (Unicode code points)**, matching
`len(text)`. It is not a UTF-8 byte count.

When even the guaranteed minimum does not fit, `sera packet review` fails with
`review_diff_budget_insufficient` and `sera next` returns that as its state.
Raise `max_packet_chars` or split the task.

## `sera context --why`

`--why` reports why each file earned or lost its place:

| Reason | Meaning |
| --- | --- |
| `explicit_context` | pinned by explicit request |
| `selected_changed_file` | changed by this task; prioritized for review |
| `selected_owned` | owned by this task and selected |
| `selected_by_relevance` | scored in on objective relevance |
| `selected_dependency` | imported by a changed file |
| `owned_not_selected` | owned and indexed, but outside this stage's context |
| `excluded_by_budget` | dropped specifically by the token budget |
| `not_in_repository_map` | genuinely not indexed — a new or filtered file |

`not_in_repository_map` is reported only when a path really is absent from the
map. In 0.4.0 any owned file outside the context cap was mislabelled this way.

## Artifact freshness

A generated artifact is never current merely because its file exists. Every task
carries a **task-contract fingerprint** — a hash of objective, requested and
derived policy, risk reasons, confirmed ownership, constraints, verification,
uncertainty, and use case. It deliberately excludes generated artifacts,
timestamps, evidence, and worktree state, so the binding can never be circular.

Each generated packet is written alongside machine-readable provenance in
`packet-<stage>.provenance.json`:

```json
{
  "schema_version": 3,
  "packet_type": "review",
  "task_id": "20260812T091006Z-low-risk-task",
  "task_contract_fingerprint": "…",
  "state_fingerprint": "…",
  "repository_identity": {"head_sha": "…", "head_tree_sha": "…"},
  "review_change_fingerprint": "…",
  "coverage_complete": true,
  "route": {"builder": "deep_builder", "reviewer": "independent_reviewer", "gate": "release_gate"}
}
```

Freshness is:

```text
contract
  + repository identity (review packets only)
  + state (review packets only)
  + coverage and change set (review packets only)
  + resolved route
  + content
```

Recorded metadata is never trusted on its own. Each identity is recomputed from
current state and compared:

- **contract** — recomputed from the task;
- **repository identity** — the exact `HEAD` commit and tree a review packet was
  generated against, re-read from Git. Moving HEAD stales the packet even when
  an empty commit leaves the tree and the working delta untouched, because the
  reviewer inspected a different repository than the one now standing. Build
  packets record identity as provenance but are *not* invalidated by it: a
  builder committing its own implementation must not stale its own handoff;
- **state** — the task/evidence/delta fingerprint a review packet embeds;
- **coverage and change set** — whether the complete task change set could be
  derived, plus a fingerprint over the committed-range endpoints, the whole
  changed-path set, and each file's status, blob identity, sources, and patch
  bytes. A displayed filename count is not relied upon;
- **route** — the *currently* resolved lane, provider, and model for every stage
  this task requires, re-derived from task and configuration and re-hashed. The
  `route` object in provenance is diagnostics only; editing it changes nothing.
  Only selected stages are bound, so changing an unused lane does not invalidate
  a packet, while changing the selected model, provider, or lane does;
- **content** — SHA-256 of the exact bytes written to `packet-<stage>.md`. The
  checksum embedded inside the Markdown is not the authority; provenance is the
  integrity envelope, so rewriting the body and its embedded checksum together
  still fails.

Validation is ordered, and every failure is a closed one:

```text
missing -> unbound -> contract -> head -> tree -> state
        -> coverage -> change set -> route -> content -> current
```

| Reason | Meaning |
| --- | --- |
| `packet_missing` | no packet has been generated |
| `packet_unbound` | provenance absent, unparseable, or missing a required binding |
| `packet_legacy_schema` | a 0.4.1 (version 2) packet, which binds no repository identity |
| `packet_stale_contract` | the task contract changed after generation |
| `packet_stale_head` | HEAD moved after the review packet was generated |
| `packet_stale_head_tree` | the HEAD tree moved after the review packet was generated |
| `packet_stale_state` | review packet's embedded diff/evidence state moved on |
| `packet_coverage_incomplete` | the complete change set cannot be shown to be represented |
| `packet_stale_change_set` | the represented change set moved |
| `packet_stale_route` | the resolved route changed, or can no longer be resolved |
| `packet_content_mismatch` | the packet bytes differ from what was generated |

So a semantic task mutation — ownership confirmation being the common one —
invalidates the previous build packet, the previous review packet, and rewrites
`capsule.md` immediately. `sera next` then requests regeneration instead of
returning `dispatch_builder`, and the regenerated packet carries the current
route rather than the superseded one.

An unbound packet from an older SERA is never dispatchable as current. Packet
provenance is at schema version 3; a 0.4.1 packet fails closed as
`packet_legacy_schema` and must be regenerated, so it can never masquerade as an
exact-head-bound 0.4.2 packet.

### Evidence follows the same rule

Verification evidence records the contract fingerprint they were produced under.
Records remain in the ledger for audit, but evidence collected under a superseded
contract no longer satisfies the current one — it must be re-run. Reviews are
already invalidated by the task fingerprint, which changes whenever the contract
does.

## `sera next`

`sera next` makes the repository state machine explicit. It can return steps such as:

- confirm ownership;
- generate a build packet;
- dispatch the builder;
- run verification;
- generate a review packet;
- repeat stale review;
- address a failed review;
- seal the accepted tree.

Required-review precedence is explicit, and a failed review is never skipped in
favour of a later stage:

```text
stale required review
  -> current failed required review
  -> missing required review
  -> seal
```

A current `fix-first` or `rethink` verdict therefore returns `fix_first` even
when a later gate has not run yet. Dispatching a release gate for work the
independent stage already refused is not a valid next step.

`reduce_context` is returned only when the context selected for the **required
next stage** exceeds the mode budget. A large ownership set alone never blocks
progress:

```text
ownership:               227k tokens
review-selected context:  29k tokens
assured budget:           32k tokens
=> within budget
```

It still blocks when a stage genuinely cannot fit — for example when one owned
file alone is larger than the whole budget. Split the task in that case.

`dispatch_builder` and `dispatch_review` are returned only when the corresponding
packet is *current* for the task contract. A packet that exists but is stale,
unbound, or built for a superseded contract yields `build_packet` or
`review_packet` instead, and `sera next --json` reports the packet state:

```json
{"state": "build_packet",
 "build_packet": {"exists": true, "current": false, "reason": "packet_stale_contract"}}
```

Currency is not coverage. A packet can be perfectly current and still not
represent the whole change set, so the two are reported separately and
`dispatch_review` requires both:

```json
{"state": "dispatch_review",
 "review_packet": {"exists": true, "current": true, "reason": null},
 "review_coverage": {"complete": true, "reason": null,
                     "change_fingerprint": "…", "committed_range": ["…", "…"]}}
```

When the complete change set cannot be derived, `sera next` returns
`review_coverage_incomplete` and `sera packet review` refuses to generate a
packet at all rather than emit one that understates what changed.

`sera next --json` also reports `review_states` and `stale_review_reasons` per
stage, `failed_reviews`, `head_identity`, and `baseline_repository_identity`.

Use `--json` when another AI controller is consuming the result.

## `sera resume`

A new chat or agent can run:

```bash
sera resume --json
```

and reconstruct the active task from repository state instead of requiring the previous chat transcript.

## `sera inbox`

```bash
sera inbox
```

shows every local SERA task and the next required action. This is the first project-level control-plane view and is designed to grow into multi-task orchestration later.

## Review identity

A review is bound to the repository the reviewer actually inspected, not merely
to the task state:

```text
review record = task/evidence/delta fingerprint + HEAD + HEAD tree
```

`sera review` never trusts a supplied commit SHA. The reviewer works from a
review packet that already binds an exact HEAD, tree, and change set; acceptance
re-validates that packet against the repository as it stands, and only then reads
the identity from Git. If HEAD moved after generation, the verdict is refused
until a fresh packet is generated and reviewed.

Freshness then requires every applicable binding to match at once:

| Reason | Meaning |
| --- | --- |
| `review_fingerprint_mismatch` | the task/evidence/delta state moved after review |
| `review_head_mismatch` | HEAD moved after review |
| `review_head_tree_mismatch` | the HEAD tree moved after review |
| `review_repository_unbound` | a 0.4.1 record carrying no repository identity |

An empty commit leaves the fingerprint and the tree untouched, so only the HEAD
binding catches it — which is precisely the case a release gate must not be
allowed to inherit. The gate is a review stage and binds identity identically.

0.4.1 review records remain readable history. They report
`review_repository_unbound`, cannot satisfy 0.4.2 exact-head acceptance, and are
not deleted; repeat the review on 0.4.2 instead.

## Committed change coverage

Every task records `baseline_repository_identity` when it is created. Review
change evidence spans that baseline commit through the current HEAD, unioned with
task-relative working-tree changes:

```text
committed changes since the task baseline
  UNION
task-relative working changes since the task's dirty baseline
```

A task whose implementation is fully committed on a clean worktree therefore
still reaches its reviewer with real patch material, and the committed range
participates in scope checking — a file committed after the task began is a task
change even with `git status --short` empty. Pre-existing dirty paths the task
never touched remain outside task scope.

Tasks created before 0.4.2 carry no baseline and report
`review_baseline_unbound`; a baseline commit that no longer exists reports
`review_baseline_unreachable`. Both fail closed rather than claiming complete
coverage.

## SERA runtime state versus tracked policy

`.sera/**` holds two different kinds of file. These are generated runtime state
and are never repository review content:

```text
.sera/cache/**
.sera/tasks/**        capsules, packets, ledgers, seals
.sera/latest-task
```

Everything else under `.sera/` — `.sera/config.json`, `.sera/POLICY.md`,
`.sera/README.md`, any tracked policy file a team adds — is ordinary repository
content, eligible for ownership, change detection, scope checking, and review
evidence. Runtime state stays excluded even when a repository commits it by
mistake, and owning a runtime path does not turn it into review content.

## Exact-head acceptance

A seal (schema version 2) binds acceptance to the exact reviewed repository
identity:

```text
task + evidence + reviews + HEAD + HEAD tree + working-tree delta + relevant untracked state
```

A seal cannot be created unless every required review is `ship`,
fingerprint-current, and bound to the current HEAD and tree, so a seal can never
describe a commit nobody reviewed:

```bash
sera review --verdict ship ...       # reviewed at HEAD A
git commit --allow-empty -m "b"      # HEAD moves to B
sera seal                            # refused: reviews describe a different commit
```

Because the seal already binds the review ledger, and reviews now bind
repository identity, a seal transitively binds the identity each review was
taken at. The seal schema itself is unchanged at version 2.

### Review-ledger binding

Acceptance composes three independent components:

```text
task_fingerprint            task + evidence + staged/unstaged delta + relevant untracked
review_ledger_fingerprint   canonical hash of every persisted review record
repository_identity         exact HEAD commit + HEAD tree
        │
        ▼
        seal identity
```

`review_ledger_fingerprint` binds the *contents* of every review record — stage,
verdict, reviewer, rationale, reviewed fingerprint, timestamp — in ledger order,
not merely the stage names. Editing an accepted reviewer's identity or rationale,
changing a verdict, deleting a required review, or appending a record all make
the seal stale with `seal_review_mismatch`, even though the task fingerprint is
untouched.

This stays deliberately non-circular: a review records the task fingerprint it
judged, so folding reviews back into that fingerprint would be self-referential.
Review freshness binds the task fingerprint; acceptance binds both.

`sera check --require-seal` therefore fails whenever HEAD moves, even if nothing
else changed:

- a new commit is made;
- HEAD is reset;
- another branch is checked out;
- a different commit carries an equivalent-looking tree.

That last case is why the commit SHA is bound and not just the tree: an empty
commit leaves both the tree and the working-tree delta byte-identical, so only
exact-commit binding catches it.

Stable failure reasons appear in `seal_stale_reasons` and, for `--require-seal`,
in `seal_required_failure_reasons`:

| Reason | Meaning |
| --- | --- |
| `seal_fingerprint_mismatch` | task, evidence, or working-tree delta changed |
| `seal_review_mismatch` | the review ledger changed after acceptance |
| `seal_head_mismatch` | HEAD commit differs from the accepted commit |
| `seal_head_tree_mismatch` | HEAD tree differs while the commit matches |
| `seal_missing_head_identity` | 0.4.0 seal with no repository identity |
| `seal_schema_unsupported` | schema version is missing or unknown |
| `seal_schema_inconsistent` | declared version disagrees with the record's contents |

`seal_status` summarises the same state as `current`, `stale_fingerprint`,
`review_mismatch`, `head_mismatch`, `legacy_unbound`, `schema_unsupported`,
`schema_inconsistent`, or `none`.

### Schema enforcement

The declared `schema_version` is interpreted *before* anything else, so a
tampered version can never be validated as if it were current:

| Record | Result |
| --- | --- |
| `2` with all required v2 fields | validated normally |
| `2` missing `repository_identity` or `review_ledger_fingerprint` | `seal_schema_inconsistent` |
| `1` with no v2-only fields (genuine 0.4.0) | `legacy_unbound` |
| `1` carrying v2-only fields | `seal_schema_inconsistent` |
| missing, or any other value | `seal_schema_unsupported` |

Every non-current outcome fails `sera check --require-seal`.

### 0.4.0 seal compatibility

0.4.0 seals contain no repository identity, so they cannot demonstrate that
acceptance happened at any particular commit. They are never silently treated as
equivalent: they report `legacy_unbound` and fail closed under `--require-seal`.
Run `sera seal` again to bind the current identity. This is deliberate — the
alternative would be inferring an exact-head guarantee that was never recorded.

### Dirty-worktree semantics

A seal binds the **baseline HEAD** plus the **accepted task delta**, following the
existing state model. Pre-existing dirty paths recorded at task creation stay the
user's baseline and are not part of the delta; changes made to owned files after
task creation are. Exact-head binding is additive — it does not replace or relax
any dirty-worktree protection.

## Configuration validation

`.sera/config.json` is validated on load, before any downstream use. Expected
mistakes surface as ordinary SERA errors on the normal CLI path — never as a
Python traceback:

```console
$ sera map
error: controller must be an object, got NoneType.

$ sera map
error: token_budgets.fast must be an integer, got str.
```

Types and shapes are checked for `default_mode`, `schema_version`,
`max_builder_attempts`, `max_file_bytes`, `max_packet_chars`, `exclude_dirs`,
`verification`, the `controller` block and its scalars, `token_budgets` and each
mode budget, `risk_policy` and its two lists, every `lanes` entry, and `rules`.

An explicit `null` is a malformed value, not an omission: omit a key entirely to
accept its default. Numeric settings reject booleans, and budgets must be
positive.

## Dirty worktrees

0.4 records the dirty worktree at task creation. Existing modified/untracked files are treated as the user's baseline, not as task scope. If one of those files changes again during the task, SERA notices the mutation and applies normal ownership checks.

This makes SERA usable in real repositories without requiring destructive cleanup, reset, or stash operations before every task.
