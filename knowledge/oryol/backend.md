# Oryol Backend Architecture & Cloudflare Edge Services

The production backend architecture for Oryol Workspace is designed for low latency, zero cold starts, and global multi-tenant scalability leveraging Cloudflare Edge Infrastructure.

---

## 1. Production Backend Topology

```mermaid
graph LR
    Client[Web & Mobile Clients] --> Edge[Cloudflare Global Edge]
    Edge --> Workers[Cloudflare Workers API Gateway]
    
    subgraph Storage["Edge Persistence Layer"]
        Workers --> D1[(Cloudflare D1: SQLite Edge DB)]
        Workers --> KV[(Cloudflare KV: Session & Cache)]
        Workers --> R2[(Cloudflare R2: Attachments & Raw Blobs)]
        Workers --> Queues[Cloudflare Queues: Async Ingestion]
    end
    
    subgraph External["External Transport (Planned)"]
        Queues --> Inbound[Inbound MX Relays]
        Workers --> Outbound[Outbound SMTP / Resend Transport]
        Workers --> AI[Google Gemini / Anthropic API]
    end
```

---

## 2. Stateless Execution & Tenant Scoping

- **Stateless Workers**: API endpoints run as lightweight edge functions.
- **Header Propagation**: The gateway extracts and validates JWT tokens, populating tenant context (`ctx.orgId`, `ctx.userId`, `ctx.membershipId`).
- **Database Connection**: Queries execute against organization-partitioned D1 relational databases with bound parameterized statements.
- **Asset Offloading**: Large file attachments are streamed directly to Cloudflare R2 using pre-signed organization-prefixed URLs.
