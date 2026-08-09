# Claude Code adapter

SERA prepares provider-neutral task and review packets. Claude Code can consume those packets as builder or reviewer context without inheriting the controller's full conversation.

For implementation, give the selected Claude builder `packet-build.md` and require it to stay inside exact ownership and avoid commits.

For independent review, start a fresh context and give it `packet-review.md`. The reviewer remains read-only and returns exactly one verdict:

```text
ship
fix-first
rethink
```

A fresh Claude session can reconstruct repository-backed task state with:

```bash
sera resume --json
sera next --json
```

If Claude is acting as the outer controller, it should follow SERA's returned lane rather than silently substituting a different model. Optional specialist lanes remain explicit and never become the sole release gate by default.
