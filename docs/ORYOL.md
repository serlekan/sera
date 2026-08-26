# SERA Specialization for Oryol Workspace (Architecture v2.1)

SERA is the official engineering intelligence, task boundary, and review controller across the **Oryol Workspace** ecosystem.

---

## 1. Overview

SERA provides deep knowledge of Oryol Workspace architecture, multi-tenant organization boundaries, security invariants, and coding standards to pair engineering agents (Google Antigravity, Claude Code, OpenAI Codex).

```text
Oryol Product Repositories (oryol-core, oryol-mail, oryol-crm, oryol-calendar, oryol-drive, virel)
                                │
                                ▼
                       SERA Knowledge Layer
                     (knowledge/oryol/v2/*.md)
                                │
            ┌───────────────────┴───────────────────┐
            ▼                                       ▼
    Task Contract & Bounds                  PR Review Intelligence
  - Two-tier platform hierarchy           - Structural tenant isolation
  - Principal identity taxonomy           - 8-step authorization algebra
  - Fail-closed policy loading            - D1 session & token families
  - Context & ownership budgeting         - Transactional outbox & inbox
```

---

## 2. Canonical Architecture v2.1 Knowledge Base (`knowledge/oryol/v2/`)

```
knowledge/oryol/v2/
├── ARCHITECTURE-BASELINE.md   # Version 2.1 Candidate Baseline & Gate Declaration
├── workspace-architecture.md  # Two-tier hierarchy (Platform -> Core -> Apps) & 7 rules
├── core-boundaries.md         # Platform capabilities vs. Application business domains
├── multi-tenancy.md           # Structural tenant isolation & organization_placement sharding
├── identity-model.md          # Principal taxonomy (Human/Service), credentials, IdP bindings
├── authorization-model.md     # 8-step authorize() algebra & 3-part permission namespace
├── session-security.md        # D1 authoritative sessions, refresh token families & 10m SLA
├── audit-and-events.md        # Audit vs. Outbox separation, Transactional Outbox & Inbox
├── cloudflare-platform.md     # Cloudflare edge primitives (Workers, D1, KV, R2, Queues)
├── data-lifecycle.md          # Deletion pipeline, D1 Time Travel reality & multi-storage purge
├── ai-platform.md             # Centralized Oryol AI Gateway & provider-retention policy compliance
├── search-platform.md         # Search contract: derived index & live authorization post-filtering
├── product-integration.md     # Application integration contracts across Workspace
└── sera-governance.md         # Standardized repository layout & fail-closed pipeline
```

---

## 3. Standardized Oryol Repository Layout (`.sera/`)

Every codebase repository in the Oryol ecosystem must contain the canonical 5-file `.sera/` directory:

```
<repository-root>/
└── .sera/
    ├── config.json            # Machine-readable SERA config (verification list of strings)
    ├── context.md             # Repository domain context, tech stack, and conventions
    ├── architecture.md        # Permanent Oryol Workspace architecture rules & boundaries
    ├── review-rules.md        # Exact-HEAD review checklist
    └── verification.md        # Automated verification matrix
```

### Deterministic Fail-Closed Policy Loading
SERA deterministically loads `.sera/architecture.md` and `.sera/context.md` into `packet-build.md`, and `.sera/review-rules.md` and `.sera/architecture.md` into `packet-review.md`. If any required policy document is missing or empty, SERA fails closed (`SeraError`).
