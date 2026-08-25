# Oryol Workspace Development Workflow

This document outlines the standard end-to-end engineering workflow for developing, reviewing, and landing features across all repositories in the Oryol Workspace ecosystem.

---

## The 8-Stage Development Lifecycle

```text
       ┌───────────────────────────────┐
       │             Idea              │
       │    (Feature / Requirement)    │
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │   SERA Architecture Review    │
       │  (Rule checks & risk triage)  │
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │      Implementation Plan      │
       │    (Bounded scope & budget)   │
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │ Claude / GPT / AGY Coding     │
       │ (Builder inside exact files)  │
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │       SERA Code Review        │
       │ (Security, Tenant, AI checks) │
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │        Automated Tests        │
       │   (Typecheck, Unit, E2E)      │
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │        Human Approval         │
       │   (Sign-off & exact seal)     │
       └──────────────┬────────────────┘
                      │
                      ▼
       ┌───────────────────────────────┐
       │             Merge             │
       │     (Production Mainline)     │
       └───────────────────────────────┘
```

---

## Detailed Stage Breakdown

### 1. Idea & Specification
- A business or technical requirement is formulated.
- Objective must define a clear, observable outcome.

### 2. SERA Architecture Review
- The request is evaluated against the 7 Permanent Oryol Workspace Rules.
- Risk policy identifies high-risk terms (auth, tenant, domain, dns, transport).
- Mode is selected (`fast`, `standard`, `assured`).

### 3. Implementation Plan
- SERA drafts task ownership (`allowed_files`) and context budget.
- Constraints and verification commands are set.
- Ownership is confirmed via `sera task confirm`.

### 4. Coding Agent Execution
- Coding agents (Claude Code, OpenAI Codex, Antigravity) receive the bounded `packet-build.md`.
- Implementation occurs strictly within declared file ownership.
- The agent does not commit code directly.

### 5. SERA Code Review
- Reviewer receives fresh `packet-review.md` bound to the exact git `HEAD` and tree.
- Evaluates code against the 6 Oryol PR Review criteria:
  1. Architecture (Workspace alignment)
  2. Security (Tenant isolation & permission enforcement)
  3. Data Model (Organization scoping & prefixed IDs)
  4. AI Principles (Permission-aware & zero retention)
  5. Testing (100% verification pass)
  6. Product (Design language & user feedback)
- Issues decisive verdict: `ship`, `fix-first`, or `rethink`.

### 6. Automated Verification Tests
- Mandatory execution of project verification suite:
  - `npm run typecheck`
  - `npm run lint`
  - `npm test`
  - `npm run build`
  - `npm run test:e2e`

### 7. Human Approval & Exact-Head Seal
- Review evidence and test logs are verified by human lead.
- The task is sealed against the exact git commit (`sera seal`).

### 8. Merge
- Feature branch is merged to `main` with complete audit provenance.
