# SERA Controller

Version 0.4 adds a controller layer on top of the original relay protocol.

The controller does **not** call model APIs. It prepares enough deterministic state for ChatGPT, Codex, Claude Code, or another runtime to dispatch the right worker without forwarding a large transcript.

## Controller loop

```text
Natural-language request
  -> delta repository map
  -> context selection with reasons
  -> automatic risk/mode draft
  -> exact ownership confirmation
  -> routed builder packet
  -> verification evidence
  -> fresh review
  -> optional release gate
  -> SERA Seal
```

## `sera run`

`sera run` turns a request into a task draft and route in one command.

```bash
sera run "fix payment reconciliation" --file src/payments.py --verify "python -m unittest"
```

When exact files are supplied, the builder packet is generated immediately. When files are inferred from the repository map, they remain **candidate ownership** until a controller confirms them:

```bash
sera task confirm
```

This is intentional. Context relevance is not the same thing as permission to edit.

## `sera next`

`sera next` makes the repository state machine explicit. It can return steps such as:

- confirm ownership;
- generate a build packet;
- dispatch the builder;
- run verification;
- generate a review packet;
- repeat stale review;
- seal the accepted tree.

Use `--json` when another AI controller is consuming the result.

## `sera resume`

A new chat or agent can run:

```bash
sera resume --json
```

and reconstruct the active task from repository state instead of requiring the previous chat transcript.

## `sera inbox`

```bash
sera inbox
```

shows every local SERA task and the next required action. This is the first project-level control-plane view and is designed to grow into multi-task orchestration later.

## Dirty worktrees

0.4 records the dirty worktree at task creation. Existing modified/untracked files are treated as the user's baseline, not as task scope. If one of those files changes again during the task, SERA notices the mutation and applies normal ownership checks.

This makes SERA usable in real repositories without requiring destructive cleanup, reset, or stash operations before every task.
