from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.core import (
    SeraError,
    budget_report,
    build_repo_map,
    check_task,
    create_seal,
    decide_route,
    initialize,
    load_task,
    new_task,
    record_evidence,
    record_review,
    task_fingerprint,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class SeraCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test User")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "src" / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
        (self.root / "tests" / "test_app.py").write_text("# test placeholder\n", encoding="utf-8")
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.repo_map = build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_task(self, **overrides):
        values = {
            "name": "adjust answer",
            "objective": "Return the correct answer",
            "mode": "standard",
            "risk": "medium",
            "allowed_files": ["src/app.py", "tests/test_app.py"],
            "constraints": ["Preserve the function name"],
            "verification": ["python -m unittest"],
            "uncertainty": 1,
            "use_case": "implementation",
        }
        values.update(overrides)
        return new_task(self.root, **values)

    def test_map_is_compact_and_hashed(self) -> None:
        self.assertGreaterEqual(self.repo_map["file_count"], 2)
        self.assertEqual(len(self.repo_map["fingerprint"]), 64)
        app = next(item for item in self.repo_map["files"] if item["path"] == "src/app.py")
        self.assertIn("answer", app["symbols"])

    def test_fast_low_risk_routes_to_fast_builder(self) -> None:
        task_dir = self.make_task(
            mode="fast",
            risk="low",
            allowed_files=["src/app.py"],
            uncertainty=0,
            verification=[],
        )
        decision = decide_route(self.root, load_task(task_dir), self.repo_map)
        self.assertEqual(decision.builder, "fast_builder")
        self.assertIsNone(decision.gate)

    def test_high_risk_routes_to_deep_builder_and_gate(self) -> None:
        task_dir = self.make_task(mode="assured", risk="high")
        decision = decide_route(self.root, load_task(task_dir), self.repo_map)
        self.assertEqual(decision.builder, "deep_builder")
        self.assertEqual(decision.reviewer, "independent_reviewer")
        self.assertEqual(decision.gate, "release_gate")

    def test_scope_and_evidence_are_enforced(self) -> None:
        task_dir = self.make_task(mode="fast", risk="low", uncertainty=0, allowed_files=["src/app.py"])
        (self.root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        blocked = check_task(self.root, task_dir)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["missing_verification"], ["python -m unittest"])
        record_evidence(task_dir, "python -m unittest", 0, "tests passed")
        ready = check_task(self.root, task_dir)
        self.assertTrue(ready["ok"])

    def test_out_of_scope_file_blocks_completion(self) -> None:
        task_dir = self.make_task(mode="fast", risk="low", uncertainty=0, allowed_files=["src/app.py"], verification=[])
        (self.root / "README.md").write_text("changed\n", encoding="utf-8")
        result = check_task(self.root, task_dir)
        self.assertIn("README.md", result["out_of_scope"])
        self.assertFalse(result["ok"])

    def test_review_becomes_stale_after_change(self) -> None:
        task_dir = self.make_task(verification=[])
        (self.root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "reviewer", "correct")
        first = check_task(self.root, task_dir)
        self.assertEqual(first["stale_reviews"], [])
        self.assertTrue(first["ok"])
        (self.root / "src" / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
        second = check_task(self.root, task_dir)
        self.assertIn("independent", second["stale_reviews"])
        self.assertFalse(second["ok"])

    def test_fable_is_disabled_and_not_a_gate_by_default(self) -> None:
        config = json.loads((self.root / ".sera" / "config.json").read_text())
        fable = config["lanes"]["optional_fable"]
        self.assertFalse(fable["enabled"])
        self.assertEqual(fable["provider"], "anthropic")
        self.assertFalse(fable["may_be_sole_release_gate"])

    def test_disabled_required_lane_never_silently_falls_back(self) -> None:
        task_dir = self.make_task(mode="assured", risk="high")
        config_path = self.root / ".sera" / "config.json"
        config = json.loads(config_path.read_text())
        config["lanes"]["deep_builder"]["enabled"] = False
        config_path.write_text(json.dumps(config))
        with self.assertRaises(SeraError):
            decide_route(self.root, load_task(task_dir), self.repo_map)

    def test_budget_reports_context_reuse(self) -> None:
        task_dir = self.make_task(mode="fast", risk="low", uncertainty=0, allowed_files=["src/app.py"], verification=[])
        report = budget_report(self.root, task_dir)
        self.assertGreater(report["orientation_tokens"], 0)
        self.assertGreaterEqual(report["estimated_orientation_tokens_avoided"], 0)

    def test_untracked_file_changes_review_fingerprint(self) -> None:
        task_dir = self.make_task(
            mode="standard",
            risk="medium",
            allowed_files=["src/new_file.py"],
            verification=[],
        )
        new_file = self.root / "src" / "new_file.py"
        new_file.write_text("VALUE = 1\n", encoding="utf-8")
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "reviewer", "correct")
        self.assertFalse(check_task(self.root, task_dir)["stale_reviews"])
        new_file.write_text("VALUE = 2\n", encoding="utf-8")
        self.assertIn("independent", check_task(self.root, task_dir)["stale_reviews"])

    def test_seal_is_created_only_after_required_reviews(self) -> None:
        task_dir = self.make_task(mode="standard", risk="medium", verification=[])
        (self.root / "src" / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        with self.assertRaises(SeraError):
            create_seal(self.root, task_dir)
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "reviewer", "correct")
        seal = create_seal(self.root, task_dir)
        self.assertEqual(seal["fingerprint"], fingerprint)
        result = check_task(self.root, task_dir)
        self.assertFalse(result["seal_stale"])
        (self.root / "src" / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
        self.assertTrue(check_task(self.root, task_dir)["seal_stale"])


if __name__ == "__main__":
    unittest.main()
