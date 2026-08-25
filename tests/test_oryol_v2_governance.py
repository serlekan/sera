"""Tests for Oryol Architecture v2 Knowledge Base and Governance in SERA."""

import unittest
from pathlib import Path


class TestOryolV2Governance(unittest.TestCase):
    """Verify that all Oryol v2 canonical architecture and governance documents exist and are structurally valid."""

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
        ]
        for doc_name in expected_docs:
            doc_path = self.knowledge_v2 / doc_name
            self.assertTrue(doc_path.is_file(), f"Missing canonical v2 document: {doc_name}")
            content = doc_path.read_text(encoding="utf-8")
            self.assertGreater(len(content), 100, f"Document {doc_name} is unexpectedly small")
            self.assertIn("CANONICAL ARCHITECTURE BASELINE (v2)", content, f"Document {doc_name} missing canonical header")

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

    def test_identity_v2_contains_principal_model(self):
        identity_doc = (self.knowledge_v2 / "identity-model.md").read_text(encoding="utf-8")
        self.assertIn("Principal", identity_doc)
        self.assertIn("principals", identity_doc)
        self.assertIn("service_accounts", identity_doc)
        self.assertIn("external_identities", identity_doc)

    def test_session_security_v2_clarifies_kv_and_d1_roles(self):
        session_doc = (self.knowledge_v2 / "session-security.md").read_text(encoding="utf-8")
        self.assertIn("authoritative store", session_doc.lower())
        self.assertIn("Cloudflare D1", session_doc)
        self.assertIn("Cloudflare KV", session_doc)

    def test_audit_and_events_v2_defines_outbox(self):
        audit_doc = (self.knowledge_v2 / "audit-and-events.md").read_text(encoding="utf-8")
        self.assertIn("Transactional Outbox Pattern", audit_doc)
        self.assertIn("outbox_events", audit_doc)


if __name__ == "__main__":
    unittest.main()
