"""Repository identity holds object IDs or `unborn`, never a symbolic revision.

Reproduces the canonical-review blocker: in a repository with no commits,
`git rev-parse HEAD` exits non-zero but still prints the literal string `HEAD`.
Trusting stdout stored `head_sha: "HEAD"` and `head_tree_sha: "HEAD^{tree}"` as
a task's baseline. After the first commit those expressions re-resolved to the
new commit, collapsing the baseline→HEAD range to nothing and losing every
committed change from review.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet
from sera.core import (
    EMPTY_TREE_SHA,
    UNBORN_HEAD,
    _rev_parse_verified,
    build_repo_map,
    check_task,
    git_head_identity,
    initialize,
    load_task,
    new_task,
    save_task,
    task_baseline_identity,
    task_committed_range,
    task_review_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


SYMBOLIC = {"HEAD", "HEAD^{tree}", "HEAD^{commit}"}


class UnbornIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_git_prints_a_symbolic_name_on_an_unborn_head(self) -> None:
        # The precondition the old implementation trusted. Documented here so a
        # future refactor cannot quietly reintroduce reliance on stdout.
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "HEAD")

    def test_unborn_identity_is_the_explicit_sentinel(self) -> None:
        identity = git_head_identity(self.root)
        self.assertEqual(identity, {"head_sha": UNBORN_HEAD, "head_tree_sha": UNBORN_HEAD})
        self.assertNotIn(identity["head_sha"], SYMBOLIC)
        self.assertNotIn(identity["head_tree_sha"], SYMBOLIC)

    def test_rev_parse_verified_reports_failure_rather_than_stdout(self) -> None:
        self.assertIsNone(_rev_parse_verified(self.root, "HEAD^{commit}"))
        self.assertIsNone(_rev_parse_verified(self.root, "HEAD^{tree}"))
        self.assertIsNone(_rev_parse_verified(self.root, "refs/heads/nope"))

    def test_task_baseline_stores_the_sentinel_not_a_revision(self) -> None:
        (self.root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        build_repo_map(self.root)
        task_dir = new_task(
            self.root, "unborn", "first commit", "standard", "medium",
            ["app.py", ".gitignore"], [], [], 1, "implementation",
        )
        baseline = task_baseline_identity(load_task(task_dir))
        self.assertEqual(baseline, {"head_sha": UNBORN_HEAD, "head_tree_sha": UNBORN_HEAD})

    def test_existing_repository_identity_is_immutable_object_ids(self) -> None:
        (self.root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        identity = git_head_identity(self.root)
        self.assertEqual(identity["head_sha"], git(self.root, "rev-parse", "HEAD").strip())
        self.assertEqual(identity["head_tree_sha"], git(self.root, "rev-parse", "HEAD^{tree}").strip())
        self.assertEqual(len(identity["head_sha"]), 40)
        self.assertEqual(len(identity["head_tree_sha"]), 40)


class UnbornBaselineCoverageTests(unittest.TestCase):
    """An unborn baseline must still yield reviewable first-commit coverage."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        build_repo_map(self.root)
        # Ownership covers everything the task will commit: with an unborn
        # baseline nothing pre-exists in history, so the whole first commit is
        # task-produced work.
        self.task_dir = new_task(
            self.root, "unborn", "make the first commit", "standard", "medium",
            ["app.py", ".gitignore"], [], [], 1, "implementation",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def coverage(self) -> dict:
        return task_review_coverage(self.root, load_task(self.task_dir), 48_000)

    def test_first_commit_after_an_unborn_baseline_is_reviewable(self) -> None:
        (self.root / "app.py").write_text("VALUE = 1\nFIRST_COMMIT_MARKER = True\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "first commit")
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

        resolved = task_committed_range(self.root, load_task(self.task_dir))
        self.assertTrue(resolved["ok"])
        self.assertEqual(resolved["range"][0], EMPTY_TREE_SHA)
        self.assertEqual(resolved["range"][1], git(self.root, "rev-parse", "HEAD").strip())

        coverage = self.coverage()
        self.assertTrue(coverage["coverage_complete"], msg=coverage["coverage_reason"])
        self.assertIn("app.py", coverage["files"])
        self.assertIn("FIRST_COMMIT_MARKER", coverage["text"])
        self.assertNotIn("No task-relative changes to review.", coverage["text"])

        build_packet(self.root, self.task_dir, "build")
        _, packet = build_packet(self.root, self.task_dir, "review")
        self.assertIn("FIRST_COMMIT_MARKER", packet)
        self.assertIn("Change coverage: complete", packet)
        self.assertNotIn("No task-relative changes to review.", packet)

    def test_multiple_commits_represent_the_net_change_from_the_unborn_baseline(self) -> None:
        (self.root / "app.py").write_text("VALUE = 1\nFIRST_COMMIT_MARKER = True\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "first commit")
        (self.root / "second.py").write_text("SECOND_COMMIT_MARKER = True\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "second commit")

        # Ownership follows the work; the point of the test is that coverage
        # spans both commits rather than collapsing to the most recent one.
        task = load_task(self.task_dir)
        task["allowed_files"] = ["app.py", ".gitignore", "second.py"]
        save_task(self.task_dir, task)

        coverage = self.coverage()
        self.assertTrue(coverage["coverage_complete"], msg=coverage["coverage_reason"])
        self.assertEqual(sorted(coverage["files"]), [".gitignore", "app.py", "second.py"])
        self.assertIn("FIRST_COMMIT_MARKER", coverage["text"])
        self.assertIn("SECOND_COMMIT_MARKER", coverage["text"])
        self.assertEqual(check_task(self.root, self.task_dir)["out_of_scope"], [])

    def test_unborn_baseline_before_any_commit_reports_no_committed_range(self) -> None:
        resolved = task_committed_range(self.root, load_task(self.task_dir))
        self.assertTrue(resolved["ok"])
        self.assertIsNone(resolved["range"])
        self.assertEqual(resolved["head"], {"head_sha": UNBORN_HEAD, "head_tree_sha": UNBORN_HEAD})


if __name__ == "__main__":
    unittest.main()
