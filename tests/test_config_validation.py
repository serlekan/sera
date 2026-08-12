"""Malformed configuration fails closed through the normal SERA error path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sera.core import SeraError, initialize, load_config


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class ConfigValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.config_path = self.root / ".sera" / "config.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write(self, payload: dict) -> None:
        self.config_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def assert_rejected(self, payload: dict) -> str:
        self.write(payload)
        with self.assertRaises(SeraError) as caught:
            load_config(self.root)
        return str(caught.exception)

    def test_null_controller_is_rejected(self) -> None:
        message = self.assert_rejected({"controller": None})
        self.assertIn("controller", message)

    def test_list_controller_is_rejected(self) -> None:
        self.assert_rejected({"controller": []})

    def test_null_token_budgets_is_rejected(self) -> None:
        self.assert_rejected({"token_budgets": None})

    def test_non_numeric_token_budget_is_rejected(self) -> None:
        message = self.assert_rejected({"token_budgets": {"fast": "six thousand"}})
        self.assertIn("token_budgets.fast", message)

    def test_negative_token_budget_is_rejected(self) -> None:
        self.assert_rejected({"token_budgets": {"fast": -1}})

    def test_zero_token_budget_is_rejected(self) -> None:
        self.assert_rejected({"token_budgets": {"fast": 0}})

    def test_boolean_token_budget_is_rejected(self) -> None:
        # bool subclasses int; a flag is never a valid budget.
        self.assert_rejected({"token_budgets": {"fast": True}})

    def test_null_risk_policy_is_rejected(self) -> None:
        # An explicit null is malformed; omitting the key accepts the default.
        self.assert_rejected({"risk_policy": None})

    def test_wrong_typed_risk_policy_lists_are_rejected(self) -> None:
        self.assert_rejected({"risk_policy": {"high_risk_terms": "payment"}})
        self.assert_rejected({"risk_policy": {"high_risk_paths": [1, 2]}})

    def test_invalid_default_mode_is_rejected(self) -> None:
        message = self.assert_rejected({"default_mode": "turbo"})
        self.assertIn("default_mode", message)

    def test_malformed_controller_scalars_are_rejected(self) -> None:
        self.assert_rejected({"controller": {"context_max_files": "twelve"}})
        self.assert_rejected({"controller": {"context_max_files": 0}})
        self.assert_rejected({"controller": {"auto_risk": "yes"}})
        self.assert_rejected({"controller": {"enforce_context_budget": 1}})

    def test_malformed_lanes_are_rejected(self) -> None:
        self.assert_rejected({"lanes": None})
        self.assert_rejected({"lanes": {"planner": None}})
        self.assert_rejected({"lanes": {"planner": {"enabled": "true"}}})
        self.assert_rejected({"lanes": {"planner": {"provider": 5}}})

    def test_malformed_scalars_are_rejected(self) -> None:
        self.assert_rejected({"max_packet_chars": "lots"})
        self.assert_rejected({"max_file_bytes": 0})
        self.assert_rejected({"max_builder_attempts": -3})
        self.assert_rejected({"exclude_dirs": "node_modules"})

    def test_valid_configuration_still_loads(self) -> None:
        config = load_config(self.root)
        self.assertEqual(config["default_mode"], "standard")
        self.assertEqual(config["token_budgets"]["assured"], 32_000)


class ConfigErrorCliTests(unittest.TestCase):
    """No expected configuration mistake may escape as a Python traceback."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "app.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        subprocess.run(
            [sys.executable, "-m", "sera", "init"], cwd=self.root, env=self.env, capture_output=True, check=True
        )
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.config_path = self.root / ".sera" / "config.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "sera", *args],
            cwd=self.root, env=self.env, text=True, capture_output=True,
        )

    def assert_controlled_failure(self, payload: dict, *args: str) -> None:
        current = json.loads(self.config_path.read_text(encoding="utf-8"))
        current.update(payload)
        self.config_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 2, msg=result.stdout + result.stderr)
        self.assertIn("error:", result.stderr)
        combined = result.stdout + result.stderr
        self.assertNotIn("Traceback (most recent call last)", combined)
        for leak in ("AttributeError", "ValueError", "TypeError", "KeyError"):
            self.assertNotIn(leak, combined, msg=f"{leak} leaked: {combined}")

    def test_null_controller_produces_a_controlled_error(self) -> None:
        self.assert_controlled_failure({"controller": None}, "map")

    def test_bad_token_budget_produces_a_controlled_error(self) -> None:
        self.assert_controlled_failure({"token_budgets": {"fast": "six thousand"}}, "map")

    def test_bad_config_fails_controlled_on_run(self) -> None:
        self.assert_controlled_failure({"controller": None}, "run", "adjust the app")


if __name__ == "__main__":
    unittest.main()
