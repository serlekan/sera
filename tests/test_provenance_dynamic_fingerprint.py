"""`provenance.dynamic_task_fingerprint` — state-sensitive, ledger-byte-free.

T11 defines the 0.5.0 dynamic task fingerprint: it covers the active contract
identity plus the same repository/task-state inputs as the legacy
`core.task_fingerprint`, but never reads any append-only `*.jsonl` assurance
ledger. `core.task_fingerprint` itself is not cut over to this primitive in
T11 (that is T13's job); this file also characterizes that the legacy
function's old, ledger-sensitive behavior is untouched.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from sera import provenance
from sera.core import task_fingerprint


CONTRACT_FP = "ab" * 32
OTHER_CONTRACT_FP = "cd" * 32


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class DynamicFingerprintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / ".sera").mkdir()
        (self.root / "src.py").write_text("value = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")

        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "task.json").write_text('{"id": "task-1"}\n', encoding="utf-8")
        (self.task_dir / "ledger.jsonl").write_text("", encoding="utf-8")

    def fp(self, contract_fp: str = CONTRACT_FP) -> str:
        return provenance.dynamic_task_fingerprint(self.root, self.task_dir, contract_fp)

    def test_identical_state_is_stable(self) -> None:
        self.assertEqual(self.fp(), self.fp())

    def test_invalid_contract_fp_is_rejected(self) -> None:
        with self.assertRaises(Exception):
            provenance.dynamic_task_fingerprint(self.root, self.task_dir, "not-a-hash")

    def test_different_contract_fp_changes_fingerprint(self) -> None:
        self.assertNotEqual(self.fp(CONTRACT_FP), self.fp(OTHER_CONTRACT_FP))

    def test_task_json_byte_change_alters_fingerprint(self) -> None:
        before = self.fp()
        (self.task_dir / "task.json").write_text('{"id": "task-1", "extra": true}\n', encoding="utf-8")
        after = self.fp()
        self.assertNotEqual(before, after)

    def test_unstaged_governed_change_alters_fingerprint(self) -> None:
        before = self.fp()
        (self.root / "src.py").write_text("value = 2\n", encoding="utf-8")
        after = self.fp()
        self.assertNotEqual(before, after)

    def test_staged_governed_change_alters_fingerprint(self) -> None:
        before = self.fp()
        (self.root / "src.py").write_text("value = 3\n", encoding="utf-8")
        git(self.root, "add", "src.py")
        after = self.fp()
        self.assertNotEqual(before, after)

    def test_relevant_untracked_project_file_alters_fingerprint(self) -> None:
        before = self.fp()
        (self.root / "new_file.py").write_text("value = 4\n", encoding="utf-8")
        after = self.fp()
        self.assertNotEqual(before, after)

    def test_untracked_sera_runtime_file_does_not_alter_fingerprint(self) -> None:
        before = self.fp()
        (self.task_dir / "packet-implementation_builder.json").write_text("{}", encoding="utf-8")
        after = self.fp()
        self.assertEqual(before, after)

    def test_verification_ledger_append_does_not_alter_fingerprint(self) -> None:
        before = self.fp()
        with (self.task_dir / "ledger.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"exit_code": 0}\n')
        after = self.fp()
        self.assertEqual(before, after)

    def test_review_ledger_append_does_not_alter_fingerprint(self) -> None:
        before = self.fp()
        with (self.task_dir / "reviews.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"verdict": "approve"}\n')
        after = self.fp()
        self.assertEqual(before, after)

    def test_policy_snapshot_ledger_append_does_not_alter_fingerprint(self) -> None:
        before = self.fp()
        with (self.task_dir / "policy-snapshots.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"snapshot_hash": "ab"}\n')
        after = self.fp()
        self.assertEqual(before, after)

    def test_route_snapshot_ledger_append_does_not_alter_fingerprint(self) -> None:
        before = self.fp()
        with (self.task_dir / "route-snapshots.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"snapshot_hash": "cd"}\n')
        after = self.fp()
        self.assertEqual(before, after)

    def test_task_contract_ledger_append_does_not_alter_fingerprint(self) -> None:
        before = self.fp()
        with (self.task_dir / "task-contracts.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"schema_version": 2}\n')
        after = self.fp()
        self.assertEqual(before, after)

    def test_every_assurance_ledger_append_leaves_fingerprint_unchanged(self) -> None:
        self.assertEqual(len(provenance.ASSURANCE_LEDGER_NAMES), 13)
        for name in provenance.ASSURANCE_LEDGER_NAMES:
            with self.subTest(ledger=name):
                before = self.fp()
                with (self.task_dir / name).open("a", encoding="utf-8") as handle:
                    handle.write('{"appended": true}\n')
                self.assertEqual(before, self.fp())

    def test_tracked_assurance_ledger_append_leaves_fingerprint_unchanged(self) -> None:
        for name in provenance.ASSURANCE_LEDGER_NAMES:
            (self.task_dir / name).write_text('{"seed": true}\n', encoding="utf-8")
        git(self.root, "add", "-f", ".sera/tasks")
        git(self.root, "commit", "-m", "track task runtime state")
        before = self.fp()
        for name in provenance.ASSURANCE_LEDGER_NAMES:
            with (self.task_dir / name).open("a", encoding="utf-8") as handle:
                handle.write('{"appended": true}\n')
        self.assertEqual(before, self.fp())
        git(self.root, "add", "-f", ".sera/tasks")
        self.assertEqual(before, self.fp(), "a staged ledger append is still not governed content")

    def test_ordinary_project_file_named_like_a_ledger_is_still_governed(self) -> None:
        before = self.fp()
        (self.root / "ledger.jsonl").write_text('{"project": "data"}\n', encoding="utf-8")
        created = self.fp()
        self.assertNotEqual(before, created)
        (self.root / "ledger.jsonl").write_text('{"project": "changed"}\n', encoding="utf-8")
        self.assertNotEqual(created, self.fp())

    def test_non_ascii_untracked_file_is_never_silently_dropped(self) -> None:
        before = self.fp()
        path = self.root / "café-漢.py"
        path.write_text("value = 1\n", encoding="utf-8")
        created = self.fp()
        self.assertNotEqual(before, created)
        path.write_text("value = 2\n", encoding="utf-8")
        self.assertNotEqual(created, self.fp())

    def test_untracked_path_identity_is_bound_not_only_content(self) -> None:
        (self.root / "one.py").write_text("same\n", encoding="utf-8")
        before = self.fp()
        (self.root / "one.py").rename(self.root / "two.py")
        self.assertNotEqual(before, self.fp())

    def test_nul_bearing_content_cannot_collide_with_a_different_file_set(self) -> None:
        (self.root / "a").write_bytes(b"x\0b\0y")
        combined = self.fp()
        (self.root / "a").write_bytes(b"x")
        (self.root / "b").write_bytes(b"y")
        self.assertNotEqual(combined, self.fp())

    def test_untracked_symlink_is_identified_by_its_link_text(self) -> None:
        (self.root / "target-a.txt").write_text("a\n", encoding="utf-8")
        (self.root / "target-b.txt").write_text("a\n", encoding="utf-8")
        link = self.root / "link"
        try:
            link.symlink_to("target-a.txt")
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        before = self.fp()
        link.unlink()
        link.symlink_to("target-b.txt")
        self.assertNotEqual(before, self.fp(), "retargeting a link to identical content is a real change")

    def test_result_is_independent_of_file_creation_order(self) -> None:
        other_temp = tempfile.TemporaryDirectory()
        self.addCleanup(other_temp.cleanup)
        other = Path(other_temp.name)
        git(other, "init", "-b", "main")
        git(other, "config", "user.name", "Test")
        git(other, "config", "user.email", "test@example.com")
        (other / ".sera").mkdir()
        (other / "src.py").write_text("value = 1\n", encoding="utf-8")
        git(other, "add", ".")
        git(other, "commit", "-m", "baseline")
        other_task = other / ".sera" / "tasks" / "task-1"
        other_task.mkdir(parents=True)
        (other_task / "task.json").write_text('{"id": "task-1"}\n', encoding="utf-8")
        for name in ("b.py", "a.py", "c.py"):
            (self.root / name).write_text(name, encoding="utf-8")
        for name in ("c.py", "a.py", "b.py"):
            (other / name).write_text(name, encoding="utf-8")
        self.assertEqual(self.fp(), provenance.dynamic_task_fingerprint(other, other_task, CONTRACT_FP))

    def test_git_failure_fails_closed(self) -> None:
        not_a_repo = tempfile.TemporaryDirectory()
        self.addCleanup(not_a_repo.cleanup)
        task_dir = Path(not_a_repo.name) / "task"
        task_dir.mkdir()
        (task_dir / "task.json").write_text("{}\n", encoding="utf-8")
        with self.assertRaises(Exception):
            provenance.dynamic_task_fingerprint(Path(not_a_repo.name), task_dir, CONTRACT_FP)

    def test_calculation_does_not_mutate_task_or_project_files(self) -> None:
        (self.root / "new_file.py").write_text("value = 4\n", encoding="utf-8")
        (self.root / "src.py").write_text("value = 5\n", encoding="utf-8")
        watched = [self.task_dir / "task.json", self.task_dir / "ledger.jsonl", self.root / "new_file.py", self.root / "src.py"]
        before = {path: path.read_bytes() for path in watched}
        listing = sorted(str(path) for path in self.root.rglob("*") if ".git" not in path.parts)
        self.fp()
        self.assertEqual(before, {path: path.read_bytes() for path in watched})
        self.assertEqual(listing, sorted(str(path) for path in self.root.rglob("*") if ".git" not in path.parts))


class SubdirectoryRootTests(unittest.TestCase):
    """`root` need not be the Git top level; scope and exclusions must agree."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        git(self.top, "init", "-b", "main")
        git(self.top, "config", "user.name", "Test")
        git(self.top, "config", "user.email", "test@example.com")
        self.root = self.top / "sub"
        (self.root / ".sera" / "tasks" / "task-1").mkdir(parents=True)
        (self.top / "other_pkg").mkdir()
        (self.root / "src.py").write_text("value = 1\n", encoding="utf-8")
        (self.top / "other_pkg" / "unrelated.py").write_text("x = 1\n", encoding="utf-8")
        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        (self.task_dir / "task.json").write_text('{"id": "task-1"}\n', encoding="utf-8")
        (self.task_dir / "ledger.jsonl").write_text("", encoding="utf-8")
        git(self.top, "add", "-f", ".")
        git(self.top, "commit", "-m", "baseline with tracked runtime state")

    def fp(self) -> str:
        return provenance.dynamic_task_fingerprint(self.root, self.task_dir, CONTRACT_FP)

    def test_tracked_ledger_append_under_a_subdirectory_root_is_invisible(self) -> None:
        before = self.fp()
        with (self.task_dir / "ledger.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"exit_code": 0}\n')
        self.assertEqual(before, self.fp())

    def test_governed_change_under_the_root_still_alters_the_fingerprint(self) -> None:
        before = self.fp()
        (self.root / "src.py").write_text("value = 2\n", encoding="utf-8")
        self.assertNotEqual(before, self.fp())

    def test_change_outside_the_root_subtree_is_not_governed_by_this_root(self) -> None:
        before = self.fp()
        (self.top / "other_pkg" / "unrelated.py").write_text("x = 2\n", encoding="utf-8")
        self.assertEqual(before, self.fp())


class CoreTaskFingerprintNotYetCutOverTests(unittest.TestCase):
    """Characterization test: T11 must not change `core.task_fingerprint`."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / ".sera").mkdir()
        (self.root / "src.py").write_text("value = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")

        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "task.json").write_text('{"id": "task-1"}\n', encoding="utf-8")
        (self.task_dir / "ledger.jsonl").write_text("", encoding="utf-8")

    def test_legacy_fingerprint_still_hashes_ledger_bytes(self) -> None:
        """Unlike the new primitive, the legacy facade still binds ledger.jsonl."""
        before = task_fingerprint(self.root, self.task_dir)
        with (self.task_dir / "ledger.jsonl").open("a", encoding="utf-8") as handle:
            handle.write('{"exit_code": 0}\n')
        after = task_fingerprint(self.root, self.task_dir)
        self.assertNotEqual(before, after, "core.task_fingerprint must not yet exclude ledger.jsonl (T13's job)")

    def test_legacy_fingerprint_ignores_the_new_contract_fp_argument_entirely(self) -> None:
        """`core.task_fingerprint` takes no `contract_fp` — it is not delegated yet."""
        import inspect

        params = inspect.signature(task_fingerprint).parameters
        self.assertEqual(list(params), ["root", "task_dir"])


if __name__ == "__main__":
    unittest.main()
