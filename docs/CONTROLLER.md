# SERA Controller

Version 0.4 adds a controller layer on top of the original relay protocol. Version
0.4.1 hardens it for high-assurance repositories.

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

## `sera next`

`sera next` makes the repository state machine explicit. It can return steps such as:

- confirm ownership;
- generate a build packet;
- dispatch the builder;
- run verification;
- generate a review packet;
- repeat stale review;
- seal the accepted tree.

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

## Exact-head acceptance

A 0.4.1 seal (schema version 2) binds acceptance to the exact reviewed repository
identity:

```text
task + evidence + reviews + HEAD + HEAD tree + working-tree delta + relevant untracked state
```

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
| `seal_head_mismatch` | HEAD commit differs from the accepted commit |
| `seal_head_tree_mismatch` | HEAD tree differs while the commit matches |
| `seal_missing_head_identity` | 0.4.0 seal with no repository identity |

`seal_status` summarises the same state as `current`, `stale_fingerprint`,
`head_mismatch`, `legacy_unbound`, or `none`.

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

## Dirty worktrees

0.4 records the dirty worktree at task creation. Existing modified/untracked files are treated as the user's baseline, not as task scope. If one of those files changes again during the task, SERA notices the mutation and applies normal ownership checks.

This makes SERA usable in real repositories without requiring destructive cleanup, reset, or stash operations before every task.
