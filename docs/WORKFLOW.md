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

High-risk terms automatically escalate an auto-drafted task to `assured`.

## Execute

`sera route` chooses the cheapest adequate configured lane. `sera packet build` creates the bounded handoff. SERA does not call provider APIs; the surrounding controller/runtime dispatches the packet.

## Review

`sera verify` records reproducible evidence. A fresh reviewer receives the review packet, bounded diff, and evidence—not the builder conversation. It returns `ship`, `fix-first`, or `rethink`.

## Accept

`sera seal` binds acceptance to the exact task/evidence/diff fingerprint. `sera check --require-seal` rejects stale or unsealed work.

## Continue from a fresh chat

```bash
sera resume
sera next
```

The repository, task capsule, evidence, reviews, and seal carry the engineering state. Chat history is disposable.
