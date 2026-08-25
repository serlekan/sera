# SERA Governance & Policy Verification for Oryol Workspace v2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Scope**: Repository Structure, Automatic Policy Loading & PR Verification Pipeline

---

## 1. Standardized Repository Governance Layout

Every codebase repository in the Oryol ecosystem (`oryol-core`, `oryol-mail`, `oryol-crm`, `oryol-calendar`, `oryol-drive`, `virel`) must contain a standardized `.sera/` governance directory:

```
<repository-root>/
└── .sera/
    ├── config.json            # Machine-readable SERA config: risk terms, paths, budgets, verification suite
    ├── context.md             # Repository domain context, technology stack, and engineering conventions
    ├── architecture.md        # Permanent Oryol Workspace architecture rules & component boundaries
    ├── review-rules.md        # Exact-HEAD review checklist (Security, Tenant Isolation, Permissions, Testing)
    └── verification.md        # Automated verification matrix (Typecheck, Lint, Unit, E2E commands)
```

---

## 2. SERA Governance & Pipeline Loading Flow

SERA automatically parses, validates, and incorporates repository policies across every task lifecycle stage:

```text
       ┌────────────────────────────────────────────────────────┐
       │                Oryol Repository (.sera/)               │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │                 1. Policy Loading Stage                │
       │   - Parse `.sera/config.json`                          │
       │   - Ingest `architecture.md` & `review-rules.md`       │
       │   - Match high-risk paths and terms                    │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │              2. Context Generation Stage               │
       │   - Sized stage context within token budget            │
       │   - Bounded file ownership (`allowed_files`)           │
       │   - Generate `packet-build.md`                         │
       └───────────────────────────┬────────────────────────────┘
                                   │
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │                 3. Review Packet Stage                 │
       │   - Bind exact git `HEAD` commit and tree hash         │
       │   - Evaluate changes against 6 review criteria         │
       │   - Require 100% verification pass before `sera seal`  │
       └────────────────────────────────────────────────────────┘
```

---

## 3. Mandatory Review Gate Dimensions

Every PR and commit is evaluated against the **6 Canonical Review Dimensions**:

1. **Architecture Alignment**: Respects the two-tier platform hierarchy; does not recreate private auth or detached databases.
2. **Multi-Tenant Security**: Enforces `organization_id` scoping in every query and storage key; no cross-tenant leakage.
3. **Identity & Authorization**: Uses standard `authorize({ ... })` checks and standard entity prefixes (`prn_`, `usr_`, `org_`, `mem_`, `dom_`, etc.).
4. **Session & Security Safety**: Relies on D1 for authoritative session state; KV is used only for caching/rate limits.
5. **AI Platform Safety**: All AI operations route through the Oryol AI Gateway with permission filtering and zero third-party retention.
6. **Testing & Verification**: 100% pass on Typecheck (`strict: true`), ESLint, Unit Tests, and E2E smoke tests.
