# Oryol Engineering & Coding Standards

All Oryol codebase repositories adhere to unified quality, typing, linting, and structural standards.

---

## 1. TypeScript Standards

- **Strict Mode Required**: `strict: true`, `noUncheckedIndexedAccess: true` must be enabled across all repositories.
- **Explicit Domain Types**: Never use `any` or ambiguous object dictionaries. Domain types live in `src/types/domain.ts` and are re-exported cleanly from `src/types/index.ts`.
- **Zero Unused Variables**: Unused imports, parameters, and dead code must be removed. Catch clauses should omit parameter binding if unused.

---

## 2. Component & Code Organization

Repositories follow a **feature-oriented layout**:

```
src/
├── app/                  # Application root, routing, global providers
├── components/           # Generic, reusable design system components
│   ├── common/           # Brand logos, navigation headers, command palette
│   └── feedback/         # Toasts, modals, alerts
├── data/                 # Deterministic mock fixtures & test seeds
├── features/             # Business domain feature modules
│   ├── mail/             # Mailbox feeds, composers, thread viewers
│   ├── domains/          # Domain management & DNS diagnostics
│   ├── settings/         # Organization & profile settings
│   └── landing/          # Public marketing & preview pages
├── lib/                  # Shared utilities (class merger, byte formatters)
├── services/             # Client-side API & AI client abstractions
└── types/                # Domain models and TypeScript contracts
```

---

## 3. Error Handling & Feedback

- **No `window.alert()`**: All user feedback must use the unified toast system (`useToast()` with `success`, `error`, `info`).
- **Fail Closed**: Network errors, missing permissions, and DNS validation errors must report descriptive, actionable messages to the user without breaking application state.
