# Codex adapter

SERA's core stays provider-neutral. Codex/ChatGPT can act as the controller that reads SERA's machine-readable state and dispatches native coding agents.

Recommended controller loop:

```bash
sera resume --json
sera next --json
```

For a new task:

```bash
sera run "<engineering objective>" --json
```

If ownership is inferred, inspect it and confirm exact editable files before dispatch:

```bash
sera context --why
sera task confirm --file path/to/file
sera packet build
```

The controlling Codex session should then:

1. dispatch only the generated builder packet;
2. preserve unrelated work and exact ownership;
3. run `sera verify` after implementation;
4. generate `sera packet review` for a fresh review context when required;
5. record the verdict and obtain the configured gate when required;
6. run `sera seal` and `sera check --require-seal` before reporting accepted.

Do not forward the entire parent ChatGPT conversation to a worker when the SERA packet is sufficient.
