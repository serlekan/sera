# Oryol Search Platform Architecture v2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Scope**: Search Contract & Multi-Tenant Indexing Blueprint

---

## 1. Search Contract & Core Invariants

> [!IMPORTANT]
> **Three Search Rules**:
> 1. **Search is NOT the Source of Truth**: Search indexes are derived read models built asynchronously from Domain Outbox Events. Primary relational stores (D1) remain authoritative.
> 2. **Search Respects Permissions**: Search queries perform document-level and mailbox-level permission trimming before returning results.
> 3. **Search Inherits Organization Isolation**: Search indexes are strictly partitioned by `organization_id`. Cross-tenant search queries are structurally impossible.

---

## 2. Searchable Workspace Domains

The centralized search index aggregates searchable business entities across all applications:

| Application | Searchable Resources | Indexed Attributes | Permission Scope |
|---|---|---|---|
| **OryolMail** | Emails, Threads, Attachments | Subject, sender, recipients, body text, AI summary, tags | `mail.read` |
| **Oryol CRM** | Contacts, Accounts, Deals | Name, company, email, notes, deal title, timeline | `crm.contacts.read` |
| **Oryol Drive** | Documents, Assets, Folders | Filename, OCR text, document body, owner, tags | `drive.read` |
| **Calendar** | Meetings, Events, Invites | Event title, description, attendees, location | `calendar.read` |
| **Virel** | Tasks, Action Items, Automations | Task name, checklist items, assigned owner | `virel.synthesize` |

---

## 3. Asynchronous Search Indexing Protocol

```text
Application Mutation (e.g. Email Received)
       │
       ▼
Transactional Outbox Event (`mail.message.created`)
       │
       ▼
Cloudflare Queue Consumer
       │
       ▼
Search Ingestion Pipeline
 1. Extract text and metadata
 2. Attach `organization_id` & `acl_tags` (e.g. `mailbox:mbx_123`, `public`)
 3. Push to Vector / Lexical Search Index (Cloudflare Vectorize / Edge Search)
```

When a user executes a search (`GET /v1/search?q=invoice`):
1. Gateway injects caller's `org_id` and active `membership_id` / `team_ids`.
2. Search query filters: `organization_id == ctx.orgId AND (acl_tags CONTAINS ctx.accessibleMailboxes)`.
3. Results are returned with highlighting, relevance scores, and deep-link resource URLs.
