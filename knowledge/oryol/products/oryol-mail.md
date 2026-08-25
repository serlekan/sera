# Product: OryolMail

## 1. Overview & Purpose

OryolMail is the professional business email and team communication platform inside the Oryol Workspace ecosystem. It delivers intelligent thread summarization, AI-assisted draft composition, custom domain DNS automation, and shared team support inboxes with internal notes and collision prevention.

---

## 2. Current Stage

- **Status**: Frontend foundation completed and verified on `feat/frontend-production-foundation`.
- **Current Data Layer**: In-memory mock datasets in `src/data/mockEmails.ts`.
- **Production Backend**: Not started (planned on Cloudflare Workers + D1 + R2 + Inbound/Outbound email transport).

---

## 3. Technology Stack

- **Frontend**: React 19, TypeScript (strict), Vite, Tailwind CSS v4, Lucide React, Geist typography
- **Routing**: React Router v7 (`/`, `/mail/*`, `/admin/*`, `/settings`, `/login`, `/onboarding`)
- **Backend / Platform (Planned)**: Cloudflare Workers, Cloudflare D1 (SQLite Edge DB), Cloudflare R2 (Attachments), Google Gemini API (Private AI)
- **Tooling**: ESLint, Vitest, React Testing Library, Playwright E2E

---

## 4. Core Domain Entities

1. **Organization (`org_...`)**: Root tenant owning all domains, mailboxes, and policies.
2. **Domain (`dom_...`)**: Custom business domain with DNS verification records (MX, SPF, DKIM, DMARC).
3. **Mailbox (`mbx_...`)**: Individual or shared email container bound to an organization.
4. **Alias (`alias_...`)**: Inbound/outbound address attached to a mailbox (e.g. `sarah@oryolhq.com`, `support@oryolhq.com`).
5. **Thread (`thd_...`)**: Conversation grouping containing one or more messages.
6. **Message (`msg_...`)**: Individual email message with sender, recipients, timestamps, body, AI summary, and action items.
7. **Attachment (`att_...`)**: File object metadata and secure R2 storage reference.
8. **Shared Inbox (`shared_...`)**: Multi-user shared mailbox with assignment state and internal discussion notes.

---

## 5. Architectural & Security Rules

1. **Organization Ownership**:
   - A mailbox **must** belong to an organization.
   - A custom domain **must** belong to an organization.
   - Cross-organization domain routing is strictly prohibited.
2. **Permission-Gated Access**:
   - Users access mailboxes solely through validated organization memberships and explicit permissions.
   - Every email operation (read, compose, send, assign, archive, delete) requires authorized scope check.
3. **Permission Scopes**:
   - `mail.read` — View messages in personal/assigned mailboxes
   - `mail.send` — Compose and send emails from authorized aliases
   - `mail.manage` — Configure aliases, signatures, filters, and rules
   - `mail.delete` — Permanently purge emails or threads
   - `domain.manage` — Add, configure, and manage custom domains
   - `domain.verify` — Execute DNS diagnostics and verification
4. **AI Safety**:
   - AI summarization and drafting must only read messages the user is authorized to view.
   - AI draft generation is advisory and requires human confirmation before dispatch.

---

## 6. Future Ecosystem Integrations

- **Oryol CRM**: Convert email threads into leads, link messages to deal timelines, customer intelligence sync.
- **Oryol Calendar**: Parse meeting proposals from email bodies into calendar events with one-click scheduling.
- **Oryol Drive**: Centralized asset management for email attachments and cloud file linking.
- **Oryol AI & Virel**: Executive cross-mailbox synthesis, automated triage, and proactive workflow automation.
