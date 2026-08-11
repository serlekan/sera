# Token efficiency

SERA treats context as a budget, not an entitlement.

The goal is not to make model output artificially terse. The goal is to stop sending the same repository, transcript, logs, and unchanged source to every worker and reviewer.

## Four rules

1. **Context must earn its place.** Every selected file should have a task reason.
2. **Pull context on demand.** Start from the capsule and compact map, then open exact source only when needed.
3. **Reuse evidence and repository structure.** Do not forward raw logs or full chat history when hashes and compact records are sufficient.
4. **Measure the reduction.** SERA reports provider-neutral estimates and never presents them as exact API billing telemetry.

## Context selection

```bash
sera context --why
```

reports:

- repository source context available;
- selected source context;
- capsule size;
- task budget;
- reduction compared with full-source availability;
- why each file was selected.

The selector scores objective terms against repository paths and exported symbols. Explicit ownership always wins. Auto-selected files are candidates until ownership is confirmed.

## Progressive disclosure

Agents should receive context in this order:

1. Task capsule
2. Relevant repository map entries
3. Exact ownership
4. Required source files/symbols
5. Bounded diff for review
6. Full logs only when investigation requires them

Do not send the entire repository or the full parent conversation by default.

## Delta repository maps

```bash
sera map --update
```

walks the repository for adds/deletes but reuses map entries whose file size, mtime, content identity, and Git state are unchanged. Changed files are rehashed and reparsed. This avoids rereading thousands of unchanged source files during routine controller startup.

## Packet budgets

The default modes remain:

| Mode | Budget |
|---|---:|
| `fast` | 6,000 estimated tokens |
| `standard` | 16,000 estimated tokens |
| `assured` | 32,000 estimated tokens |

The estimator uses approximately four characters per token and is intentionally provider-neutral.

## Before/after measurement

`sera cost` provides an honest comparison between **repository context available** and **task context selected**.

Example:

```text
Repository context available: ~5,562,935 tokens
Selected orientation: ~18,400 tokens
Context reduction: ~5,544,535 tokens (99.67%)
Evidence tokens avoided: ~9,300
SERA efficiency score: 100/100
```

This does **not** claim that a non-SERA workflow would literally submit the whole repository to an API. It measures how aggressively SERA narrowed the available context and how much raw evidence was replaced by compact records.

## Evidence compression

Full verification logs stay local. Review packets receive compact records:

- exact command;
- exit code;
- short summary;
- output hash;
- output size.

`sera cost` compares raw local output size with the compact evidence records to estimate review-context reduction.

## Context ledger

Each context inspection records:

- stage;
- repository fingerprint;
- selected files;
- reason for each inclusion;
- estimated selected tokens;
- reduction percentage.

This makes context selection auditable rather than invisible.

## Loop control

The default maximum remains two builder attempts. After that, respecify, split, or return to architecture. Repeating an unchanged prompt with a larger model is not a token strategy.
