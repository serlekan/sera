"""Confirming ownership re-runs risk policy and rerouting."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import auto_task, confirm_task_ownership
from sera.core import build_repo_map, decide_route, initialize, load_task, new_task


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class OwnershipPolicyTests(unittest.TestCase):
    """Reproduces the fail-open routing defect: ownership replacement used to
    keep a low-risk, review-free route onto a high-risk path."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "docs").mkdir()
        (self.root / "critical").mkdir()
        (self.root / "docs" / "note.py").write_text("NOTE = 1\n", encoding="utf-8")
        (self.root / "docs" / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        (self.root / "critical" / "secret.py").write_text("TOKEN = 'x'\n", encoding="utf-8")
        initialize(self.root)
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["default_mode"] = "fast"
        config["risk_policy"] = {"high_risk_terms": [], "high_risk_paths": ["critical/**"]}
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def low_risk_task(self, **overrides: object) -> Path:
        values: dict = {
            "name": "low risk task",
            "objective": "low-risk task",
            "allowed_files": ["docs/note.py"],
        }
        values.update(overrides)
        return new_task(self.root, **values)

    def route(self, task_dir: Path):
        return decide_route(self.root, load_task(task_dir))

    def test_confirming_a_high_risk_path_escalates_and_reroutes(self) -> None:
        task_dir = self.low_risk_task()
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "low")
        self.assertEqual(task["mode"], "fast")

        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])

        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")
        decision = self.route(task_dir)
        self.assertEqual(decision.builder, "deep_builder")
        self.assertEqual(decision.reviewer, "independent_reviewer")
        self.assertEqual(decision.gate, "release_gate")

    def test_risk_is_not_permanently_sticky_after_reverting_ownership(self) -> None:
        task_dir = self.low_risk_task()
        confirm_task_ownership(self.root, task_dir, ["docs/note.py"])
        self.assertEqual(load_task(task_dir)["risk"], "low")

        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        self.assertEqual(load_task(task_dir)["risk"], "high")

        confirm_task_ownership(self.root, task_dir, ["docs/note.py"])
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "low")
        self.assertEqual(task["mode"], "fast")
        self.assertEqual(task["mode_source"], "config")

    def test_explicit_high_risk_is_a_floor_that_survives_ownership_changes(self) -> None:
        task_dir = self.low_risk_task(risk="high")
        self.assertEqual(load_task(task_dir)["mode"], "assured")

        confirm_task_ownership(self.root, task_dir, ["docs/other.py"])

        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")
        self.assertEqual(task["requested_risk"], "high")

    def test_explicit_mode_override_survives_ownership_changes(self) -> None:
        task_dir = self.low_risk_task(mode="standard")
        confirm_task_ownership(self.root, task_dir, ["docs/other.py"])
        task = load_task(task_dir)
        self.assertEqual(task["mode"], "standard")
        self.assertEqual(task["mode_source"], "explicit")

    def test_objective_risk_terms_survive_ownership_changes(self) -> None:
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["risk_policy"]["high_risk_terms"] = ["critical_rule"]
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        task_dir = self.low_risk_task(objective="adjust critical_rule threshold")
        self.assertEqual(load_task(task_dir)["risk"], "high")

        confirm_task_ownership(self.root, task_dir, ["docs/other.py"])
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertIn({"type": "project_term", "value": "critical_rule"}, task["risk_reasons"])

    def test_one_risky_file_among_many_escalates(self) -> None:
        task_dir = self.low_risk_task()
        confirm_task_ownership(
            self.root, task_dir, ["docs/note.py", "docs/other.py", "critical/secret.py"]
        )
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")

    def test_risk_reasons_reflect_current_ownership_only(self) -> None:
        task_dir = self.low_risk_task()
        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        escalated = load_task(task_dir)["risk_reasons"]
        self.assertTrue(any(item["type"] == "project_path" for item in escalated))

        confirm_task_ownership(self.root, task_dir, ["docs/note.py"])
        current = load_task(task_dir)["risk_reasons"]
        self.assertFalse(
            any(item["type"] == "project_path" for item in current),
            msg=f"obsolete project-path reason retained: {current}",
        )

    def test_auto_drafted_task_reroutes_on_confirmation(self) -> None:
        task_dir, _ = auto_task(self.root, "low-risk task")
        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")
        self.assertTrue(task["controller"]["ownership_confirmed"])
        self.assertEqual(task["controller"]["risk_reasons"], task["risk_reasons"])


if __name__ == "__main__":
    unittest.main()
