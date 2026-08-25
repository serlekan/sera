# Product: Oryol CRM

## 1. Overview & Purpose

Oryol CRM is the customer relationship management engine within Oryol Workspace. It centralizes customer communications, pipeline deals, company accounts, and activity history into a unified, privacy-first interface.

---

## 2. Core Entities

1. **Contact (`ctc_...`)**: Individual person linked to an organization, with email, phone, and timeline.
2. **Company / Account (`acc_...`)**: Corporate client entity grouping multiple contacts and domains.
3. **Deal / Opportunity (`deal_...`)**: Revenue pipeline item with stage, value, close date, and owner.
4. **Pipeline (`pipe_...`)**: Configurable stage workflow for sales, partnerships, or support onboarding.
5. **Activity (`act_...`)**: Unified record of emails (from OryolMail), calls, notes, and calendar meetings.

---

## 3. Architecture Rules & Integrations

- **Organization Bound**: All contacts, deals, and accounts belong strictly to the active Organization.
- **OryolMail Native Integration**: Bidirectional sync between incoming client emails and contact activity timelines without manual forwarders.
- **Permission Scopes**: `crm.view`, `crm.edit`, `crm.deal.manage`, `crm.export`, `crm.admin`.
