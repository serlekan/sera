"""Cross-platform task and registry lock invariants."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from sera.schemas import (
    LockHeld,
    SchemaError,
    TaskLockGuard,
    append_ledger_record,
    registry_lock,
    task_lock,
    with_registration_then_task,
)


REGISTRATION_HASH = "ab" * 32


class LockingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".sera").mkdir()
        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        self.task_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_task_lock_is_exclusive_and_guard_protects_only_task_files(self) -> None:
        with task_lock(self.task_dir) as guard:
            self.assertIsInstance(guard, TaskLockGuard)
            self.assertTrue(guard.held)
            self.assertTrue((self.task_dir / ".lock").is_dir())
            self.assertTrue(guard.protects(self.task_dir / "history.jsonl"))
            self.assertFalse(guard.protects(self.root / ".sera" / "global.jsonl"))
            with self.assertRaisesRegex(LockHeld, "task is locked"):
                with task_lock(self.task_dir):
                    self.fail("same-path re-entry must not succeed")
        self.assertFalse(guard.held)
        self.assertFalse((self.task_dir / ".lock").exists())

    def test_registry_lock_is_exclusive(self) -> None:
        with registry_lock(self.root) as guard:
            self.assertTrue(guard.held)
            self.assertTrue((self.root / ".sera" / ".registry.lock").is_dir())
            self.assertTrue(guard.protects(self.root / ".sera" / "execution-evidence-sources.jsonl"))
            self.assertFalse(guard.protects(self.task_dir / "history.jsonl"))
            with self.assertRaisesRegex(LockHeld, "registry is locked"):
                with registry_lock(self.root):
                    self.fail("registry re-entry must not succeed")

    def test_lock_metadata_is_diagnostic_only(self) -> None:
        with task_lock(self.task_dir):
            metadata = json.loads((self.task_dir / ".lock" / "owner.json").read_text(encoding="utf-8"))
            self.assertEqual(set(metadata), {"acquired_at", "host", "pid"})
            self.assertIsInstance(metadata["pid"], int)
            self.assertTrue(metadata["host"])
            self.assertTrue(metadata["acquired_at"].endswith("Z"))

    def test_leftover_lock_is_never_silently_removed(self) -> None:
        lock_dir = self.task_dir / ".lock"
        lock_dir.mkdir()
        (lock_dir / "owner.json").write_text('{"pid":999}\n', encoding="utf-8")
        with self.assertRaisesRegex(LockHeld, "inspect and remove it manually"):
            with task_lock(self.task_dir):
                self.fail("a crash-stale lock must fail closed")
        self.assertTrue(lock_dir.is_dir())
        self.assertEqual((lock_dir / "owner.json").read_text(encoding="utf-8"), '{"pid":999}\n')

    def test_two_threads_contend_and_only_one_acquires(self) -> None:
        owner_entered = threading.Event()
        contender_finished = threading.Event()
        outcomes: list[str] = []

        def owner() -> None:
            with task_lock(self.task_dir):
                outcomes.append("owner-acquired")
                owner_entered.set()
                contender_finished.wait(timeout=5)

        def contender() -> None:
            owner_entered.wait(timeout=5)
            try:
                with task_lock(self.task_dir):
                    outcomes.append("contender-acquired")
            except LockHeld:
                outcomes.append("contender-blocked")
            finally:
                contender_finished.set()

        first = threading.Thread(target=owner)
        second = threading.Thread(target=contender)
        first.start()
        second.start()
        first.join(timeout=10)
        second.join(timeout=10)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(outcomes, ["owner-acquired", "contender-blocked"])

    def test_task_then_registry_order_is_forbidden_without_creating_registry_lock(self) -> None:
        with task_lock(self.task_dir):
            with self.assertRaisesRegex(LockHeld, "must never be held simultaneously"):
                with registry_lock(self.root):
                    self.fail("inverse lock order must not succeed")
            self.assertFalse((self.root / ".sera" / ".registry.lock").exists())

    def test_registry_then_task_nesting_is_also_forbidden(self) -> None:
        with registry_lock(self.root):
            with self.assertRaisesRegex(LockHeld, "must never be held simultaneously"):
                with task_lock(self.task_dir):
                    self.fail("the correct flow releases registry before task")
            self.assertFalse((self.task_dir / ".lock").exists())

    def test_registration_helper_releases_registry_before_task_and_rereads(self) -> None:
        observed: list[tuple[bool, bool]] = []

        def read_registration(root: Path, registration_hash: str) -> dict[str, str]:
            observed.append(
                (
                    (root / ".sera" / ".registry.lock").exists(),
                    (self.task_dir / ".lock").exists(),
                )
            )
            return {"registration_hash": registration_hash}

        with with_registration_then_task(
            self.root,
            self.task_dir,
            REGISTRATION_HASH,
            registration_reader=read_registration,
        ) as (registration, guard):
            self.assertEqual(registration, {"registration_hash": REGISTRATION_HASH})
            self.assertTrue(guard.held)
            self.assertFalse((self.root / ".sera" / ".registry.lock").exists())
            self.assertTrue((self.task_dir / ".lock").exists())
        self.assertEqual(observed, [(True, False), (False, True)])

    def test_registration_helper_fails_if_exact_hash_changes(self) -> None:
        calls = 0

        def moving_registration(_root: Path, registration_hash: str) -> dict[str, str]:
            nonlocal calls
            calls += 1
            return {"registration_hash": registration_hash if calls == 1 else "cd" * 32}

        with self.assertRaisesRegex(SchemaError, "registration changed before task lock"):
            with with_registration_then_task(
                self.root,
                self.task_dir,
                REGISTRATION_HASH,
                registration_reader=moving_registration,
            ):
                self.fail("changed registration must not be authorized")
        self.assertFalse((self.task_dir / ".lock").exists())
        self.assertFalse((self.root / ".sera" / ".registry.lock").exists())

    def test_task_guard_satisfies_append_ledger_contract(self) -> None:
        ledger = self.task_dir / "history.jsonl"
        with task_lock(self.task_dir) as guard:
            append_ledger_record(ledger, {"sequence": 1}, guard)
        self.assertEqual(ledger.read_bytes(), b'{"sequence":1}\n')


if __name__ == "__main__":
    unittest.main()
