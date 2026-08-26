# SERA Governance & Fail-Closed Policy Enforcement v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Structural Multi-Signal Repository Detection, Zero Fail-Open Fallbacks & Packet Provenance

---

## 1. Standardized Repository Governance Layout

Every codebase repository in the Oryol ecosystem (`oryol-core`, `oryol-mail`, `oryol-crm`, `oryol-calendar`, `oryol-drive`, `virel`) must contain the standardized `.sera/` governance directory with the exact 5 canonical files:

```
<repository-root>/
└── .sera/
    ├── config.json            # Machine-readable SERA config (modes, budgets, risk policy, verification list)
    ├── context.md             # Repository domain context, technology stack, and engineering conventions
    ├── architecture.md        # Permanent Oryol Workspace architecture rules & component boundaries
    ├── review-rules.md        # Exact-HEAD review checklist (Security, Tenant Isolation, Permissions, Testing)
    └── verification.md        # Automated verification matrix and validation commands
```

---

## 2. Deterministic Multi-Signal Detection & Fail-Closed Enforcement

To guarantee that an Oryol repository can never escape governance by deleting `.sera/` or `config.json`, SERA identifies Oryol repositories via independent signals outside of `.sera/`:

1. **Registered Repository Directory / Basename**: (`oryol-mail`, `oryol-core`, `oryol-crm`, `oryol-calendar`, `oryol-drive`, `virel`).
2. **Project Package Metadata**: `package.json` `name` matching `oryol-*` or `@oryol/*`.
3. **Cloudflare Worker Metadata**: `wrangler.toml` `name` matching `oryol-*` or `virel`.
4. **Committed Project Marker**: `.oryol-project` or `.oryol` marker file.
5. **Git Remote Origin**: Remote URL containing `serlekan/oryol-*` or `/virel`.

```text
       ┌────────────────────────────────────────────────────────┐
       │             Oryol Repository Identification            │
       │  (Ecosystem registry / package.json / wrangler.toml)   │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │          1. Fail-Closed Directory & Config Check       │
       │   - Missing `.sera/` ──► FAILS CLOSED (SeraError)      │
       │   - Missing `.sera/config.json` ──► FAILS CLOSED       │
       │   - Malformed config ──► FAILS CLOSED (SeraError)      │
       │   - (Never falls back to unconfigured defaults)        │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │          2. Mandatory Policy File & Non-Empty Check    │
       │   - Verifies existence of `architecture.md`,           │
       │     `review-rules.md`, `context.md`, `verification.md` │
       │   - Missing or empty file ──► FAILS CLOSED (SeraError) │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │             3. Deterministic Packet Injection          │
       │   - `packet-build.md`: Embeds Architecture + Context   │
       │   - `packet-review.md`: Embeds Review Rules + Arch     │
       │   - Embeds Governance Version, Baseline & Commit SHA   │
       └────────────────────────────────────────────────────────┘
```

---

## 3. Mandatory Review Gate Checklist

Every review packet evaluated by SERA against an Oryol repository strictly verifies:
1. **Multi-Tenant Scoping**: All D1 queries include parameterized `WHERE organization_id = ?` clauses; compound foreign keys are preserved.
2. **Permission Invariant**: No endpoint allows access without executing `authorize({ principal, membership, organization, action, resource, context })`.
3. **Centralized Identity Invariant**: Zero private credential stores, login forms, or standalone password hashes.
4. **Deterministic Verification**: All commands in `.sera/config.json` `verification` pass 100% with exit code 0.
