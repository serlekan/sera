# Changelog

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
