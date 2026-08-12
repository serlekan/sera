"""Acceptance is bound to the exact reviewed repository identity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sera.core import (
    SEAL_FINGERPRINT_MISMATCH,
    SEAL_HEAD_MISMATCH,
    SEAL_MISSING_HEAD_IDENTITY,
    SEAL_REVIEW_MISMATCH,
    SEAL_SCHEMA_INCONSISTENT,
    SEAL_SCHEMA_UNSUPPORTED,
    SEAL_SCHEMA_VERSION,
    build_repo_map,
    check_task,
    create_seal,
    git_head_identity,
    initialize,
    new_task,
    record_review,
    review_ledger_fingerprint,
    task_fingerprint,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class SealRepository:
    """A sealed single-file task on a one-commit repository."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
        (self.root / "README.md").write_text("readme\n", encoding="utf-8")
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def seal_task(self, owned: str = "src/app.py") -> tuple[Path, dict]:
        task_dir = new_task(
            self.root,
            "adjust answer",
            "adjust the returned number",
            "standard",
            "medium",
            [owned],
            [],
            [],
            1,
            "implementation",
        )
        path = self.root / owned
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def answer():\n    return 42\n", encoding="utf-8")
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "reviewer", "correct")
        return task_dir, create_seal(self.root, task_dir)


class SealHeadBindingTests(SealRepository, unittest.TestCase):
    def test_seal_records_exact_head_and_tree(self) -> None:
        task_dir, seal = self.seal_task()
        identity = git_head_identity(self.root)
        self.assertEqual(seal["schema_version"], SEAL_SCHEMA_VERSION)
        self.assertIn("sera_version", seal)
        self.assertEqual(seal["repository_identity"]["head_sha"], identity["head_sha"])
        self.assertEqual(seal["repository_identity"]["head_tree_sha"], identity["head_tree_sha"])
        self.assertEqual(len(seal["repository_identity"]["head_sha"]), 40)

    def test_unchanged_head_keeps_the_seal_current(self) -> None:
        task_dir, _ = self.seal_task()
        result = check_task(self.root, task_dir)
        self.assertFalse(result["seal_stale"])
        self.assertEqual(result["seal_status"], "current")
        self.assertEqual(result["seal_stale_reasons"], [])

    def test_new_commit_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        git(self.root, "add", "src/app.py")
        git(self.root, "commit", "-m", "land the change")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_HEAD_MISMATCH, result["seal_stale_reasons"])

    def test_identical_tree_on_a_different_commit_still_invalidates(self) -> None:
        # An empty commit leaves the tree and the working-tree delta untouched,
        # so only exact-HEAD binding can catch it.
        task_dir, seal = self.seal_task()
        before = check_task(self.root, task_dir)
        git(self.root, "commit", "--allow-empty", "-m", "no-op commit")
        after = check_task(self.root, task_dir)
        self.assertEqual(before["fingerprint"], after["fingerprint"])
        self.assertEqual(
            seal["repository_identity"]["head_tree_sha"], after["head_identity"]["head_tree_sha"]
        )
        self.assertNotEqual(seal["repository_identity"]["head_sha"], after["head_identity"]["head_sha"])
        self.assertTrue(after["seal_stale"])
        self.assertEqual(after["seal_status"], "head_mismatch")
        self.assertEqual(after["seal_stale_reasons"], [SEAL_HEAD_MISMATCH])

    def test_checking_out_a_different_head_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        git(self.root, "checkout", "-b", "other")
        git(self.root, "commit", "--allow-empty", "-m", "sibling")
        git(self.root, "checkout", "main")
        self.assertFalse(check_task(self.root, task_dir)["seal_stale"])
        git(self.root, "checkout", "other")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertEqual(result["seal_status"], "head_mismatch")

    def test_reset_invalidates_the_seal(self) -> None:
        git(self.root, "commit", "--allow-empty", "-m", "second")
        build_repo_map(self.root)
        task_dir, _ = self.seal_task()
        self.assertFalse(check_task(self.root, task_dir)["seal_stale"])
        git(self.root, "reset", "--soft", "HEAD~1")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_HEAD_MISMATCH, result["seal_stale_reasons"])

    def test_staged_change_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        git(self.root, "add", "src/app.py")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_FINGERPRINT_MISMATCH, result["seal_stale_reasons"])

    def test_unstaged_change_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        (self.root / "src" / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_FINGERPRINT_MISMATCH, result["seal_stale_reasons"])

    def test_relevant_untracked_change_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task(owned="src/created.py")
        self.assertFalse(check_task(self.root, task_dir)["seal_stale"])
        (self.root / "src" / "created.py").write_text("def answer():\n    return 44\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_FINGERPRINT_MISMATCH, result["seal_stale_reasons"])

    def test_dirty_worktree_baseline_remains_sealable(self) -> None:
        (self.root / "README.md").write_text("pre-existing user edit\n", encoding="utf-8")
        task_dir, seal = self.seal_task()
        result = check_task(self.root, task_dir)
        self.assertNotIn("README.md", result["out_of_scope"])
        self.assertEqual(result["changed_files"], ["src/app.py"])
        self.assertFalse(result["seal_stale"])
        self.assertEqual(seal["changed_files"], ["src/app.py"])

    def test_040_seal_without_head_identity_fails_closed(self) -> None:
        task_dir, seal = self.seal_task()
        legacy = {
            "schema_version": 1,
            "task_id": seal["task_id"],
            "sealed_at": seal["sealed_at"],
            "fingerprint": seal["fingerprint"],
            "evidence_records": seal["evidence_records"],
            "review_stages": seal["review_stages"],
            "changed_files": seal["changed_files"],
        }
        (task_dir / "seal.json").write_text(json.dumps(legacy, indent=2), encoding="utf-8")
        result = check_task(self.root, task_dir)
        # The fingerprint still matches; only the missing repository identity
        # makes this seal unusable for exact-head acceptance.
        self.assertEqual(result["seal"]["fingerprint"], result["fingerprint"])
        self.assertTrue(result["seal_stale"])
        self.assertEqual(result["seal_status"], "legacy_unbound")
        self.assertEqual(result["seal_stale_reasons"], [SEAL_MISSING_HEAD_IDENTITY])
        self.assertIn("0.4.0", result["next_action"])

    def test_resealing_upgrades_a_legacy_seal(self) -> None:
        task_dir, seal = self.seal_task()
        legacy = dict(seal)
        legacy.pop("repository_identity")
        legacy["schema_version"] = 1
        (task_dir / "seal.json").write_text(json.dumps(legacy, indent=2), encoding="utf-8")
        self.assertTrue(check_task(self.root, task_dir)["seal_stale"])
        create_seal(self.root, task_dir)
        self.assertFalse(check_task(self.root, task_dir)["seal_stale"])


class SealReviewBindingTests(SealRepository, unittest.TestCase):
    """Reproduces the acceptance defect: editing the accepted reviewer or
    rationale left the seal reporting `current`."""

    def rewrite_reviews(self, task_dir: Path, mutate) -> None:
        path = task_dir / "reviews.jsonl"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        mutate(records)
        path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8"
        )

    def test_seal_records_the_review_ledger_fingerprint(self) -> None:
        task_dir, seal = self.seal_task()
        self.assertEqual(seal["review_ledger_fingerprint"], review_ledger_fingerprint(task_dir))
        self.assertEqual(len(seal["review_ledger_fingerprint"]), 64)

    def test_changing_the_reviewer_identity_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        before = check_task(self.root, task_dir)
        self.rewrite_reviews(task_dir, lambda records: records[0].__setitem__("reviewer", "someone-else"))
        after = check_task(self.root, task_dir)
        # The task/evidence/delta fingerprint is untouched; only review binding catches this.
        self.assertEqual(before["fingerprint"], after["fingerprint"])
        self.assertTrue(after["seal_stale"])
        self.assertEqual(after["seal_status"], "review_mismatch")
        self.assertEqual(after["seal_stale_reasons"], [SEAL_REVIEW_MISMATCH])

    def test_changing_the_rationale_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        self.rewrite_reviews(task_dir, lambda records: records[0].__setitem__("reason", "rewritten rationale"))
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_REVIEW_MISMATCH, result["seal_stale_reasons"])

    def test_changing_the_verdict_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        self.rewrite_reviews(task_dir, lambda records: records[0].__setitem__("verdict", "rethink"))
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_REVIEW_MISMATCH, result["seal_stale_reasons"])

    def test_deleting_a_required_review_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        (task_dir / "reviews.jsonl").write_text("", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_REVIEW_MISMATCH, result["seal_stale_reasons"])
        self.assertFalse(result["ok"])
        self.assertIn("independent", result["missing_reviews"])

    def test_appending_a_review_record_invalidates_the_seal(self) -> None:
        task_dir, _ = self.seal_task()
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "second-reviewer", "also fine", "supplementary")
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_REVIEW_MISMATCH, result["seal_stale_reasons"])

    def test_gate_review_mutation_invalidates_the_seal(self) -> None:
        task_dir = new_task(
            self.root, "gated change", "adjust the returned number", "assured", "high",
            ["src/app.py"], [], [], 1, "implementation",
        )
        (self.root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "independent-peer", "correct", "independent")
        record_review(task_dir, fingerprint, "ship", "release-gate", "acceptable", "gate")
        create_seal(self.root, task_dir)
        self.assertFalse(check_task(self.root, task_dir)["seal_stale"])

        self.rewrite_reviews(
            task_dir,
            lambda records: records[-1].__setitem__("reviewer", "not-the-gate"),
        )
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertIn(SEAL_REVIEW_MISMATCH, result["seal_stale_reasons"])

    def test_review_ledger_fingerprint_is_deterministic(self) -> None:
        task_dir, _ = self.seal_task()
        self.assertEqual(review_ledger_fingerprint(task_dir), review_ledger_fingerprint(task_dir))


class SealSchemaEnforcementTests(SealRepository, unittest.TestCase):
    """Reproduces the schema defect: a v2 seal relabelled `schema_version: 1`
    still validated as current."""

    def write_seal(self, task_dir: Path, seal: dict) -> None:
        (task_dir / "seal.json").write_text(json.dumps(seal, indent=2), encoding="utf-8")

    def test_valid_v2_seal_is_current(self) -> None:
        task_dir, seal = self.seal_task()
        self.assertEqual(seal["schema_version"], SEAL_SCHEMA_VERSION)
        self.assertEqual(check_task(self.root, task_dir)["seal_status"], "current")

    def test_v2_record_relabelled_as_v1_fails_closed(self) -> None:
        task_dir, seal = self.seal_task()
        tampered = dict(seal)
        tampered["schema_version"] = 1
        self.write_seal(task_dir, tampered)
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertEqual(result["seal_status"], "schema_inconsistent")
        self.assertEqual(result["seal_stale_reasons"], [SEAL_SCHEMA_INCONSISTENT])

    def test_unknown_schema_version_fails_closed(self) -> None:
        task_dir, seal = self.seal_task()
        tampered = dict(seal)
        tampered["schema_version"] = 999
        self.write_seal(task_dir, tampered)
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertEqual(result["seal_status"], "schema_unsupported")
        self.assertEqual(result["seal_stale_reasons"], [SEAL_SCHEMA_UNSUPPORTED])

    def test_missing_schema_version_fails_closed(self) -> None:
        task_dir, seal = self.seal_task()
        tampered = dict(seal)
        tampered.pop("schema_version")
        self.write_seal(task_dir, tampered)
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertEqual(result["seal_status"], "schema_unsupported")

    def test_v2_seal_missing_repository_identity_fails_closed(self) -> None:
        task_dir, seal = self.seal_task()
        tampered = dict(seal)
        tampered.pop("repository_identity")
        self.write_seal(task_dir, tampered)
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertEqual(result["seal_status"], "schema_inconsistent")

    def test_v2_seal_missing_review_binding_fails_closed(self) -> None:
        task_dir, seal = self.seal_task()
        tampered = dict(seal)
        tampered.pop("review_ledger_fingerprint")
        self.write_seal(task_dir, tampered)
        result = check_task(self.root, task_dir)
        self.assertTrue(result["seal_stale"])
        self.assertEqual(result["seal_status"], "schema_inconsistent")

    def test_genuine_legacy_seal_still_reports_legacy_unbound(self) -> None:
        task_dir, seal = self.seal_task()
        legacy = {
            "schema_version": 1,
            "task_id": seal["task_id"],
            "sealed_at": seal["sealed_at"],
            "fingerprint": seal["fingerprint"],
            "evidence_records": seal["evidence_records"],
            "review_stages": seal["review_stages"],
            "changed_files": seal["changed_files"],
        }
        self.write_seal(task_dir, legacy)
        result = check_task(self.root, task_dir)
        self.assertEqual(result["seal_status"], "legacy_unbound")
        self.assertEqual(result["seal_stale_reasons"], [SEAL_MISSING_HEAD_IDENTITY])


class RequireSealCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, "-m", "sera", *args],
            cwd=self.root,
            env=self.env,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, expected, msg=result.stderr + result.stdout)
        return result

    def test_require_seal_fails_after_head_moves(self) -> None:
        self.run_cli("init")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.run_cli("map")
        self.run_cli(
            "task", "new", "change return",
            "--objective", "Return one",
            "--mode", "fast", "--risk", "low", "--uncertainty", "0",
            "--file", "app.py",
        )
        (self.root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        self.run_cli("review", "--verdict", "ship", "--reviewer", "peer", "--reason", "correct")
        sealed = self.run_cli("seal")
        self.assertIn("Bound to HEAD", sealed.stdout)
        self.run_cli("check", "--require-seal")

        git(self.root, "commit", "--allow-empty", "-m", "head moves")
        failed = self.run_cli("check", "--require-seal", "--json", expected=2)
        payload = json.loads(failed.stdout)
        self.assertTrue(payload["seal_required_failure"])
        self.assertIn(SEAL_HEAD_MISMATCH, payload["seal_required_failure_reasons"])


if __name__ == "__main__":
    unittest.main()
