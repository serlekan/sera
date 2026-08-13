# Changelog

## 0.4.2 - unreleased

### Canonical-review correction

Canonical review of the first 0.4.2 candidate reproduced two blocking defects.
Both are corrected here.

- **Coverage completeness could be false.** Review evidence is generated from a
  task's `allowed_files`, while the authoritative change set is the whole
  repository delta, and nothing proved the two agreed. A task owning
  `src/alpha.py` that committed both `src/alpha.py` and `src/unowned.py` on a
  clean worktree reported `coverage_complete: true` and emitted a packet stating
  "Change coverage: complete" while carrying no patch for `src/unowned.py`.
  `coverage_complete` is now true only when the committed range resolved, diff
  budgeting succeeded, no task change lies outside declared ownership, and every
  authoritative changed path is represented by real evidence — a rename or copy
  block representing both its destination and its `old_path`. New reasons
  `review_scope_unresolved` and `review_evidence_incomplete`, with
  `out_of_scope_paths` and `missing_evidence_paths` diagnostics. `sera packet
  review` refuses on direct invocation, not only through `sera next`, and
  unresolved scope is reported as `resolve_scope` because that is the root cause
  an operator must act on. Ownership is never widened automatically. The
  change-set fingerprint continues to bind the full authoritative path set, so
  out-of-scope work appearing or disappearing still moves it.
- **Unborn repository identity stored a symbolic revision.** `git rev-parse HEAD`
  exits non-zero on a repository with no commits but still prints the literal
  string `HEAD`, and identity resolution trusted stdout instead of exit status.
  A task created before the first commit stored `head_sha: "HEAD"` and
  `head_tree_sha: "HEAD^{tree}"`; after the first commit those expressions
  re-resolved to the new commit, collapsing the baseline→HEAD range and losing
  every committed change from review. Repository identity now holds immutable
  Git object IDs or the explicit `unborn` sentinel, resolved through
  `git rev-parse --verify` with exit status as the authority. An unborn baseline
  diffs against Git's empty tree, so the first commit and every commit after it
  remain reviewable as the net change from an empty repository. A commit that
  cannot resolve its tree fails closed with a `SeraError` rather than being
  reported as an absent HEAD.

Review identity and coverage integrity. Three controller defects and one
reporting weakness found by integrating 0.4.1 into a real high-assurance
repository, fixed generically rather than downstream.

### Reviews bind the exact repository identity (P0)

A review recorded at HEAD A stayed `current` after HEAD moved to B: an empty
commit leaves the tree and the working-tree delta untouched, so the
task/evidence/delta fingerprint did not move, and `sera seal` could then bind B
on the strength of a review of A.

- Review packet provenance now records the exact `head_sha` and `head_tree_sha`
  it was generated against, and the packet body states them for the human
  reviewer. `packet_state` recomputes repository identity and reports
  `packet_stale_head` or `packet_stale_head_tree` rather than dispatching.
- `sera review` records a verdict only against a current review packet, and then
  derives the reviewed identity from Git. A caller-supplied SHA is never
  trusted. If HEAD moved after packet generation, the verdict is refused until a
  fresh packet is generated and reviewed.
- Accepted review records carry `repository_identity`. Review freshness now
  requires the task/evidence/delta fingerprint **and** the exact HEAD **and** the
  exact tree to match; `check_task` reports `review_fingerprint_mismatch`,
  `review_head_mismatch`, `review_head_tree_mismatch`, or
  `review_repository_unbound` per stage in `stale_review_reasons`.
- The release gate is a review stage and binds identity identically, so a gate
  can never inherit an independent review taken at a different commit.
- `sera seal` refuses unless every required review is `ship`,
  fingerprint-current, and bound to the current HEAD and tree. Post-seal
  behaviour is unchanged: `sera check --require-seal` still fails with
  `seal_head_mismatch` once HEAD moves.
- Repository identity is bound where it is semantically required. Build packets
  record it as provenance but are not invalidated by it, so a builder committing
  its own implementation does not stale its own handoff.

### Review evidence covers committed changes (P0)

Change evidence was built from staged and unstaged state only, so once
implementation was committed and the worktree went clean a review packet
reported "No task-relative changes to review" for a task that had produced a
substantial commit.

- Tasks record `baseline_repository_identity` at creation. The review change set
  spans that baseline commit through the current HEAD, unioned with
  task-relative working-tree changes.
- One file is exactly one canonical review block carrying its cumulative change
  across `committed`, `staged`, and `unstaged` sources, with blob identity
  spanning the whole range. Per-file budgeting, minimum patch guarantees,
  deterministic rendering, the exact character budget, and binary/rename
  handling are unchanged.
- The committed range participates in scope checking: a file committed after the
  task began is a task change even with a clean worktree, so committing an
  out-of-scope edit cannot hide it.
- Dirty-worktree baseline safety is preserved. A pre-existing dirty path the
  task never touches is still not task scope; touching it again still is.
- Tasks created before 0.4.2 have no baseline commit and cannot have their
  committed range derived. They report `review_baseline_unbound` and fail closed
  rather than claiming complete coverage. A baseline commit that no longer
  exists reports `review_baseline_unreachable`.

### Tracked SERA policy is reviewable (P0)

Excluding all of `.sera/**` was too coarse and hid a project's own reviewed
policy from ownership, change detection, scope checking, and review evidence.

- Only generated runtime state is excluded now: `.sera/cache/**`,
  `.sera/tasks/**` (capsules, packets, ledgers, seals), and `.sera/latest-task`.
- Everything else under `.sera/` — `.sera/config.json`, `.sera/POLICY.md`,
  `.sera/README.md`, any tracked policy file — is ordinary repository content.
- Runtime state cannot reach a review packet even when a repository commits it
  by mistake, and owning a runtime path does not turn it into review content.

### Coverage completeness is reported separately (P1)

`current` never meant "the reviewer is seeing everything" and is no longer
allowed to imply it.

- `sera next` exposes `review_coverage` with `complete`, `reason`,
  `change_fingerprint`, and `committed_range` alongside packet currency.
- A review packet is dispatchable only when it is current **and** its coverage is
  complete; otherwise the controller reports `review_coverage_incomplete`.
  SERA refuses to generate a review packet whose coverage is incomplete.
- Review provenance binds a `review_change_fingerprint` over the range
  endpoints, the complete changed-path set, and each file's status, blob
  identity, sources, and patch bytes. Any movement in the represented change set
  yields `packet_stale_change_set`; a filename count is not relied upon.
- `review_diff_budget_insufficient` behaviour is unchanged.

### Failed reviews outrank missing later stages (P1)

`sera next` evaluated missing reviews before failed ones, so an assured task with
a current `fix-first` independent review and no gate yet was told to dispatch the
release gate for work the independent stage had already refused.

- Required-review precedence is now: stale review → current failed review →
  missing review → seal, in both `sera next` and `check_task`.

### Compatibility

- Packet provenance schema is version 3. 0.4.1 packets (version 2) carry no
  repository identity or change-set binding and fail closed as
  `packet_legacy_schema`; regenerate them.
- 0.4.1 review records remain readable history but report
  `review_repository_unbound` and cannot satisfy 0.4.2 exact-head acceptance.
  Repeat the review on 0.4.2.
- Seal schema is unchanged at version 2. Because a seal already binds the review
  ledger, and reviews now bind identity, seals transitively bind reviewed
  identity.
- No runtime dependencies added, no model-provider SDKs, no automatic provider
  invocation, and no change to routing or risk policy. The threat model remains
  local consistency via SHA-256 over local Git and file state.

## 0.4.1 - 2026-08-12

High-assurance controller hardening. Corrections found by integrating 0.4.0 into a
real high-assurance repository, fixed generically rather than downstream.

### Third correction pass (packet integrity)

A third exact-head review confirmed all prior blockers fixed and reproduced one
P1: packet provenance recorded a route but never validated it, so rewriting the
stored route to `release_gate` for every stage left the packet `current` and
still dispatchable. Packet Markdown could likewise be edited without
invalidation.

- Packet freshness is now `contract + state (review only) + resolved route +
  content`. `packet_state` independently re-resolves the current route — the
  selected lane, provider, and model for every stage the task requires — and
  re-hashes it, rather than trusting the stored object. Mismatch, or a route
  that can no longer be resolved, yields `packet_stale_route`.
- Only selected stages are bound, so changing an unrelated lane does not
  invalidate a packet while changing the selected model, provider, or lane does.
- Provenance now carries `content_sha256` over the exact bytes written to disk,
  recomputed at validation time. A tampered packet body yields
  `packet_content_mismatch`; the checksum embedded inside the Markdown is not
  trusted, so rewriting body and embedded checksum together still fails.
- Packet provenance schema is version 2; a packet lacking either binding fails
  closed as `packet_unbound`.

### Second correction pass (independent review of the corrected candidate)

A second exact-head independent review confirmed the five earlier blockers were
fixed and reproduced two further defects. Both are corrected here.

- **Handoff packets are freshness-bound to the task contract.** `sera next`
  treated packet existence as packet freshness, so after `sera task confirm`
  moved a task from `fast`/`low` onto a high-risk path it still returned
  `dispatch_builder` for a packet carrying the old `fast_builder` route and the
  old ownership — a fail-open orchestration defect. Tasks now carry a
  `task_contract_fingerprint` over their semantic contract only, packets are
  written with adjacent machine-readable provenance, and a packet is dispatchable
  only when that provenance parses and matches. Review packets additionally bind
  the task/evidence/delta state they embed. `capsule.md` is rewritten on
  contract mutation so it never remains stale documentation, and verification
  evidence records the contract it was gathered under — retained for audit, but
  no longer satisfying a superseded contract. Missing or malformed provenance
  fails closed, so an unbound packet is never dispatchable as current.
- **The review-diff character budget is now exact.** With 50 changed files,
  `review_diff_coverage` reported success at a 25,700-character budget while the
  rendered text ran 28,598 characters — 2,898 over — because allocation modelled
  raw patch bodies while the renderer also emitted headers, blob identity, patch
  hashes, `shown`/`total` counters, and omission markers. A single canonical
  per-file renderer now produces every block, and budgeting measures that
  renderer's real output: the guaranteed minimum for every changed file is
  rendered and measured before success is possible, the remainder is allocated,
  and the result is re-rendered and shrunk deterministically until the measured
  length fits. Reported success now guarantees `len(text) <= max_chars` exactly.
  `max_chars` is documented as Python string characters (Unicode code points).

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
