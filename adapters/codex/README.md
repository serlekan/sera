# Codex adapter

Use Codex as one or more configured lanes without forwarding the full task conversation.

## Planner or gate

Start from the generated capsule and repository map:

```text
Read .sera/tasks/<task-id>/capsule.md and .sera/cache/repo-map.md.
Resolve remaining ambiguity without implementation. Preserve the exact ownership and verification contract.
```

## Builder

Generate the packet:

```bash
sera packet build <task-id>
```

Give the native Codex worker the resulting `packet-build.md`. Instruct it to inspect source only within the owned paths unless an interface dependency requires read-only inspection elsewhere.

## Reviewer

Generate:

```bash
sera packet review <task-id>
```

Use a fresh context. The reviewer must not edit files and must return `ship`, `fix-first`, or `rethink`.

Record the verdict with `sera review`, then run `sera check`.
