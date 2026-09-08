"""ExecutionRepositoryStateV1 capture, validation, and TOCTOU tests."""

from __future__ import annotations

import copy
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.core import (
    build_repo_map,
    initialize,
    load_config,
    load_task,
    new_task,
    task_contract_fingerprint,
    task_fingerprint,
    task_review_coverage,
)
from sera.provenance import (
    EXECUTION_HEAD_MISMATCH,
    EXECUTION_INPUT_STATE_MISMATCH,
    EXECUTION_OUTPUT_STATE_MISSING,
    EXECUTION_REPOSITORY_MISMATCH,
    EXECUTION_TREE_MISMATCH,
    RepositoryStateError,
    repository_state,
    validate_repository_state,
)
from sera.schemas import canonical_json


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class RepositoryStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="sera-repo-state-")
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "SERA Tests")
        git(self.root, "config", "user.email", "sera-tests@example.com")
        (self.root / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)
        self.task_dir = new_task(
            self.root,
            "repository state",
            "change app",
            "standard",
            "medium",
            ["app.py"],
            [],
            [],
            1,
            "implementation",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fingerprints(self) -> tuple[str, str]:
        task = load_task(self.task_dir)
        return task_contract_fingerprint(task), task_fingerprint(self.root, self.task_dir)

    def capture(self, state_kind: str = "committed") -> dict[str, object]:
        contract_fp, dynamic_fp = self.fingerprints()
        return repository_state(
            self.root,
            self.task_dir,
            state_kind=state_kind,
            contract_fp=contract_fp,
            dynamic_fp=dynamic_fp,
        )

    def assert_code(self, code: str, callable_obj: object, *args: object, **kwargs: object) -> None:
        with self.assertRaises(RepositoryStateError) as caught:
            callable_obj(*args, **kwargs)  # type: ignore[operator]
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(str(caught.exception).startswith(f"{code}:"))

    def test_committed_state_round_trips_with_exact_shape(self) -> None:
        state = self.capture()
        self.assertEqual(validate_repository_state(state), state)
        self.assertEqual(
            set(state),
            {
                "schema_version",
                "repository_identity",
                "head_sha",
                "tree_sha",
                "task_contract_fingerprint",
                "task_fingerprint",
                "state_kind",
            },
        )
        self.assertEqual(state["state_kind"], "committed")
        self.assertEqual(state["head_sha"], git(self.root, "rev-parse", "HEAD"))
        self.assertEqual(state["tree_sha"], git(self.root, "rev-parse", "HEAD^{tree}"))

    def test_equal_committed_states_hash_equally(self) -> None:
        first = self.capture()
        second = self.capture()
        self.assertEqual(first, second)
        self.assertEqual(
            hashlib.sha256(canonical_json(first).encode()).hexdigest(),
            hashlib.sha256(canonical_json(second).encode()).hexdigest(),
        )

    def test_working_tree_state_reuses_authoritative_review_fingerprint(self) -> None:
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        state = self.capture("working_tree")
        config = load_config(self.root)
        coverage = task_review_coverage(
            self.root,
            load_task(self.task_dir),
            int(config["max_packet_chars"]),
        )
        self.assertEqual(state["review_change_fingerprint"], coverage["change_fingerprint"])

    def test_working_tree_without_fingerprint_is_rejected(self) -> None:
        state = self.capture("working_tree")
        state.pop("review_change_fingerprint")
        self.assert_code(EXECUTION_INPUT_STATE_MISMATCH, validate_repository_state, state)

    def test_committed_and_working_tree_representations_remain_distinct(self) -> None:
        committed = self.capture("committed")
        working = self.capture("working_tree")
        self.assertNotEqual(committed, working)
        self.assertNotIn("review_change_fingerprint", committed)
        self.assertIn("review_change_fingerprint", working)

    def test_dirty_task_delta_cannot_be_captured_as_committed(self) -> None:
        (self.root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        self.assert_code(EXECUTION_INPUT_STATE_MISMATCH, self.capture, "committed")

    def test_current_context_detects_head_and_tree_mismatches(self) -> None:
        state = self.capture()
        git(self.root, "commit", "--allow-empty", "-m", "move head only")
        self.assert_code(EXECUTION_HEAD_MISMATCH, validate_repository_state, state, root=self.root)

        current = self.capture()
        wrong_tree = copy.deepcopy(current)
        wrong_tree["tree_sha"] = "0" * 40
        self.assert_code(EXECUTION_TREE_MISMATCH, validate_repository_state, wrong_tree, root=self.root)

    def test_repository_identity_is_bound_to_current_context(self) -> None:
        state = self.capture()
        altered = copy.deepcopy(state)
        altered["repository_identity"]["logical_id"] = "0" * 64  # type: ignore[index]
        self.assert_code(
            EXECUTION_REPOSITORY_MISMATCH,
            validate_repository_state,
            altered,
            root=self.root,
        )

    def test_malformed_repository_identity_fails_as_a_stable_state_error(self) -> None:
        state = self.capture()
        malformed_strategy = copy.deepcopy(state)
        malformed_strategy["repository_identity"]["strategy"] = []  # type: ignore[index]
        self.assert_code(
            EXECUTION_INPUT_STATE_MISMATCH,
            validate_repository_state,
            malformed_strategy,
        )

        unborn_component = copy.deepcopy(state)
        unborn_component["repository_identity"]["components"]["root_commit"] = "unborn"  # type: ignore[index]
        self.assert_code(
            EXECUTION_INPUT_STATE_MISMATCH,
            validate_repository_state,
            unborn_component,
        )

    def test_contract_and_dynamic_fingerprints_are_bound_to_context(self) -> None:
        state = self.capture()
        self.assert_code(
            EXECUTION_INPUT_STATE_MISMATCH,
            validate_repository_state,
            state,
            contract_fp="1" * 64,
        )
        self.assert_code(
            EXECUTION_INPUT_STATE_MISMATCH,
            validate_repository_state,
            state,
            dynamic_fp="2" * 64,
        )
        self.assertEqual(
            validate_repository_state(state, root=self.root, task_dir=self.task_dir),
            state,
        )
        self.assert_code(
            EXECUTION_INPUT_STATE_MISMATCH,
            validate_repository_state,
            state,
            root=self.root,
            task_dir=self.task_dir,
            contract_fp="",
        )

    def test_capture_then_repository_movement_is_not_returned_as_current(self) -> None:
        captured_a = self.capture()
        (self.root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        git(self.root, "add", "app.py")
        git(self.root, "commit", "-m", "state B")
        self.assert_code(
            EXECUTION_HEAD_MISMATCH,
            validate_repository_state,
            captured_a,
            root=self.root,
        )

    def test_invalid_schema_hash_state_kind_and_missing_output_fail_closed(self) -> None:
        state = self.capture()
        for field, value in (
            ("schema_version", 2),
            ("head_sha", "HEAD"),
            ("tree_sha", "A" * 40),
            ("state_kind", "snapshot"),
        ):
            with self.subTest(field=field):
                malformed = copy.deepcopy(state)
                malformed[field] = value
                self.assert_code(
                    EXECUTION_INPUT_STATE_MISMATCH,
                    validate_repository_state,
                    malformed,
                )
        self.assert_code(
            EXECUTION_OUTPUT_STATE_MISSING,
            validate_repository_state,
            None,
            output_required=True,
        )
        contract_fp, dynamic_fp = self.fingerprints()
        self.assert_code(
            EXECUTION_INPUT_STATE_MISMATCH,
            repository_state,
            self.root,
            self.task_dir,
            state_kind=[],
            contract_fp=contract_fp,
            dynamic_fp=dynamic_fp,
        )

    def test_unborn_repository_cannot_fabricate_a_committed_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sera-repo-state-unborn-") as directory:
            root = Path(directory)
            git(root, "init", "-b", "main")
            self.assert_code(
                EXECUTION_INPUT_STATE_MISMATCH,
                repository_state,
                root,
                root / ".sera" / "tasks" / "legacy",
                state_kind="committed",
                contract_fp="1" * 64,
                dynamic_fp="2" * 64,
            )


if __name__ == "__main__":
    unittest.main()
