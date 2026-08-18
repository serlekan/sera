"""Coverage is separate from currency, and a failed review stops the workflow.

Reproduces the 0.4.1 ordering defect: with a current `fix-first` independent
review and a missing gate, `sera next` asked for the release gate instead of
surfacing the rejection the independent reviewer had already recorded.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet, next_action
from sera.core import (
    PACKET_COVERAGE_INCOMPLETE,
    PACKET_STALE_CHANGE_SET,
    REVIEW_BASELINE_UNBOUND,
    REVIEW_DIFF_BUDGET_INSUFFICIENT,
    SeraError,
    accept_review,
    build_repo_map,
    check_task,
    git_head_identity,
    initialize,
    load_task,
    new_task,
    packet_provenance_path,
    packet_state,
    record_review,
    save_task,
    task_fingerprint,
    task_review_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class PrecedenceRepository:
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("VALUE = 0\n" * 30, encoding="utf-8")
        (self.root / "src" / "other.py").write_text("OTHER = 0\n" * 30, encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def set_packet_chars(self, value: int) -> None:
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["max_packet_chars"] = value
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def gated_task(self, owned: list[str] | None = None) -> Path:
        task_dir = new_task(
            self.root, "gated change", "adjust the marker values", "assured", "high",
            owned or ["src/app.py"], [], [], 1, "implementation",
        )
        return task_dir

    def implemented_task(self) -> Path:
        task_dir = self.gated_task()
        build_packet(self.root, task_dir, "build")
        (self.root / "src" / "app.py").write_text("VALUE = 1\nEDITED = True\n" * 30, encoding="utf-8")
        build_packet(self.root, task_dir, "review")
        return task_dir


class ReviewCoverageTests(PrecedenceRepository, unittest.TestCase):
    # --- F22: current plus complete coverage may be dispatched --------------
    def test_current_and_complete_coverage_allows_dispatch(self) -> None:
        task_dir = self.implemented_task()
        coverage = task_review_coverage(self.root, load_task(task_dir), 48_000)
        self.assertTrue(coverage["ok"])
        self.assertTrue(coverage["coverage_complete"])
        self.assertIsNone(coverage["coverage_reason"])
        report = next_action(self.root, task_dir)
        self.assertEqual(report["state"], "dispatch_review")
        self.assertTrue(report["review_coverage"]["complete"])

    def test_coverage_is_unassessed_rather_than_complete_outside_review(self) -> None:
        task_dir = self.gated_task()
        report = next_action(self.root, task_dir)
        self.assertEqual(report["required_stage"], "build")
        # Never report completeness that was not computed.
        self.assertIsNone(report["review_coverage"]["complete"])

    # --- F23: fresh but incomplete coverage is refused ----------------------
    def test_incomplete_coverage_refuses_dispatch(self) -> None:
        task_dir = self.implemented_task()
        self.assertTrue(
            packet_state(
                self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
            )["current"]
        )
        # A 0.4.1 task carries no baseline identity, so its committed range —
        # and therefore its complete change set — cannot be derived.
        task = load_task(task_dir)
        task.pop("baseline_repository_identity")
        save_task(task_dir, task)

        coverage = task_review_coverage(self.root, load_task(task_dir), 48_000)
        self.assertTrue(coverage["ok"], msg="the diff itself still renders")
        self.assertFalse(coverage["coverage_complete"])
        self.assertEqual(coverage["coverage_reason"], REVIEW_BASELINE_UNBOUND)
        # Currency alone must never be read as completeness: the controller
        # refuses the dispatch on coverage grounds before anything else.
        report = next_action(self.root, task_dir)
        self.assertEqual(report["state"], "review_coverage_incomplete")
        self.assertFalse(report["review_coverage"]["complete"])
        self.assertEqual(report["review_coverage"]["reason"], REVIEW_BASELINE_UNBOUND)

    def test_a_packet_claiming_incomplete_coverage_is_never_dispatchable(self) -> None:
        task_dir = self.implemented_task()
        provenance_path = packet_provenance_path(task_dir, "review")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["coverage_complete"] = False
        provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
        state = packet_state(
            self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
        )
        self.assertFalse(state["current"])
        self.assertEqual(state["reason"], PACKET_COVERAGE_INCOMPLETE)

    def test_incomplete_coverage_refuses_packet_generation(self) -> None:
        task_dir = self.gated_task()
        build_packet(self.root, task_dir, "build")
        (self.root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        task = load_task(task_dir)
        task.pop("baseline_repository_identity")
        save_task(task_dir, task)
        with self.assertRaises(SeraError) as caught:
            build_packet(self.root, task_dir, "review")
        self.assertIn(REVIEW_BASELINE_UNBOUND, str(caught.exception))

    def test_change_set_movement_stales_the_packet_before_the_route(self) -> None:
        task_dir = self.implemented_task()
        provenance_path = packet_provenance_path(task_dir, "review")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["review_change_fingerprint"] = "0" * 64
        provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
        state = packet_state(
            self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
        )
        self.assertEqual(state["reason"], PACKET_STALE_CHANGE_SET)

    def test_missing_change_binding_fails_closed(self) -> None:
        task_dir = self.implemented_task()
        provenance_path = packet_provenance_path(task_dir, "review")
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance.pop("review_change_fingerprint")
        provenance_path.write_text(json.dumps(provenance, indent=2, sort_keys=True), encoding="utf-8")
        self.assertFalse(
            packet_state(
                self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
            )["current"]
        )

    # --- F24: an insufficient diff budget still fails exactly as before -----
    def test_insufficient_diff_budget_fails_closed(self) -> None:
        self.set_packet_chars(200)
        task_dir = self.gated_task(["src/app.py", "src/other.py"])
        for name in ("app", "other"):
            (self.root / "src" / f"{name}.py").write_text(f"{name.upper()} = 1\nEDITED = 1\n" * 30, encoding="utf-8")
        coverage = task_review_coverage(self.root, load_task(task_dir), 200)
        self.assertFalse(coverage["ok"])
        self.assertEqual(coverage["reason"], REVIEW_DIFF_BUDGET_INSUFFICIENT)
        self.assertFalse(coverage["coverage_complete"])
        build_packet(self.root, task_dir, "build")
        self.assertEqual(next_action(self.root, task_dir)["state"], "review_diff_budget_insufficient")
        with self.assertRaises(SeraError):
            build_packet(self.root, task_dir, "review")


class FailedReviewPrecedenceTests(PrecedenceRepository, unittest.TestCase):
    # --- G25: a current fix-first outranks a missing gate -------------------
    def test_fix_first_outranks_a_missing_gate(self) -> None:
        task_dir = self.implemented_task()
        accept_review(self.root, task_dir, "fix-first", "independent-peer", "found a defect", "independent")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["failed_reviews"], ["independent"])
        self.assertEqual(result["missing_reviews"], ["gate"])
        report = next_action(self.root, task_dir)
        self.assertEqual(report["state"], "fix_first")
        self.assertIn("fix-first", report["reason"])
        self.assertIn("Address reviewer findings", result["next_action"])

    # --- G26: rethink behaves the same way ----------------------------------
    def test_rethink_outranks_a_missing_gate(self) -> None:
        task_dir = self.implemented_task()
        accept_review(self.root, task_dir, "rethink", "independent-peer", "wrong approach", "independent")
        report = next_action(self.root, task_dir)
        self.assertEqual(report["state"], "fix_first")
        self.assertNotEqual(report["state"], "dispatch_review")

    # --- G27: a passing independent stage does progress to the gate ---------
    def test_ship_progresses_to_the_gate(self) -> None:
        task_dir = self.implemented_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["failed_reviews"], [])
        self.assertEqual(result["missing_reviews"], ["gate"])
        self.assertIn(next_action(self.root, task_dir)["state"], {"dispatch_review", "review_packet"})

    def test_a_stale_review_outranks_a_current_failed_one(self) -> None:
        task_dir = self.implemented_task()
        accept_review(self.root, task_dir, "fix-first", "independent-peer", "found a defect", "independent")
        self.assertEqual(next_action(self.root, task_dir)["state"], "fix_first")
        git(self.root, "commit", "--allow-empty", "-m", "head moves")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["stale_reviews"], ["independent"])
        self.assertEqual(result["failed_reviews"], [])
        self.assertEqual(next_action(self.root, task_dir)["state"], "review")

    def test_a_legacy_unbound_failed_review_is_stale_not_failed(self) -> None:
        task_dir = self.implemented_task()
        record_review(task_dir, task_fingerprint(self.root, task_dir), "fix-first", "peer", "0.4.1 verdict")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["stale_reviews"], ["independent"])
        self.assertEqual(result["failed_reviews"], [])
        self.assertFalse(result["ok"])

    def test_failed_review_blocks_the_seal(self) -> None:
        task_dir = self.implemented_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        accept_review(self.root, task_dir, "fix-first", "release-gate", "not releasable", "gate")
        result = check_task(self.root, task_dir)
        self.assertEqual(result["failed_reviews"], ["gate"])
        self.assertFalse(result["ok"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "fix_first")

    def test_review_states_expose_verdict_and_freshness(self) -> None:
        task_dir = self.implemented_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        states = check_task(self.root, task_dir)["review_states"]
        self.assertEqual(states["independent"]["status"], "current")
        self.assertEqual(states["independent"]["verdict"], "ship")
        self.assertEqual(states["independent"]["reasons"], [])
        self.assertEqual(
            load_task(task_dir)["baseline_repository_identity"]["head_sha"],
            git_head_identity(self.root)["head_sha"],
        )


if __name__ == "__main__":
    unittest.main()
