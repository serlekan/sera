# Oryol AI Platform Architecture v2 — Centralized Gateway

**Status**: CANONICAL ARCHITECTURE BASELINE (v2)  
**Supersedes**: `knowledge/oryol/ai-principles.md` (v1)

---

## 1. Canonical AI Gateway Flow

> [!IMPORTANT]
> **Strict Platform Rule**:  
> Applications (OryolMail, CRM, Drive, Virel) must **never** invoke third-party AI foundation models directly.  
> All AI requests must route through the **Oryol AI Gateway** inside Oryol Core.

```
┌─────────────────┐
│ Application     │ (OryolMail: "Summarize Thread thd_123")
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Oryol AI Gateway                               │
│                                                                             │
│ 1. Permission Gate ────► Evaluates `authorize({ action: 'mail.read', ...})` │
│                                                                             │
│ 2. Context Retrieval ──► Fetches message context strictly within `org_id`   │
│                                                                             │
│ 3. PII & Secret Mask ──► Strips credentials/tokens from prompt envelope     │
│                                                                             │
│ 4. Provider Router ────► Enforces Zero Data Retention headers (Gemini/Claude)│
│                                                                             │
│ 5. Schema Validation ──► Validates strict JSON response against DTO schema  │
│                                                                             │
│ 6. Usage & Audit ──────► Emits AI usage token count and audit event         │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│           Enterprise AI Model Provider (Zero Data Retention)                │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Core AI Gateway Responsibilities

1. **Permission Filtering**: Gateway verifies the caller's membership possesses appropriate read access for the target entity before any context is retrieved.
2. **Context Isolation**: Only records matching the authenticated `organization_id` can be injected into the prompt envelope.
3. **Provider Abstraction**: Applications specify intent (`task: "summarize_thread"`, `tier: "fast" | "reasoning"`), and the Gateway selects the optimal configured provider (Gemini 2.5/3.0, Anthropic Claude, etc.).
4. **AI Audit Logging**: Every AI interaction logs tokens consumed, latency, model version, and initiating principal ID to `audit_events`.
5. **Zero Data Retention**: All commercial provider connections contractually enforce zero training and zero external data retention.
