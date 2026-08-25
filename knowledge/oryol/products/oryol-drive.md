# Product: Oryol Drive

## 1. Overview & Purpose

Oryol Drive is the secure document, asset, and file storage engine for Oryol Workspace, providing encrypted storage, versioning, access control, and seamless attachment linking across all products.

---

## 2. Core Entities

1. **Drive / Folder (`fld_...`)**: Hierarchical organization container.
2. **File / Asset (`file_...`)**: Stored binary object with metadata, MIME type, size, hash, and Cloudflare R2 reference.
3. **File Version (`ver_...`)**: Immutable previous version snapshot.
4. **Share Link (`share_...`)**: Time-bounded or password-protected external access grant.

---

## 3. Architecture Rules & Integrations

- **Cloudflare R2 Backing**: Files are stored in encrypted R2 buckets partitioned by `org_<id>`.
- **Zero Cross-Org Access**: Pre-signed URLs are short-lived and validate membership tokens.
- **OryolMail Integration**: Large attachments in emails are stored in Oryol Drive and linked directly.
- **Permission Scopes**: `drive.read`, `drive.write`, `drive.share`, `drive.admin`.
