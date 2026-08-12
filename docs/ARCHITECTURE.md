# Architecture

SERA 0.4 has three layers.

## 1. Repository-state core

The zero-dependency Python core owns deterministic work:

- content-hashed repository maps;
- configuration validation;
- task contracts and dirty-worktree baselines;
- mode precedence and risk composition, re-derived whenever ownership changes;
- routing inputs;
- evidence storage;
- review fingerprints;
- scope/freshness checks;
- SERA Seals and exact-head identity.

## 2. Context-selection layer

`sera.context` sits between the core and the controller. It depends on the core
and nothing above it, so selection stays a pure function of repository state:
stage-aware ranking, budget-bounded selection, and the inclusion/exclusion reason
taxonomy.

## 3. Controller layer

The controller translates a natural-language engineering request into compact, inspectable state:

- automatic risk/mode draft;
- relevance-ranked context selection;
- exact ownership confirmation;
- next-action state machine;
- resume/inbox views;
- context and evidence-efficiency reports.

The controller is still provider-neutral. It prepares dispatch; it does not hide provider calls inside the core.

## 4. Runtime adapters

Codex, Claude Code, or another runtime consumes SERA packets and performs actual model invocation. Provider SDK churn stays outside the repository-state protocol.

## Ownership versus context

0.4.1 separates two things 0.4.0 conflated:

- **ownership** — the files a task may modify; an authorization surface;
- **selected context** — the files one stage reads; a token budget.

Both are sized and reported independently. The mode budget constrains selected
context only, so a large owned set never blocks a task whose stage context fits.
A file removed from context keeps its ownership.

## Context selection

Selection is lexical and symbol-aware. Paths and exported symbols that match
objective terms receive a score; ownership, changed status, and explicit pinning
add priority above that. Selection is deterministic: candidates are ordered by
score, then size, then path, and filled greedily against the file cap and the
stage token allowance. The highest-priority candidate is always included so a
stage is never handed empty context.

Change evidence is assembled per file, not as one combined patch trimmed at its
ends, so no changed file can be lost between the first and last. One canonical
renderer produces each block and the budget algorithm measures that renderer's
actual output, so header width, counter digits, and omission markers cannot drift
from what a reviewer receives. Budget is allocated in two phases — a measured
guaranteed minimum for every changed file, then water-filled relevance for the
remainder — followed by a final rendered-length guard. Reported success therefore
means `len(text) <= max_chars` exactly, in Unicode code points; otherwise
generation fails closed.

Review selection is diff-aware. Changed files rank above unchanged owned files,
and files imported by changed files are pulled in as dependencies using a bounded,
language-agnostic scan of import-like lines in the changed files only. Every
changed file remains represented through the bounded diff regardless of selection.

Inferred files remain candidate ownership until confirmed.

Later versions can replace the scorer with a richer dependency graph without changing the task protocol.

## Dirty-worktree baseline

Task creation records fingerprints of pre-existing dirty paths. Scope checks compare the current worktree against that baseline. Unchanged user work is preserved; new mutations are attributed to the task.

## Delta maps

`sera map --update` reuses unchanged map entries and rescans changed files. It uses previous HEAD, Git dirty paths, size, and mtime to decide what must be reread.

## Fingerprint-bound review

Review validity still depends on the exact task, evidence, staged diff, unstaged diff, and relevant untracked content. Any later mutation makes a required verdict stale.

## Derived-artifact freshness

Handoff artifacts are bound to a task-contract fingerprint covering only the
semantic contract — objective, requested and derived policy, ownership,
constraints, verification. Generated content is excluded, so an artifact can be
compared against the contract that produced it without circularity.

Packet provenance is stored adjacent to each packet rather than parsed back out
of markdown. Existence never implies freshness: missing, malformed, or mismatched
provenance fails closed and forces regeneration. Verification evidence carries
the same binding, so evidence gathered under a superseded contract stops
satisfying it while remaining in the ledger for audit.

## Acceptance composition

Acceptance composes three independent fingerprints rather than one:

```text
task_fingerprint  +  review_ledger_fingerprint  +  repository_identity
```

`review_ledger_fingerprint` is a canonical hash of every persisted review record.
Keeping it separate from `task_fingerprint` avoids a circular definition: reviews
record the task fingerprint they judged, so reviews cannot also be inside it.
Review freshness binds the task fingerprint; acceptance binds all three.

Seal records are versioned, and the declared version is interpreted before any
other field, so a relabelled or unknown schema fails closed instead of being
validated as current.

## Exact-head acceptance

Seals additionally bind the exact `HEAD` commit and `HEAD` tree. Review
fingerprints deliberately remain HEAD-independent so a reviewer's verdict is not
invalidated by unrelated commit activity; acceptance is the step that must name a
single commit. Seals are versioned, and a 0.4.0 seal without repository identity
fails closed rather than being read as an exact-head acceptance.
