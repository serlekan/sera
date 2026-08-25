# SERA

**Specify. Execute. Review. Accept.**

A token-efficient, model-neutral controller for AI-assisted software delivery.

SERA is the layer between a product request and the coding agents that implement it. It maps a repository, turns intent into a bounded task, selects the cheapest adequate lane, records reproducible evidence, requires fresh review when risk demands it, and seals only the exact reviewed tree.

Version **0.4.2** binds review identity and change coverage: a review packet and an accepted review record are both bound to the exact `HEAD` commit and tree the reviewer inspected, review evidence covers what the task committed as well as what is still dirty, tracked SERA policy is reviewable while SERA runtime state stays out, and a failed review stops the workflow before any later gate. It builds on 0.4.1's project-defined risk policy, honored mode defaults, ownership separated from stage context, and seals bound to the exact Git commit.

```text
You / AI controller
        ↓
     sera run
        ↓
Delta Map → Context Selection → Task Capsule → Ownership
        ↓
      Route
        ↓
Builder Packet → Implementation → Evidence Ledger
        ↓
Fresh Review → Optional Release Gate → SERA Seal
```

The core remains provider-neutral. SERA prepares and validates work; Codex, Claude Code, or another runtime performs provider invocation.

## Why SERA

AI coding gets expensive and unreliable when every agent starts from zero.

Common waste looks like:

- rereading thousands of repository files;
- forwarding long chat history between models;
- using premium reasoning for small mechanical work;
- sending raw test/build logs into review context;
- losing track of which files an agent was allowed to edit;
- trusting “done” after the reviewed tree changed;
- forcing a clean worktree before every agent task.

SERA turns those into explicit repository state.

### Before SERA

```text
request
+ old chat
+ repository summary
+ repeated source
+ builder narrative
+ raw logs
+ reviewer narrative
→ another large prompt
```

### With SERA

```text
Task Capsule
+ justified context
+ exact ownership
+ bounded diff
+ compact evidence
→ only the model that needs it
```

## What 0.4 adds

### Controller state machine

```bash
sera next
sera resume
sera inbox
```

A fresh AI session can reconstruct the active engineering state from the repository instead of requiring the previous conversation.

### Natural-language task preparation

```bash
sera run "fix payment reconciliation"
```

SERA can:

- update the repository map;
- rank relevant files/symbols;
- compose a risk level from built-in and project-defined policy;
- escalate high-risk work to `assured`;
- choose configured builder/reviewer/gate lanes;
- prepare a compact builder handoff.

Inferred files are **candidate ownership**, not permission to edit. Confirm them first:

```bash
sera task confirm
```

Confirming ownership re-runs risk policy against the new file set. A task drafted
low-risk and then confirmed onto `critical/**` is escalated to `assured` and
rerouted to independent review plus the release gate — ownership changes cannot
bypass policy.

Handoff artifacts are freshness-bound to the task contract, so a packet built for
the old contract is never dispatched. `sera next` asks you to regenerate, and the
new packet carries the current route and ownership. Packet existence alone never
means packet freshness.

Or provide exact files up front:

```bash
sera run "fix payment reconciliation" \
  --file src/payments/service.py \
  --file tests/test_payments.py \
  --verify "python -m unittest"
```

### Project-defined risk policy

Built-in risk terminology is generic on purpose. Declare what *your* repository
treats as dangerous in `.sera/config.json`:

```json
{
  "risk_policy": {
    "high_risk_terms": ["payment", "authentication", "production deployment"],
    "high_risk_paths": ["src/auth/**", "src/payments/**", "migrations/**"]
  }
}
```

Terms match case-insensitively as whole tokens or phrases, so `order` matches
`cancel the order` but never `reorder`. Paths use `**` / `*` / `?` glob syntax.
Effective risk is the maximum of the built-in classifier, your terms, your paths,
and any explicit `--risk`; an explicit level can raise risk but never silently
lowers a detected one. Every escalation is explained:

```json
{
  "risk": "high",
  "risk_reasons": [
    {"type": "project_term", "value": "execution"},
    {"type": "project_path", "value": "trading/risk/**", "matched": "trading/risk/limits.py"}
  ]
}
```

### Ownership is not context

Ownership is what a task may **change**. Context is what a stage must **read**.
They are budgeted separately, so a task owning 36 files can still hand a builder a
12-file packet and a reviewer a different one:

```json
{
  "ownership": {"file_count": 36, "estimated_tokens": 227307},
  "selected_context": {"file_count": 10, "estimated_tokens": 28140}
}
```

A file excluded from context keeps its ownership, and a large owned set never on
its own triggers `reduce_context`.

### Context that earns its place

```bash
sera context --why
```

Every changed file reaches the reviewer with its own bounded patch — SERA never
truncates one combined diff at its ends, which used to drop the files in between.
Reported success means the rendered diff is **exactly** within budget
(`len(text) <= max_chars`, counting Unicode code points), because budgeting
measures the real rendered output rather than estimating it. If the budget cannot
cover every changed file, packet generation fails with
`review_diff_budget_insufficient` rather than implying complete coverage.

SERA reports why each file earned or lost its place — `selected_owned`,
`selected_changed_file`, `selected_dependency`, `owned_not_selected`,
`excluded_by_budget`, `not_in_repository_map` — and compares selected stage
context with repository context available.

### Delta repository maps

```bash
sera map --update
```

Unchanged files reuse their previous map metadata. Changed files are reread and reparsed.

### Measurable efficiency

```bash
sera cost
```

Example shape:

```text
SERA efficiency score: 98/100
Repository context available: ~5,562,935 tokens
Selected orientation: ~18,400 tokens
Context reduction: ~5,544,535 tokens (99.67%)
Evidence tokens avoided: ~9,300
```

These are local, provider-neutral estimates—not API billing telemetry and not a claim that a non-SERA workflow would literally transmit the full repository.

### Dirty-worktree safety

SERA now snapshots pre-existing dirty paths when a task starts.

Existing user changes are treated as baseline. If they remain unchanged, they do not become task scope. If they change again during the task, SERA detects the mutation and applies ownership rules.

## What 0.4.2 adds

### Reviews are bound to the repository that was reviewed

A review packet records the exact `HEAD` commit and tree it was generated
against, and states them in the packet body. `sera review` refuses to record a
verdict unless that packet is still current, then reads the reviewed identity
from Git rather than accepting a caller-supplied SHA. Once HEAD moves — including
by an empty commit that leaves the tree and the working delta untouched — the
packet and the accepted review both go stale:

```bash
sera packet review          # bound to HEAD A
git commit --allow-empty -m "anything"
sera next                   # review: packet_stale_head
sera seal                   # refused: reviews describe a different commit
```

The release gate is a review stage and obeys the same rule, so a gate can never
inherit an independent review taken at a different commit. `sera seal` binds a
seal only when every required review is `ship`, fingerprint-current, and bound to
the current `HEAD` and tree.

Downstream projects no longer need to implement a manual reviewed-HEAD
comparison of their own: on 0.4.2 the controller enforces it natively. 0.4.1 and
earlier did not — reviews recorded by those versions carry no repository
identity, report `review_repository_unbound`, and fail closed until the review is
repeated on 0.4.2.

### Review evidence covers committed work

Change evidence used to be built from staged and unstaged state alone, so a task
whose implementation was committed reported "No task-relative changes to review"
on a clean worktree. The change set now spans the task's baseline commit through
the current `HEAD`, unioned with task-relative working-tree changes, with one
canonical review block per file carrying its cumulative change:

```text
### `src/payments/gateway.py`
- status: modified
- location: committed+unstaged
```

Committed work is scope-checked too: a file committed after the task began is a
task change even with a clean worktree. Every **project-visible** side of a Git
change is preserved for scope and review reasoning, including renames across the
SERA runtime boundary:

| Change | Preserved as |
| --- | --- |
| `src/app.py` → `src/new.py` | ordinary rename; both paths |
| `src/app.py` → `.sera/tasks/x.py` | deletion of `src/app.py` |
| `.sera/tasks/x.py` → `src/app.py` | addition of `src/app.py` |
| `.sera/tasks/a` → `.sera/cache/b` | excluded entirely |

Runtime state itself is never turned into review content by any of these — only
the project-visible side of the move survives. A copy into runtime state
synthesizes no deletion, because a copy leaves its source in place.

Tasks created before 0.4.2 have no baseline commit recorded and cannot
have their committed range derived; they report `review_baseline_unbound` and
fail closed rather than claiming coverage they do not have.

A task started in a repository with **no commits yet** records the explicit
sentinel `unborn` for its baseline rather than a symbolic revision such as
`HEAD`, which would silently re-resolve to whatever HEAD became later. Its first
commit is diffed against Git's empty tree, so the whole first commit — and every
commit after it — remains reviewable.

### Tracked SERA policy is reviewable; runtime state is not

`.sera/**` holds two different kinds of file, and 0.4.1 excluded both. Now only
generated runtime state is excluded — `.sera/cache/**`, `.sera/tasks/**`, and
`.sera/latest-task`. Anything else a team tracks there, such as
`.sera/config.json` or `.sera/POLICY.md`, is ordinary repository content eligible
for ownership, change detection, scope checking, and review evidence. Task
capsules, packets, ledgers, and seals still cannot reach a review packet, even if
a repository commits them by mistake.

**Known limitation.** The compact repository map still excludes `.sera/**`
entirely, so a tracked policy file appears in packets as "not yet indexed in the
repository map". That affects orientation and context ranking only. Ownership,
change detection, scope checking, and review evidence for those files are
complete; the limitation is not a review-coverage gap.

### Currency and coverage are reported separately

`current` means nothing was tampered with and nothing moved. It never meant "the
reviewer is seeing everything", and it is no longer allowed to imply it:

```json
{ "state": "dispatch_review", "review_coverage": { "complete": true, "reason": null } }
```

`coverage_complete` is true only when **all four** of these hold:

1. the committed range resolved;
2. review diff budgeting succeeded;
3. no task change lies outside declared ownership;
4. every authoritative changed path is represented by real evidence.

Conditions 3 and 4 matter because evidence is generated from the files a task
*owns*, while the authoritative change set is the whole repository delta. A task
owning `src/alpha.py` that commits both `src/alpha.py` and `src/unowned.py` has
no evidence for the second file, so its coverage is not complete — regardless of
the owned-file diff having rendered perfectly:

```bash
sera next            # resolve_scope: src/unowned.py is outside declared ownership
sera packet review   # refused: review_scope_unresolved
```

`sera packet review` fails closed on that directly, not only through `sera next`,
so a packet claiming "Change coverage: complete" can never be emitted while an
out-of-scope task change stands. SERA does not silently widen ownership to make
the problem go away; splitting, reverting, or deliberately declaring ownership is
the operator's decision. `sera next --json` reports `out_of_scope_paths` and
`missing_evidence_paths` under `review_coverage`.

A review packet is dispatchable only when it is current **and** its coverage is
complete. If every changed file cannot fit the character budget, the existing
`review_diff_budget_insufficient` failure is unchanged.

### A failed review outranks a missing later gate

`sera next` evaluated missing reviews before failed ones, so a task with a
current `fix-first` independent review and no gate yet was told to run the
release gate. Required-review precedence is now: stale review → current failed
review → missing review → seal.

```bash
sera next   # fix_first: independent returned `fix-first`; address it first
```

## Core principles

1. **Repository state outranks agent narrative.**
2. **Context is a budget, not an entitlement.**
3. **Every context item needs a reason.**
4. **Inferred relevance is not edit permission.**
5. **The cheapest adequate lane wins.**
6. **Builders do not self-approve.**
7. **Raw logs stay local unless investigation needs them.**
8. **Any post-review mutation invalidates acceptance.**
9. **Completion, commit, merge, deploy, and release are separate decisions.**
10. **SERA measures what it saves.**

## Productivity modes

| Mode | Intended use | Review policy | Default budget |
|---|---|---|---:|
| `fast` | Low-risk bounded work | Review only when scope expands | 6,000 |
| `standard` | Normal feature/bug work | Independent review | 16,000 |
| `assured` | Security, money, migrations, public APIs, broad changes | Independent review + release gate | 32,000 |

The mode comes from your project's `default_mode` unless an explicit `--mode` is
given. Precedence is `explicit CLI mode > default_mode > standard`, and invalid
values fail closed. High-risk work is escalated to `assured` regardless of the
requested mode. Budgets apply to selected stage context, not to total ownership.

## Default lanes

Model names are configuration, not architecture.

| Lane | Default | Responsibility |
|---|---|---|
| Planner | `openai/gpt-5.6-sol` | Intent and architecture |
| Fast builder | `openai/gpt-5.6-luna` | Mechanical/bounded implementation |
| Deep builder | `anthropic/claude-sonnet-5` | Substantial implementation/debugging |
| Independent reviewer | `anthropic/claude-opus-5` | Fresh read-only review |
| Release gate | `openai/gpt-5.6-sol` | High-risk acceptance |
| Optional specialist | `anthropic/claude-fable-5` | Explicit specialist lane, disabled by default |

Change lanes in `.sera/config.json`. Required disabled lanes fail closed; SERA never silently substitutes another model.

## Install

Requires Python 3.11+ and Git.

Install directly from GitHub:

```bash
python -m pip install --user --upgrade "git+https://github.com/serlekan/serlekan-sera.git@main"
```

From a local clone:

```bash
python -m pip install --user --no-build-isolation .
```

Windows PowerShell:

```powershell
./scripts/install.ps1
```

macOS/Linux:

```bash
./scripts/install.sh
```

Confirm:

```bash
sera --version
```

If the scripts directory is not on PATH:

```bash
python -m sera.cli --version
```

## Initialize a project

```bash
sera init
sera map
```

Creates:

```text
.sera/
  config.json
  cache/
    repo-map.json
    repo-map.md
  tasks/
```

Commit `.sera/config.json` when it represents project policy. Runtime cache/packets are ignored by default.

## Recommended 0.4 workflow

### 1. Start from a normal product request

```bash
sera run "prevent completed invoice state when payment authority is incomplete"
```

If SERA inferred candidate files:

```bash
sera context --why
sera task confirm --file src/invoices/status.py --file tests/test_invoice_status.py
sera packet build
```

If exact files were supplied to `sera run`, the builder packet is created immediately.

### 2. Ask what happens next

```bash
sera next
```

Typical states include:

```text
confirm_ownership
build_packet
dispatch_builder
verify
review_packet
dispatch_review
fix_first
seal
accepted
```

### 3. Verify implementation

```bash
sera verify
```

The evidence ledger records command, exit code, summary, output hash, and size. Full logs remain local.

### 4. Review from fresh context

```bash
sera packet review
```

That packet is bound to the exact `HEAD` commit and tree it was generated
against, and it carries every task change — committed, staged, and unstaged.
Give it to the configured reviewer and record the result:

```bash
sera review \
  --stage independent \
  --verdict ship \
  --reviewer "claude-opus-5" \
  --reason "Scope and verification satisfy the contract"
```

SERA refuses the verdict unless that packet is still current, then records the
reviewed `HEAD` and tree from Git. If HEAD moved in between, regenerate the
packet and repeat the review; the earlier verdict does not carry over.

Assured/high-risk work also requires the configured gate, which binds identity
the same way. A `fix-first` or `rethink` verdict stops the workflow immediately
rather than letting a later gate be dispatched for rejected work.

### 5. Seal exact acceptance

```bash
sera seal
sera check --require-seal
```

A seal binds the task, evidence, working-tree delta, relevant untracked state,
**the review ledger that justified acceptance**, and **the exact `HEAD` commit and
tree**. Since 0.4.2 the seal cannot be created at all unless every required
review is `ship`, fingerprint-current, and bound to that same current `HEAD` and
tree, so a seal can never describe a commit nobody reviewed. Editing an accepted
reviewer, rationale, or verdict afterwards makes the seal stale with
`seal_review_mismatch`, and a seal whose `schema_version` is
missing, unknown, or inconsistent with its contents fails closed. A later code change makes the
verdict and seal stale — and so does moving HEAD at all:

```bash
git commit -m "anything"
sera check --require-seal   # exit 2: seal_head_mismatch
```

That holds for a new commit, a reset, a branch checkout, and a different commit
carrying an identical tree. Seals written by 0.4.0 carry no repository identity;
they report `legacy_unbound` and fail closed until re-sealed.

## Fresh chat? Resume, don't re-explain

```bash
sera resume
```

or for AI controllers:

```bash
sera resume --json
```

The output includes the objective, mode/risk, ownership, budget, route, fingerprint, and next required action.

## Project inbox

```bash
sera inbox
```

Shows every local SERA task and its current next action.

## Machine-readable controller API

These commands support `--json`:

```text
sera map
sera task auto
sera run
sera route
sera context
sera budget
sera cost
sera next
sera resume
sera inbox
sera check
sera status
```

This is the preferred interface for ChatGPT/Codex or another orchestrator.

Example:

```bash
sera next --json
```

```json
{
  "next_action": "verify",
  "command": "sera verify",
  "reason": "Required verification evidence is missing."
}
```

## Commands

```text
sera init                  Initialize project policy/state
sera map                   Build the repository map
sera map --update          Refresh only changed map entries
sera task new              Create an explicit task capsule
sera task auto             Draft task/risk/context from a request
sera task confirm          Confirm exact edit ownership
sera run                   Prepare a task and route in one step
sera context --why         Show selected context and inclusion/exclusion reasons
sera route                 Select configured lanes
sera packet build          Generate implementation handoff
sera verify                Run verification and record evidence
sera packet review         Generate fresh-review handoff
sera review                Record a verdict bound to the reviewed HEAD and tree
sera next                  Return the next state-machine action
sera resume                Reconstruct active task from repository state
sera inbox                 Show all local tasks
sera budget                Show stage context budget
sera cost                  Show token/context efficiency estimates
sera summary               Generate PR/handoff summary
sera seal                  Seal exact accepted tree
sera check                 Enforce scope/evidence/reviews/seal
sera status                Show current task state
```

## What SERA does not do

SERA 0.4 intentionally does not hide model-provider calls inside the core.

It can say:

```text
builder: anthropic/claude-sonnet-5
reviewer: anthropic/claude-opus-5
release_gate: openai/gpt-5.6-sol
```

The surrounding runtime then dispatches the generated packet.

This keeps SERA usable with Codex, Claude Code, local agents, CI, and future providers without tying repository safety to one SDK.

## Documentation

- [Oryol Workspace Specialization](docs/ORYOL.md)
- [Controller](docs/CONTROLLER.md)
- [Token efficiency](docs/TOKEN-EFFICIENCY.md)
- [Workflow](docs/WORKFLOW.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Codex adapter](adapters/codex/README.md)
- [Claude Code adapter](adapters/claude/README.md)
- [Optional Fable usage](docs/FABLE-5.md)

## Development

```bash
python -m pip install -e . --no-build-isolation
python -m unittest discover -s tests -v
git diff --check
sera --version
```

The project intentionally keeps the core dependency-free.

## Status

Version `0.4.2` is an alpha controller release. The repository-state protocol is functional; provider-specific automatic dispatch remains adapter/controller-runtime work for later releases.

The threat model is unchanged and remains local consistency: SHA-256 over local Git and file state detects drift and accidental or careless tampering with SERA's own records. It is not signing, HMAC, remote attestation, or a claim of adversarial authenticity.

## History and attribution

An early prototype explored the architect/builder/reviewer pattern used by Sol Advisor. SERA's current implementation is independently centered on repository maps, task capsules, context budgets, evidence ledgers, controller state, dirty-worktree baselines, and fingerprint-bound acceptance. It does not import or depend on Sol Advisor code.

## License

MIT
