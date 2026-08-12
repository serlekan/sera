"""Ownership and stage context are independent budgets."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.context import (
    REASON_BUDGET,
    REASON_CHANGED,
    REASON_NOT_IN_MAP,
    REASON_OWNED_NOT_SELECTED,
    select_task_context,
)
from sera.controller import context_report, next_action, build_packet
from sera.core import build_repo_map, initialize, load_task, new_task

OWNED_FILE_COUNT = 36
FILE_CHARS = 8_000


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class EngineRepository:
    """A repository whose total ownership dwarfs any single stage budget."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "engine").mkdir()
        self.owned = []
        for index in range(OWNED_FILE_COUNT):
            relative = f"engine/module_{index:02d}.py"
            body = f"def engine_step_{index:02d}():\n    return {index}\n"
            padding = "# engine detail\n" * (FILE_CHARS // 16)
            (self.root / relative).write_text(body + padding, encoding="utf-8")
            self.owned.append(relative)
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.repo_map = build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def set_controller(self, **updates: object) -> None:
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config.setdefault("controller", {}).update(updates)
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def make_task(self, owned: list[str] | None = None) -> Path:
        return new_task(
            self.root,
            "engine sweep",
            "adjust the engine step sequence",
            "assured",
            "medium",
            owned if owned is not None else self.owned,
            [],
            [],
            1,
            "implementation",
        )


class OwnershipVersusContextTests(EngineRepository, unittest.TestCase):
    """Reproduces the integration failure: a large owned set must not be read
    as an oversized packet."""

    def test_large_ownership_fits_a_small_stage_context(self) -> None:
        task_dir = self.make_task()
        report = context_report(self.root, task_dir, stage="build", record=False)
        budget = report["stage_budget_tokens"]

        self.assertEqual(report["ownership"]["file_count"], OWNED_FILE_COUNT)
        self.assertGreater(report["ownership"]["estimated_tokens"], budget)
        self.assertLess(report["selected_context"]["file_count"], OWNED_FILE_COUNT)
        self.assertLessEqual(report["estimated_context_tokens"], budget)
        self.assertTrue(report["within_budget"])

    def test_ownership_survives_context_exclusion(self) -> None:
        task_dir = self.make_task()
        context_report(self.root, task_dir, stage="build", record=False)
        task = load_task(task_dir)
        self.assertEqual(task["allowed_files"], sorted(self.owned))
        self.assertEqual(len(task["allowed_files"]), OWNED_FILE_COUNT)

    def test_controller_progresses_past_reduce_context(self) -> None:
        task_dir = self.make_task()
        state = next_action(self.root, task_dir)
        self.assertNotEqual(state["state"], "reduce_context")
        self.assertEqual(state["state"], "build_packet")
        self.assertGreater(state["ownership"]["estimated_tokens"], state["stage_budget_tokens"])
        self.assertTrue(state["within_budget"])

    def test_builder_and_reviewer_contexts_may_differ(self) -> None:
        task_dir = self.make_task()
        changed = self.owned[30:33]
        for relative in changed:
            (self.root / relative).write_text(f"# rewritten {relative}\n", encoding="utf-8")

        builder = select_task_context(self.root, task_dir, "build")
        reviewer = select_task_context(self.root, task_dir, "review")
        builder_paths = [item["path"] for item in builder["selected_files"]]
        reviewer_paths = [item["path"] for item in reviewer["selected_files"]]

        self.assertNotEqual(builder_paths, reviewer_paths)
        for relative in changed:
            self.assertIn(relative, reviewer_paths)
        reasons = {item["path"]: item["reason"] for item in reviewer["selected_files"]}
        self.assertEqual(reasons[changed[0]], REASON_CHANGED)
        self.assertGreater(reviewer["diff_tokens"], 0)
        self.assertEqual(builder["diff_tokens"], 0)

    def test_every_changed_file_is_represented_in_the_review_packet(self) -> None:
        task_dir = self.make_task()
        changed = self.owned[5:12]
        for relative in changed:
            (self.root / relative).write_text(f"# rewritten {relative}\n", encoding="utf-8")
        _, text = build_packet(self.root, task_dir, "review")
        for relative in changed:
            self.assertIn(relative, text, msg=f"{relative} is missing from the review packet")

    def test_selection_is_deterministic(self) -> None:
        task_dir = self.make_task()
        first = select_task_context(self.root, task_dir, "build")
        second = select_task_context(self.root, task_dir, "build")
        self.assertEqual(
            [item["path"] for item in first["selected_files"]],
            [item["path"] for item in second["selected_files"]],
        )


class ContextReasonTaxonomyTests(EngineRepository, unittest.TestCase):
    def test_owned_but_unselected_files_are_labelled_accurately(self) -> None:
        task_dir = self.make_task()
        report = select_task_context(self.root, task_dir, "build")
        excluded = {item["path"]: item["reason"] for item in report["excluded_files"]}
        selected = {item["path"] for item in report["selected_files"]}

        self.assertTrue(excluded, "a 36-file task must report unselected owned files")
        self.assertEqual(set(excluded) | selected, set(self.owned))
        self.assertIn(REASON_OWNED_NOT_SELECTED, set(excluded.values()))

    def test_mapped_files_are_never_reported_as_missing_from_the_map(self) -> None:
        # The 0.4.0 regression: unselected owned files were labelled
        # `not present in the current repository map` even though they were
        # indexed and merely outside the context cap.
        task_dir = self.make_task()
        report = select_task_context(self.root, task_dir, "build")
        mapped = {item["path"] for item in self.repo_map["files"]}
        for item in report["excluded_files"]:
            self.assertIn(item["path"], mapped)
            self.assertNotEqual(
                item["reason"],
                REASON_NOT_IN_MAP,
                msg=f"{item['path']} is in the repository map but was labelled missing",
            )

    def test_genuinely_absent_paths_are_labelled_not_in_repository_map(self) -> None:
        task_dir = self.make_task(owned=[*self.owned[:3], "engine/never_created.py"])
        report = select_task_context(self.root, task_dir, "build")
        excluded = {item["path"]: item["reason"] for item in report["excluded_files"]}
        self.assertEqual(excluded.get("engine/never_created.py"), REASON_NOT_IN_MAP)

    def test_budget_exclusion_is_distinguished_from_cap_exclusion(self) -> None:
        # Raise the file cap so the token budget becomes the binding constraint.
        self.set_controller(context_max_files=OWNED_FILE_COUNT + 5)
        task_dir = self.make_task()
        report = select_task_context(self.root, task_dir, "build")
        reasons = {item["reason"] for item in report["excluded_files"]}
        self.assertIn(REASON_BUDGET, reasons)
        self.assertTrue(report["within_budget"])
        self.assertLessEqual(report["estimated_context_tokens"], report["stage_budget_tokens"])

    def test_reasons_are_recorded_in_the_context_ledger(self) -> None:
        task_dir = self.make_task()
        context_report(self.root, task_dir, stage="build", record=True)
        lines = (task_dir / "context-ledger.jsonl").read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        self.assertIn("ownership", record)
        self.assertIn("selected_context", record)
        self.assertTrue(all("reason" in item for item in record["files"]))
        self.assertTrue(all("reason" in item for item in record["excluded"]))


class SingleOversizedFileTests(unittest.TestCase):
    """A stage that cannot fit even its top-priority file must still block."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "src" / "huge.py").write_text("x = '" + ("a" * 60_000) + "'\n", encoding="utf-8")
        initialize(self.root)
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_context_that_cannot_fit_still_reports_reduce_context(self) -> None:
        task_dir = new_task(
            self.root, "huge fix", "adjust the huge helper", "fast", "low",
            ["src/huge.py"], [], [], 0, "implementation",
        )
        report = context_report(self.root, task_dir, stage="build", record=False)
        self.assertFalse(report["within_budget"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "reduce_context")


if __name__ == "__main__":
    unittest.main()
