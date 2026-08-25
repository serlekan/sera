# Oryol Core — System Boundaries & Service Contracts

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**System**: `serlekan/oryol-core`

---

## 1. Responsibilities of Oryol Core

Oryol Core provides the single pane of glass for all cross-cutting infrastructure, identity, and governance concerns.

### 1.1 Identity & Principal Registry
- Global Principal management (`prn_...`) representing human users, service accounts, and external identity federations.
- FIDO2 / WebAuthn Passkeys, Magic Links, MFA verification, and Enterprise IdP mappings.
- Authoritative session store with cryptographic token rotation and instant revocation.

### 1.2 Multi-Tenant Hierarchy & Organization Governance
- Organization (`org_...`) lifecycle management: `active`, `suspended`, `archived`, `deletion_pending`.
- Organization Memberships (`mem_...`), Invitations (`inv_...`), and Team structures (`team_...`).
- Global and custom Role definitions (`rol_...`) and permission bindings.
- Organization-level security policies (MFA enforcement, IP allowlisting, session timeouts).

### 1.3 Authorization Engine
- Uniform evaluation contract: `authorize({ principal, organization, action, resource, context })`.
- Unified permission registry with dot-notation namespaces (`core.*`, `mail.*`, `crm.*`, `calendar.*`, `drive.*`, `virel.*`).
- Deny-precedence rule resolution and scope validation.

### 1.4 Central Platform Pipelines
- **Audit Logging**: Immutable, append-only security logs.
- **Domain Event Bus**: Outbox-backed event dispatching for cross-application sync.
- **AI Gateway**: Permission-filtered LLM routing, context sanitation, zero data retention enforcement.
- **Search Contracts**: Indexing protocols and permission-aware search query contracts.
- **Application Entitlements & Feature Flags**: Managing licensed application modules per organization.

---

## 2. Boundaries: What Oryol Core Does NOT Do

To prevent architectural bloat and maintain separation of concerns, Oryol Core strictly delegates the following to product repositories:

1. **No Email Storage or Transport**: OryolMail owns inbound/outbound MX, SMTP relays, IMAP/POP gateways, mailboxes, threads, messages, and raw RFC822 blobs.
2. **No CRM Pipeline Mechanics**: Oryol CRM owns deal stages, sales pipelines, lead scoring, contact timelines, and accounting objects.
3. **No Calendar Scheduling Engines**: Oryol Calendar owns CalDAV, meeting availability algorithms, recurring event math, and ICS parsing.
4. **No Raw Asset Storage**: Oryol Drive owns folder hierarchies, file version trees, and direct Cloudflare R2 binary bucket streaming.
5. **No Bespoke Workflow Logic**: Virel owns automated agent workflows, cross-app task triggers, and execution heuristics.

---

## 3. Communication Contract Between Core and Applications

```text
Product Client ──► Product Worker (e.g. oryol-mail)
                         │
                         ├──► 1. Validate Session JWT via Core Public Ed25519 Key (Local Edge)
                         ├──► 2. Check Permissions via Core Authorize Function (Local/Cached)
                         ├──► 3. Execute Product Business Mutation (e.g. Store Email Message)
                         └──► 4. Append Transactional Outbox Event (Atomic with Mutation)
```
