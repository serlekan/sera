"""Historical bootstrap exceptions are bounded, read-only audit records."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sera.core as core
from sera.controller import build_packet, next_action
from sera.core import (
    PACKET_STALE_CONTRACT,
    PACKET_UNBOUND,
    accept_review,
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

    def historical_task(self, *, register: bool = True) -> Path:
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
        if register:
            self.register_historical_task(task_dir)
        self.answer += 1
        (self.root / "src" / "app.py").write_text(f"ANSWER = {self.answer}\n", encoding="utf-8")
        git(self.root, "add", "src/app.py")
        git(self.root, "commit", "-m", "implementation")
        build_packet(self.root, task_dir, "review")
        return task_dir

    def reviewed_historical_task(self) -> Path:
        task_dir = self.historical_task()
        accept_review(self.root, task_dir, "ship", "independent-peer", "correct", "independent")
        return task_dir

    def empty_historical_task(self) -> Path:
        task_dir = new_task(
            self.root,
            "empty historical implementation",
            "update the application",
            "assured",
            "high",
            ["src/app.py"],
            [],
            [],
            1,
            "implementation",
        )
        self.register_historical_task(task_dir)
        build_packet(self.root, task_dir, "review")
        return task_dir

    def native_builder_task(self) -> Path:
        task_dir = new_task(
            self.root,
            "native builder handoff",
            "preserve the application",
            "assured",
            "high",
            ["src/app.py"],
            [],
            [],
            1,
            "implementation",
        )
        build_packet(self.root, task_dir, "build")
        return task_dir

    def register_historical_task(self, task_dir: Path, **overrides: object) -> None:
        task = load_task(task_dir)
        baseline = task["baseline_repository_identity"]
        entry = {
            "task_id": task["id"],
            "created_at": task["created_at"],
            "baseline_head_sha": baseline["head_sha"],
            "baseline_tree_sha": baseline["head_tree_sha"],
            "eligibility_type": "historical_builder_handoff_gap",
            "schema_version": 1,
        }
        entry.update(overrides)
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["historical_bootstrap_eligibility"] = [entry]
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    def write_registry(self, entries: object) -> None:
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["historical_bootstrap_eligibility"] = entries
        path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

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

    def filesystem_snapshot(self) -> dict[str, bytes | None]:
        """Capture every non-Git filesystem entry without changing repository state."""
        snapshot: dict[str, bytes | None] = {}
        for path in sorted(self.root.rglob("*")):
            relative = path.relative_to(self.root)
            if ".git" in relative.parts:
                continue
            snapshot[relative.as_posix()] = None if path.is_dir() else path.read_bytes()
        return snapshot


class BootstrapExceptionTests(BootstrapExceptionRepository):
    def test_missing_repo_map_fails_closed_without_mutating_repository_state(self) -> None:
        task_dir = self.historical_task()
        self.write_exception(task_dir)
        cache = self.root / ".sera" / "cache"
        (cache / "repo-map.json").unlink()
        (cache / "repo-map.md").unlink()
        before = self.filesystem_snapshot()

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("review_packet_validation_failed", state["validation_errors"])
        self.assertEqual(self.filesystem_snapshot(), before)
        self.assertFalse((cache / "repo-map.json").exists())
        self.assertFalse((cache / "repo-map.md").exists())

    def test_unreadable_repo_map_fails_closed_without_mutating_repository_state(self) -> None:
        task_dir = self.historical_task()
        self.write_exception(task_dir)
        repo_map = self.root / ".sera" / "cache" / "repo-map.json"
        repo_map.write_text("{not json}\n", encoding="utf-8")
        before = self.filesystem_snapshot()

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("review_packet_validation_failed", state["validation_errors"])
        self.assertEqual(self.filesystem_snapshot(), before)

    def test_wrong_shaped_repo_map_fails_closed_without_mutating_repository_state(self) -> None:
        task_dir = self.historical_task()
        self.write_exception(task_dir)
        repo_map = self.root / ".sera" / "cache" / "repo-map.json"
        repo_map.write_text("[]\n", encoding="utf-8")
        before = self.filesystem_snapshot()

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("review_packet_validation_failed", state["validation_errors"])
        self.assertEqual(self.filesystem_snapshot(), before)

    def test_invalid_repo_map_entry_bytes_fail_closed_without_mutating_repository_state(self) -> None:
        cases = [
            ("list", [], False),
            ("string", "1", False),
            ("float", 1.5, False),
            ("negative integer", -1, False),
            ("boolean", True, False),
            ("null", None, False),
            ("missing", None, True),
            ("object", {"value": 1}, False),
        ]
        for name, bytes_value, omit in cases:
            with self.subTest(name):
                build_repo_map(self.root)
                task_dir = self.historical_task()
                self.write_exception(task_dir)
                repo_map_path = self.root / ".sera" / "cache" / "repo-map.json"
                repo_map = json.loads(repo_map_path.read_text(encoding="utf-8"))
                entry = next(item for item in repo_map["files"] if item["path"] == "src/app.py")
                if omit:
                    entry.pop("bytes")
                else:
                    entry["bytes"] = bytes_value
                repo_map_path.write_text(json.dumps(repo_map, indent=2) + "\n", encoding="utf-8")
                before = self.filesystem_snapshot()

                state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

                self.assertFalse(state["accepted"])
                self.assertEqual(state["reason"], "bootstrap_exception_invalid")
                self.assertIn("review_packet_validation_failed", state["validation_errors"])
                self.assertEqual(self.filesystem_snapshot(), before)

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
        self.assertEqual(
            state["audit_message"],
            "Historical eligibility confirmed by policy registry. Bootstrap exception records missing "
            "historical builder evidence. No builder stage is claimed to have occurred.",
        )
        self.assertFalse((task_dir / "packet-build.md").exists())
        self.assertFalse((task_dir / "packet-build.provenance.json").exists())
        self.assertEqual((task_dir / "context-ledger.jsonl").read_bytes(), before_context)

    def test_current_task_cannot_self_authorize_with_a_valid_exception(self) -> None:
        task_dir = self.historical_task(register=False)
        self.write_exception(task_dir)

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("historical_eligibility_missing", state["validation_errors"])

    def test_empty_registry_rejects_every_unregistered_exception(self) -> None:
        task_dir = self.historical_task(register=False)
        self.write_exception(task_dir)
        self.write_registry([])

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertIn("historical_eligibility_missing", state["validation_errors"])

    def test_registry_must_match_the_exact_historical_task_identity(self) -> None:
        cases = [
            ("wrong task id", {"task_id": "different-task"}, "historical_eligibility_missing"),
            ("wrong timestamp", {"created_at": "2000-01-01T00:00:00Z"}, "historical_eligibility_mismatch"),
            ("wrong baseline head", {"baseline_head_sha": "0" * 40}, "historical_eligibility_mismatch"),
            ("wrong baseline tree", {"baseline_tree_sha": "0" * 40}, "historical_eligibility_mismatch"),
            ("wrong eligibility type", {"eligibility_type": "current_task"}, "historical_eligibility_registry_invalid"),
            ("wrong schema", {"schema_version": 2}, "historical_eligibility_registry_invalid"),
            ("boolean schema", {"schema_version": True}, "historical_eligibility_registry_invalid"),
        ]
        for name, override, expected_error in cases:
            with self.subTest(name):
                task_dir = self.historical_task()
                self.register_historical_task(task_dir, **override)
                self.write_exception(task_dir)

                state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

                self.assertFalse(state["accepted"])
                self.assertEqual(state["reason"], "bootstrap_exception_invalid")
                self.assertIn(expected_error, state["validation_errors"])

    def test_malformed_or_duplicate_registry_fails_closed(self) -> None:
        cases: list[object] = [None, {}, "eligible", [None]]
        for registry in cases:
            with self.subTest(registry=registry):
                task_dir = self.historical_task(register=False)
                self.write_exception(task_dir)
                self.write_registry(registry)

                state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

                self.assertFalse(state["accepted"])
                self.assertIn("historical_eligibility_registry_invalid", state["validation_errors"])

        task_dir = self.historical_task()
        config = json.loads((self.root / ".sera" / "config.json").read_text(encoding="utf-8"))
        entry = config["historical_bootstrap_eligibility"][0]
        self.write_registry([entry, dict(entry)])
        self.write_exception(task_dir)

        duplicate = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(duplicate["accepted"])
        self.assertIn("historical_eligibility_registry_invalid", duplicate["validation_errors"])

    def test_exception_requires_an_authoritative_implementation_change(self) -> None:
        task_dir = self.empty_historical_task()
        self.write_exception(task_dir)

        state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

        self.assertFalse(state["accepted"])
        self.assertEqual(state["reason"], "bootstrap_exception_invalid")
        self.assertEqual(state["validation_errors"], ["implementation_change_missing"])

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

    def test_schema_type_and_missing_stage_bindings_report_exact_errors(self) -> None:
        cases = [
            ("boolean schema version", {"schema_version": True}, (), "schema_version_invalid"),
            ("wrong type", {"type": "other"}, (), "type_invalid"),
            ("missing type", {}, ("type",), "type_invalid"),
            ("wrong missing stage", {"missing_stage": "packet"}, (), "missing_stage_invalid"),
            ("missing missing stage", {}, ("missing_stage",), "missing_stage_invalid"),
        ]
        for name, overrides, omit, expected_error in cases:
            with self.subTest(name):
                task_dir = self.historical_task()
                self.write_exception(task_dir, omit=omit, **overrides)

                state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

                self.assertFalse(state["accepted"])
                self.assertEqual(state["reason"], "bootstrap_exception_invalid")
                self.assertEqual(state["validation_errors"], [expected_error])

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


class BootstrapExceptionControllerTests(BootstrapExceptionRepository):
    def test_accepted_exception_continues_to_existing_gate_dispatch(self) -> None:
        task_dir = self.reviewed_historical_task()
        self.write_exception(task_dir)

        report = next_action(self.root, task_dir)

        self.assertEqual(report["state"], "dispatch_review")
        self.assertEqual(report["next_action"], "dispatch_review")
        self.assertIn("gate", report["reason"])
        self.assertTrue(report["bootstrap_exception"]["accepted"])

    def test_identity_mismatch_fails_closed_in_controller(self) -> None:
        task_dir = self.reviewed_historical_task()
        self.write_exception(task_dir, implementation_tree_sha="0" * 40)

        report = next_action(self.root, task_dir)

        self.assertEqual(report["state"], "invalid")
        self.assertEqual(report["next_action"], "bootstrap_exception_invalid")
        self.assertEqual(report["reason"], "bootstrap_exception_invalid")

    def test_false_no_fabricated_evidence_fails_closed_in_controller(self) -> None:
        task_dir = self.reviewed_historical_task()
        self.write_exception(task_dir, no_fabricated_evidence=False)

        report = next_action(self.root, task_dir)

        self.assertEqual(report["state"], "invalid")
        self.assertEqual(report["next_action"], "bootstrap_exception_invalid")
        self.assertEqual(report["reason"], "bootstrap_exception_invalid")

    def test_empty_change_exception_fails_closed_in_controller(self) -> None:
        task_dir = self.empty_historical_task()
        self.write_exception(task_dir)

        report = next_action(self.root, task_dir)

        self.assertEqual(report["state"], "invalid")
        self.assertEqual(report["next_action"], "bootstrap_exception_invalid")
        self.assertEqual(report["reason"], "bootstrap_exception_invalid")
        self.assertEqual(
            report["bootstrap_exception"]["validation_errors"],
            ["implementation_change_missing"],
        )

    def test_no_exception_preserves_packet_missing_behavior(self) -> None:
        task_dir = self.historical_task(register=False)

        report = next_action(self.root, task_dir)

        self.assertEqual(report["state"], "build_packet")
        self.assertEqual(report["next_action"], "build_packet")
        self.assertEqual(report["build_packet"]["reason"], "packet_missing")
        self.assertFalse(report["bootstrap_exception"]["exists"])

    def test_future_task_cannot_bypass_builder_handoff(self) -> None:
        task_dir = self.historical_task(register=False)
        self.write_exception(task_dir)

        report = next_action(self.root, task_dir)

        self.assertEqual(report["state"], "invalid")
        self.assertEqual(report["next_action"], "bootstrap_exception_invalid")
        self.assertEqual(report["reason"], "bootstrap_exception_invalid")
        self.assertFalse(report["bootstrap_exception"]["accepted"])
        self.assertFalse((task_dir / "packet-build.md").exists())
        self.assertFalse((task_dir / "packet-build.provenance.json").exists())

    def test_native_builder_packet_alongside_exception_is_invalid(self) -> None:
        task_dir = self.historical_task()
        build_packet(self.root, task_dir, "build")
        self.write_exception(task_dir)

        report = next_action(self.root, task_dir)

        self.assertEqual(report["state"], "invalid")
        self.assertEqual(report["next_action"], "bootstrap_exception_invalid")
        self.assertFalse(report["bootstrap_exception"]["accepted"])
        self.assertEqual(report["bootstrap_exception"]["reason"], "bootstrap_exception_invalid")
        self.assertIn("builder_packet_present", report["bootstrap_exception"]["validation_errors"])
        self.assertIn("builder_provenance_present", report["bootstrap_exception"]["validation_errors"])

    def test_stale_or_unbound_builder_packet_alongside_exception_is_invalid(self) -> None:
        stale_task = self.native_builder_task()
        (stale_task / "bootstrap-exception.json").write_text("{}\n", encoding="utf-8")
        stale_task_data = load_task(stale_task)
        stale_task_data["verification"] = ["pytest"]
        (stale_task / "task.json").write_text(json.dumps(stale_task_data, indent=2) + "\n", encoding="utf-8")

        stale_report = next_action(self.root, stale_task)

        self.assertEqual(stale_report["state"], "invalid")
        self.assertEqual(stale_report["next_action"], "bootstrap_exception_invalid")
        self.assertEqual(stale_report["build_packet"]["reason"], PACKET_STALE_CONTRACT)
        self.assertEqual(stale_report["bootstrap_exception"]["reason"], "bootstrap_exception_invalid")

        unbound_task = self.native_builder_task()
        (unbound_task / "bootstrap-exception.json").write_text("{}\n", encoding="utf-8")
        (unbound_task / "packet-build.provenance.json").write_text("{}\n", encoding="utf-8")

        unbound_report = next_action(self.root, unbound_task)

        self.assertEqual(unbound_report["state"], "invalid")
        self.assertEqual(unbound_report["next_action"], "bootstrap_exception_invalid")
        self.assertEqual(unbound_report["build_packet"]["reason"], PACKET_UNBOUND)
        self.assertEqual(unbound_report["bootstrap_exception"]["reason"], "bootstrap_exception_invalid")


if __name__ == "__main__":
    unittest.main()
