"""Every changed file must reach the reviewer with real change material."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet, next_action
from sera.core import (
    REVIEW_DIFF_BUDGET_INSUFFICIENT,
    SeraError,
    build_repo_map,
    compact_diff,
    initialize,
    new_task,
    review_diff_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class ReviewDiffCoverageTests(unittest.TestCase):
    """Reproduces the truncation defect: with three changed files the packet
    showed `a.py` and reduced `b.py`/`c.py` to bare filenames."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        for name in ("a", "b", "c"):
            (self.root / f"{name}.py").write_text(f"VALUE_{name.upper()} = 0\n" * 40, encoding="utf-8")
        initialize(self.root)
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

    def make_task(self, owned: list[str]) -> Path:
        return new_task(
            self.root, "three file change", "adjust the marker values",
            "standard", "medium", owned, [], [], 1, "implementation",
        )

    def change_three(self) -> list[str]:
        markers = {}
        for name in ("a", "b", "c"):
            marker = f"UNIQUE_MARKER_{name.upper()}_42"
            (self.root / f"{name}.py").write_text(
                f"VALUE_{name.upper()} = 1\n{marker} = True\n" * 40, encoding="utf-8"
            )
            markers[name] = marker
        return [markers[name] for name in ("a", "b", "c")]

    def test_all_three_changed_files_carry_real_change_material(self) -> None:
        markers = self.change_three()
        owned = ["a.py", "b.py", "c.py"]
        # Deliberately tight: the old head/tail algorithm dropped the middle.
        coverage = review_diff_coverage(self.root, owned, 3_000)
        self.assertTrue(coverage["ok"])
        for path in owned:
            self.assertIn(path, coverage["text"])
        for marker in markers:
            self.assertIn(
                marker, coverage["text"], msg=f"{marker} absent — a changed file lost its content"
            )

    def test_middle_files_survive_with_many_changed_files(self) -> None:
        names = [f"m{index:02d}.py" for index in range(12)]
        for name in names:
            (self.root / name).write_text("X = 0\n" * 30, encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "more files")
        for index, name in enumerate(names):
            (self.root / name).write_text(f"X = 1\nMARK_{index:02d}_HERE = 1\n" * 30, encoding="utf-8")
        coverage = review_diff_coverage(self.root, names, 20_000)
        self.assertTrue(coverage["ok"])
        for index in range(12):
            self.assertIn(f"MARK_{index:02d}_HERE", coverage["text"])

    def test_insufficient_budget_fails_closed_instead_of_hiding_files(self) -> None:
        self.change_three()
        owned = ["a.py", "b.py", "c.py"]
        coverage = review_diff_coverage(self.root, owned, 200)
        self.assertFalse(coverage["ok"])
        self.assertEqual(coverage["reason"], REVIEW_DIFF_BUDGET_INSUFFICIENT)
        self.assertGreater(coverage["required_chars"], 200)
        with self.assertRaises(SeraError) as caught:
            compact_diff(self.root, owned, 200)
        self.assertIn(REVIEW_DIFF_BUDGET_INSUFFICIENT, str(caught.exception))

    def test_review_packet_generation_fails_rather_than_omitting(self) -> None:
        self.change_three()
        self.set_packet_chars(200)
        task_dir = self.make_task(["a.py", "b.py", "c.py"])
        with self.assertRaises(SeraError):
            build_packet(self.root, task_dir, "review")

    def test_controller_reports_insufficient_review_budget(self) -> None:
        self.set_packet_chars(200)
        # The task must exist before the edits so they count as task changes
        # rather than pre-existing dirty baseline.
        task_dir = self.make_task(["a.py", "b.py", "c.py"])
        self.change_three()
        state = next_action(self.root, task_dir)
        self.assertEqual(state["state"], "review_diff_budget_insufficient")

    def test_one_huge_file_does_not_starve_the_others(self) -> None:
        (self.root / "huge.py").write_text("H = 0\n" * 20, encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "add huge")
        (self.root / "huge.py").write_text("H = 1\nHUGE_MARKER = 1\n" * 4_000, encoding="utf-8")
        markers = self.change_three()
        owned = ["a.py", "b.py", "c.py", "huge.py"]
        coverage = review_diff_coverage(self.root, owned, 8_000)
        self.assertTrue(coverage["ok"])
        for marker in markers:
            self.assertIn(marker, coverage["text"])
        self.assertIn("HUGE_MARKER", coverage["text"])
        self.assertIn("omitted from this file by review budget", coverage["text"])

    def test_binary_file_gets_deterministic_representation(self) -> None:
        (self.root / "blob.bin").write_bytes(bytes(range(256)) * 4)
        git(self.root, "add", "blob.bin")
        git(self.root, "commit", "-m", "add binary")
        (self.root / "blob.bin").write_bytes(bytes(range(255, -1, -1)) * 4)
        coverage = review_diff_coverage(self.root, ["blob.bin"], 8_000)
        self.assertTrue(coverage["ok"])
        self.assertIn("blob.bin", coverage["text"])
        self.assertIn("binary/non-text", coverage["text"])

    def test_added_deleted_and_renamed_files_are_represented(self) -> None:
        (self.root / "added.py").write_text("ADDED_MARKER = 1\n", encoding="utf-8")
        (self.root / "a.py").unlink()
        git(self.root, "mv", "b.py", "renamed.py")
        owned = ["added.py", "a.py", "b.py", "renamed.py"]
        coverage = review_diff_coverage(self.root, owned, 20_000)
        self.assertTrue(coverage["ok"])
        self.assertIn("added.py", coverage["text"])
        self.assertIn("ADDED_MARKER", coverage["text"])
        self.assertIn("a.py", coverage["text"])
        self.assertIn("status: deleted", coverage["text"])
        self.assertIn("renamed.py", coverage["text"])

    def test_coverage_is_deterministic(self) -> None:
        self.change_three()
        owned = ["a.py", "b.py", "c.py"]
        first = review_diff_coverage(self.root, owned, 6_000)
        second = review_diff_coverage(self.root, owned, 6_000)
        self.assertEqual(first["text"], second["text"])
        self.assertEqual(first["covered"], second["covered"])

    def test_staged_and_unstaged_changes_are_both_represented(self) -> None:
        self.change_three()
        git(self.root, "add", "b.py")
        coverage = review_diff_coverage(self.root, ["a.py", "b.py", "c.py"], 10_000)
        self.assertTrue(coverage["ok"])
        self.assertIn("UNIQUE_MARKER_B_42", coverage["text"])
        self.assertIn("staged", coverage["text"])


if __name__ == "__main__":
    unittest.main()
