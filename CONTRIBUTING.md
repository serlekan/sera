# Contributing

Keep the core provider-neutral and dependency-light.

## Development

```bash
python -m pip install -e . --no-build-isolation
python -m unittest discover -s tests -v
python -m sera --version
```

## Rules

- Add deterministic repository-state behavior to the core.
- Put provider-specific behavior in adapters.
- Do not require remote APIs for tests.
- Preserve review fingerprint invalidation.
- Include tests for scope or evidence changes.
- Avoid adding dependencies without a measurable benefit.
