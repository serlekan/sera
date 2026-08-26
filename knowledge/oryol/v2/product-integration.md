# Oryol Product Integration Architecture v2.2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**Scope**: Integration Patterns across Oryol Workspace Applications

---

## 1. Application Integration Topology

All applications in the Oryol Workspace communicate via standardized outbox events and Core platform contracts rather than tight cross-database couplings.

```mermaid
graph TD
    Core[Oryol Core: Identity, Auth, Outbox, AI Gateway, Search]
    
    subgraph Products["Oryol Applications"]
        Mail[OryolMail: Email Domain & Attachments]
        CRM[Oryol CRM: Deals & Contacts]
        Cal[Oryol Calendar: Scheduling]
        Drive[Oryol Drive: Cloud Documents]
        Virel[Virel: Financial Ledgers & Wallets]
    end
    
    Mail <--> Core
    CRM <--> Core
    Cal <--> Core
    Drive <--> Core
    Virel <--> Core
    
    Mail -. Event: mail.message.received .-> CRM
    Mail -. Event: mail.invite.parsed .-> Cal
    Mail -. User-Initiated Copy .-> Drive
    Virel -. Financial Workflows .-> Core & Mail & CRM
```

---

## 2. Standard Integration Contracts

### 2.1 OryolMail Integration
- **Auth & Session**: Consumes Core Session JWT (`X-Oryol-Org-Id`).
- **Domain Verification**: OryolMail owns email-specific DNS routing (MX, SPF, 2048-bit DKIM selector generation, DMARC validation); Core manages generic organization domain claims (`dom_...`).
- **Attachment Persistence**: OryolMail owns all email attachment persistence within mail storage buckets. Mail attachments are **not** automatically relocated to Oryol Drive. Oryol Drive integration exposes explicit, user-initiated copy/link workflows.
- **CRM Linking**: Emits `mail.message.received` event; Oryol CRM listens and appends email to matching contact timeline.
- **Calendar Linking**: Emits `mail.invite.parsed` event; Oryol Calendar offers one-click RSVP.

### 2.2 Virel Financial & Ledger Workflows
- **Financial Ownership**: Virel owns organization wallets, payment methods, transaction ledgers, invoice issuance, and billing reconciliation workflows.
- **Cross-App Automation**: Subscribes to outbox events (e.g. `crm.deal.won`, `core.membership.provisioned`) to generate billing invoices and reconcile subscription balances.

### 2.3 Oryol Drive Integration
- **Asset Storage**: Manages organization documents, assets, and folders.
- **User-Controlled Mail Links**: Allows users to explicitly save email attachments into Drive folders or link Drive documents into outgoing email compositions.
