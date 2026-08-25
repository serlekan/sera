# Oryol Workspace Architecture v2 — Canonical Platform Model

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Supersedes**: `knowledge/oryol/workspace.md` (v1)

---

## 1. The Canonical Platform Hierarchy

Oryol Workspace is structured as a two-tier system: the **Oryol Core Platform** layer providing universal multi-tenant capabilities, and specialized **Product Applications** owning domain-specific business data and user experiences.

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
│  (Mail Domain)  ││  (Sales/Leads)  ││  (Scheduling)   ││ (Asset Storage) ││  (Automation)   │
└─────────────────┘└─────────────────┘└─────────────────┘└─────────────────┘└─────────────────┘
```

---

## 2. Core Separation Rule

> [!IMPORTANT]
> **Fundamental Platform Invariant**:  
> **Oryol Core** provides platform capabilities and multi-tenant security guarantees.  
> **Product Applications** own their specific business domains, data lifecycles, and workflows.

| Layer | System Ownership | Must NOT Own |
|---|---|---|
| **Oryol Core Platform** | Identity (Principals, Users, Service Accounts), Organizations, Memberships, Teams, Roles, Permissions, Authoritative Sessions, Audit Stream, Domain Events, AI Gateway Contracts, Search Contracts, Feature Flags, Application Entitlements. | Mail transport, email storage, CRM deal pipelines, calendar invites, binary file storage, accounting ledgers. |
| **Applications** (`oryol-mail`, `oryol-crm`, `oryol-calendar`, `oryol-drive`, `virel`) | Mailbox state, email threads, attachments, contacts, deals, calendar events, drive folders, custom domain logic, application UI/UX. | Independent user authentication, private session cookie issuing, isolated membership schemas, bypassable permission tables. |

---

## 3. Seven Permanent Architecture Rules (v2)

### Rule 1: Every Oryol product belongs to Oryol Workspace
No product exists as a standalone silo or independent account database. All applications plug into the centralized Core platform.

### Rule 2: Multi-Tenant Hierarchy via Principals & Organizations
Every business object belongs strictly to an Organization. Access is mediated through Principals holding Memberships within the Organization:
```
Principal (User / Service / External) ──► Membership ──► Organization ──► Teams ──► Application ──► Business Object
```

### Rule 3: Centralized Authentication & Unified Identity
Applications must **never** create custom login forms, credential stores, password hashes, or private session cookies. Authentication is strictly delegated to Oryol Identity.

### Rule 4: Fine-Grained Authorization Contract
Authorization is evaluated through a unified context-aware interface:
`authorize({ principal, organization, action, resource, context })` combining RBAC, resource ACLs, and deny-precedence rules.

### Rule 5: Separation of Audit, Events, and Observability
- **Audit Events**: Immutable record of "What happened?" for compliance and security forensics.
- **Domain Events**: Distributed state broadcasts of "What should other systems know?" via the Transactional Outbox pattern.
- **Observability**: Telemetry and metrics measuring "How is the system behaving?".

### Rule 6: Permission-Aware AI Platform
Applications must **never** call AI LLM providers directly. All AI requests pass through the Oryol AI Gateway, which enforces pre-flight authorization, tenant context isolation, zero-retention policies, and usage audit logging.

### Rule 7: Search as a Derived Index
Search indexes are derived read models, **never** the source of truth. Search queries inherit organization isolation and enforce document-level permission trimming.
