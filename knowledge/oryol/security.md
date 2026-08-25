# Oryol Workspace Security & Authorization Model

Security in Oryol Workspace is built on zero-trust principles, strict tenant isolation, cryptographic verification, and non-bypassable permission enforcement.

---

## 1. Multi-Tenant Isolation Guarantees

1. **Logical Data Partitioning**: All storage rows in relational databases (D1), document keys in KV, and file objects in bucket storage (R2) must include the `org_<id>` prefix or partition key.
2. **Row-Level Security (RLS) & Query Scoping**:
   ```sql
   -- Standard mandatory query pattern
   SELECT * FROM mailboxes 
   WHERE organization_id = ? AND id = ?;
   ```
   Unscoped queries such as `SELECT * FROM mailboxes WHERE id = ?` are strictly rejected by static analysis and code review.
3. **Storage Object Keys**:
   ```
   R2 Object Key: /org_{org_id}/{app_name}/{entity_type}/{entity_id}/{filename}
   ```
   Direct object access across organization boundaries is impossible by design.

---

## 2. Granular Permission System

Permissions follow the standard dot-notation namespace: `<app>.<resource>.<action>`

| Scope | Permission | Description |
|---|---|---|
| **Mail** | `mail.read` | View messages in personal/assigned mailboxes |
| | `mail.send` | Compose and dispatch messages from assigned aliases |
| | `mail.shared.read` | View messages in shared team mailboxes |
| | `mail.shared.assign` | Reassign thread tickets and add internal notes |
| | `mail.manage` | Configure filters, aliases, and signatures |
| | `mail.delete` | Permanently remove or purge email threads |
| **Domain** | `domain.view` | View verified and pending custom domains |
| | `domain.manage` | Add custom domains and edit routing records |
| | `domain.verify` | Trigger automated DNS checks and diagnostics |
| **Org** | `org.members.manage` | Invite, promote, or remove members |
| | `org.audit.view` | Read organization audit trails |
| | `org.billing.manage` | Manage subscription tiers and payment methods |

---

## 3. Mandatory Audit Trail

All sensitive events produce structured audit entries:
```json
{
  "event_id": "aud_01HXYZ789...",
  "timestamp": "2026-08-25T19:25:00.000Z",
  "organization_id": "org_acme_corp",
  "actor": {
    "user_id": "usr_sarah_1",
    "membership_id": "mem_sarah_acme",
    "ip_address": "198.51.100.42",
    "user_agent": "OryolClient/1.0"
  },
  "action": "domain.verify",
  "resource": {
    "type": "domain",
    "id": "dom_oryolhq",
    "metadata": { "domain": "oryolhq.com", "status": "verified" }
  },
  "outcome": "success"
}
```

---

## 4. Secret & Token Management

- No plaintext secrets in code or repository configurations.
- Platform secrets are stored in secure environment bindings (e.g. Cloudflare Worker Secrets).
- Cryptographic keys for DKIM generation and API signatures are isolated per organization and rotated on demand.
