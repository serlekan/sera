# Oryol AI Engineering & Privacy Principles (v1)

> [!WARNING]
> **Status: SUPERSEDED**  
> This document describes Oryol AI Principles v1. For the current canonical centralized AI Gateway architecture, see [`knowledge/oryol/v2/ai-platform.md`](v2/ai-platform.md).

Oryol Workspace incorporates native, edge-assisted artificial intelligence designed for high productivity without compromising enterprise privacy, multi-tenant boundaries, or user authorization.

---

## 1. Core AI Principles

### 1. Privacy By Design & Zero Third-Party Training
- Customer email content, CRM communications, notes, and documents must **never** be used to train, fine-tune, or reinforce public third-party foundation models.
- Enterprise zero-retention API policies (Google Gemini Enterprise, Anthropic Commercial, OpenAI Zero Data Retention) are enforced at the backend platform layer.

### 2. Strict Permission Enforcement in AI Contexts
- Before any email thread summary, contact synthesis, or draft generation occurs, the backend orchestrator must verify that the requesting user possesses valid `mail.read` (or corresponding scope) for the underlying entities.
- AI features must never aggregate data across organizations or across unshared mailboxes that the user cannot directly inspect.

### 3. Human in the Loop (Supervised AI)
- AI operations default to **drafting, summarizing, and recommending** rather than autonomous irreversible actions.
- Automatic sending of emails or deletion of records by AI without explicit user confirmation is prohibited.

### 4. Structured Output & Graceful Degradation
- All AI endpoints must request deterministic JSON schemas (e.g. `SummarizeResponse`, `DraftReplyResponse`, `DnsTroubleshootResponse`).
- Frontend clients must handle AI service outages, missing API keys, or invalid responses cleanly with polite error states and fallback mock heuristics where appropriate.

---

## 2. Standard AI Service Architecture

```
Frontend Feature Component (e.g. ComposeModal, ReadingPane)
      │
      ▼
Client Service Abstraction (`src/services/ai.ts`)
      │  (POST /api/ai/...)
      ▼
Server Route Handler (`server.ts` or Cloudflare Worker API)
      │
      ├─► 1. Verify Authorization & Membership Scope
      ├─► 2. Sanitize & Prepare Prompt Envelope
      ├─► 3. Call AI Provider with Zero-Retention Policy
      └─► 4. Validate Structured Schema & Return Result
```
