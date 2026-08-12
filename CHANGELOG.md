# Changelog

## 0.4.1 - 2026-08-12

High-assurance controller hardening. Corrections found by integrating 0.4.0 into a
real high-assurance repository, fixed generically rather than downstream.

### Correction pass (independent review of the first 0.4.1 candidate)

An exact-head independent review reproduced five defects in the first 0.4.1
candidate. All five are corrected here.

- **Ownership confirmation now re-runs risk policy.** `sera task confirm`
  replaced `allowed_files` without re-resolving risk or mode, so a task drafted
  low-risk and confirmed onto a high-risk path kept its fast, review-free route.
  Creation and confirmation now share one policy resolver. Tasks persist
  `requested_mode` / `requested_risk` separately from derived `mode` / `risk`, so
  an explicit risk floor survives ownership changes while a transiently confirmed
  high-risk path never makes risk permanently sticky. `risk_reasons` is rebuilt
  from the current contract.
- **Seals now bind the review ledger.** Acceptance recorded only review stage
  names, so editing an accepted reviewer identity, rationale, or verdict left the
  seal reporting `current`. Seals now carry `review_ledger_fingerprint`, a
  canonical hash of every persisted review record. Mutating, deleting, or
  appending review records makes the seal stale with `seal_review_mismatch`. The
  composition stays non-circular: reviews bind the task fingerprint, acceptance
  binds task, reviews, and repository identity.
- **Review diffs are now per file.** One combined patch was truncated at its head
  and tail, which could reduce every file in between to a bare filename. Change
  evidence is now assembled per changed file with status, blob identity, patch
  hash, and bounded body. Budget is allocated in two phases — a guaranteed
  minimum for every changed file, then water-filled relevance — and generation
  fails with `review_diff_budget_insufficient` rather than emitting a packet that
  hides changes.
- **Seal schema versions are now enforced.** A v2 record relabelled
  `schema_version: 1` still validated as current. The declared version is now
  interpreted first: unknown or missing versions fail with
  `seal_schema_unsupported`, and a version that disagrees with the record's
  contents fails with `seal_schema_inconsistent`. Genuine 0.4.0 seals still
  report `legacy_unbound`.
- **Malformed configuration now fails closed.** `controller: null` and
  `token_budgets.fast: "six thousand"` escaped as uncaught `AttributeError` /
  `ValueError` tracebacks. `validate_config` now type- and shape-checks the
  controller block, token budgets, risk policy, lanes, rules, and scalar limits,
  reporting ordinary SERA errors. An explicit `null` is malformed; omit a key to
  accept its default.

### Controller

- `default_mode` is now honored. One canonical resolver applies the precedence
  `explicit CLI mode > configured default_mode > built-in fallback (standard)`;
  `sera run`, `sera task auto`, and `sera task new` all route through it. The
  0.4.0 heuristic that silently re-derived mode from risk and file count is gone.
- Invalid configured or explicit modes fail closed instead of falling back.
- Mode and risk are now resolved once, in `new_task`, so no entry point can drift.

### Project risk policy

- Added `risk_policy.high_risk_terms` and `risk_policy.high_risk_paths` so a
  repository can declare its own high-risk vocabulary and directories. Built-in
  terminology stays generic.
- Term matching is case-insensitive and token/phrase-aware, not substring: `order`
  matches `cancel the order` and `src/order_book.py` but never `reorder`.
  Multi-word phrases such as `production deployment` match as contiguous runs.
- Path patterns use a documented glob syntax (`**` crosses directories, `*` and
  `?` stay within one segment).
- Effective risk is the maximum of the built-in classifier, project terms,
  project paths, and explicit user risk. An explicit level can raise risk but
  never silently lowers automatically detected risk; a rejected downgrade is
  recorded as `explicit_risk_not_applied`.
- Tasks and routing output now carry structured `risk_reasons`.

### Acceptance

- Seals are schema version 2 and bind the exact `HEAD` commit SHA and `HEAD` tree
  SHA alongside the existing task, evidence, staged, unstaged, and relevant
  untracked fingerprints.
- `sera check --require-seal` now fails when HEAD moves — including a new commit,
  a reset, a different branch checkout, and a different commit carrying an
  identical tree.
- Stable failure reasons: `seal_fingerprint_mismatch`, `seal_head_mismatch`,
  `seal_head_tree_mismatch`, `seal_missing_head_identity`.
- 0.4.0 seals carry no repository identity and are never treated as equivalent.
  They report `legacy_unbound` and fail closed under `--require-seal` until
  re-sealed.

### Ownership versus context

- Ownership (what a task may change) and stage context (what a stage reads) are
  now separate, separately reported budgets. A task can own 36 files while a
  builder packet selects 12 and a reviewer packet selects a different set.
- Excluding a file from context never removes its ownership.
- `sera next` returns `reduce_context` only when the context selected for the
  required next stage exceeds budget, not when total ownership is large.
- Review context is diff-aware: changed files and their imported dependencies are
  prioritized, and every changed file stays represented through the bounded diff
  even when its full contents are not selected.

### Explainability

- `sera context --why` reports an accurate reason taxonomy: `explicit_context`,
  `selected_changed_file`, `selected_owned`, `selected_by_relevance`,
  `selected_dependency`, `owned_not_selected`, `excluded_by_budget`, and
  `not_in_repository_map`.
- Fixed the 0.4.0 defect where owned files that were merely outside the context
  cap were reported as absent from the repository map.

### Fixes

- Task IDs no longer collide when two tasks with the same name are drafted within
  the same second.

### Preserved

- Provider neutrality: no model-provider SDKs or API calls; the core remains
  dependency-free and standard-library only.
- No-silent-fallback routing, independent review, builder self-approval
  prohibition, dirty-worktree baselines, and existing diff/evidence fingerprints.

## 0.4.0 - 2026-08-09

### Controller

- Added `sera run` to draft a task, classify risk/mode, select context, route work, and prepare a builder packet when ownership is confirmed.
- Added `sera task auto` and `sera task confirm` so inferred context never silently becomes edit permission.
- Added `sera next`, `sera resume`, and `sera inbox` for repository-backed task continuation across fresh AI chats.
- Added JSON output to controller-critical commands for machine-readable orchestration.

### Token efficiency

- Added relevance-ranked context selection with per-file inclusion reasons.
- Added `sera context --why` and a context ledger.
- Added `sera cost` with provider-neutral before/after context estimates and an efficiency score.
- Added `sera map --update` to reuse unchanged map entries instead of rereading every source file.
- Preserved bounded review packets and compact evidence rather than forwarding raw logs or builder conversations.

### Safety

- Added dirty-worktree baselines: pre-existing user edits no longer become task scope unless they change again after task creation.
- Auto-detected high-risk work escalates to `assured` mode.
- Fable remains optional, disabled by default, and is represented under the Anthropic provider lane.
- Preserved no-silent-fallback routing, independent review, stale-review invalidation, and fingerprint-bound seals.

## 0.3.0 - 2026-08-02

- Rebuilt the project around an original token-efficient relay protocol.
- Added a zero-dependency Python CLI.
- Added compact content-hashed repository maps.
- Added task capsules, adaptive routing, and evidence ledgers.
- Added fingerprint-bound review invalidation.
- Added fast, standard, and assured productivity modes.
- Made every model lane configurable.
- Added optional, explicit Fable 5 support.
- Removed project-specific profiles and vendor-coupled orchestration logic.
