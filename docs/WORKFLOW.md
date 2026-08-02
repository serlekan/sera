# SERA workflow

SERA means **Specify, Execute, Review, Accept**.

## Specify

Create a task capsule with one observable objective, exact ownership, constraints, risk, uncertainty, and verification commands.

## Execute

Route to the cheapest adequate builder. The builder receives the compact packet, reads source on demand, stays inside ownership, runs checks, and does not commit.

## Review

A fresh reviewer receives the review packet, actual bounded diff, and evidence ledger. It returns `ship`, `fix-first`, or `rethink`. High-risk tasks can require an additional gate stage.

## Accept

`sera check` proves scope and evidence state. `sera seal` binds acceptance to the exact fingerprint. `sera check --require-seal` blocks stale or unsealed release candidates.

## After any correction

1. Rerun verification.
2. Regenerate the review packet.
3. Repeat every required review stage.
4. Create a new seal.

No verdict or seal survives a changed fingerprint.
