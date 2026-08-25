# Oryol Quality Assurance & Testing Standards

Quality assurance in Oryol Workspace combines automated type-safety, unit tests, end-to-end browser verification, and SERA-verified exact-tree review.

---

## 1. Testing Pyramid

### 1. Static Typecheck
- Command: `npm run typecheck` (`tsc --noEmit`)
- Threshold: Zero type errors under `strict: true` and `noUncheckedIndexedAccess: true`.

### 2. Linting & Formatting
- Command: `npm run lint` (`eslint .`)
- Threshold: Zero errors, no dead code or unhandled catch parameters.

### 3. Unit & Component Tests (Vitest + React Testing Library)
- Command: `npm test` (`vitest run`)
- Coverage expectations:
  - Routing and navigation rendering
  - Feature filtering, tag selection, and search query execution
  - Modal workflows (open, close, field validation, submission)
  - Service abstraction contracts (AI client mock fetch, HTTP response mapping)

### 4. End-to-End Browser Smoke Tests (Playwright)
- Command: `npm run test:e2e` (`playwright test`)
- Mandatory critical paths:
  - Landing page → Webmail navigation
  - Inbox reading and message selection
  - Email composition, alias selection, dispatch, and toast confirmation
  - Shared inbox filtering, teammate assignment, and internal note creation
  - Domain setup, DNS record inspection, and verification simulation
  - Mobile responsive rendering (375px viewport)
  - Unknown route / 404 fallback handling

---

## 2. Review Gate Checklist

Before code is approved for merge by SERA:
1. All static type checks pass cleanly.
2. All unit tests pass.
3. Production build succeeds (`npm run build`).
4. Playwright E2E smoke tests pass.
5. Exact reviewed HEAD commit is sealed.
