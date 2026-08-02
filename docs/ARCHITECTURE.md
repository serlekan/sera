# Architecture

SERA has two layers.

## Portable control plane

The Python CLI owns deterministic work:

- repository mapping;
- task contracts;
- routing inputs;
- packet generation;
- evidence storage;
- diff fingerprints;
- scope and freshness checks.

This layer never calls a model API. It stays local, inspectable, and provider-neutral.

## Runtime adapters

Codex, Claude Code, Fable 5, and other tools consume generated packets. Adapters translate the portable contract into the runtime's native agent or command format.

The boundary is deliberate. Provider SDK churn cannot corrupt the repository-state protocol.

## Artifacts

### Repository map

A content-hashed list of text files, language, line count, size, and important symbols. The map is cached and excluded from Git by default.

### Task capsule

The smallest stable representation of user intent. It contains no transcript.

### Evidence ledger

Append-only JSONL. Full outputs remain outside the packet; hashes preserve identity.

### Review fingerprint

SHA-256 over:

- task contract;
- evidence ledger;
- unstaged diff;
- staged diff.

A verdict is valid only for the exact fingerprint it reviewed.

## Routing

Routing is based on task shape:

- risk;
- uncertainty;
- number of owned files;
- estimated owned context;
- requested assurance mode.

It is not based on model prestige. Lane names stay stable while model assignments change in configuration.

## SERA Seal

The seal is created only after scope, verification, and all required review stages pass for the current fingerprint. It records the fingerprint, changed files, evidence count, and completed review stages. CI or release tooling can require a current seal with `sera check --require-seal`.
