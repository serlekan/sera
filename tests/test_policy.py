"""Mode precedence, project risk policy, and high-risk routing."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import auto_task, infer_risk, prepare_run
from sera.core import (
    BUILTIN_DEFAULT_MODE,
    SeraError,
    assess_risk,
    build_repo_map,
    decide_route,
    initialize,
    load_config,
    load_task,
    new_task,
    path_matches,
    phrase_matches,
    resolve_mode,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class ModePrecedenceTests(unittest.TestCase):
    """`explicit CLI mode > configured default_mode > built-in fallback`."""

    def test_each_configured_mode_is_honored(self) -> None:
        for mode in ("fast", "standard", "assured"):
            with self.subTest(mode=mode):
                resolved, source = resolve_mode({"default_mode": mode})
                self.assertEqual(resolved, mode)
                self.assertEqual(source, "config")

    def test_explicit_override_beats_configuration(self) -> None:
        resolved, source = resolve_mode({"default_mode": "fast"}, "assured")
        self.assertEqual(resolved, "assured")
        self.assertEqual(source, "explicit")

    def test_auto_is_not_an_override(self) -> None:
        resolved, source = resolve_mode({"default_mode": "assured"}, "auto")
        self.assertEqual(resolved, "assured")
        self.assertEqual(source, "config")

    def test_absent_configuration_uses_builtin_fallback(self) -> None:
        resolved, source = resolve_mode({})
        self.assertEqual(resolved, BUILTIN_DEFAULT_MODE)
        self.assertEqual(resolved, "standard")
        self.assertEqual(source, "builtin")

    def test_invalid_configured_mode_fails_closed(self) -> None:
        with self.assertRaises(SeraError):
            resolve_mode({"default_mode": "turbo"})

    def test_invalid_explicit_mode_fails_closed(self) -> None:
        with self.assertRaises(SeraError):
            resolve_mode({"default_mode": "standard"}, "turbo")


class RiskVocabularyTests(unittest.TestCase):
    def test_phrase_matching_is_case_insensitive(self) -> None:
        self.assertTrue(phrase_matches("Execution", "adjust the EXECUTION path"))

    def test_non_matching_term_does_not_fire(self) -> None:
        self.assertFalse(phrase_matches("execution", "adjust the reporting path"))

    def test_multi_word_phrase_matches_contiguously(self) -> None:
        self.assertTrue(phrase_matches("production deployment", "block the production deployment step"))
        self.assertFalse(phrase_matches("production deployment", "production readiness and deployment notes"))

    def test_substring_collisions_do_not_match(self) -> None:
        # `order` must not fire on `reorder`; this is the specific accidental
        # substring case that token-aware matching exists to prevent.
        self.assertFalse(phrase_matches("order", "reorder the dashboard widgets"))
        self.assertTrue(phrase_matches("order", "cancel the order"))
        self.assertTrue(phrase_matches("order", "src/order_book.py"))

    def test_project_terms_augment_builtin_vocabulary(self) -> None:
        config = {"risk_policy": {"high_risk_terms": ["critical_rule"]}}
        project = assess_risk(config, "change critical_rule threshold")
        self.assertEqual(project["risk"], "high")
        self.assertIn({"type": "project_term", "value": "critical_rule"}, project["reasons"])

        builtin = assess_risk(config, "rotate the payment secret")
        self.assertEqual(builtin["risk"], "high")
        self.assertTrue(any(item["type"] == "builtin_term" for item in builtin["reasons"]))

    def test_generic_defaults_carry_no_project_vocabulary(self) -> None:
        neutral = assess_risk({}, "change critical_rule threshold")
        self.assertEqual(neutral["risk"], "low")

    def test_infer_risk_renders_reasons_as_text(self) -> None:
        risk, reasons = infer_risk("Change payment reconciliation state")
        self.assertEqual(risk, "high")
        self.assertIn("high-risk term: payment", reasons)


class RiskPathTests(unittest.TestCase):
    def test_pattern_syntax(self) -> None:
        self.assertTrue(path_matches("src/payments/gateway.py", "src/payments/**"))
        self.assertTrue(path_matches("src/payments/eu/sepa.py", "src/payments/**"))
        self.assertFalse(path_matches("src/payments_legacy.py", "src/payments/**"))
        self.assertTrue(path_matches("app/db/migrations/001.sql", "**/migrations/**"))
        self.assertTrue(path_matches("migrations/001.sql", "**/migrations/**"))
        self.assertTrue(path_matches("src/auth/login.py", "src/auth/*.py"))
        self.assertFalse(path_matches("src/auth/oidc/login.py", "src/auth/*.py"))

    def test_matching_file_escalates_and_explains(self) -> None:
        config = {"risk_policy": {"high_risk_paths": ["trading/risk/**"]}}
        result = assess_risk(config, "tune thresholds", ["trading/risk/limits.py"])
        self.assertEqual(result["risk"], "high")
        reason = next(item for item in result["reasons"] if item["type"] == "project_path")
        self.assertEqual(reason["value"], "trading/risk/**")
        self.assertEqual(reason["matched"], "trading/risk/limits.py")

    def test_non_matching_file_does_not_escalate(self) -> None:
        config = {"risk_policy": {"high_risk_paths": ["trading/risk/**"]}}
        result = assess_risk(config, "tune thresholds", ["docs/notes.md"])
        self.assertEqual(result["risk"], "low")

    def test_one_matching_file_among_many_escalates(self) -> None:
        config = {"risk_policy": {"high_risk_paths": ["trading/risk/**"]}}
        owned = ["docs/a.md", "src/ui/b.py", "trading/risk/limits.py", "src/ui/c.py"]
        result = assess_risk(config, "tune thresholds", owned)
        self.assertEqual(result["risk"], "high")


class RiskCompositionTests(unittest.TestCase):
    def test_explicit_low_cannot_downgrade_detected_high(self) -> None:
        result = assess_risk({}, "rotate the production secret", explicit_risk="low")
        self.assertEqual(result["risk"], "high")
        self.assertIn({"type": "explicit_risk_not_applied", "value": "low"}, result["reasons"])

    def test_explicit_high_can_upgrade_quiet_work(self) -> None:
        result = assess_risk({}, "rename a local variable", explicit_risk="high")
        self.assertEqual(result["risk"], "high")
        self.assertIn({"type": "explicit_risk", "value": "high"}, result["reasons"])

    def test_effective_risk_is_the_maximum_of_all_sources(self) -> None:
        config = {"risk_policy": {"high_risk_paths": ["migrations/**"]}}
        result = assess_risk(config, "tidy formatting", ["migrations/003.sql"], explicit_risk="low")
        self.assertEqual(result["risk"], "high")

    def test_invalid_explicit_risk_fails_closed(self) -> None:
        with self.assertRaises(SeraError):
            assess_risk({}, "anything", explicit_risk="catastrophic")


class RepositoryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "trading" / "risk").mkdir(parents=True)
        (self.root / "src" / "report.py").write_text("def render():\n    return 1\n", encoding="utf-8")
        (self.root / "trading" / "risk" / "limits.py").write_text("LIMIT = 1\n", encoding="utf-8")
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_config(self, **updates: object) -> None:
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config.update(updates)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def test_configured_default_mode_reaches_the_created_task(self) -> None:
        for mode in ("fast", "standard", "assured"):
            with self.subTest(mode=mode):
                self.write_config(default_mode=mode)
                task_dir, _ = auto_task(self.root, "render the report", files=["src/report.py"])
                task = load_task(task_dir)
                self.assertEqual(task["mode"], mode)
                self.assertEqual(task["mode_source"], "config")

    def test_explicit_cli_mode_overrides_configured_default(self) -> None:
        self.write_config(default_mode="fast")
        task_dir, _ = auto_task(self.root, "render the report", mode="assured", files=["src/report.py"])
        task = load_task(task_dir)
        self.assertEqual(task["mode"], "assured")
        self.assertEqual(task["mode_source"], "explicit")

    def test_invalid_configured_mode_fails_closed_on_load(self) -> None:
        self.write_config(default_mode="turbo")
        with self.assertRaises(SeraError):
            load_config(self.root)

    def test_project_risk_path_escalates_task_to_assured(self) -> None:
        self.write_config(
            default_mode="fast",
            risk_policy={"high_risk_terms": [], "high_risk_paths": ["trading/risk/**"]},
        )
        task_dir, _ = auto_task(self.root, "tune limits", files=["trading/risk/limits.py"])
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")
        self.assertIn({"type": "mode_escalation", "value": "assured"}, task["risk_reasons"])

    def test_high_risk_can_never_route_fast(self) -> None:
        self.write_config(
            default_mode="standard",
            risk_policy={"high_risk_terms": ["critical_rule"], "high_risk_paths": []},
        )
        # Even an explicit `--mode fast` is escalated rather than honored.
        task_dir, _ = auto_task(
            self.root, "change critical_rule threshold", mode="fast", files=["src/report.py"]
        )
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")

    def test_risk_reasons_are_machine_readable_in_run_output(self) -> None:
        self.write_config(
            default_mode="assured",
            risk_policy={"high_risk_terms": ["critical_rule"], "high_risk_paths": []},
        )
        report = prepare_run(self.root, "change critical_rule threshold", files=["src/report.py"])
        self.assertEqual(report["risk"], "high")
        self.assertIn({"type": "project_term", "value": "critical_rule"}, report["risk_reasons"])

    def test_explicit_task_creation_shares_the_same_resolver(self) -> None:
        self.write_config(default_mode="assured")
        task_dir = new_task(self.root, "quiet fix", "adjust the report", allowed_files=["src/report.py"])
        task = load_task(task_dir)
        self.assertEqual(task["mode"], "assured")
        self.assertEqual(task["risk"], "low")

    def test_required_lane_disabled_still_fails_closed(self) -> None:
        self.write_config(
            default_mode="assured",
            risk_policy={"high_risk_terms": ["critical_rule"], "high_risk_paths": []},
        )
        task_dir, _ = auto_task(self.root, "change critical_rule threshold", files=["src/report.py"])
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["lanes"]["release_gate"]["enabled"] = False
        path.write_text(json.dumps(config), encoding="utf-8")
        with self.assertRaises(SeraError) as caught:
            decide_route(self.root, load_task(task_dir))
        self.assertIn("no silent fallback", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
