"""Tests for Oryol Architecture v2.2 Knowledge Base, Corpus Consistency and Fail-Closed Governance in SERA."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src/ is first in sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from sera.core import (
    ORYOL_ARCHITECTURE_BASELINE_VERSION,
    ORYOL_ARCHITECTURE_SPEC_SHA,
    ORYOL_REQUIRED_POLICY_FILES,
    SERA_GOVERNANCE_VERSION,
    SeraError,
    generate_packet,
    initialize,
    is_oryol_repository,
    load_config,
    load_repository_policies,
    new_task,
    validate_config,
)


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class TestOryolV2CorpusIntegrity(unittest.TestCase):
    """Verify that all Oryol v2.2 canonical architecture and governance documents exist and are structurally valid."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.knowledge_v2 = cls.root / "knowledge" / "oryol" / "v2"

    def test_v2_knowledge_directory_exists(self):
        self.assertTrue(self.knowledge_v2.is_dir(), "knowledge/oryol/v2 directory must exist")

    def test_all_13_canonical_v2_documents_exist(self):
        expected_docs = [
            "workspace-architecture.md",
            "core-boundaries.md",
            "multi-tenancy.md",
            "identity-model.md",
            "authorization-model.md",
            "session-security.md",
            "audit-and-events.md",
            "cloudflare-platform.md",
            "data-lifecycle.md",
            "ai-platform.md",
            "search-platform.md",
            "product-integration.md",
            "sera-governance.md",
            "ARCHITECTURE-BASELINE.md",
        ]
        for doc_name in expected_docs:
            doc_path = self.knowledge_v2 / doc_name
            self.assertTrue(doc_path.is_file(), f"Missing canonical v2 document: {doc_name}")
            content = doc_path.read_text(encoding="utf-8")
            self.assertGreater(len(content), 100, f"Document {doc_name} is unexpectedly small")
            self.assertTrue(
                "CANONICAL ARCHITECTURE BASELINE (v2.2)" in content or "CANDIDATE FOR FINAL" in content,
                f"Document {doc_name} missing canonical v2.2 header"
            )

    def test_v1_documents_marked_superseded(self):
        knowledge_v1 = self.root / "knowledge" / "oryol"
        v1_docs = [
            "workspace.md",
            "identity.md",
            "security.md",
            "data-model.md",
            "ai-principles.md",
            "backend.md",
            "oryol-core-architecture.md",
            "oryol-core-implementation-roadmap.md",
            "oryol-core-phase1-specification.md",
        ]
        for doc_name in v1_docs:
            doc_path = knowledge_v1 / doc_name
            if doc_path.is_file():
                content = doc_path.read_text(encoding="utf-8")
                self.assertIn("SUPERSEDED", content, f"v1 document {doc_name} must be marked SUPERSEDED")

    def test_corpus_contradiction_scan_no_stale_patterns(self):
        """Scans all v2 files for prohibited stale patterns identified during GPT reviews."""
        canonical_files = list(self.knowledge_v2.glob("*.md"))
        self.assertGreaterEqual(len(canonical_files), 13)

        for file_path in canonical_files:
            text = file_path.read_text(encoding="utf-8")
            filename = file_path.name

            # 1. No "0ms revocation" or "0ms delay" claims
            self.assertNotIn("0ms revocation", text.lower(), f"Prohibited '0ms revocation' claim found in {filename}")
            self.assertNotIn("0ms delay", text.lower(), f"Prohibited '0ms delay' claim found in {filename}")

            # 2. No transport-level "exactly-once" delivery claims
            self.assertNotIn("exactly-once delivery", text.lower(), f"Prohibited 'exactly-once delivery' claim found in {filename}")
            self.assertNotIn("guarantees exactly-once", text.lower(), f"Prohibited 'guarantees exactly-once' claim found in {filename}")

            # 3. Principal taxonomy must never include 'external' as a top-level principal type
            if filename in ("workspace-architecture.md", "identity-model.md"):
                self.assertNotIn("Principal (User / Service / External)", text, f"Stale external principal taxonomy found in {filename}")

            # 4. Search authorization must require live authorization
            if filename == "search-platform.md":
                self.assertTrue("live `authorize()`" in text or "live authorization" in text.lower(), f"Live authorization check required in {filename}")

            # 5. Core AI Gateway must not directly query product databases
            if filename in ("core-boundaries.md", "ai-platform.md", "workspace-architecture.md"):
                self.assertNotIn("Gateway directly queries", text, f"Direct product DB query pattern found in {filename}")

    def test_identity_v2_2_contains_principal_model(self):
        identity_doc = (self.knowledge_v2 / "identity-model.md").read_text(encoding="utf-8")
        self.assertIn("Principal", identity_doc)
        self.assertIn("Human Principal", identity_doc)
        self.assertIn("Service Principal", identity_doc)
        self.assertIn("principals", identity_doc)
        self.assertIn("service_accounts", identity_doc)
        self.assertIn("identity_provider_bindings", identity_doc)
        self.assertIn("recovery_methods", identity_doc)
        self.assertIn("last-owner protection", identity_doc.lower())

    def test_multi_tenancy_v2_2_structural_isolation_and_roles(self):
        mt_doc = (self.knowledge_v2 / "multi-tenancy.md").read_text(encoding="utf-8")
        self.assertIn("team_memberships", mt_doc)
        self.assertIn("organization_permission_registries", mt_doc)
        self.assertIn("role_definitions", mt_doc)
        self.assertIn("UNIQUE(organization_id, id)", mt_doc)
        self.assertIn("role_permissions", mt_doc)
        self.assertIn("FOREIGN KEY (registry_version, permission_name) REFERENCES permission_definitions", mt_doc)
        self.assertIn("membership_role_assignments", mt_doc)
        self.assertIn("FOREIGN KEY (organization_id, role_id) REFERENCES role_definitions(organization_id, id)", mt_doc)
        self.assertIn("organization_service_principals", mt_doc)
        self.assertIn("authorization_subjects", mt_doc)
        self.assertIn("subject_type IN ('membership', 'team', 'service_principal')", mt_doc)
        self.assertIn("invitations", mt_doc)
        self.assertIn("resource_registry", mt_doc)
        self.assertIn("PRIMARY KEY (organization_id, resource_type, resource_id)", mt_doc)
        self.assertIn("resource_grants", mt_doc)
        self.assertIn("FOREIGN KEY (organization_id, resource_type, resource_id) REFERENCES resource_registry", mt_doc)
        self.assertIn("cross_org_grants", mt_doc)
        self.assertIn("organization_placement", mt_doc)

    def test_authorization_algebra_v2_2_registry_and_typed_subjects(self):
        auth_doc = (self.knowledge_v2 / "authorization-model.md").read_text(encoding="utf-8")
        self.assertIn("authorize({ principal, membership, organization, action, resource, context })", auth_doc)
        self.assertIn("permission_registry_versions", auth_doc)
        self.assertIn("permission_definitions", auth_doc)
        self.assertIn("organization_permission_registries", auth_doc)
        self.assertIn("organization_id TEXT PRIMARY KEY", auth_doc)
        self.assertIn("role_permissions", auth_doc)
        self.assertIn("FOREIGN KEY (registry_version, permission_name) REFERENCES permission_definitions(registry_version, name)", auth_doc)
        self.assertIn("organization_service_principals", auth_doc)
        self.assertIn("authorization_subjects", auth_doc)
        self.assertIn("CHECK (", auth_doc)
        self.assertIn("subject_type = 'membership'", auth_doc)
        self.assertIn("subject_type = 'team'", auth_doc)
        self.assertIn("subject_type = 'service_principal'", auth_doc)
        self.assertIn("explicit_denies", auth_doc)
        self.assertIn("Service-to-Application Entitlement Mapping", auth_doc)
        self.assertIn("Always Entitled", auth_doc)
        self.assertIn("Single Active Registry per Authorization Decision", auth_doc)
        self.assertNotIn("role: string;", auth_doc)
        self.assertNotIn("customPermissions?: string[];", auth_doc)

    def test_session_security_v2_2_cas_and_host_cookie(self):
        session_doc = (self.knowledge_v2 / "session-security.md").read_text(encoding="utf-8")
        self.assertIn("account_sessions", session_doc)
        self.assertIn("refresh_token_families", session_doc)
        self.assertIn("refresh_tokens", session_doc)
        self.assertIn("principal_security_versions", session_doc)
        self.assertIn("token_hash = :presented_hash", session_doc)
        self.assertIn("affected_rows", session_doc)
        self.assertIn("Case A: Hash Mismatch or Nonexistent Token", session_doc)
        self.assertIn("Case B: Token Already Consumed or Revoked", session_doc)
        self.assertIn("Account-Level Replay Defense", session_doc)
        self.assertIn("__Host-Oryol-Refresh", session_doc)
        self.assertIn("Path=/", session_doc)
        self.assertNotIn("Path=/v1/auth", session_doc)
        self.assertIn("UNKNOWN_KEY_IDENTIFIER", session_doc)

    def test_audit_and_events_v2_2_privacy_overlays_and_legal_holds(self):
        audit_doc = (self.knowledge_v2 / "audit-and-events.md").read_text(encoding="utf-8")
        self.assertIn("outbox_events", audit_doc)
        self.assertIn("idx_outbox_eligibility", audit_doc)
        self.assertIn("lease_expires_at <= datetime('now')", audit_doc)
        self.assertIn("blocked_on_gap", audit_doc)
        self.assertIn("inbox_events", audit_doc)
        self.assertIn("audit_redactions", audit_doc)
        self.assertIn("audit_legal_holds", audit_doc)
        self.assertIn("placed_by_actor_metadata", audit_doc)
        self.assertIn("released_by_actor_metadata", audit_doc)
        self.assertIn("core.audit.legal_hold.create", audit_doc)
        self.assertIn("core.audit.legal_hold.release", audit_doc)
        self.assertIn("core.audit.legal_hold.read", audit_doc)
        self.assertIn("trg_audit_no_update", audit_doc)
        self.assertIn("trg_audit_no_delete", audit_doc)
        self.assertIn("Zero In-Place Updates / No Physical Purge in Phase 1", audit_doc)
        self.assertIn("Atomic Security Mutations", audit_doc)

    def test_data_lifecycle_v2_2_reconciled_with_audit_retention(self):
        lifecycle_doc = (self.knowledge_v2 / "data-lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("D1 Time Travel", lifecycle_doc)
        self.assertIn("Permanently Retained (No Physical Purge / Zero In-Place Updates)", lifecycle_doc)
        self.assertIn("soft_deleted", lifecycle_doc)

    def test_virel_domain_ownership_v2_2(self):
        ws_doc = (self.knowledge_v2 / "workspace-architecture.md").read_text(encoding="utf-8")
        self.assertIn("Virel", ws_doc)
        self.assertIn("Wallets", ws_doc)
        self.assertIn("invoices", ws_doc.lower())


class TestOryolFailClosedGovernance(unittest.TestCase):
    """Prove fail-closed policy enforcement under all negative conditions including missing .sera."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        # Configure repository as a registered Oryol repository via package.json project name
        (self.repo / "package.json").write_text(json.dumps({"name": "oryol-mail", "version": "0.1.0"}), encoding="utf-8")
        run_git(self.repo, "init", "-b", "main")
        run_git(self.repo, "config", "user.name", "Oryol Test")
        run_git(self.repo, "config", "user.email", "test@oryol.com")
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "index.ts").write_text("console.log('hello');", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_registered_oryol_repo_detected_without_sera_directory(self):
        """Oryol repository is identified even when .sera/ does not exist."""
        self.assertTrue(is_oryol_repository(self.repo), "Oryol repository must be identified by package.json/name")

    def test_registered_oryol_repo_without_sera_directory_fails_closed(self):
        """Missing .sera/ directory in registered Oryol repo must fail closed with SeraError."""
        self.assertFalse((self.repo / ".sera").exists())
        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("Oryol governance violation", str(ctx.exception))
        self.assertIn("missing", str(ctx.exception).lower())

    def test_registered_oryol_repo_without_config_json_fails_closed(self):
        """Missing .sera/config.json must fail closed rather than silently returning default config."""
        (self.repo / ".sera").mkdir(parents=True)
        (self.repo / ".sera" / "context.md").write_text("# Context\n", encoding="utf-8")
        (self.repo / ".sera" / "architecture.md").write_text("# Arch\n", encoding="utf-8")
        (self.repo / ".sera" / "review-rules.md").write_text("# Rules\n", encoding="utf-8")
        (self.repo / ".sera" / "verification.md").write_text("# Verif\n", encoding="utf-8")
        self.assertFalse((self.repo / ".sera" / "config.json").exists())

        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn(".sera/config.json' is missing", str(ctx.exception))

    def test_missing_architecture_policy_fails_closed(self):
        """Missing architecture.md must raise SeraError."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (sera_dir / "config.json").write_text(json.dumps({"schema_version": 1, "verification": ["npm test"]}), encoding="utf-8")
        (sera_dir / "context.md").write_text("# Context\n", encoding="utf-8")
        (sera_dir / "review-rules.md").write_text("# Rules\n", encoding="utf-8")
        (sera_dir / "verification.md").write_text("# Verif\n", encoding="utf-8")

        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("architecture.md' is missing", str(ctx.exception))

    def test_missing_context_policy_fails_closed(self):
        """Missing context.md must raise SeraError."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (sera_dir / "config.json").write_text(json.dumps({"schema_version": 1, "verification": ["npm test"]}), encoding="utf-8")
        (sera_dir / "architecture.md").write_text("# Arch\n", encoding="utf-8")
        (sera_dir / "review-rules.md").write_text("# Rules\n", encoding="utf-8")
        (sera_dir / "verification.md").write_text("# Verif\n", encoding="utf-8")

        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("context.md' is missing", str(ctx.exception))

    def test_missing_review_rules_policy_fails_closed(self):
        """Missing review-rules.md must raise SeraError."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (sera_dir / "config.json").write_text(json.dumps({"schema_version": 1, "verification": ["npm test"]}), encoding="utf-8")
        (sera_dir / "context.md").write_text("# Context\n", encoding="utf-8")
        (sera_dir / "architecture.md").write_text("# Arch\n", encoding="utf-8")
        (sera_dir / "verification.md").write_text("# Verif\n", encoding="utf-8")

        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("review-rules.md' is missing", str(ctx.exception))

    def test_missing_verification_policy_fails_closed(self):
        """Missing verification.md must raise SeraError."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (sera_dir / "config.json").write_text(json.dumps({"schema_version": 1, "verification": ["npm test"]}), encoding="utf-8")
        (sera_dir / "context.md").write_text("# Context\n", encoding="utf-8")
        (sera_dir / "architecture.md").write_text("# Arch\n", encoding="utf-8")
        (sera_dir / "review-rules.md").write_text("# Rules\n", encoding="utf-8")

        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("verification.md' is missing", str(ctx.exception))

    def test_empty_architecture_policy_fails_closed(self):
        """Empty architecture.md must raise SeraError."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (sera_dir / "config.json").write_text(json.dumps({"schema_version": 1, "verification": ["npm test"]}), encoding="utf-8")
        (sera_dir / "context.md").write_text("# Context\n", encoding="utf-8")
        (sera_dir / "architecture.md").write_text("   \n\n  ", encoding="utf-8")
        (sera_dir / "review-rules.md").write_text("# Rules\n", encoding="utf-8")
        (sera_dir / "verification.md").write_text("# Verif\n", encoding="utf-8")

        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("architecture.md' is empty", str(ctx.exception))

    def test_malformed_config_fails_closed(self):
        """Malformed JSON in config.json must raise SeraError."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (sera_dir / "config.json").write_text("{ invalid json }", encoding="utf-8")
        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("Malformed configuration file", str(ctx.exception))

    def test_valid_oryol_repository_passes_and_injects_packets(self):
        """Valid 5-file .sera configuration passes and injects architecture/context into build and review packets."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        config = {
            "schema_version": 1,
            "verification": ["npm run typecheck", "npm test"],
            "risk_policy": {
                "high_risk_terms": ["oryol", "auth"],
                "high_risk_paths": ["src/index.ts"],
            },
        }
        (sera_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (sera_dir / "context.md").write_text("# Context\n- Tech stack: TypeScript\n", encoding="utf-8")
        (sera_dir / "architecture.md").write_text("# Architecture Policy\n- Invariant: Compound tenant keys\n", encoding="utf-8")
        (sera_dir / "review-rules.md").write_text("# Review Rules\n- Checklist: 100% test pass\n", encoding="utf-8")
        (sera_dir / "verification.md").write_text("# Verification\n- npm test\n", encoding="utf-8")

        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "Valid repo setup")

        loaded = load_config(self.repo)
        self.assertEqual(loaded["verification"], ["npm run typecheck", "npm test"])

        task_dir = new_task(
            self.repo,
            name="Valid Task",
            objective="Test packet injection",
            allowed_files=["src/index.ts"],
            verification=["npm test"],
        )
        b_path, b_text = generate_packet(self.repo, task_dir, "build")
        self.assertIn("## Repository architecture policy", b_text)
        self.assertIn("Invariant: Compound tenant keys", b_text)
        self.assertIn("## Repository context", b_text)
        self.assertIn("Tech stack: TypeScript", b_text)

        (self.repo / "src" / "index.ts").write_text("console.log('modified');", encoding="utf-8")
        r_path, r_text = generate_packet(self.repo, task_dir, "review")
        self.assertIn("## Repository review policy", r_text)
        self.assertIn("Checklist: 100% test pass", r_text)
        self.assertIn("## Repository architecture policy", r_text)

    def test_builder_and_reviewer_packets_contain_exact_v2_2_provenance(self):
        """Verify that both builder and reviewer packets embed Architecture v2.2 and exact specification commit SHA."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        config = {
            "schema_version": 1,
            "verification": ["npm test"],
            "risk_policy": {
                "high_risk_terms": ["oryol"],
                "high_risk_paths": ["src/index.ts"],
            },
        }
        (sera_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
        (sera_dir / "context.md").write_text("# Context\n", encoding="utf-8")
        (sera_dir / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
        (sera_dir / "review-rules.md").write_text("# Rules\n", encoding="utf-8")
        (sera_dir / "verification.md").write_text("# Verification\n", encoding="utf-8")

        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "Provenance test repo")

        task_dir = new_task(
            self.repo,
            name="Provenance Task",
            objective="Test provenance embedding",
            allowed_files=["src/index.ts"],
            verification=["npm test"],
        )
        _, build_text = generate_packet(self.repo, task_dir, "build")
        self.assertIn(f"SERA governance version: `{SERA_GOVERNANCE_VERSION}`", build_text)
        self.assertIn(f"Architecture baseline version: `{ORYOL_ARCHITECTURE_BASELINE_VERSION}`", build_text)
        self.assertIn(f"Architecture specification commit: `{ORYOL_ARCHITECTURE_SPEC_SHA}`", build_text)

        (self.repo / "src" / "index.ts").write_text("console.log('reviewed');", encoding="utf-8")
        _, review_text = generate_packet(self.repo, task_dir, "review")
        self.assertIn(f"SERA governance version: `{SERA_GOVERNANCE_VERSION}`", review_text)
        self.assertIn(f"Architecture baseline version: `{ORYOL_ARCHITECTURE_BASELINE_VERSION}`", review_text)
        self.assertIn(f"Architecture specification commit: `{ORYOL_ARCHITECTURE_SPEC_SHA}`", review_text)


class TestOryolV23GovernanceAndSecurityInvariants(unittest.TestCase):
    """Verify that Proposed Architecture v2.3 satisfies all hardened security invariants, template integrity, tenant ownership, and migration contracts."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.knowledge_v23 = cls.root / "knowledge" / "oryol" / "v2.3"
        cls.adr1 = cls.knowledge_v23 / "adr" / "ADR-001-step8-security-policy.md"
        cls.adr2 = cls.knowledge_v23 / "adr" / "ADR-002-service-principal-rbac.md"
        cls.auth_model = cls.knowledge_v23 / "authorization-model.md"
        cls.multi_tenancy = cls.knowledge_v23 / "multi-tenancy.md"
        cls.identity_model = cls.knowledge_v23 / "identity-model.md"

    def test_non_system_role_cannot_have_template_key(self):
        """A: Non-system role + template_key is strictly prohibited by canonical schema text."""
        check_pattern = r"\(is_system_template\s*=\s*0\s+AND\s+template_key\s+IS\s+NULL\)"
        for doc in [self.adr2, self.auth_model, self.multi_tenancy]:
            content = doc.read_text(encoding="utf-8")
            self.assertRegex(content, check_pattern, f"{doc.name} must enforce template_key IS NULL when is_system_template = 0")

    def test_admin_owner_semantics_require_dual_signals(self):
        """B: Admin/Owner semantics require BOTH is_system_template = TRUE and template_key."""
        adr1_text = self.adr1.read_text(encoding="utf-8")
        auth_text = self.auth_model.read_text(encoding="utf-8")
        adr2_text = self.adr2.read_text(encoding="utf-8")

        # Step 8 MFA check in ADR-001
        self.assertIn("role.is_system_template === true AND role.template_key IN ('owner', 'admin')", adr1_text)
        # Step 7 ACL bypass in authorization-model.md
        self.assertIn("rd.is_system_template == true and rd.template_key IN ('owner', 'admin')", auth_text)
        # Privilege escalation ceiling in authorization-model.md
        self.assertIn("rd.is_system_template = TRUE AND rd.template_key = 'owner'", auth_text)
        # Privilege escalation ceiling in ADR-002
        self.assertIn("role.is_system_template = TRUE AND role.template_key = 'owner'", adr2_text)

    def test_template_key_uniqueness_per_organization(self):
        """C: template_key uniqueness per organization is specified via partial unique index."""
        uq_pattern = r"CREATE UNIQUE INDEX uq_role_definitions_org_template\s+ON role_definitions\(organization_id, template_key\)\s+WHERE template_key IS NOT NULL;"
        for doc in [self.adr2, self.auth_model, self.multi_tenancy]:
            content = doc.read_text(encoding="utf-8")
            self.assertRegex(content, uq_pattern, f"{doc.name} must specify uq_role_definitions_org_template partial index")

    def test_service_accounts_target_schema_contains_organization_ownership(self):
        """D: service_accounts target schema contains organization ownership (organization_id)."""
        for doc in [self.adr2, self.identity_model]:
            content = doc.read_text(encoding="utf-8")
            self.assertIn("organization_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT", content)
            self.assertIn("UNIQUE(organization_id, principal_id)", content)

    def test_organization_service_principals_compound_tenant_binding(self):
        """E: organization_service_principals has canonical compound tenant ownership binding."""
        fk_pattern = r"FOREIGN KEY\s*\(organization_id,\s*principal_id\)\s+REFERENCES service_accounts\(organization_id,\s*principal_id\)\s+ON DELETE CASCADE"
        for doc in [self.adr2, self.auth_model, self.multi_tenancy, self.identity_model]:
            content = doc.read_text(encoding="utf-8")
            self.assertRegex(content, fk_pattern, f"{doc.name} must enforce compound FK to service_accounts(organization_id, principal_id)")

    def test_migration_0005_contract_handles_existing_role_definitions(self):
        """F: Migration 0005 contract explicitly handles existing role_definitions upgrade and deterministic backfill."""
        adr2_text = self.adr2.read_text(encoding="utf-8")
        self.assertIn("## 7. Migration 0005 Upgrade Contract", adr2_text)
        self.assertIn("ALTER TABLE role_definitions ADD COLUMN template_key TEXT;", adr2_text)
        self.assertIn("WHEN is_system_template = 1 AND LOWER(TRIM(name)) = 'owner' THEN 'owner'", adr2_text)
        self.assertIn("ERR_MIGRATION_UNMAPPABLE_SYSTEM_TEMPLATE", adr2_text)
        self.assertIn("ERR_MIGRATION_DUPLICATE_SYSTEM_TEMPLATE", adr2_text)

    def test_migration_0005_contract_handles_existing_service_accounts(self):
        """G: Migration 0005 contract explicitly handles existing service_accounts upgrade."""
        adr2_text = self.adr2.read_text(encoding="utf-8")
        self.assertIn("ALTER TABLE service_accounts ADD COLUMN organization_id TEXT", adr2_text)
        self.assertIn("UPDATE service_accounts", adr2_text)
        self.assertIn("SELECT osp.organization_id", adr2_text)

    def test_ambiguous_service_account_ownership_fails_closed(self):
        """H: Ambiguous service-account ownership fails closed during Migration 0005 preflight."""
        adr2_text = self.adr2.read_text(encoding="utf-8")
        self.assertIn("ERR_MIGRATION_ORPHAN_SERVICE_ACCOUNT", adr2_text)
        self.assertIn("ERR_MIGRATION_AMBIGUOUS_SERVICE_ACCOUNT_OWNERSHIP", adr2_text)

    def test_internal_execution_deny_semantics_without_cidr_matching(self):
        """I: internal_execution with allow_internal_dispatch=false denies instead of performing CIDR matching."""
        adr1_text = self.adr1.read_text(encoding="utf-8")
        auth_text = self.auth_model.read_text(encoding="utf-8")
        self.assertIn("DENY(CONTEXT_INTERNAL_DISPATCH_DENIED)", adr1_text)
        self.assertIn("DENY(CONTEXT_INTERNAL_DISPATCH_DENIED)", auth_text)
        self.assertNotIn("must match configured internal CIDR", adr1_text)

    def test_policy_authorship_fks_use_single_column_set_null(self):
        """Verify organization_security_policies and IP allowlist use single-column membership FK with ON DELETE SET NULL."""
        for doc in [self.adr1, self.auth_model, self.multi_tenancy, self.adr2]:
            content = doc.read_text(encoding="utf-8")
            self.assertIn("updated_by_membership_id TEXT REFERENCES memberships(id) ON DELETE SET NULL", content, f"{doc.name} must use single-column FK for updated_by")
            self.assertIn("created_by_membership_id TEXT REFERENCES memberships(id) ON DELETE SET NULL", content, f"{doc.name} must use single-column FK for created_by")

    def test_invitations_compound_binding_and_migration(self):
        """Verify invitations table has compound FK to role_definitions and Migration 0005 defines its reconstruction."""
        for doc in [self.identity_model, self.multi_tenancy]:
            content = doc.read_text(encoding="utf-8")
            self.assertIn("FOREIGN KEY (organization_id, role_id) REFERENCES role_definitions(organization_id, id) ON DELETE RESTRICT", content)
            self.assertIn("FOREIGN KEY (organization_id, invited_by_membership_id) REFERENCES memberships(organization_id, id) ON DELETE CASCADE", content)
        adr2_text = self.adr2.read_text(encoding="utf-8")
        self.assertIn("### 7.4 Step 3: `invitations` Re-Binding & Compound Role Integrity", adr2_text)
        self.assertIn("ERR_MIGRATION_ORPHAN_INVITATION", adr2_text)
        self.assertIn("ERR_MIGRATION_CROSS_ORG_INVITATION_ROLE", adr2_text)


class TestOryolMailLiveRepositoryConfig(unittest.TestCase):
    """Validate that the live OryolMail repository's .sera directory satisfies fail-closed governance."""

    def test_oryolmail_live_sera_config(self):
        oryolmail_root = Path(r"c:\Users\lekan\Documents\oryolmail")
        if not (oryolmail_root / ".sera").is_dir():
            self.skipTest("OryolMail repository not found at expected path")

        self.assertTrue(is_oryol_repository(oryolmail_root))
        config = load_config(oryolmail_root)
        self.assertIsInstance(config["verification"], list)
        for cmd in config["verification"]:
            self.assertIsInstance(cmd, str)

        policies = load_repository_policies(oryolmail_root)
        self.assertIn("architecture", policies)
        self.assertIn("review_rules", policies)
        self.assertIn("context", policies)
        self.assertIn("verification", policies)


if __name__ == "__main__":
    unittest.main()

