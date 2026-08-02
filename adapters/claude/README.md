# Claude Code adapter

Claude Code can use the portable task packets with native subagents.

## Builder prompt

```text
Read the generated packet-build.md. Implement only the owned files. Do not commit. Run every verification command and return exact evidence.
```

## Reviewer prompt

```text
Start a fresh read-only review. Read packet-review.md and inspect the actual diff. Do not edit. Return exactly one verdict: ship, fix-first, or rethink.
```

Use Sonnet, Opus, or another configured Claude model according to `.sera/config.json`. The protocol does not depend on a specific model alias.

After the review, bind the verdict to the exact tree:

```bash
sera review --verdict ship --reviewer "claude-opus" --reason "..."
sera check
```
