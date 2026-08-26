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
    ORYOL_GOVERNANCE_PINNED_SHA,
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
    """Scan the entire canonical Architecture v2.2 corpus for prohibited stale terms and contradictions."""

    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.knowledge_v2 = cls.root / "knowledge" / "oryol" / "v2"

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
                "CANONICAL ARCHITECTURE BASELINE (v2.2)" in content or "CANDIDATE FOR FINAL IMPLEMENTATION REVIEW" in content,
                f"Document {doc_name} missing canonical v2.2 header"
            )

    def test_corpus_contradiction_scan_no_stale_patterns(self):
        """Scans all v2 files for prohibited stale patterns identified during GPT reviews."""
        canonical_files = list(self.knowledge_v2.glob("*.md"))
        self.assertGreaterEqual(len(canonical_files), 13)

        for file_path in canonical_files:
            text = file_path.read_text(encoding="utf-8")
            filename = file_path.name

            # 1. No "0ms revocation" or "0ms delay" claims
            self.assertNotIn(
                "0ms revocation",
                text.lower(),
                f"Prohibited '0ms revocation' claim found in {filename}"
            )
            self.assertNotIn(
                "0ms delay",
                text.lower(),
                f"Prohibited '0ms delay' claim found in {filename}"
            )

            # 2. No transport-level "exactly-once" delivery claims
            self.assertNotIn(
                "exactly-once delivery",
                text.lower(),
                f"Prohibited 'exactly-once delivery' claim found in {filename}"
            )
            self.assertNotIn(
                "guarantees exactly-once",
                text.lower(),
                f"Prohibited 'guarantees exactly-once' claim found in {filename}"
            )

            # 3. Principal taxonomy must never include 'external' as a top-level principal type
            if filename in ("workspace-architecture.md", "identity-model.md"):
                self.assertNotIn(
                    "Principal (User / Service / External)",
                    text,
                    f"Stale external principal taxonomy found in {filename}"
                )

            # 4. Search authorization must require live authorization
            if filename == "search-platform.md":
                self.assertTrue("live `authorize()`" in text or "live authorization" in text.lower(), f"Live authorization check required in {filename}")

            # 5. Core AI Gateway must not directly query product databases
            if filename in ("core-boundaries.md", "ai-platform.md", "workspace-architecture.md"):
                self.assertNotIn(
                    "Gateway directly queries",
                    text,
                    f"Direct product DB query pattern found in {filename}"
                )


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
        # architecture.md is missing!

        with self.assertRaises(SeraError) as ctx:
            load_config(self.repo)
        self.assertIn("architecture.md' is missing", str(ctx.exception))

    def test_empty_architecture_policy_fails_closed(self):
        """Empty architecture.md must raise SeraError."""
        sera_dir = self.repo / ".sera"
        sera_dir.mkdir(parents=True)
        (sera_dir / "config.json").write_text(json.dumps({"schema_version": 1, "verification": ["npm test"]}), encoding="utf-8")
        (sera_dir / "context.md").write_text("# Context\n", encoding="utf-8")
        (sera_dir / "architecture.md").write_text("   \n\n  ", encoding="utf-8")  # Empty!
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

        # Generate build packet
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
        self.assertIn("SERA governance version: `0.4.2`", b_text)
        self.assertIn("Architecture baseline version: `2.2`", b_text)

        # Generate review packet
        (self.repo / "src" / "index.ts").write_text("console.log('modified');", encoding="utf-8")
        r_path, r_text = generate_packet(self.repo, task_dir, "review")
        self.assertIn("## Repository review policy", r_text)
        self.assertIn("Checklist: 100% test pass", r_text)
        self.assertIn("## Repository architecture policy", r_text)


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
