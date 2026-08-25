"""Historical bootstrap exceptions are bounded, read-only audit records."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sera.core as core
from sera.controller import build_packet
from sera.core import (
    build_repo_map,
    git_head_identity,
    initialize,
    load_task,
    new_task,
    read_packet_provenance,
)


FUTURE_WORKFLOW = ["builder", "packet", "independent_review", "gate", "seal"]


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class BootstrapExceptionRepository(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("ANSWER = 41\n", encoding="utf-8")
        initialize(self.root)
        (self.root / ".gitignore").write_text(".sera/\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)
        self.answer = 41

    def tearDown(self) -> None:
        self.temp.cleanup()

    def historical_task(self) -> Path:
        task_dir = new_task(
            self.root,
            "historical implementation",
            "update the application",
            "assured",
            "high",
            ["src/app.py"],
            [],
            [],
            1,
            "implementation",
        )
        self.answer += 1
        (self.root / "src" / "app.py").write_text(f"ANSWER = {self.answer}\n", encoding="utf-8")
        git(self.root, "add", "src/app.py")
        git(self.root, "commit", "-m", "implementation")
        build_packet(self.root, task_dir, "review")
        return task_dir

    def write_exception(
        self,
        task_dir: Path,
        *,
        omit: tuple[str, ...] = (),
        **overrides: object,
    ) -> Path:
        identity = read_packet_provenance(task_dir, "review")["repository_identity"]
        payload = {
            "schema_version": 1,
            "type": "historical_workflow_bootstrap_exception",
            "reason": "Task predates native builder handoff capture.",
            "missing_stage": "builder_handoff",
            "no_fabricated_evidence": True,
            "implementation_head_sha": identity["head_sha"],
            "implementation_tree_sha": identity["head_tree_sha"],
            "future_workflow_required": FUTURE_WORKFLOW,
        }
        payload.update(overrides)
        for field in omit:
            payload.pop(field, None)
        path = task_dir / "bootstrap-exception.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path


class BootstrapExceptionTests(BootstrapExceptionRepository):
    def test_valid_exception_is_accepted_and_preserves_missing_builder_history(self) -> None:
        task_dir = self.historical_task()
        self.write_exception(task_dir)
        before_context = (task_dir / "context-ledger.jsonl").read_bytes()

        validator = getattr(core, "bootstrap_exception_state", None)
        self.assertTrue(callable(validator), "bootstrap exception validator is missing")
        state = validator(self.root, task_dir, load_task(task_dir))

        self.assertTrue(state["exists"])
        self.assertTrue(state["applicable"])
        self.assertTrue(state["accepted"])
        self.assertEqual(state["validation_errors"], [])
        self.assertIn("explicitly preserved as missing", state["audit_message"])
        self.assertFalse((task_dir / "packet-build.md").exists())
        self.assertFalse((task_dir / "packet-build.provenance.json").exists())
        self.assertEqual((task_dir / "context-ledger.jsonl").read_bytes(), before_context)

    def test_exception_identity_must_match_review_packet_artifact(self) -> None:
        task_dir = self.historical_task()
        current = git_head_identity(self.root)
        self.write_exception(task_dir, implementation_head_sha="0" * len(current["head_sha"]))

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("implementation_head_mismatch", state["validation_errors"])

    def test_exception_tree_must_match_review_packet_artifact(self) -> None:
        task_dir = self.historical_task()
        current = git_head_identity(self.root)
        self.write_exception(task_dir, implementation_tree_sha="0" * len(current["head_tree_sha"]))

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("implementation_tree_mismatch", state["validation_errors"])

    def test_schema_and_no_fabricated_evidence_are_strict(self) -> None:
        cases = [
            ("schema version must be an int", {"schema_version": "1"}, (), "schema_version_invalid"),
            ("false is rejected", {"no_fabricated_evidence": False}, (), "no_fabricated_evidence_invalid"),
            ("string true is rejected", {"no_fabricated_evidence": "true"}, (), "no_fabricated_evidence_invalid"),
            ("integer one is rejected", {"no_fabricated_evidence": 1}, (), "no_fabricated_evidence_invalid"),
            ("yes is rejected", {"no_fabricated_evidence": "yes"}, (), "no_fabricated_evidence_invalid"),
            ("omission is rejected", {}, ("no_fabricated_evidence",), "no_fabricated_evidence_invalid"),
        ]
        for name, overrides, omit, error in cases:
            with self.subTest(name):
                task_dir = self.historical_task()
                self.write_exception(task_dir, omit=omit, **overrides)

                state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

                self.assertFalse(state["accepted"])
                self.assertEqual(state["reason"], "bootstrap_exception_invalid")
                self.assertIn(error, state["validation_errors"])

    def test_future_workflow_must_be_the_exact_required_list(self) -> None:
        cases = [
            ("missing", {}, ("future_workflow_required",)),
            ("reordered", {"future_workflow_required": list(reversed(FUTURE_WORKFLOW))}, ()),
            ("extended", {"future_workflow_required": FUTURE_WORKFLOW + ["archive"]}, ()),
        ]
        for name, overrides, omit in cases:
            with self.subTest(name):
                task_dir = self.historical_task()
                self.write_exception(task_dir, omit=omit, **overrides)

                state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

                self.assertFalse(state["accepted"])
                self.assertEqual(state["reason"], "bootstrap_exception_invalid")
                self.assertIn("future_workflow_required_invalid", state["validation_errors"])

    def test_exception_reason_must_be_non_empty(self) -> None:
        for reason in ("", "   "):
            with self.subTest(reason=repr(reason)):
                task_dir = self.historical_task()
                self.write_exception(task_dir, reason=reason)

                state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

                self.assertFalse(state["accepted"])
                self.assertEqual(state["reason"], "bootstrap_exception_invalid")
                self.assertIn("reason_invalid", state["validation_errors"])

    def test_malformed_exception_is_rejected(self) -> None:
        task_dir = self.historical_task()
        (task_dir / "bootstrap-exception.json").write_text("{not json}\n", encoding="utf-8")

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertEqual(state["validation_errors"], ["exception_unreadable"])

    def test_invalid_utf8_exception_is_rejected(self) -> None:
        task_dir = self.historical_task()
        (task_dir / "bootstrap-exception.json").write_bytes(b"\xff\xfe\x80")

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertEqual(state["validation_errors"], ["exception_unreadable"])

    def test_exception_requires_a_current_review_packet_not_just_checkout_identity(self) -> None:
        task_dir = self.historical_task()
        self.write_exception(task_dir)
        provenance_path = task_dir / "packet-review.provenance.json"
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        provenance["repository_identity"]["head_sha"] = "0" * len(
            provenance["repository_identity"]["head_sha"]
        )
        provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertFalse(state["review_packet"]["current"])
        self.assertIn("review_packet_not_current", state["validation_errors"])

    def test_orphan_builder_provenance_is_rejected_without_creating_a_builder_packet(self) -> None:
        task_dir = self.historical_task()
        self.write_exception(task_dir)
        (task_dir / "packet-build.provenance.json").write_text("{}\n", encoding="utf-8")

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("builder_provenance_present", state["validation_errors"])
        self.assertFalse((task_dir / "packet-build.md").exists())


if __name__ == "__main__":
    unittest.main()
