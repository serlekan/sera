"""Tracked SERA policy is repository content; SERA runtime state is not.

Reproduces the 0.4.1 exclusion defect: excluding all of `.sera/**` hid a
project's own reviewed policy — `.sera/config.json`, `.sera/POLICY.md` — from
ownership, change detection, scope checking, and review evidence.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet
from sera.core import (
    build_repo_map,
    check_task,
    initialize,
    is_sera_runtime_path,
    load_task,
    new_task,
    task_changed_files,
    task_review_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class RuntimePathClassificationTests(unittest.TestCase):
    def test_runtime_state_is_excluded(self) -> None:
        for path in (
            ".sera",
            ".sera/",
            ".sera/cache",
            ".sera/cache/repo-map.json",
            ".sera/tasks",
            ".sera/tasks/20260101T000000Z-task/task.json",
            ".sera/tasks/20260101T000000Z-task/packet-review.md",
            ".sera/tasks/20260101T000000Z-task/reviews.jsonl",
            ".sera/latest-task",
            "./.sera/cache/repo-map.md",
            ".sera\\tasks\\t\\seal.json",
        ):
            self.assertTrue(is_sera_runtime_path(path), msg=path)

    def test_tracked_policy_is_repository_content(self) -> None:
        for path in (
            ".sera/config.json",
            ".sera/POLICY.md",
            ".sera/README.md",
            ".sera/policies/payments.md",
            "src/app.py",
            "sera/config.json",
            ".serafile",
        ):
            self.assertFalse(is_sera_runtime_path(path), msg=path)


class TrackedPolicyReviewTests(unittest.TestCase):
    """A repository that commits its SERA policy the way a team would."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".sera" / "POLICY.md").write_text("# Review policy\n\nOriginal policy.\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline with tracked SERA policy")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_task(self, owned: list[str]) -> Path:
        return new_task(
            self.root, "policy change", "adjust repository policy", "standard", "medium",
            owned, [], [], 1, "implementation",
        )

    def raise_high_risk_paths(self) -> None:
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["risk_policy"]["high_risk_paths"] = ["src/payments/**"]
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    # --- E16: tracked config is reviewable ----------------------------------
    def test_tracked_config_change_reaches_review_evidence(self) -> None:
        task_dir = self.make_task([".sera/config.json"])
        self.raise_high_risk_paths()
        self.assertEqual(task_changed_files(self.root, load_task(task_dir)), [".sera/config.json"])
        coverage = task_review_coverage(self.root, load_task(task_dir), 40_000)
        self.assertTrue(coverage["ok"])
        self.assertTrue(coverage["coverage_complete"])
        self.assertIn(".sera/config.json", coverage["text"])
        self.assertIn("src/payments/**", coverage["text"])

    def test_committed_config_change_reaches_review_evidence(self) -> None:
        task_dir = self.make_task([".sera/config.json"])
        self.raise_high_risk_paths()
        git(self.root, "add", ".sera/config.json")
        git(self.root, "commit", "-m", "raise policy")
        coverage = task_review_coverage(self.root, load_task(task_dir), 40_000)
        self.assertIn(".sera/config.json", coverage["text"])
        self.assertIn("src/payments/**", coverage["text"])
        self.assertIn("location: committed", coverage["text"])

    # --- E17: tracked POLICY.md is reviewable -------------------------------
    def test_tracked_policy_document_is_reviewable(self) -> None:
        task_dir = self.make_task([".sera/POLICY.md"])
        (self.root / ".sera" / "POLICY.md").write_text(
            "# Review policy\n\nPOLICY_MARKER: two reviewers required.\n", encoding="utf-8"
        )
        coverage = task_review_coverage(self.root, load_task(task_dir), 40_000)
        self.assertIn(".sera/POLICY.md", coverage["text"])
        self.assertIn("POLICY_MARKER", coverage["text"])

    # --- E18: tracked README is reviewable ----------------------------------
    def test_tracked_sera_readme_is_reviewable(self) -> None:
        task_dir = self.make_task([".sera/README.md"])
        (self.root / ".sera" / "README.md").write_text("README_MARKER local state notes\n", encoding="utf-8")
        coverage = task_review_coverage(self.root, load_task(task_dir), 40_000)
        self.assertIn(".sera/README.md", coverage["text"])
        self.assertIn("README_MARKER", coverage["text"])

    def test_policy_change_is_scope_checked_like_any_other_file(self) -> None:
        task_dir = self.make_task(["app.py"])
        self.raise_high_risk_paths()
        result = check_task(self.root, task_dir)
        self.assertIn(".sera/config.json", result["out_of_scope"])
        self.assertFalse(result["ok"])

    # --- E19/E20/E21: runtime state never leaks -----------------------------
    def test_task_runtime_state_never_becomes_a_task_change(self) -> None:
        task_dir = self.make_task(["app.py"])
        (self.root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        build_packet(self.root, task_dir, "build")
        build_packet(self.root, task_dir, "review")
        changed = task_changed_files(self.root, load_task(task_dir))
        self.assertEqual(changed, ["app.py"])
        for path in changed:
            self.assertFalse(path.startswith(".sera/tasks/"))

    def test_runtime_paths_are_absent_from_review_evidence(self) -> None:
        task_dir = self.make_task(
            ["app.py", ".sera/tasks/runtime.json", ".sera/cache/repo-map.json", ".sera/latest-task"]
        )
        (self.root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        (self.root / ".sera" / "tasks" / "runtime.json").write_text('{"leak": true}\n', encoding="utf-8")
        coverage = task_review_coverage(self.root, load_task(task_dir), 40_000)
        self.assertEqual(coverage["files"], ["app.py"])
        self.assertNotIn(".sera/tasks/", coverage["text"])
        self.assertNotIn(".sera/cache/", coverage["text"])
        self.assertNotIn(".sera/latest-task", coverage["text"])
        self.assertNotIn("leak", coverage["text"])

    def test_committed_runtime_state_is_still_excluded(self) -> None:
        # Even a repository that mistakenly commits `.sera/tasks/**` must not
        # have that runtime state reviewed as if it were project content.
        task_dir = self.make_task(["app.py"])
        runtime = self.root / ".sera" / "tasks" / "committed-runtime.json"
        runtime.write_text('{"runtime": 1}\n', encoding="utf-8")
        git(self.root, "add", "-f", ".sera/tasks/committed-runtime.json")
        git(self.root, "commit", "-m", "accidentally commit runtime state")
        self.assertEqual(task_changed_files(self.root, load_task(task_dir)), [])
        self.assertEqual(check_task(self.root, task_dir)["out_of_scope"], [])

    def test_review_packet_body_contains_no_runtime_state(self) -> None:
        task_dir = self.make_task(["app.py"])
        (self.root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
        build_packet(self.root, task_dir, "build")
        _, packet = build_packet(self.root, task_dir, "review")
        self.assertNotIn(".sera/tasks/", packet)
        self.assertNotIn(".sera/cache/", packet)


if __name__ == "__main__":
    unittest.main()
