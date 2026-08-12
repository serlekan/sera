# SERA

**Specify. Execute. Review. Accept.**

A token-efficient, model-neutral controller for AI-assisted software delivery.

SERA is the layer between a product request and the coding agents that implement it. It maps a repository, turns intent into a bounded task, selects the cheapest adequate lane, records reproducible evidence, requires fresh review when risk demands it, and seals only the exact reviewed tree.

Version **0.4.1** hardens the 0.4 controller for high-assurance repositories: project-defined risk policy, honored mode defaults, ownership separated from stage context, and acceptance bound to the exact Git commit.

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

Give that packet to the configured reviewer. Record the result:

```bash
sera review \
  --stage independent \
  --verdict ship \
  --reviewer "claude-opus-5" \
  --reason "Scope and verification satisfy the contract"
```

Assured/high-risk work also requires the configured gate.

### 5. Seal exact acceptance

```bash
sera seal
sera check --require-seal
```

A seal binds the task, evidence, working-tree delta, relevant untracked state,
**the review ledger that justified acceptance**, and **the exact `HEAD` commit and
tree**. Editing an accepted reviewer, rationale, or verdict afterwards makes the
seal stale with `seal_review_mismatch`, and a seal whose `schema_version` is
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
sera review                Record fingerprint-bound verdict
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

Version `0.4.1` is an alpha controller release. The repository-state protocol is functional; provider-specific automatic dispatch remains adapter/controller-runtime work for later releases.

## History and attribution

An early prototype explored the architect/builder/reviewer pattern used by Sol Advisor. SERA's current implementation is independently centered on repository maps, task capsules, context budgets, evidence ledgers, controller state, dirty-worktree baselines, and fingerprint-bound acceptance. It does not import or depend on Sol Advisor code.

## License

MIT
