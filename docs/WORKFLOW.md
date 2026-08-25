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
original low-risk lane. It also refreshes `capsule.md` and invalidates any
previously generated handoff packet: `sera next` will ask you to regenerate
rather than dispatch a packet built for the old contract.

The task's mode comes from your project's `default_mode` unless you pass an
explicit `--mode`. High-risk work — from built-in terminology or from your own
`risk_policy` terms and paths — escalates to `assured` regardless.

Ownership is authorization, not a reading list. A task may own many files while
each stage selects only the context it needs; see
[CONTROLLER.md](CONTROLLER.md) for both budgets.

## Execute

`sera route` chooses the cheapest adequate configured lane. `sera packet build` creates the bounded handoff carrying stage-selected context. SERA does not call provider APIs; the surrounding controller/runtime dispatches the packet.

### Historical workflow bootstrap exceptions

An exception is available only for a pre-native historical task whose builder
handoff history is unavailable. It preserves that history as missing and does
not claim that the builder ran or fabricate a builder packet, provenance, log,
or evidence. The exception does not remove the independent review, gate, or
seal requirements: those stages still run and remain visible in the audit
state. All future work must use the native full workflow, including the
builder and packet stages.

## Review

`sera verify` records reproducible evidence. A fresh reviewer receives the review packet, bounded diff, and evidence—not the builder conversation. It returns `ship`, `fix-first`, or `rethink`.

Review context is diff-aware rather than "every owned file", but every changed
file stays represented through its own bounded per-file patch. Narrower context,
same authority. If the budget cannot cover every changed file, packet generation
fails with `review_diff_budget_insufficient` instead of quietly omitting one.

The change set spans what the task committed since its baseline commit as well as
what is still staged or unstaged, so a fully committed branch on a clean worktree
still reaches the reviewer with real patch material. Generation fails rather than
emitting a packet that understates what changed — including when the task
committed work outside its declared ownership, since evidence is produced from
owned files and could not represent that change:

```bash
sera next            # resolve_scope: src/unowned.py is outside declared ownership
sera packet review   # refused: review_scope_unresolved
```

Split or revert the out-of-scope work, or declare ownership of it deliberately.
SERA does not widen ownership on its own.

Renaming a project file into SERA runtime state does not remove it from scope:
the project-visible side of the move is preserved as a deletion, while the
runtime destination stays out of review content.

The packet is bound to the exact `HEAD` commit and tree it was generated against,
and states them in its body. `sera review` records a verdict only against a
current packet, then reads the reviewed identity from Git:

```bash
sera packet review                     # bound to HEAD A
git commit --allow-empty -m "anything" # HEAD is now B
sera review --verdict ship ...         # refused: packet_stale_head
```

A `fix-first` or `rethink` verdict stops the workflow immediately. SERA will not
dispatch a later release gate for an implementation the independent stage already
refused.

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

A seal cannot be created at all unless every required review is `ship`,
fingerprint-current, and bound to that same current `HEAD` and tree, so a seal
can never describe a commit nobody reviewed.

Seals written by 0.4.0 carry no repository identity. They report `legacy_unbound`
and fail closed under `--require-seal`; re-run `sera seal` to bind the current
commit. Review records written by 0.4.1 carry no repository identity either: they
report `review_repository_unbound` and must be repeated on 0.4.2.

## Continue from a fresh chat

```bash
sera resume
sera next
```

The repository, task capsule, evidence, reviews, and seal carry the engineering state. Chat history is disposable.
