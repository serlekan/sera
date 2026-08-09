# Optional Fable 5 lane

Fable 5 is supported as an optional specialist lane.

It is disabled in the default configuration because SERA treats it as an explicit specialist lane rather than an automatic fallback. Enabling it requires an explicit configuration change.

Recommended uses:

- rapid prototypes;
- creative interface exploration;
- a second implementation attempt after a corrected specification;
- supplementary review of a large or visually complex change.

Default restrictions:

- never selected through silent fallback;
- never reviews its own implementation as the only reviewer;
- never acts as the sole release gate;
- receives the same task capsule and ownership limits as every other builder;
- must return evidence, not only a completion claim.

Example configuration:

```json
{
  "lanes": {
    "optional_fable": {
      "provider": "anthropic",
      "model": "claude-fable-5",
      "enabled": true,
      "allowed_uses": ["prototype", "creative-ui", "second-attempt", "supplementary-review"],
      "may_be_sole_release_gate": false
    }
  }
}
```
