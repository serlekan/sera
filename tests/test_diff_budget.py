"""Reported review-diff success guarantees an exact rendered character budget."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.core import (
    REVIEW_DIFF_BUDGET_INSUFFICIENT,
    initialize,
    review_diff_coverage,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class DiffBudgetRepository(unittest.TestCase):
    """`max_chars` counts Python string characters (Unicode code points)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        initialize(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, message: str = "baseline") -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", message)

    def assert_within(self, owned: list[str], max_chars: int) -> dict:
        """The core contract: ok=True implies len(text) <= max_chars, exactly."""
        coverage = review_diff_coverage(self.root, owned, max_chars)
        if coverage["ok"]:
            self.assertLessEqual(
                len(coverage["text"]), max_chars,
                msg=f"rendered {len(coverage['text'])} chars against a {max_chars} budget",
            )
            self.assertEqual(coverage["rendered_chars"], len(coverage["text"]))
        else:
            self.assertEqual(coverage["reason"], REVIEW_DIFF_BUDGET_INSUFFICIENT)
        return coverage

    def minimum_for(self, owned: list[str]) -> int:
        return review_diff_coverage(self.root, owned, 1)["required_chars"]


class FiftyFileTests(DiffBudgetRepository):
    """The exact reviewer reproduction: 50 changed files reported ok=True while
    the rendered text ran 2,898 characters over the stated budget."""

    FILES = 50

    def setUp(self) -> None:
        super().setUp()
        for index in range(self.FILES):
            (self.root / f"mod_{index:02d}.py").write_text(f"VALUE_{index:02d} = 0\n" * 25, encoding="utf-8")
        self.commit()
        for index in range(self.FILES):
            (self.root / f"mod_{index:02d}.py").write_text(
                f"VALUE_{index:02d} = 1\nCHANGED_MARKER_{index:02d} = True\n" * 25, encoding="utf-8"
            )
        self.owned = [f"mod_{index:02d}.py" for index in range(self.FILES)]

    def test_reviewer_reproduction_budget_is_exact(self) -> None:
        coverage = self.assert_within(self.owned, 25_700)
        if coverage["ok"]:
            self.assertEqual(len(coverage["files"]), self.FILES)

    def test_budget_is_exact_across_a_wide_sweep(self) -> None:
        minimum = self.minimum_for(self.owned)
        for max_chars in (
            minimum, minimum + 1, minimum + 137, minimum + 1_000,
            minimum + 2_898, minimum + 10_000, 25_700, 40_000, 250_000,
        ):
            with self.subTest(max_chars=max_chars):
                self.assert_within(self.owned, max_chars)

    def test_exact_boundary_succeeds(self) -> None:
        minimum = self.minimum_for(self.owned)
        coverage = self.assert_within(self.owned, minimum)
        self.assertTrue(coverage["ok"])
        self.assertEqual(len(coverage["text"]), minimum)

    def test_one_character_under_the_minimum_fails_closed(self) -> None:
        minimum = self.minimum_for(self.owned)
        coverage = review_diff_coverage(self.root, self.owned, minimum - 1)
        self.assertFalse(coverage["ok"])
        self.assertEqual(coverage["reason"], REVIEW_DIFF_BUDGET_INSUFFICIENT)
        self.assertEqual(coverage["text"], "")

    def test_every_changed_file_keeps_representation_at_the_minimum(self) -> None:
        minimum = self.minimum_for(self.owned)
        coverage = self.assert_within(self.owned, minimum)
        for path in self.owned:
            self.assertIn(path, coverage["text"])

    def test_rerender_is_deterministic(self) -> None:
        first = review_diff_coverage(self.root, self.owned, 30_000)
        second = review_diff_coverage(self.root, self.owned, 30_000)
        self.assertEqual(first["text"], second["text"])
        self.assertEqual(first["rendered_chars"], second["rendered_chars"])
        self.assertEqual(first["covered"], second["covered"])


class BudgetEdgeCaseTests(DiffBudgetRepository):
    def test_long_paths_are_counted_exactly(self) -> None:
        deep = Path("very") / "deeply" / "nested" / "package" / "with" / "long" / "directory" / "names"
        (self.root / deep).mkdir(parents=True)
        owned = []
        for index in range(6):
            relative = deep / f"an_extremely_long_module_filename_number_{index:03d}.py"
            (self.root / relative).write_text("X = 0\n" * 40, encoding="utf-8")
            owned.append(relative.as_posix())
        self.commit()
        for relative in owned:
            (self.root / relative).write_text("X = 1\nLONGPATH_MARKER = 1\n" * 40, encoding="utf-8")
        minimum = self.minimum_for(owned)
        for max_chars in (minimum, minimum + 50, minimum + 5_000):
            with self.subTest(max_chars=max_chars):
                self.assert_within(owned, max_chars)

    def test_large_shown_and_total_counters_stay_within_budget(self) -> None:
        # Counter digit width changes as allocation grows; the rendered length
        # is what must be respected, not the raw allocation.
        (self.root / "huge.py").write_text("H = 0\n" * 10, encoding="utf-8")
        self.commit()
        (self.root / "huge.py").write_text("H = 1\nBIG = 1\n" * 30_000, encoding="utf-8")
        minimum = self.minimum_for(["huge.py"])
        for max_chars in (minimum, minimum + 9, minimum + 99, minimum + 999, minimum + 99_999):
            with self.subTest(max_chars=max_chars):
                self.assert_within(["huge.py"], max_chars)

    def test_omission_marker_overhead_is_counted(self) -> None:
        owned = []
        for index in range(4):
            relative = f"big_{index}.py"
            (self.root / relative).write_text("B = 0\n" * 50, encoding="utf-8")
            owned.append(relative)
        self.commit()
        for index, relative in enumerate(owned):
            (self.root / relative).write_text(f"B = 1\nMARK_{index} = 1\n" * 3_000, encoding="utf-8")
        minimum = self.minimum_for(owned)
        coverage = self.assert_within(owned, minimum + 400)
        self.assertTrue(coverage["ok"])
        self.assertIn("omitted from this file by review budget", coverage["text"])

    def test_binary_representation_is_budgeted(self) -> None:
        (self.root / "blob.bin").write_bytes(bytes(range(256)) * 8)
        self.commit()
        (self.root / "blob.bin").write_bytes(bytes(range(255, -1, -1)) * 8)
        minimum = self.minimum_for(["blob.bin"])
        for max_chars in (minimum, minimum + 1_000):
            with self.subTest(max_chars=max_chars):
                coverage = self.assert_within(["blob.bin"], max_chars)
                self.assertTrue(coverage["ok"])
                self.assertIn("binary/non-text", coverage["text"])

    def test_rename_metadata_with_long_paths_stays_within_budget(self) -> None:
        old = "a_module_with_a_very_long_original_filename_for_rename_testing.py"
        new = "a_module_with_an_even_longer_destination_filename_after_rename.py"
        (self.root / old).write_text("R = 1\n" * 40, encoding="utf-8")
        self.commit()
        git(self.root, "mv", old, new)
        owned = [old, new]
        minimum = self.minimum_for(owned)
        for max_chars in (minimum, minimum + 2_000):
            with self.subTest(max_chars=max_chars):
                self.assert_within(owned, max_chars)

    def test_unicode_paths_and_content_use_character_semantics(self) -> None:
        relative = "модуль_日本語_ünïcode.py"
        (self.root / relative).write_text("VALUE = 'αβγ'\n" * 30, encoding="utf-8")
        self.commit()
        (self.root / relative).write_text("VALUE = '🎯 δεζ ünïcode 日本語'\n" * 30, encoding="utf-8")
        owned = [relative]
        minimum = self.minimum_for(owned)
        for max_chars in (minimum, minimum + 200, minimum + 4_000):
            with self.subTest(max_chars=max_chars):
                coverage = self.assert_within(owned, max_chars)
                if coverage["ok"]:
                    # Character count, not UTF-8 byte count.
                    self.assertLessEqual(len(coverage["text"]), max_chars)
                    self.assertGreater(len(coverage["text"].encode("utf-8")), 0)

    def test_no_changes_reports_success_within_any_budget(self) -> None:
        coverage = review_diff_coverage(self.root, [], 10)
        self.assertTrue(coverage["ok"])
        self.assertEqual(coverage["files"], [])


if __name__ == "__main__":
    unittest.main()
