"""Review evidence covers what a task committed, not only what is still dirty.

Reproduces the 0.4.1 coverage defect: once implementation was committed and the
worktree went clean, review packets reported "No task-relative changes to
review" for a task that had produced a substantial commit.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet, next_action
from sera.core import (
    build_repo_map,
    check_task,
    initialize,
    load_task,
    new_task,
    packet_state,
    task_baseline_identity,
    task_changed_files,
    task_committed_range,
    task_fingerprint,
    task_review_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class CommittedReviewRepository:
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        for name in ("alpha", "beta"):
            (self.root / "src" / f"{name}.py").write_text(f"{name.upper()} = 0\n" * 20, encoding="utf-8")
        (self.root / "src" / "unowned.py").write_text("UNOWNED = 0\n", encoding="utf-8")
        (self.root / "README.md").write_text("readme\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_task(self, owned: list[str]) -> Path:
        return new_task(
            self.root, "committed work", "adjust the marker values", "standard", "medium",
            owned, [], [], 1, "implementation",
        )

    def coverage(self, task_dir: Path) -> dict:
        return task_review_coverage(self.root, load_task(task_dir), 60_000)


class CommittedChangeCoverageTests(CommittedReviewRepository, unittest.TestCase):
    def test_task_records_its_baseline_repository_identity(self) -> None:
        head_before = git(self.root, "rev-parse", "HEAD").strip()
        task_dir = self.make_task(["src/alpha.py"])
        baseline = task_baseline_identity(load_task(task_dir))
        self.assertEqual(baseline["head_sha"], head_before)
        self.assertEqual(baseline["head_tree_sha"], git(self.root, "rev-parse", "HEAD^{tree}").strip())

    # --- D11: a committed change on a clean worktree still reaches review ---
    def test_committed_change_reaches_review_with_a_clean_worktree(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\nCOMMITTED_MARKER = True\n" * 20, encoding="utf-8")
        git(self.root, "add", "src/alpha.py")
        git(self.root, "commit", "-m", "land alpha")
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["ok"])
        self.assertTrue(coverage["coverage_complete"])
        self.assertEqual(coverage["files"], ["src/alpha.py"])
        self.assertIn("COMMITTED_MARKER", coverage["text"])
        self.assertIn("location: committed", coverage["text"])
        self.assertNotIn("No task-relative changes to review.", coverage["text"])

        build_packet(self.root, task_dir, "build")
        _, packet = build_packet(self.root, task_dir, "review")
        self.assertIn("COMMITTED_MARKER", packet)
        self.assertEqual(next_action(self.root, task_dir)["state"], "dispatch_review")

    # --- D12: every committed file is represented ---------------------------
    def test_all_committed_files_are_represented(self) -> None:
        task_dir = self.make_task(["src/alpha.py", "src/beta.py"])
        for name in ("alpha", "beta"):
            (self.root / "src" / f"{name}.py").write_text(
                f"{name.upper()} = 1\nMARK_{name.upper()}_HERE = True\n" * 20, encoding="utf-8"
            )
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "land both")
        coverage = self.coverage(task_dir)
        self.assertEqual(coverage["files"], ["src/alpha.py", "src/beta.py"])
        self.assertIn("MARK_ALPHA_HERE", coverage["text"])
        self.assertIn("MARK_BETA_HERE", coverage["text"])

    def test_committed_and_working_changes_share_one_block_per_file(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\nCOMMITTED_MARKER = True\n", encoding="utf-8")
        git(self.root, "add", "src/alpha.py")
        git(self.root, "commit", "-m", "land alpha")
        (self.root / "src" / "alpha.py").write_text(
            "ALPHA = 1\nCOMMITTED_MARKER = True\nWORKING_MARKER = True\n", encoding="utf-8"
        )
        coverage = self.coverage(task_dir)
        # One file is exactly one canonical review block, carrying both sources.
        self.assertEqual(coverage["text"].count("### `src/alpha.py`"), 1)
        self.assertIn("location: committed+unstaged", coverage["text"])
        self.assertIn("COMMITTED_MARKER", coverage["text"])
        self.assertIn("WORKING_MARKER", coverage["text"])

    # --- D13: committed out-of-scope work cannot hide behind a clean tree ---
    def test_committed_out_of_scope_file_is_a_scope_violation(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        build_packet(self.root, task_dir, "build")
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        (self.root / "src" / "unowned.py").write_text("UNOWNED = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "land both, one unowned")
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

        result = check_task(self.root, task_dir)
        self.assertIn("src/unowned.py", result["changed_files"])
        self.assertEqual(result["out_of_scope"], ["src/unowned.py"])
        self.assertFalse(result["ok"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "resolve_scope")

    # --- D14: committed renames --------------------------------------------
    def test_committed_rename_is_represented(self) -> None:
        task_dir = self.make_task(["src/alpha.py", "src/renamed.py"])
        git(self.root, "mv", "src/alpha.py", "src/renamed.py")
        git(self.root, "commit", "-m", "rename alpha")
        changed = task_changed_files(self.root, load_task(task_dir))
        self.assertIn("src/renamed.py", changed)
        self.assertIn("src/alpha.py", changed)
        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["ok"])
        self.assertIn("src/renamed.py", coverage["text"])

    # --- D15: committed binary changes --------------------------------------
    def test_committed_binary_change_is_represented_safely(self) -> None:
        (self.root / "blob.bin").write_bytes(bytes(range(256)) * 4)
        git(self.root, "add", "blob.bin")
        git(self.root, "commit", "-m", "add binary")
        build_repo_map(self.root)
        task_dir = self.make_task(["blob.bin"])
        (self.root / "blob.bin").write_bytes(bytes(range(255, -1, -1)) * 4)
        git(self.root, "add", "blob.bin")
        git(self.root, "commit", "-m", "rotate binary")
        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["ok"])
        self.assertIn("blob.bin", coverage["text"])
        self.assertIn("binary/non-text", coverage["text"])

    def test_change_fingerprint_moves_with_the_committed_range(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        before = self.coverage(task_dir)["change_fingerprint"]
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        git(self.root, "add", "src/alpha.py")
        git(self.root, "commit", "-m", "land alpha")
        after = self.coverage(task_dir)["change_fingerprint"]
        self.assertNotEqual(before, after)
        # An empty commit does not change the represented change set...
        self.assertEqual(after, self.coverage(task_dir)["change_fingerprint"])

    def test_committing_the_implementation_does_not_stale_the_build_packet(self) -> None:
        # Repository identity is bound where it is semantically required. A
        # builder committing its own work must not invalidate its own handoff.
        task_dir = self.make_task(["src/alpha.py"])
        build_packet(self.root, task_dir, "build")
        self.assertTrue(packet_state(self.root, task_dir, "build", load_task(task_dir))["current"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        git(self.root, "add", "src/alpha.py")
        git(self.root, "commit", "-m", "land alpha")
        self.assertTrue(packet_state(self.root, task_dir, "build", load_task(task_dir))["current"])

    def test_unreachable_baseline_fails_closed(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        task = load_task(task_dir)
        task["baseline_repository_identity"] = {"head_sha": "0" * 40, "head_tree_sha": "0" * 40}
        resolved = task_committed_range(self.root, task)
        self.assertFalse(resolved["ok"])
        self.assertEqual(resolved["reason"], "review_baseline_unreachable")


class DirtyBaselineWithCommitsTests(CommittedReviewRepository, unittest.TestCase):
    """Committed coverage must not regress dirty-worktree baseline safety."""

    # --- H28: an untouched pre-existing edit is not task scope --------------
    def test_pre_existing_dirty_file_left_alone_is_not_a_task_change(self) -> None:
        (self.root / "README.md").write_text("pre-existing user edit\n", encoding="utf-8")
        task_dir = self.make_task(["src/alpha.py"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["src/alpha.py"])
        self.assertEqual(result["out_of_scope"], [])

    # --- H29: touching it again does make it task scope ---------------------
    def test_pre_existing_dirty_file_modified_again_is_detected(self) -> None:
        (self.root / "README.md").write_text("pre-existing user edit\n", encoding="utf-8")
        task_dir = self.make_task(["src/alpha.py"])
        (self.root / "README.md").write_text("changed again by the task\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertIn("README.md", result["changed_files"])
        self.assertIn("README.md", result["out_of_scope"])

    # --- H30: dirty baseline plus a committed owned change ------------------
    def test_dirty_baseline_and_committed_owned_change(self) -> None:
        (self.root / "README.md").write_text("pre-existing user edit\n", encoding="utf-8")
        task_dir = self.make_task(["src/alpha.py"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\nBOTH_MARKER = True\n", encoding="utf-8")
        git(self.root, "add", "src/alpha.py")
        git(self.root, "commit", "-m", "land alpha, leave README dirty")

        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["src/alpha.py"])
        self.assertEqual(result["out_of_scope"], [])
        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["coverage_complete"])
        self.assertIn("BOTH_MARKER", coverage["text"])
        self.assertNotIn("README.md", coverage["text"])

    def test_dirty_baseline_survives_a_full_review_and_seal_cycle(self) -> None:
        (self.root / "README.md").write_text("pre-existing user edit\n", encoding="utf-8")
        task_dir = self.make_task(["src/alpha.py"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\nCYCLE_MARKER = True\n", encoding="utf-8")
        git(self.root, "add", "src/alpha.py")
        git(self.root, "commit", "-m", "land alpha")
        build_packet(self.root, task_dir, "build")
        _, packet = build_packet(self.root, task_dir, "review")
        self.assertIn("CYCLE_MARKER", packet)
        self.assertTrue(
            packet_state(
                self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
            )["current"]
        )


if __name__ == "__main__":
    unittest.main()
