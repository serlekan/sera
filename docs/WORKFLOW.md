# SERA workflow

SERA means **Specify, Execute, Review, Accept**.

Version 0.4 adds a controller state machine so a fresh AI session can ask SERA what happens next instead of carrying a long chat forward.

## Specify

Start from the repository map and create a bounded task contract.

```bash
sera run "fix the reconciliation state"
```

SERA can draft candidate ownership, risk, mode, and context. Auto-selected ownership must be confirmed before dispatch:

```bash
sera task confirm --file src/example.py
```

Confirming ownership re-runs risk policy against the new file set, so a task
confirmed onto a high-risk path is escalated and rerouted rather than keeping its
original low-risk lane.

The task's mode comes from your project's `default_mode` unless you pass an
explicit `--mode`. High-risk work — from built-in terminology or from your own
`risk_policy` terms and paths — escalates to `assured` regardless.

Ownership is authorization, not a reading list. A task may own many files while
each stage selects only the context it needs; see
[CONTROLLER.md](CONTROLLER.md) for both budgets.

## Execute

`sera route` chooses the cheapest adequate configured lane. `sera packet build` creates the bounded handoff carrying stage-selected context. SERA does not call provider APIs; the surrounding controller/runtime dispatches the packet.

## Review

`sera verify` records reproducible evidence. A fresh reviewer receives the review packet, bounded diff, and evidence—not the builder conversation. It returns `ship`, `fix-first`, or `rethink`.

Review context is diff-aware rather than "every owned file", but every changed
file stays represented through its own bounded per-file patch. Narrower context,
same authority. If the budget cannot cover every changed file, packet generation
fails with `review_diff_budget_insufficient` instead of quietly omitting one.

## Accept

`sera seal` binds acceptance to the exact task/evidence/diff fingerprint, the
**review ledger that justified it**, and the **exact `HEAD` commit and tree**.
Editing an accepted reviewer, rationale, or verdict after sealing makes the seal
stale with `seal_review_mismatch`. `sera check --require-seal` rejects stale,
unsealed, or moved-HEAD work:

```bash
sera seal                     # accepted at HEAD A
sera check --require-seal     # exit 0

git commit -m "anything"      # HEAD is now B
sera check --require-seal     # exit 2: seal_head_mismatch
```

Seals written by 0.4.0 carry no repository identity. They report `legacy_unbound`
and fail closed under `--require-seal`; re-run `sera seal` to bind the current
commit.

## Continue from a fresh chat

```bash
sera resume
sera next
```

The repository, task capsule, evidence, reviews, and seal carry the engineering state. Chat history is disposable.
