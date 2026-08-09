# Architecture

SERA 0.4 has three layers.

## 1. Repository-state core

The zero-dependency Python core owns deterministic work:

- content-hashed repository maps;
- task contracts and dirty-worktree baselines;
- routing inputs;
- evidence storage;
- review fingerprints;
- scope/freshness checks;
- SERA Seals.

## 2. Controller layer

The controller translates a natural-language engineering request into compact, inspectable state:

- automatic risk/mode draft;
- relevance-ranked context selection;
- exact ownership confirmation;
- next-action state machine;
- resume/inbox views;
- context and evidence-efficiency reports.

The controller is still provider-neutral. It prepares dispatch; it does not hide provider calls inside the core.

## 3. Runtime adapters

Codex, Claude Code, or another runtime consumes SERA packets and performs actual model invocation. Provider SDK churn stays outside the repository-state protocol.

## Context selection

Selection is lexical and symbol-aware in 0.4. Paths and exported symbols that match objective terms receive a score. Explicit files are always included. Inferred files remain candidate ownership until confirmed.

Later versions can replace the scorer with a richer dependency graph without changing the task protocol.

## Dirty-worktree baseline

Task creation records fingerprints of pre-existing dirty paths. Scope checks compare the current worktree against that baseline. Unchanged user work is preserved; new mutations are attributed to the task.

## Delta maps

`sera map --update` reuses unchanged map entries and rescans changed files. It uses previous HEAD, Git dirty paths, size, and mtime to decide what must be reread.

## Fingerprint-bound review

Review validity still depends on the exact task, evidence, staged diff, unstaged diff, and relevant untracked content. Any later mutation makes a required verdict stale.
