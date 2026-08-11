from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import (
    auto_task,
    confirm_task_ownership,
    context_report,
    efficiency_report,
    infer_risk,
    inbox_report,
    next_action,
    prepare_run,
    select_context,
)
from sera.core import build_repo_map, check_task, initialize, load_task, new_task, update_repo_map


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class SeraControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "payments.py").write_text(
            "def reconcile_payment():\n    return 'pending'\n", encoding="utf-8"
        )
        (self.root / "src" / "profile.py").write_text(
            "def update_profile():\n    return True\n", encoding="utf-8"
        )
        (self.root / "tests" / "test_payments.py").write_text("# payments tests\n", encoding="utf-8")
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_risk_detection_escalates_financial_work(self) -> None:
        risk, reasons = infer_risk("Change payment reconciliation state")
        self.assertEqual(risk, "high")
        self.assertTrue(any("payment" in reason for reason in reasons))

    def test_context_selection_explains_inclusion(self) -> None:
        report = select_context(self.root, "fix payment reconciliation")
        paths = [item["path"] for item in report["selected_files"]]
        self.assertIn("src/payments.py", paths)
        item = next(item for item in report["selected_files"] if item["path"] == "src/payments.py")
        self.assertTrue(item["reasons"])
        self.assertGreater(report["context_reduction_percent"], 0)

    def test_auto_task_requires_ownership_confirmation(self) -> None:
        task_dir, _ = auto_task(self.root, "fix payment reconciliation")
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")
        self.assertFalse(task["controller"]["ownership_confirmed"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "confirm_ownership")
        confirm_task_ownership(self.root, task_dir)
        self.assertEqual(next_action(self.root, task_dir)["state"], "build_packet")

    def test_explicit_files_make_auto_task_confirmed(self) -> None:
        task_dir, _ = auto_task(
            self.root,
            "fix payment reconciliation",
            files=["src/payments.py", "tests/test_payments.py"],
        )
        task = load_task(task_dir)
        self.assertTrue(task["controller"]["ownership_confirmed"])
        self.assertEqual(task["allowed_files"], ["src/payments.py", "tests/test_payments.py"])

    def test_dirty_worktree_baseline_is_preserved(self) -> None:
        readme = self.root / "README.md"
        readme.write_text("pre-existing\n", encoding="utf-8")
        task_dir = new_task(
            self.root,
            "profile fix",
            "fix profile",
            "fast",
            "low",
            ["src/profile.py"],
            [],
            [],
            0,
            "implementation",
        )
        (self.root / "src" / "profile.py").write_text("def update_profile():\n    return False\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertNotIn("README.md", result["out_of_scope"])
        self.assertEqual(result["changed_files"], ["src/profile.py"])
        readme.write_text("changed again\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertIn("README.md", result["out_of_scope"])

    def test_context_and_efficiency_reports_are_measurements_not_billing_claims(self) -> None:
        task_dir, _ = auto_task(self.root, "fix profile", files=["src/profile.py"], uncertainty=0)
        report = context_report(self.root, task_dir)
        self.assertIn("selected_source_tokens", report)
        efficiency = efficiency_report(self.root, task_dir)
        self.assertIn("efficiency_score", efficiency)
        self.assertIn("not API billing telemetry", efficiency["measurement_note"])

    def test_prepare_run_generates_packet_but_does_not_claim_provider_dispatch(self) -> None:
        report = prepare_run(self.root, "fix profile", files=["src/profile.py"], uncertainty=0)
        self.assertTrue((self.root / report["builder_packet"]).exists())
        self.assertIn("controller-managed", report["provider_dispatch"])

    def test_delta_map_reuses_unchanged_files(self) -> None:
        first = update_repo_map(self.root)
        self.assertGreaterEqual(first["file_count"], 3)
        second = update_repo_map(self.root)
        self.assertGreater(second["reused_files"], 0)
        self.assertEqual(second["rescanned_files"], 0)
        (self.root / "src" / "profile.py").write_text("def update_profile():\n    return False\n", encoding="utf-8")
        third = update_repo_map(self.root)
        self.assertGreaterEqual(third["rescanned_files"], 1)

    def test_run_without_explicit_files_waits_for_ownership_confirmation(self) -> None:
        report = prepare_run(self.root, "fix payment reconciliation")
        self.assertIsNone(report["builder_packet"])
        self.assertEqual(report["next"]["state"], "confirm_ownership")

    def test_context_budget_blocks_packet_generation(self) -> None:
        large = self.root / "src" / "large.py"
        large.write_text("x = '" + ("a" * 30000) + "'\n", encoding="utf-8")
        build_repo_map(self.root)
        report = prepare_run(
            self.root,
            "adjust large helper",
            mode="fast",
            risk="low",
            uncertainty=0,
            files=["src/large.py"],
        )
        self.assertIsNone(report["builder_packet"])
        self.assertEqual(report["next"]["state"], "reduce_context")

    def test_inbox_reports_multiple_tasks(self) -> None:
        auto_task(self.root, "fix profile", files=["src/profile.py"], uncertainty=0)
        auto_task(self.root, "fix payment reconciliation", files=["src/payments.py"])
        inbox = inbox_report(self.root)
        self.assertEqual(inbox["count"], 2)
        self.assertEqual(len(inbox["tasks"]), 2)


if __name__ == "__main__":
    unittest.main()
