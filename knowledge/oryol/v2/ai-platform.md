# Oryol AI Platform Architecture v2.1 — Centralized Gateway

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.1)  
**P0 Remediation**: Centralized AI Gateway, Provider Retention Governance & Application Context-Provider Model

---

## 1. Canonical AI Gateway Flow

> [!IMPORTANT]
> **Strict Platform Rule**:  
> Applications (OryolMail, CRM, Drive, Virel) must **never** invoke third-party AI foundation models directly.  
> All AI requests must route through the **Oryol AI Gateway** inside Oryol Core.

```
┌─────────────────┐
│ Application     │ (OryolMail packages sanitized thread context)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Oryol AI Gateway                               │
│                                                                             │
│ 1. Permission Gate ────► Evaluates `authorize({ action: 'mail.messages.read'│
│                                                                             │
│ 2. Context Validation ─► Validates context ownership strictly within org_id │
│                                                                             │
│ 3. PII & Secret Mask ──► Strips credentials and session tokens from payload │
│                                                                             │
│ 4. Provider Router ────► Routes to provider matching retention policy       │
│                                                                             │
│ 5. Schema Validation ──► Validates strict JSON response against DTO schema  │
│                                                                             │
│ 6. Usage & Audit ──────► Emits AI usage token count and audit event         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│             Verified Enterprise Model Provider (Approved Policy)            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core AI Gateway Responsibilities & Retention Policy

1. **Permission Filtering**: Gateway verifies the caller's membership possesses appropriate read access for the target entity before dispatching prompts.
2. **Context Isolation**: Only records matching the authenticated `organization_id` can be injected into the prompt envelope.
3. **Provider Abstraction**: Applications specify intent (`task: "summarize_thread"`, `tier: "fast" | "reasoning"`), and the Gateway selects the optimal configured provider.
4. **AI Audit Logging**: Every AI interaction logs tokens consumed, latency, model version, and initiating principal ID to `audit_events`.
5. **Provider Retention & Training Governance**:
   - Providers handling Oryol workspace data must satisfy the approved **Oryol provider-retention and training policy**.
   - Sensitive workspace data requires **verified no-training / zero-retention configurations where contractually available and approved**.
   - Provider retention and data handling guarantees must be verified and certified before production enablement.
