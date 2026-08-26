# SERA Governance & Fail-Closed Policy Enforcement v2.1

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Fail-Closed Policy Loading, Standardized Layout & Packet Injection

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

## 2. Deterministic Fail-Closed Policy Loading Pipeline

Because `.sera/**` runtime data is excluded from ordinary repository file mapping to prevent context bloat, SERA implements **explicit deterministic policy loading**:

```text
       ┌────────────────────────────────────────────────────────┐
       │                Oryol Repository (.sera/)               │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │             1. Strict Config Validation                │
       │   - `verification` MUST be a list of strings           │
       │   - `risk_policy` MUST declare terms and paths         │
       │   - Malformed config ──► FAILS CLOSED (SeraError)      │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │             2. Mandatory Policy File Check             │
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
       └────────────────────────────────────────────────────────┘
```

---

## 3. Mandatory Review Gate Checklist

1. **Architecture Alignment**: Respects two-tier platform hierarchy; no private auth or isolated tenant silos.
2. **Structural Multi-Tenancy**: Enforces `organization_id` in compound keys and parameterized queries.
3. **Identity & Authorization**: Adheres to the Principal model and 8-step `authorize()` algebra.
4. **Authoritative Session Security**: Uses D1 for session state; KV restricted to caching and rate limits.
5. **Audit & Outbox**: Implements transactional outbox on domain mutations and preserves non-cascading audit logs.
6. **Testing Verification**: 100% pass on Typecheck (`strict: true`), ESLint, Unit Tests, and E2E smoke tests.
