"""Tests for Oryol Architecture v2.1 Knowledge Base and Fail-Closed Governance in SERA."""

import json
import os
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
    SeraError,
    generate_packet,
    initialize,
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


class TestOryolV2Governance(unittest.TestCase):
    """Verify that all Oryol v2.1 canonical architecture and governance documents exist and are structurally valid."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.knowledge_v2 = cls.root / "knowledge" / "oryol" / "v2"

    def test_v2_knowledge_directory_exists(self):
        self.assertTrue(self.knowledge_v2.is_dir(), "knowledge/oryol/v2 directory must exist")

    def test_all_canonical_v2_documents_exist(self):
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
                "CANONICAL ARCHITECTURE BASELINE" in content or "CANDIDATE FOR FINAL REVIEW" in content,
                f"Document {doc_name} missing canonical header"
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

    def test_identity_v2_1_contains_principal_model(self):
        identity_doc = (self.knowledge_v2 / "identity-model.md").read_text(encoding="utf-8")
        self.assertIn("Principal", identity_doc)
        self.assertIn("Human Principal", identity_doc)
        self.assertIn("Service Principal", identity_doc)
        self.assertIn("principals", identity_doc)
        self.assertIn("service_accounts", identity_doc)
        self.assertIn("identity_provider_bindings", identity_doc)
        self.assertIn("last-owner protection", identity_doc.lower())

    def test_multi_tenancy_v2_1_structural_isolation(self):
        mt_doc = (self.knowledge_v2 / "multi-tenancy.md").read_text(encoding="utf-8")
        self.assertIn("team_memberships", mt_doc)
        self.assertIn("organization_placement", mt_doc)
        self.assertIn("FOREIGN KEY (organization_id, team_id)", mt_doc)

    def test_authorization_algebra_v2_1(self):
        auth_doc = (self.knowledge_v2 / "authorization-model.md").read_text(encoding="utf-8")
        self.assertIn("authorize({", auth_doc)
        self.assertIn("Mandatory 8-Step Evaluation Algebra", auth_doc)
        self.assertIn("core.members.invite", auth_doc)
        self.assertIn("mail.messages.send", auth_doc)

    def test_session_security_v2_1_state_machine(self):
        session_doc = (self.knowledge_v2 / "session-security.md").read_text(encoding="utf-8")
        self.assertIn("Refresh Token Family", session_doc)
        self.assertIn("refresh_tokens", session_doc)
        self.assertIn("successor_generation", session_doc)
        self.assertIn("10 Minutes", session_doc)

    def test_audit_and_events_v2_1_defines_outbox_and_inbox(self):
        audit_doc = (self.knowledge_v2 / "audit-and-events.md").read_text(encoding="utf-8")
        self.assertIn("outbox_events", audit_doc)
        self.assertIn("inbox_events", audit_doc)
        self.assertIn("attempt_count", audit_doc)
        self.assertIn("idempotency_key", audit_doc)

    def test_data_lifecycle_v2_1_deletion_pipeline(self):
        lifecycle_doc = (self.knowledge_v2 / "data-lifecycle.md").read_text(encoding="utf-8")
        self.assertIn("D1 Time Travel", lifecycle_doc)
        self.assertIn("Physical Purge", lifecycle_doc)
        self.assertIn("soft_deleted", lifecycle_doc)


class TestOryolGovernanceIntegration(unittest.TestCase):
    """Prove fail-closed policy enforcement and deterministic packet generation for Oryol repositories."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        run_git(self.repo, "init", "-b", "main")
        run_git(self.repo, "config", "user.name", "Oryol Test")
        run_git(self.repo, "config", "user.email", "test@oryol.com")

        # Create standardized Oryol repository fixture
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (self.repo / "src").mkdir(parents=True)
        (self.repo / "src" / "index.ts").write_text("console.log('hello');", encoding="utf-8")

        config = {
            "schema_version": 1,
            "default_mode": "standard",
            "max_builder_attempts": 2,
            "max_file_bytes": 300000,
            "max_packet_chars": 48000,
            "exclude_dirs": [".git", ".sera", "node_modules"],
            "token_budgets": {"fast": 6000, "standard": 16000, "assured": 32000},
            "lanes": {
                "planner": {"provider": "openai", "model": "gpt-5", "enabled": True},
                "fast_builder": {"provider": "openai", "model": "gpt-5", "enabled": True},
                "deep_builder": {"provider": "anthropic", "model": "claude", "enabled": True},
                "independent_reviewer": {"provider": "anthropic", "model": "claude", "enabled": True},
                "release_gate": {"provider": "openai", "model": "gpt-5", "enabled": True},
            },
            "verification": ["npm run typecheck", "npm test"],
            "risk_policy": {
                "high_risk_terms": ["oryol", "auth", "session"],
                "high_risk_paths": ["src/index.ts"],
            },
        }
        (sera_dir / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
        (sera_dir / "architecture.md").write_text("# Oryol Architecture Policy\n- Invariant: Multi-tenant org isolation.\n", encoding="utf-8")
        (sera_dir / "context.md").write_text("# Oryol Context\n- Stack: TypeScript 5.8\n", encoding="utf-8")
        (sera_dir / "review-rules.md").write_text("# Oryol Review Rules\n- Gate: 100% verification pass.\n", encoding="utf-8")
        (sera_dir / "verification.md").write_text("# Verification\n- npm test\n", encoding="utf-8")

        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "Initial commit")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valid_oryol_config_loads_cleanly(self):
        config = load_config(self.repo)
        self.assertEqual(config["verification"], ["npm run typecheck", "npm test"])
        policies = load_repository_policies(self.repo)
        self.assertIn("architecture", policies)
        self.assertIn("context", policies)
        self.assertIn("review_rules", policies)
        self.assertIn("verification", policies)

    def test_architecture_policy_enters_builder_packet(self):
        task_dir = new_task(
            self.repo,
            name="Test Task",
            objective="Test builder packet policy injection",
            allowed_files=["src/index.ts"],
            verification=["npm test"],
        )
        packet_path, packet_text = generate_packet(self.repo, task_dir, "build")
        self.assertTrue(packet_path.exists())
        self.assertIn("## Repository architecture policy", packet_text)
        self.assertIn("Invariant: Multi-tenant org isolation.", packet_text)
        self.assertIn("## Repository context", packet_text)
        self.assertIn("Stack: TypeScript 5.8", packet_text)

    def test_review_policy_enters_review_packet(self):
        task_dir = new_task(
            self.repo,
            name="Review Task",
            objective="Test review packet policy injection",
            allowed_files=["src/index.ts"],
            verification=["npm test"],
        )
        (self.repo / "src" / "index.ts").write_text("console.log('modified');", encoding="utf-8")
        packet_path, packet_text = generate_packet(self.repo, task_dir, "review")
        self.assertTrue(packet_path.exists())
        self.assertIn("## Repository review policy", packet_text)
        self.assertIn("Gate: 100% verification pass.", packet_text)
        self.assertIn("## Repository architecture policy", packet_text)

    def test_missing_required_policy_fails_closed(self):
        (self.repo / ".sera" / "review-rules.md").unlink()
        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("review-rules.md", str(ctx.exception))
        self.assertIn("missing", str(ctx.exception).lower())

    def test_malformed_config_fails_closed(self):
        malformed = {"schema_version": 1, "verification": [{"name": "bad", "command": "fail"}]}
        (self.repo / ".sera" / "config.json").write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("verification must be a list of strings", str(ctx.exception))


class TestOryolMailLiveRepositoryConfig(unittest.TestCase):
    """Validate that the real OryolMail repository's .sera directory satisfies fail-closed governance."""

    def test_oryolmail_live_sera_config(self):
        oryolmail_root = Path(r"c:\Users\lekan\Documents\oryolmail")
        if not (oryolmail_root / ".sera").is_dir():
            self.skipTest("OryolMail repository not found at expected path")

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
