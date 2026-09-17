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
