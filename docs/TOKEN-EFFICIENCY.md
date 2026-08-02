# Token efficiency

SERA reduces repeated context rather than trying to make model output artificially terse.

## Context hierarchy

Agents should receive context in this order:

1. Task capsule
2. Compact repository map
3. Metadata for owned files
4. Exact source sections required by the current decision
5. Bounded diff for review

Do not send the full repository or full conversation by default.

## Reuse

The repository map is keyed by a content fingerprint. Reuse it until the repository changes materially.

Evidence records contain output hashes and concise summaries. A reviewer can request a full log only when the summary or hash is insufficient.

## Packet budgets

The default modes use 6k, 16k, and 32k estimated-token budgets. The current estimator uses approximately four characters per token. It is intentionally conservative and provider-neutral.

## Loop control

The default maximum is two builder attempts. After that:

- correct the task contract;
- change the lane;
- reduce scope;
- or return to architecture.

Repeating the same task with a larger model usually wastes tokens without removing ambiguity.

## Review compression

Review packets include:

- exact objective;
- owned files and hashes;
- constraints;
- verification evidence;
- changed file list;
- bounded diff.

They exclude builder conversation history and non-actionable logs.

For very small repositories, the map and capsule can be larger than reading the source directly. The budget command reports zero estimated savings in that case; the main benefit begins when repository context is larger than the reusable orientation artifacts.

## PR and handoff summaries

`sera summary` turns the task contract, changed files, evidence, reviews, and seal status into a small Markdown artifact. This avoids rewriting the same explanation for a pull request or the next model.
