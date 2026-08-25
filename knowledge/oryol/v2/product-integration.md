# Oryol Product Integration Architecture v2

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Scope**: Integration Patterns across Oryol Workspace Applications

---

## 1. Application Integration Topology

All applications in the Oryol Workspace communicate via standardized integration patterns rather than tight cross-database couplings.

```mermaid
graph TD
    Core[Oryol Core: Identity, Auth, Outbox, AI Gateway, Search]
    
    subgraph Products["Oryol Applications"]
        Mail[OryolMail: Email Domain]
        CRM[Oryol CRM: Deals & Contacts]
        Cal[Oryol Calendar: Scheduling]
        Drive[Oryol Drive: Cloud Documents]
        Virel[Virel: Intelligence & Automation]
    end
    
    Mail <--> Core
    CRM <--> Core
    Cal <--> Core
    Drive <--> Core
    Virel <--> Core
    
    Mail -. Event: message.received .-> CRM
    Mail -. Event: invite.received .-> Cal
    Mail -. Link: attachment .-> Drive
    Virel -. Synthesize .-> Mail & CRM & Cal & Drive
```

---

## 2. Standard Integration Contracts

### 2.1 OryolMail Integration
- **Auth & Session**: Consumes Core Session JWT (`X-Oryol-Org-Id`).
- **Domain Verification**: Dispatches verification requests to Core Domain Service.
- **CRM Linking**: Emits `mail.message.received` event; Oryol CRM listens and appends email to matching contact timeline.
- **Calendar Linking**: Emits `mail.invite.parsed` event; Oryol Calendar offers one-click RSVP.
- **Drive Attachments**: Large attachments are uploaded to Oryol Drive, and only reference links (`att_...`) are embedded in email bodies.

### 2.2 Virel Cross-App Intelligence
- **AI Synthesis**: Calls Core AI Gateway with `virel.synthesize` scope to aggregate insights across Mail, Calendar, and CRM without bypassing individual document ACLs.
- **Automated Workflows**: Subscribes to domain outbox queues to trigger multi-step automations.
