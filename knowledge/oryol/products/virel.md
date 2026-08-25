# Product: Virel

## 1. Overview & Purpose

Virel is the intelligent workflow automation, cross-product synthesis, and executive assistant layer across Oryol Workspace. It bridges mail, calendar, CRM, and drive into automated proactive business intelligence.

---

## 2. Core Capabilities

1. **Cross-App Synthesis**: Correlate communications in OryolMail with deal progress in Oryol CRM and upcoming meetings in Oryol Calendar.
2. **Proactive Workflow Automation**: Trigger automated multi-step workflows (e.g. on receiving customer contract email -> parse PDF -> update CRM deal -> schedule onboarding kick-off).
3. **Executive Daily Briefings**: Digest organization priorities, unanswered high-priority client threads, and urgent action items.

---

## 3. Architecture Rules & Security

- **Permission Envelope Bound**: Virel operations execute strictly under the requesting user's membership permissions.
- **Audit Logging**: All automated decisions, workflow triggers, and generated content are logged in the organization audit trail.
- **Human in the Loop**: Reversible actions execute automatically; destructive or external communications require explicit approval.
