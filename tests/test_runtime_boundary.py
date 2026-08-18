"""Renames across the SERA runtime boundary keep their project-visible side.

Reproduces the second canonical-review blocker: a change record was classified
by its destination path alone, so committing `git mv app.py
.sera/tasks/smuggled.py` discarded the whole record. `app.py` vanished from
`changed_files`, scope checking, and coverage; `sera next` returned
`dispatch_builder`; and a review packet was emitted stating "Change coverage:
complete" and "No task-relative changes to review". Runtime exclusion had become
a way to make project changes invisible.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet, next_action
from sera.core import (
    REVIEW_SCOPE_UNRESOLVED,
    SeraError,
    build_repo_map,
    changed_files,
    check_task,
    initialize,
    load_task,
    new_task,
    normalize_runtime_boundary,
    task_changed_files,
    task_review_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


RUNTIME = ".sera/tasks/smuggled.py"


class BoundaryNormalizationTests(unittest.TestCase):
    """The rule itself, independent of any repository."""

    def normalize(self, status: str, path: str, old_path: str | None, is_copy: bool = False) -> list[dict]:
        record = {
            "status": status, "path": path, "old_path": old_path,
            "old_sha": "aaaaaaa", "new_sha": "bbbbbbb",
        }
        return normalize_runtime_boundary(
            record, is_copy=is_copy, deleted_status="D", added_status="A"
        )

    def test_project_to_project_rename_is_untouched(self) -> None:
        result = self.normalize("R100", "src/new.py", "src/app.py")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "src/new.py")
        self.assertEqual(result[0]["old_path"], "src/app.py")
        self.assertEqual(result[0]["status"], "R100")

    def test_project_to_runtime_rename_becomes_a_project_deletion(self) -> None:
        result = self.normalize("R100", RUNTIME, "app.py")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "app.py")
        self.assertIsNone(result[0]["old_path"])
        self.assertEqual(result[0]["status"], "D")
        self.assertEqual(result[0]["old_sha"], "aaaaaaa")
        self.assertEqual(result[0]["new_sha"], "0000000")

    def test_project_to_runtime_copy_invents_no_deletion(self) -> None:
        # A copy leaves its source in place, so the project side did not change.
        self.assertEqual(self.normalize("C100", RUNTIME, "app.py", is_copy=True), [])

    def test_runtime_to_project_rename_becomes_a_project_addition(self) -> None:
        result = self.normalize("R100", "surfaced.py", ".sera/tasks/generated.py")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "surfaced.py")
        self.assertIsNone(result[0]["old_path"])
        self.assertEqual(result[0]["status"], "A")
        self.assertEqual(result[0]["old_sha"], "0000000")

    def test_runtime_to_project_copy_becomes_a_project_addition(self) -> None:
        result = self.normalize("C100", "surfaced.py", ".sera/cache/blob.json", is_copy=True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["path"], "surfaced.py")
        self.assertEqual(result[0]["status"], "A")

    def test_runtime_to_runtime_is_excluded_entirely(self) -> None:
        self.assertEqual(self.normalize("R100", RUNTIME, ".sera/cache/x.json"), [])

    def test_plain_records_follow_ordinary_runtime_exclusion(self) -> None:
        self.assertEqual(self.normalize("M", RUNTIME, None), [])
        self.assertEqual(len(self.normalize("M", "app.py", None)), 1)
        # Tracked policy is repository content, not runtime state.
        self.assertEqual(len(self.normalize("M", ".sera/config.json", None)), 1)


class BoundaryRepository:
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "alpha.py").write_text("ALPHA = 0\n", encoding="utf-8")
        (self.root / "app.py").write_text("APP_MARKER = True\n", encoding="utf-8")
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_task(self, owned: list[str]) -> Path:
        return new_task(
            self.root, "boundary", "adjust the project", "standard", "medium",
            owned, [], [], 1, "implementation",
        )

    def coverage(self, task_dir: Path) -> dict:
        return task_review_coverage(self.root, load_task(task_dir), 48_000)

    def smuggle(self, commit: bool = True) -> None:
        git(self.root, "mv", "app.py", RUNTIME)
        if commit:
            git(self.root, "commit", "-m", "move app.py into runtime state")


class CommittedBoundaryTests(BoundaryRepository, unittest.TestCase):
    # --- 1: the exact canonical reproduction --------------------------------
    def test_unowned_project_to_runtime_rename_is_out_of_scope(self) -> None:
        task_dir = self.make_task(["alpha.py"])
        build_packet(self.root, task_dir, "build")
        self.smuggle()

        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["app.py"])
        self.assertEqual(result["out_of_scope"], ["app.py"])
        self.assertFalse(result["ok"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "resolve_scope")

        coverage = self.coverage(task_dir)
        self.assertFalse(coverage["coverage_complete"])
        self.assertEqual(coverage["coverage_reason"], REVIEW_SCOPE_UNRESOLVED)
        with self.assertRaises(SeraError):
            build_packet(self.root, task_dir, "review")
        self.assertFalse((task_dir / "packet-review.md").exists())

    # --- 2: owned source keeps real deletion evidence, no leakage -----------
    def test_owned_project_to_runtime_rename_is_reviewed_as_a_deletion(self) -> None:
        task_dir = self.make_task(["app.py"])
        build_packet(self.root, task_dir, "build")
        self.smuggle()

        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["app.py"])
        self.assertEqual(result["out_of_scope"], [])

        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["coverage_complete"], msg=coverage["coverage_reason"])
        self.assertEqual(coverage["files"], ["app.py"])
        self.assertIn("status: deleted", coverage["text"])
        self.assertIn("APP_MARKER", coverage["text"])

        _, packet = build_packet(self.root, task_dir, "review")
        self.assertIn("APP_MARKER", packet)
        self.assertIn("Change coverage: complete", packet)
        # Runtime state is still not review content.
        self.assertNotIn("smuggled", packet)
        self.assertNotIn(".sera/tasks/", packet)

    def test_runtime_destination_never_leaks_even_when_owned(self) -> None:
        # Owning a runtime path must not make it reviewable, and must not let
        # rename pairing print it beside the project deletion.
        task_dir = self.make_task(["app.py", RUNTIME])
        self.smuggle()
        coverage = self.coverage(task_dir)
        self.assertEqual(coverage["files"], ["app.py"])
        self.assertNotIn("smuggled", coverage["text"])
        self.assertNotIn(".sera/tasks/", coverage["text"])
        self.assertIn("APP_MARKER", coverage["text"])

    # --- 3/4: the reverse boundary ------------------------------------------
    def surface(self) -> None:
        (self.root / ".sera" / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / ".sera" / "tasks" / "generated.py").write_text(
            "GENERATED_MARKER = True\n", encoding="utf-8"
        )
        git(self.root, "add", "-f", ".sera/tasks/generated.py")
        git(self.root, "commit", "-m", "track a runtime file")
        build_repo_map(self.root)

    def test_owned_runtime_to_project_rename_is_reviewed_as_an_addition(self) -> None:
        self.surface()
        task_dir = self.make_task(["surfaced.py"])
        git(self.root, "mv", ".sera/tasks/generated.py", "surfaced.py")
        git(self.root, "commit", "-m", "surface a runtime file into project content")

        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["surfaced.py"])
        self.assertEqual(result["out_of_scope"], [])
        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["coverage_complete"], msg=coverage["coverage_reason"])
        self.assertIn("GENERATED_MARKER", coverage["text"])
        self.assertNotIn(".sera/tasks/", coverage["text"])

    def test_unowned_runtime_to_project_rename_is_out_of_scope(self) -> None:
        self.surface()
        task_dir = self.make_task(["alpha.py"])
        build_packet(self.root, task_dir, "build")
        git(self.root, "mv", ".sera/tasks/generated.py", "surfaced.py")
        git(self.root, "commit", "-m", "surface a runtime file into project content")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["out_of_scope"], ["surfaced.py"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "resolve_scope")

    # --- 5: runtime to runtime stays excluded -------------------------------
    def test_runtime_to_runtime_rename_is_not_a_task_change(self) -> None:
        self.surface()
        task_dir = self.make_task(["alpha.py"])
        git(self.root, "mv", ".sera/tasks/generated.py", ".sera/tasks/moved.py")
        git(self.root, "commit", "-m", "shuffle runtime state")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], [])
        self.assertEqual(result["out_of_scope"], [])

    # --- 6: ordinary renames are untouched ----------------------------------
    def test_ordinary_project_rename_is_preserved(self) -> None:
        task_dir = self.make_task(["app.py", "renamed.py"])
        git(self.root, "mv", "app.py", "renamed.py")
        git(self.root, "commit", "-m", "ordinary rename")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["app.py", "renamed.py"])
        self.assertEqual(result["out_of_scope"], [])
        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["coverage_complete"])
        self.assertIn("renamed.py", coverage["text"])

    # --- 16: the change fingerprint moves with the boundary rename ----------
    def test_boundary_rename_moves_the_change_fingerprint(self) -> None:
        task_dir = self.make_task(["app.py"])
        before = self.coverage(task_dir)["change_fingerprint"]
        self.smuggle()
        after = self.coverage(task_dir)["change_fingerprint"]
        self.assertNotEqual(before, after)
        self.assertIn("app.py", self.coverage(task_dir)["changed_paths"])


class WorkingTreeBoundaryTests(BoundaryRepository, unittest.TestCase):
    # --- 9: staged boundary rename ------------------------------------------
    def test_staged_unowned_boundary_rename_is_out_of_scope(self) -> None:
        task_dir = self.make_task(["alpha.py"])
        build_packet(self.root, task_dir, "build")
        self.smuggle(commit=False)
        self.assertIn("R", git(self.root, "status", "--porcelain=v1"))
        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["app.py"])
        self.assertEqual(result["out_of_scope"], ["app.py"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "resolve_scope")

    def test_staged_owned_boundary_rename_is_reviewed_as_a_deletion(self) -> None:
        task_dir = self.make_task(["app.py"])
        self.smuggle(commit=False)
        coverage = self.coverage(task_dir)
        self.assertEqual(coverage["changed_paths"], ["app.py"])
        self.assertTrue(coverage["coverage_complete"], msg=coverage["coverage_reason"])
        self.assertIn("APP_MARKER", coverage["text"])
        self.assertNotIn("smuggled", coverage["text"])

    # --- 10: staged reverse boundary ----------------------------------------
    def test_staged_runtime_to_project_rename_is_detected(self) -> None:
        (self.root / ".sera" / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / ".sera" / "tasks" / "generated.py").write_text("GEN = 1\n", encoding="utf-8")
        git(self.root, "add", "-f", ".sera/tasks/generated.py")
        git(self.root, "commit", "-m", "track a runtime file")
        build_repo_map(self.root)
        task_dir = self.make_task(["alpha.py"])
        git(self.root, "mv", ".sera/tasks/generated.py", "surfaced.py")
        self.assertEqual(task_changed_files(self.root, load_task(task_dir)), ["surfaced.py"])

    # --- 11: the worktree-only equivalent -----------------------------------
    def test_unstaged_move_into_runtime_keeps_the_project_deletion(self) -> None:
        # Git may present a worktree move as a plain deletion plus an untracked
        # file rather than a rename; the project side must survive either way.
        task_dir = self.make_task(["app.py"])
        (self.root / ".sera" / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / ".sera" / "tasks" / "smuggled.py").write_text(
            (self.root / "app.py").read_text(encoding="utf-8"), encoding="utf-8"
        )
        (self.root / "app.py").unlink()
        self.assertEqual(task_changed_files(self.root, load_task(task_dir)), ["app.py"])
        self.assertEqual(changed_files(self.root), ["app.py"])
        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["coverage_complete"], msg=coverage["coverage_reason"])
        self.assertIn("APP_MARKER", coverage["text"])


class DirtyBaselineBoundaryTests(BoundaryRepository, unittest.TestCase):
    # --- 12: pre-existing dirt stays out of task scope ----------------------
    def test_pre_existing_dirty_project_file_is_not_task_scope(self) -> None:
        (self.root / "app.py").write_text("APP_MARKER = True\nEDITED = 1\n", encoding="utf-8")
        task_dir = self.make_task(["alpha.py"])
        (self.root / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["alpha.py"])
        self.assertEqual(result["out_of_scope"], [])

    # --- 13: pre-existing dirt later moved across the boundary --------------
    def test_pre_existing_dirty_file_moved_into_runtime_is_a_task_change(self) -> None:
        (self.root / "app.py").write_text("APP_MARKER = True\nEDITED = 1\n", encoding="utf-8")
        task_dir = self.make_task(["alpha.py"])
        git(self.root, "add", "app.py")
        git(self.root, "mv", "app.py", RUNTIME)
        result = check_task(self.root, task_dir)
        self.assertIn("app.py", result["changed_files"])
        self.assertIn("app.py", result["out_of_scope"])

    def test_pre_existing_runtime_file_moved_into_project_is_a_task_change(self) -> None:
        (self.root / ".sera" / "tasks").mkdir(parents=True, exist_ok=True)
        (self.root / ".sera" / "tasks" / "generated.py").write_text("GEN = 1\n", encoding="utf-8")
        git(self.root, "add", "-f", ".sera/tasks/generated.py")
        git(self.root, "commit", "-m", "track a runtime file")
        build_repo_map(self.root)
        task_dir = self.make_task(["alpha.py"])
        git(self.root, "mv", ".sera/tasks/generated.py", "surfaced.py")
        result = check_task(self.root, task_dir)
        self.assertIn("surfaced.py", result["changed_files"])
        self.assertIn("surfaced.py", result["out_of_scope"])


if __name__ == "__main__":
    unittest.main()
