# SERA Specialization for Oryol Workspace

SERA is the official engineering intelligence, task boundary, and review controller across the **Oryol Workspace** ecosystem.

---

## 1. Overview

SERA provides deep knowledge of Oryol Workspace architecture, multi-tenant organization boundaries, security invariants, and coding standards to all coding agents (Claude Code, OpenAI Codex, Google Antigravity).

```text
Oryol Product Repositories (oryol-mail, oryol-crm, oryol-calendar, oryol-drive, virel)
                                │
                                ▼
                       SERA Knowledge Layer
                   (knowledge/oryol/*.md)
                                │
            ┌───────────────────┴───────────────────┐
            ▼                                       ▼
    Task Contract & Bounds                  PR Review Intelligence
  - Organization boundaries               - Tenant isolation checks
  - High-risk term escalation             - Permission verification
  - Context & ownership budgeting         - Exact-HEAD review sealing
```

---

## 2. Oryol Knowledge Base Structure

```
knowledge/
 └── oryol/
     ├── workspace.md         # Permanent 7 Architecture Rules & multi-tenant model
     ├── identity.md          # Centralized auth, user accounts & memberships
     ├── security.md          # Zero-trust, permission system & audit logs
     ├── data-model.md        # Entity conventions, ID prefixes & schemas
     ├── ai-principles.md     # Permission-aware AI & zero-retention rules
     ├── coding-standards.md  # TypeScript strictness & feature structure
     ├── frontend.md          # React 19, Tailwind CSS v4, Geist design system
     ├── backend.md           # Cloudflare Workers, D1, KV, R2 edge topology
     ├── testing.md           # Typecheck, Vitest, Playwright E2E standards
     ├── workflow.md          # 8-stage engineering lifecycle from Idea to Merge
     └── products/
         ├── oryol-mail.md    # OryolMail domain model, rules & roadmap
         ├── oryol-crm.md     # Oryol CRM contacts, deals & timeline
         ├── oryol-calendar.md# Oryol Calendar events, availability & invites
         ├── oryol-drive.md   # Oryol Drive assets, versions & R2 storage
         └── virel.md         # Virel AI synthesis & workflow automation
```

---

## 3. How to Connect a New Oryol Product Repository

When onboarding a new repository (e.g. `oryol-crm` or `oryol-calendar`):

1. **Create `.sera/config.json`** in the repository root:
   - Configure project `risk_policy` with domain-specific high-risk terms and paths.
   - Configure `verification` test commands.
2. **Add `.sera/architecture-rules.md`**:
   - Link back to the 7 Permanent Oryol Workspace Rules.
   - Specify product-specific entity definitions.
3. **Add `.sera/review-rules.md`**:
   - Define PR review criteria covering tenant isolation, permissions, data scoping, AI safety, and UX.
4. **Register Product Knowledge**:
   - Add `knowledge/oryol/products/<product-name>.md` inside SERA with core entities, permissions, and cross-product integration hooks.

---

## 4. How Coding Agents Consume Oryol Knowledge

When an agent initiates a task on any Oryol repository:
1. `sera run "<objective>"` analyzes repository changes and consults the Oryol risk policy.
2. SERA generates `packet-build.md` embedding the relevant Oryol architecture constraints, token budgets, and ownership bounds.
3. The coding agent operates strictly within the bounded file ownership.
4. SERA prepares `packet-review.md` for independent review, verifying tenant isolation and permission checks before issuing an exact-head seal.
