# Oryol Workspace Architecture v2.2 — Canonical Platform Model

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**Supersedes**: `knowledge/oryol/workspace.md` (v1) and all v2.0/v2.1 drafts  

---

## 1. The Canonical Platform Hierarchy

Oryol Workspace is structured as a two-tier system: the **Oryol Core Platform** layer providing universal multi-tenant capabilities, and specialized **Product Applications** owning domain-specific business data, ledgers, and user experiences.

```
                    ┌────────────────────────────────────────────────────────┐
                    │                    Oryol Workspace                     │
                    └───────────────────────────┬────────────────────────────┘
                                                │
                                                ▼
                    ┌────────────────────────────────────────────────────────┐
                    │                  Oryol Core Platform                   │
                    │ Identity · Organizations · Memberships · Teams · RBAC  │
                    │ Sessions · Audit · Events · AI Gateway · Entitlements  │
                    └───────────────────────────┬────────────────────────────┘
                                                │
         ┌──────────────────┬───────────────────┼──────────────────┬──────────────────┐
         ▼                  ▼                   ▼                  ▼                  ▼
┌─────────────────┐┌─────────────────┐┌─────────────────┐┌─────────────────┐┌─────────────────┐
│    OryolMail    ││    Oryol CRM    ││ Oryol Calendar  ││   Oryol Drive   ││      Virel      │
│  (Mail Domain)  ││  (Sales/Deals)  ││  (Scheduling)   ││ (Asset Storage) ││ (Fintech/Ledger)│
└─────────────────┘└─────────────────┘└─────────────────┘└─────────────────┘└─────────────────┘
```

---

## 2. Core Separation Rule & Domain Ownership

> [!IMPORTANT]
> **Fundamental Platform Invariant**:  
> **Oryol Core** provides platform capabilities and multi-tenant security guarantees.  
> **Product Applications** own their specific business domains, data lifecycles, and workflows.

| Layer | System Ownership | Must NOT Own |
|---|---|---|
| **Oryol Core Platform** | Identity (Principals: `human` or `service`), Organizations, Memberships, Teams, Roles, Permissions, Authoritative Sessions, Audit Stream, Domain Events Bus, AI Gateway Contracts, Search Contracts, Feature Flags, Application Entitlements, Generic Domain Claims (`dom_...`). | Mail transport, email storage, CRM deal pipelines, calendar invites, binary file storage, financial ledgers/wallets/invoices, application UI/UX. |
| **OryolMail** (`oryol-mail`) | Mailbox state, email threads, messages, attachment persistence, email-specific DNS routing verification (MX, SPF, 2048-bit DKIM selector key generation, DMARC), email delivery/ingestion. | Independent user authentication, private session cookie issuing, isolated membership schemas, bypassable permission tables. |
| **Oryol CRM** (`oryol-crm`) | Accounts, contacts, leads, deals, sales pipeline stages, CRM activity timelines. | Custom authentication, raw SMTP transport. |
| **Oryol Calendar** (`oryol-calendar`) | Calendars, events, availability schedules, meeting invites, RSVP state. | Custom authentication, private user credentials. |
| **Oryol Drive** (`oryol-drive`) | File assets, versions, folder hierarchy, storage quota accounting, R2 object metadata. | Independent identity, automatic forced relocation of mail attachments. |
| **Virel** (`virel`) | Wallets, financial transactions, invoices, payment records, billing reconciliation, financial workflows, and accounting ledgers. | Independent identity, bypassable session stores. |

---

## 3. Seven Permanent Architecture Rules (v2.2)

### Rule 1: Every Oryol product belongs to Oryol Workspace
No product exists as a standalone silo or independent account database. All applications plug into the centralized Core platform.

### Rule 2: Multi-Tenant Hierarchy via Principals & Organizations
Every business object belongs strictly to an Organization. Access is mediated through Principals holding Memberships within the Organization:
```
Principal (human | service) ──► Membership ──► Organization ──► Teams ──► Application ──► Business Object
```
*Note: Guest, contractor, employee, and external collaborator designations are strictly membership-level attributes, never global principal types.*

### Rule 3: Centralized Authentication & Unified Identity
Applications must **never** create custom login forms, credential stores, password hashes, or private session cookies. Authentication is strictly delegated to Oryol Identity.

### Rule 4: Canonical Authorization Contract
Authorization is evaluated through the unified context-aware interface:
```typescript
authorize({
  principal,
  membership,
  organization,
  action,
  resource,
  context
})
```
All actions use canonical 3-part namespaces (e.g. `mail.messages.read`, `drive.documents.read`, `crm.deals.manage`, `virel.invoices.create`, `core.members.invite`).

### Rule 5: Separation of Audit, Events, and Observability
- **Audit Events**: Immutable append-only record of "What happened?" for compliance and security forensics.
- **Domain Events**: Distributed state broadcasts of "What should other systems know?" via the Transactional Outbox pattern.
- **Observability**: Telemetry and metrics measuring "How is the system behaving?".

### Rule 6: Permission-Aware AI Platform
Applications must **never** call AI LLM providers directly. All AI requests pass through the Oryol AI Gateway, which enforces pre-flight authorization, tenant context isolation, verified provider-retention and training compliance, and usage audit logging.

### Rule 7: Search as a Derived Index with Live Authorization
Search indexes are derived read models, **never** the source of truth. Search queries enforce organization isolation and perform live `authorize()` evaluations before returning candidate titles, snippets, or metadata.
