# Oryol Search Platform Architecture v2.2 — Derived Read Models

**Status**: CANONICAL ARCHITECTURE BASELINE (v2.2)  
**P0 Remediation**: Strict Live Authorization Check, Sensitive Snippet Protection & AI Retrieval Alignment

---

## 1. Universal Search Architecture Principles

Search in Oryol Workspace is implemented as a **derived secondary index** built asynchronously via domain outbox events.

> [!IMPORTANT]
> **Strict Authorization Invariant**:  
> Projected ACL tags or index metadata are **NEVER sufficient to authorize result exposure**.  
> Search query results (including titles, highlighted snippets, and metadata) must undergo a **live `authorize()` check** before being returned to the client.

---

## 2. Canonical Search Query Flow

```text
┌─────────────────┐
│ Client Request  │ (Query: "Acme contract", Authenticated Principal & Membership)
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            Oryol Search Gateway                             │
│                                                                             │
│ 1. Organization Partition ─► Scope search strictly to `organization_id`     │
│                                                                             │
│ 2. Index Pre-Filter ───────► Query derived index (Vectorize/BM25) with ACL  │
│                              tags used solely as a performance pre-filter   │
│                                                                             │
│ 3. Extract Candidates ─────► Collect candidate resource IDs (e.g. 50 items) │
│                                                                             │
│ 4. Live Authorization ─────► Dispatch candidate batch to Core `authorize()` │
│                              evaluator in parallel                          │
│                                                                             │
│ 5. Filter Unauthorized ────► Drop any candidate where `authorize()` returns │
│                              DENY (stale permissions trimmed dynamically)   │
│                                                                             │
│ 6. Render Authorized Hits ─► Return search response containing authorized   │
│                              titles, snippets, and resource references      │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────┐ (Client opens result)
│ App Data Fetch  │ ──► Owning application serves authoritative full payload
└─────────────────┘
```

---

## 3. Sensitive Snippet Governance

Highlighted text snippets frequently contain confidential PII, email bodies, financial numbers, or deal negotiations. Therefore:
1. Snippets are **never returned** for candidate records prior to live authorization approval.
2. Inverted index storage encrypts snippet fields at rest using organization-scoped keys.

---

## 4. Alignment with AI Retrieval Contracts

When the Oryol AI Gateway performs Retrieval-Augmented Generation (RAG):
1. The AI retrieval worker executes searches **strictly through the same authorized Search Gateway path**.
2. Context documents dropped by live authorization are excluded from the prompt envelope before foundation model dispatch.
