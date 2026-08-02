# SERA

**Specify. Execute. Review. Accept.**

A token-efficient multi-model software delivery protocol by Serlekan.

SERA is not another “let several agents talk until something works” framework. It is a small local control plane that gives each model the minimum context needed for one bounded job, records machine-checkable evidence, and prevents stale reviews from approving a changed tree.

The core idea is **relay, not conversation**:

```text
SERA Map → Task Capsule → Cheapest adequate builder → Evidence Ledger
         → Compact review packet → Fingerprint-bound verdict → SERA Seal
```

It works with Codex, Claude Code, Fable 5, or any other model because the source of truth is the repository state and a compact task packet—not a long shared chat transcript.

## Why this is different

Most agent workflows waste time and tokens in four places:

1. Every model rereads the repository.
2. Full conversation history is forwarded between agents.
3. Reviewers receive prose claims instead of reproducible evidence.
4. A review remains “approved” even after more edits are made.

SERA addresses each one directly.

### 1. Compact repository maps

`sera map` scans text files once and stores paths, sizes, hashes, languages, and important symbols. Agents can orient from a small reusable map before opening source files.

### 2. Task capsules

Every task is reduced to a stable contract: objective, exact file ownership, constraints, risk, uncertainty, and verification commands. The capsule is intentionally smaller than the discussion that produced it.

### 3. Adaptive routing

The router chooses the cheapest adequate lane from task risk, uncertainty, owned file count, context size, and assurance mode. Model names are configuration, not architecture.

### 4. Evidence ledgers

Verification results are stored as compact JSONL records containing the command, exit code, summary, output hash, and output size. Reviewers consume evidence instead of repeated logs.

### 5. Fingerprint-bound review

A review verdict is bound to a fingerprint of the task contract, evidence ledger, staged diff, and unstaged diff. Any post-review change makes the verdict stale automatically.

### 6. Hard loop limits

The default policy allows two builder attempts. Repeating an unchanged prompt is not a strategy; the task must be respecified, escalated, or returned to architecture.

## Productivity modes

| Mode | Intended use | Review policy | Typical token budget |
|---|---|---|---:|
| `fast` | Low-risk, bounded edits | Review only when scope expands | 6,000 |
| `standard` | Normal feature and bug work | Independent review | 16,000 |
| `assured` | Security, money, migrations, public APIs, broad changes | Independent review plus release gate | 32,000 |

Budgets are warnings and routing inputs, not provider billing controls.

## Model-neutral lanes

The default configuration demonstrates one possible setup:

| Lane | Default model | Responsibility |
|---|---|---|
| Planner | GPT-5.6 Sol | Resolve intent and architecture |
| Fast builder | GPT-5.6 Luna | Mechanical, bounded implementation |
| Deep builder | Claude Sonnet 5 | Substantial implementation and debugging |
| Independent reviewer | Claude Opus 5 | Fresh-context review without edits |
| Release gate | GPT-5.6 Sol | Final high-risk acceptance |
| Optional specialist | Fable 5, disabled by default | Prototypes, creative UI, recovery attempts, supplementary review |

Change any lane in `.sera/config.json`. A team can run Claude-only, Codex-only, Fable-assisted, local-model, or mixed-provider workflows.

Fable 5 is optional. It is never a silent fallback and never the sole release gate by default.

## Install

Requires Python 3.11 or newer and Git.

```bash
python -m pip install --user --no-build-isolation .
```

Windows PowerShell:

```powershell
./scripts/install.ps1
```

macOS or Linux:

```bash
./scripts/install.sh
```

Confirm installation:

```bash
sera --version
```

Portable single-file build without installation:

```bash
python scripts/build_zipapp.py
python dist/sera.pyz --version
```

## Five-minute workflow

### 1. Initialize a repository

```bash
sera init
sera map
```

This creates:

```text
.sera/config.json
.sera/cache/repo-map.json
.sera/cache/repo-map.md
.sera/tasks/
```

### 2. Create a task capsule

```bash
sera task new "fix invoice status" \
  --objective "Prevent completed status when payment authority is incomplete" \
  --mode assured \
  --risk high \
  --uncertainty 1 \
  --file src/invoices/status.ts \
  --file tests/invoices/status.test.ts \
  --constraint "Fail closed when required sources are unavailable" \
  --verify "npm test -- tests/invoices/status.test.ts" \
  --verify "npm run typecheck"
```

### 3. Route it

```bash
sera route
```

Example:

```text
deep_builder: anthropic/claude-sonnet-5
independent_reviewer: anthropic/claude-opus-5
release_gate: openai/gpt-5.6-sol
owned-context estimate: 7,830 tokens
stage budget: 32,000 tokens
```

### 4. Generate the builder packet

```bash
sera packet build
```

The generated packet contains only the contract, route, owned-file metadata, constraints, and verification requirements. It does not forward the full chat history.

### 5. Run and record verification automatically

```bash
sera verify
```

The CLI runs the task's commands, stores local logs, and appends compact evidence records. Manual recording remains available for commands run elsewhere.

```bash
sera record \
  --command "npm test -- tests/invoices/status.test.ts" \
  --exit-code 0 \
  --summary "Focused invoice status tests passed: 12/12" \
  --output-file test-output.txt
```

### 6. Generate a review packet

```bash
sera packet review
```

The review packet contains a bounded diff, changed files, evidence hashes, and an exact verdict contract.

### 7. Bind the verdict to the current tree

```bash
sera review \
  --stage independent \
  --verdict ship \
  --reviewer "claude-opus-5" \
  --reason "Scope, tests, and fail-closed behavior match the task contract"

# Assured and high-risk tasks also require the configured gate.
sera review \
  --stage gate \
  --verdict ship \
  --reviewer "gpt-5.6-sol" \
  --reason "The independently reviewed tree satisfies the release boundary"

sera seal
sera check --require-seal
```

The seal is a compact completion artifact for the exact reviewed tree. A later edit changes the fingerprint, makes the seal stale, and blocks release until verification and review run again.

## Commands

```text
sera init                     Initialize local policy and state
sera map                      Build the compact repository map
sera task new                 Create a task capsule
sera route                    Select the cheapest adequate lanes
sera packet build             Generate a compact implementation handoff
sera packet review            Generate a compact review handoff
sera verify                   Run verification and record evidence automatically
sera record                   Append verification evidence manually
sera budget                   Estimate context reuse and token savings
sera review                   Record a fingerprint-bound verdict
sera summary                  Generate a compact PR or handoff summary
sera seal                     Seal the exact verified and reviewed tree
sera check                    Enforce scope, evidence, review, and seal freshness
sera status                   Show the current task state and next action
```

## Measure the savings

```bash
sera budget
```

Example output:

```text
Full-source orientation: ~84,200 tokens
Map + capsule orientation: ~2,100 tokens
Estimated orientation avoided: ~82,100 tokens (97.5%)
Owned context: ~7,830 tokens
Stage budget: 16,000 tokens
Within budget: yes
```

These are provider-neutral estimates, not billing claims. They make context growth visible before an agent session becomes expensive.

## How it saves tokens

A normal agent handoff may resend architecture discussion, repository summaries, source files, tool logs, and previous agent messages. SERA replaces that with four reusable artifacts:

- repository map;
- task capsule;
- evidence ledger;
- bounded diff packet.

The CLI prints estimated packet size so a team can see when context is growing beyond the selected mode. See [Token efficiency](docs/TOKEN-EFFICIENCY.md).

## How it saves time

- Repository orientation is cached by content hash.
- Builders receive exact ownership before they start.
- Verification evidence is recorded once and reused.
- Review packets and PR summaries are generated automatically.
- Post-review changes invalidate approval and the SERA Seal immediately.
- Two-attempt defaults prevent expensive prompt loops.
- Risk modes remove unnecessary heavyweight review from trivial tasks.

## Native model adapters

SERA does not pretend Codex can spawn Claude or Claude Code can spawn GPT. The CLI generates portable packets, while each runtime handles its own native agent invocation.

- [Codex adapter](adapters/codex/README.md)
- [Claude Code adapter](adapters/claude/README.md)
- [Optional Fable 5 usage](docs/FABLE-5.md)

## Repository structure

```text
src/sera/       Zero-dependency Python CLI
adapters/                   Runtime-specific operating instructions
config/                     Example team configurations
docs/                       Architecture and token-efficiency design
examples/                   End-to-end task examples
templates/                  Portable task and review contracts
tests/                      CLI and safety tests
```

## Design principles

1. Repository state outranks agent narrative.
2. Context is pulled only when needed.
3. Every builder owns an explicit file set.
4. The cheapest adequate model is preferred.
5. Evidence is hashed and reusable.
6. Reviewers do not implement their own fixes.
7. Every change after review invalidates the verdict and final seal.
8. Completion, commit, merge, and deployment are separate decisions.

## Status

Version `0.3.0` is an alpha release. The CLI is functional and uses only the Python standard library. Provider-specific automatic invocation is intentionally outside the core; adapters can evolve without coupling the protocol to one vendor.

## History and attribution

An early prototype explored the architect/builder/reviewer pattern used by Sol Advisor. Version 0.3.0 is a clean implementation centered on repository maps, task capsules, adaptive budgets, evidence ledgers, and fingerprint-bound review. The current core does not import or depend on Sol Advisor code.

## License

MIT
