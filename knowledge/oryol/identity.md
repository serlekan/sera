# Oryol Identity, Authentication & Membership

Identity across Oryol Workspace is centralized, strictly partitioned by organization membership, and decoupled from individual application services.

---

## 1. Identity Hierarchy

```
Global User Account (Account Level)
   ├── ID: usr_<uuid>
   ├── Primary Email
   ├── MFA Credentials / WebAuthn / Passkeys
   └── Global Profile (Display Name, Avatar)
        │
        └── Memberships (Tenant Level)
             ├── ID: mem_<uuid>
             ├── Organization ID: org_<uuid>
             ├── Role: Owner | Admin | Member | Guest
             ├── Direct Permissions: ["mail.read", "mail.send", "crm.view"]
             ├── Assigned Mailbox Aliases: ["sarah@acme.com", "support@acme.com"]
             ├── Status: active | suspended | pending_invite
             └── Joined At: ISO-8601 Timestamp
```

---

## 2. Authentication Flow

1. **Centralized Sign-In**: Authentication happens exclusively through Oryol Identity (Oryol Auth).
2. **Session Token Issuance**: On successful MFA/Passkey verification, an encrypted, cryptographically signed Workspace Session Token is issued.
3. **Organization Context Switching**: 
   - When a user selects or switches to an active Organization, a short-lived scoped JWT is generated carrying:
     - `sub`: User ID (`usr_...`)
     - `org_id`: Active Organization ID (`org_...`)
     - `mem_id`: Active Membership ID (`mem_...`)
     - `roles`: Assigned roles inside this organization
     - `permissions`: Effective permission bitmap/scopes
4. **App Service Validation**: Products and microservices inspect and cryptographically verify the JWT claims at edge gateways without re-authenticating the user.

---

## 3. Core Identity Invariants

- **No Product-Specific Auth**: OryolMail, Oryol CRM, etc., never store passwords, issue session cookies, or maintain custom auth tables.
- **Tenant Context Header**: Every inter-service and API request carries `X-Oryol-Org-Id` and `Authorization: Bearer <token>`, which must match the verified token claims.
- **Immediate Revocation**: Membership revocation in the organization immediately invalidates that user's access across all Oryol apps for that organization.
