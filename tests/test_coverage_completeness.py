"""Complete review coverage means every task change carries real evidence.

Reproduces the canonical-review blocker: a task owning `src/alpha.py` that
committed both `src/alpha.py` and `src/unowned.py` reported
`coverage_complete: true` and emitted a packet stating "Change coverage:
complete", while the packet carried no patch for `src/unowned.py` at all.
Evidence is generated from `allowed_files`; the authoritative change set is the
whole repository delta; nothing proved the two agreed.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sera.controller import build_packet, confirm_task_ownership, next_action
from sera.core import (
    PACKET_COVERAGE_INCOMPLETE,
    REVIEW_EVIDENCE_INCOMPLETE,
    REVIEW_SCOPE_UNRESOLVED,
    SeraError,
    build_repo_map,
    check_task,
    evidence_represented_paths,
    initialize,
    load_task,
    new_task,
    packet_state,
    task_fingerprint,
    task_review_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class CoverageRepository:
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "src" / "alpha.py").write_text("ALPHA = 0\n", encoding="utf-8")
        (self.root / "src" / "unowned.py").write_text("UNOWNED = 0\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_task(self, owned: list[str]) -> Path:
        return new_task(
            self.root, "coverage", "adjust the marker values", "standard", "medium",
            owned, [], [], 1, "implementation",
        )

    def coverage(self, task_dir: Path) -> dict:
        return task_review_coverage(self.root, load_task(task_dir), 48_000)

    def commit_both(self) -> None:
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\nALPHA_MARKER = True\n", encoding="utf-8")
        (self.root / "src" / "unowned.py").write_text("UNOWNED = 1\nUNOWNED_MARKER = True\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "land both")


class OutOfScopeCoverageTests(CoverageRepository, unittest.TestCase):
    # --- A: the exact canonical reproduction --------------------------------
    def test_committed_out_of_scope_work_is_never_complete_coverage(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        build_packet(self.root, task_dir, "build")
        self.commit_both()
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

        result = check_task(self.root, task_dir)
        self.assertEqual(result["changed_files"], ["src/alpha.py", "src/unowned.py"])
        self.assertEqual(result["out_of_scope"], ["src/unowned.py"])

        coverage = self.coverage(task_dir)
        self.assertFalse(coverage["coverage_complete"])
        self.assertEqual(coverage["coverage_reason"], REVIEW_SCOPE_UNRESOLVED)
        self.assertEqual(coverage["out_of_scope_paths"], ["src/unowned.py"])
        self.assertEqual(coverage["missing_evidence_paths"], ["src/unowned.py"])
        # The owned-file diff rendered perfectly well; that is exactly why it
        # must not be read as completeness.
        self.assertTrue(coverage["ok"])
        self.assertEqual(coverage["files"], ["src/alpha.py"])

    def test_unresolved_scope_is_the_reported_next_action(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        build_packet(self.root, task_dir, "build")
        self.commit_both()
        report = next_action(self.root, task_dir)
        self.assertEqual(report["state"], "resolve_scope")
        self.assertIn("src/unowned.py", report["reason"])
        self.assertFalse(report["review_coverage"]["complete"])
        self.assertEqual(report["review_coverage"]["reason"], REVIEW_SCOPE_UNRESOLVED)
        self.assertEqual(report["review_coverage"]["out_of_scope_paths"], ["src/unowned.py"])

    def test_direct_packet_generation_refuses_out_of_scope_work(self) -> None:
        # Fail closed even when the operator bypasses `sera next` entirely.
        task_dir = self.make_task(["src/alpha.py"])
        self.commit_both()
        with self.assertRaises(SeraError) as caught:
            build_packet(self.root, task_dir, "review")
        message = str(caught.exception)
        self.assertIn(REVIEW_SCOPE_UNRESOLVED, message)
        self.assertIn("src/unowned.py", message)
        self.assertFalse((task_dir / "packet-review.md").exists())

    def test_an_existing_packet_stops_being_current_once_scope_breaks(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        build_packet(self.root, task_dir, "build")
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        build_packet(self.root, task_dir, "review")
        self.assertTrue(
            packet_state(
                self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
            )["current"]
        )
        (self.root / "src" / "unowned.py").write_text("UNOWNED = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "land out-of-scope work too")
        state = packet_state(
            self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
        )
        self.assertFalse(state["current"])

    # --- B: coverage becomes complete once scope is corrected ---------------
    def test_coverage_is_complete_once_ownership_is_corrected(self) -> None:
        task_dir = self.make_task(["src/alpha.py"])
        self.commit_both()
        self.assertFalse(self.coverage(task_dir)["coverage_complete"])

        confirm_task_ownership(self.root, task_dir, ["src/alpha.py", "src/unowned.py"])
        coverage = self.coverage(task_dir)
        self.assertTrue(coverage["coverage_complete"])
        self.assertIsNone(coverage["coverage_reason"])
        self.assertEqual(coverage["out_of_scope_paths"], [])
        self.assertEqual(coverage["missing_evidence_paths"], [])
        self.assertEqual(coverage["files"], ["src/alpha.py", "src/unowned.py"])
        self.assertIn("ALPHA_MARKER", coverage["text"])
        self.assertIn("UNOWNED_MARKER", coverage["text"])

        build_packet(self.root, task_dir, "build")
        _, packet = build_packet(self.root, task_dir, "review")
        self.assertIn("Change coverage: complete", packet)
        self.assertIn("ALPHA_MARKER", packet)
        self.assertIn("UNOWNED_MARKER", packet)

    def test_out_of_scope_paths_still_move_the_change_fingerprint(self) -> None:
        # The fingerprint binds the authoritative change set, not the evidenced
        # subset, so out-of-scope work appearing must never go unnoticed.
        task_dir = self.make_task(["src/alpha.py"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        before = self.coverage(task_dir)["change_fingerprint"]
        (self.root / "src" / "unowned.py").write_text("UNOWNED = 1\n", encoding="utf-8")
        after = self.coverage(task_dir)["change_fingerprint"]
        self.assertNotEqual(before, after)


class EvidenceRepresentationTests(CoverageRepository, unittest.TestCase):
    # --- C: a rename block represents both of its identities ----------------
    def test_rename_block_covers_source_and_destination(self) -> None:
        task_dir = self.make_task(["src/alpha.py", "src/renamed.py"])
        git(self.root, "mv", "src/alpha.py", "src/renamed.py")
        git(self.root, "commit", "-m", "rename alpha")
        coverage = self.coverage(task_dir)
        self.assertEqual(coverage["changed_paths"], ["src/alpha.py", "src/renamed.py"])
        self.assertTrue(coverage["coverage_complete"])
        self.assertEqual(coverage["missing_evidence_paths"], [])
        # One canonical destination block truthfully carrying its source.
        self.assertEqual(coverage["text"].count("### `src/"), 1)
        represented = evidence_represented_paths(coverage["entries"])
        self.assertEqual(represented, {"src/alpha.py", "src/renamed.py"})

    def test_evidence_representation_counts_old_path(self) -> None:
        entries = [
            {"path": "src/renamed.py", "old_path": "src/alpha.py"},
            {"path": "src/beta.py", "old_path": None},
        ]
        self.assertEqual(
            evidence_represented_paths(entries), {"src/renamed.py", "src/alpha.py", "src/beta.py"}
        )

    # --- D: a missing evidence entry is caught generically ------------------
    def test_missing_evidence_for_an_owned_path_fails_closed(self) -> None:
        task_dir = self.make_task(["src/alpha.py", "src/unowned.py"])
        self.commit_both()
        self.assertTrue(self.coverage(task_dir)["coverage_complete"])

        import sera.core as core

        real = core.review_diff_coverage

        def drop_one(*args, **kwargs):
            result = real(*args, **kwargs)
            result["entries"] = [e for e in result["entries"] if e["path"] != "src/unowned.py"]
            result["files"] = [p for p in result["files"] if p != "src/unowned.py"]
            return result

        # The invariant must hold against the evidence actually produced, not
        # against an assumption that owned files always render.
        with mock.patch.object(core, "review_diff_coverage", drop_one):
            coverage = self.coverage(task_dir)
        self.assertFalse(coverage["coverage_complete"])
        self.assertEqual(coverage["coverage_reason"], REVIEW_EVIDENCE_INCOMPLETE)
        self.assertEqual(coverage["missing_evidence_paths"], ["src/unowned.py"])
        self.assertEqual(coverage["out_of_scope_paths"], [])

    def test_missing_evidence_refuses_packet_generation(self) -> None:
        task_dir = self.make_task(["src/alpha.py", "src/unowned.py"])
        self.commit_both()

        import sera.core as core

        real = core.review_diff_coverage

        def drop_one(*args, **kwargs):
            result = real(*args, **kwargs)
            result["entries"] = [e for e in result["entries"] if e["path"] != "src/unowned.py"]
            return result

        with mock.patch.object(core, "review_diff_coverage", drop_one):
            with self.assertRaises(SeraError) as caught:
                build_packet(self.root, task_dir, "review")
            self.assertIn(REVIEW_EVIDENCE_INCOMPLETE, str(caught.exception))
            self.assertIn("src/unowned.py", str(caught.exception))

    def test_packet_state_rejects_a_packet_whose_coverage_lapsed(self) -> None:
        task_dir = self.make_task(["src/alpha.py", "src/unowned.py"])
        (self.root / "src" / "alpha.py").write_text("ALPHA = 1\n", encoding="utf-8")
        (self.root / "src" / "unowned.py").write_text("UNOWNED = 1\n", encoding="utf-8")
        build_packet(self.root, task_dir, "build")
        build_packet(self.root, task_dir, "review")
        fingerprint = task_fingerprint(self.root, task_dir)
        self.assertTrue(
            packet_state(self.root, task_dir, "review", load_task(task_dir), fingerprint)["current"]
        )

        import sera.core as core

        real = core.review_diff_coverage

        def drop_one(*args, **kwargs):
            result = real(*args, **kwargs)
            result["entries"] = [e for e in result["entries"] if e["path"] != "src/unowned.py"]
            return result

        with mock.patch.object(core, "review_diff_coverage", drop_one):
            state = packet_state(self.root, task_dir, "review", load_task(task_dir), fingerprint)
        self.assertFalse(state["current"])
        self.assertEqual(state["reason"], PACKET_COVERAGE_INCOMPLETE)


if __name__ == "__main__":
    unittest.main()
