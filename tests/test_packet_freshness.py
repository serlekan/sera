"""Generated artifacts are freshness-bound to the task contract."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import build_packet, confirm_task_ownership, next_action
from sera.core import (
    PACKET_MISSING,
    PACKET_STALE_CONTRACT,
    PACKET_STALE_STATE,
    PACKET_UNBOUND,
    build_repo_map,
    check_task,
    initialize,
    load_task,
    new_task,
    packet_provenance_path,
    packet_state,
    record_evidence,
    record_review,
    save_task,
    task_contract_fingerprint,
    task_fingerprint,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class PacketFreshnessTests(unittest.TestCase):
    """Reproduces the fail-open orchestration defect: after ownership changed,
    `sera next` returned `dispatch_builder` for a stale fast/low packet."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "docs").mkdir()
        (self.root / "critical").mkdir()
        (self.root / "docs" / "note.py").write_text("NOTE = 1\n", encoding="utf-8")
        (self.root / "critical" / "secret.py").write_text("TOKEN = 'x'\n", encoding="utf-8")
        initialize(self.root)
        path = self.root / ".sera" / "config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        config["default_mode"] = "fast"
        config["risk_policy"] = {"high_risk_terms": [], "high_risk_paths": ["critical/**"]}
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        build_repo_map(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def low_risk_task(self) -> Path:
        return new_task(
            self.root, "low risk task", "low-risk task", allowed_files=["docs/note.py"]
        )

    # --- Test A: the exact reviewer reproduction ---------------------------
    def test_stale_packet_is_never_dispatched_after_ownership_escalation(self) -> None:
        task_dir = self.low_risk_task()
        build_packet(self.root, task_dir, "build")
        self.assertEqual(next_action(self.root, task_dir)["state"], "dispatch_builder")

        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")

        state = packet_state(self.root, task_dir, "build", task)
        self.assertTrue(state["exists"])
        self.assertFalse(state["current"])
        self.assertEqual(state["reason"], PACKET_STALE_CONTRACT)

        nxt = next_action(self.root, task_dir)
        self.assertNotEqual(nxt["state"], "dispatch_builder")
        self.assertEqual(nxt["state"], "build_packet")

        _, text = build_packet(self.root, task_dir, "build")
        self.assertIn("critical/secret.py", text)
        self.assertNotIn("docs/note.py", text)
        self.assertIn("deep_builder", text)
        self.assertNotIn("fast_builder", text)
        self.assertIn("independent_reviewer", text)
        self.assertIn("release_gate", text)
        self.assertEqual(next_action(self.root, task_dir)["state"], "dispatch_builder")

    # --- Test B: capsule freshness -----------------------------------------
    def test_capsule_is_refreshed_by_ownership_confirmation(self) -> None:
        task_dir = self.low_risk_task()
        capsule = task_dir / "capsule.md"
        self.assertIn("docs/note.py", capsule.read_text(encoding="utf-8"))

        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        refreshed = capsule.read_text(encoding="utf-8")
        self.assertIn("critical/secret.py", refreshed)
        self.assertNotIn("docs/note.py", refreshed)
        self.assertIn("Mode: `assured`", refreshed)
        self.assertIn("Risk: `high`", refreshed)

    # --- Test C: high -> low ------------------------------------------------
    def test_packet_generated_under_high_risk_goes_stale_when_ownership_drops(self) -> None:
        task_dir = self.low_risk_task()
        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        build_packet(self.root, task_dir, "build")
        self.assertTrue(packet_state(self.root, task_dir, "build", load_task(task_dir))["current"])

        confirm_task_ownership(self.root, task_dir, ["docs/note.py"])
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "low")
        self.assertEqual(task["mode"], "fast")
        self.assertFalse(packet_state(self.root, task_dir, "build", task)["current"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "build_packet")

        _, text = build_packet(self.root, task_dir, "build")
        self.assertIn("docs/note.py", text)
        self.assertNotIn("critical/secret.py", text)
        self.assertIn("fast_builder", text)

    # --- Test D: semantically identical reconfirmation ----------------------
    def test_reconfirming_identical_ownership_keeps_the_packet_current(self) -> None:
        task_dir = self.low_risk_task()
        before = task_contract_fingerprint(load_task(task_dir))
        build_packet(self.root, task_dir, "build")

        confirm_task_ownership(self.root, task_dir, ["docs/note.py"])
        task = load_task(task_dir)
        self.assertEqual(task_contract_fingerprint(task), before)
        self.assertTrue(packet_state(self.root, task_dir, "build", task)["current"])
        self.assertEqual(next_action(self.root, task_dir)["state"], "dispatch_builder")

    # --- Test E: verification requirements are contract state ---------------
    def test_changing_verification_requirements_invalidates_the_packet(self) -> None:
        task_dir = self.low_risk_task()
        build_packet(self.root, task_dir, "build")
        task = load_task(task_dir)
        self.assertTrue(packet_state(self.root, task_dir, "build", task)["current"])

        task["verification"] = ["python -m unittest"]
        save_task(task_dir, task)
        self.assertFalse(packet_state(self.root, task_dir, "build", load_task(task_dir))["current"])

    def test_evidence_from_an_older_contract_does_not_satisfy_the_new_one(self) -> None:
        task_dir = new_task(
            self.root, "verified task", "low-risk task",
            allowed_files=["docs/note.py"], verification=["python -c 'pass'"],
        )
        record_evidence(task_dir, "python -c 'pass'", 0, "ok")
        self.assertEqual(check_task(self.root, task_dir)["missing_verification"], [])

        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        result = check_task(self.root, task_dir)
        self.assertEqual(result["missing_verification"], ["python -c 'pass'"])
        # The historical record is retained for audit.
        ledger = (task_dir / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(ledger), 1)

    # --- Test F: tampered or missing provenance fails closed ----------------
    def test_tampered_provenance_refuses_dispatch(self) -> None:
        task_dir = self.low_risk_task()
        build_packet(self.root, task_dir, "build")
        path = packet_provenance_path(task_dir, "build")
        provenance = json.loads(path.read_text(encoding="utf-8"))
        provenance["task_contract_fingerprint"] = "0" * 64
        path.write_text(json.dumps(provenance), encoding="utf-8")
        self.assertEqual(
            packet_state(self.root, task_dir, "build", load_task(task_dir))["reason"], PACKET_STALE_CONTRACT
        )
        self.assertEqual(next_action(self.root, task_dir)["state"], "build_packet")

    def test_unbound_packet_fails_closed(self) -> None:
        task_dir = self.low_risk_task()
        build_packet(self.root, task_dir, "build")
        packet_provenance_path(task_dir, "build").unlink()
        state = packet_state(self.root, task_dir, "build", load_task(task_dir))
        self.assertTrue(state["exists"])
        self.assertFalse(state["current"])
        self.assertEqual(state["reason"], PACKET_UNBOUND)
        self.assertEqual(next_action(self.root, task_dir)["state"], "build_packet")

    def test_malformed_provenance_fails_closed(self) -> None:
        task_dir = self.low_risk_task()
        build_packet(self.root, task_dir, "build")
        packet_provenance_path(task_dir, "build").write_text("{not json", encoding="utf-8")
        self.assertEqual(
            packet_state(self.root, task_dir, "build", load_task(task_dir))["reason"], PACKET_UNBOUND
        )

    def test_missing_packet_reports_missing(self) -> None:
        task_dir = self.low_risk_task()
        self.assertEqual(packet_state(self.root, task_dir, "build", load_task(task_dir))["reason"], PACKET_MISSING)

    # --- Test G: review packet freshness ------------------------------------
    def test_review_packet_goes_stale_on_contract_and_state_change(self) -> None:
        task_dir = new_task(
            self.root, "review freshness", "low-risk task",
            mode="standard", risk="medium", allowed_files=["docs/note.py"],
        )
        (self.root / "docs" / "note.py").write_text("NOTE = 2\n", encoding="utf-8")
        build_packet(self.root, task_dir, "build")
        build_packet(self.root, task_dir, "review")
        task = load_task(task_dir)
        self.assertTrue(
            packet_state(self.root, task_dir, "review", task, task_fingerprint(self.root, task_dir))["current"]
        )
        self.assertEqual(next_action(self.root, task_dir)["state"], "dispatch_review")

        # Working-tree state moves on: the embedded diff is no longer accurate.
        (self.root / "docs" / "note.py").write_text("NOTE = 3\n", encoding="utf-8")
        state = packet_state(self.root, task_dir, "review", task, task_fingerprint(self.root, task_dir))
        self.assertFalse(state["current"])
        self.assertEqual(state["reason"], PACKET_STALE_STATE)
        self.assertEqual(next_action(self.root, task_dir)["state"], "review_packet")

        build_packet(self.root, task_dir, "review")
        self.assertEqual(next_action(self.root, task_dir)["state"], "dispatch_review")

        # A contract mutation invalidates it too.
        confirm_task_ownership(self.root, task_dir, ["docs/note.py", "critical/secret.py"])
        self.assertFalse(
            packet_state(
                self.root, task_dir, "review", load_task(task_dir), task_fingerprint(self.root, task_dir)
            )["current"]
        )

    def test_route_is_recorded_in_packet_provenance(self) -> None:
        task_dir = self.low_risk_task()
        build_packet(self.root, task_dir, "build")
        provenance = json.loads(packet_provenance_path(task_dir, "build").read_text(encoding="utf-8"))
        self.assertEqual(provenance["route"]["builder"]["lane"], "fast_builder")
        self.assertEqual(provenance["route"]["builder"]["provider"], "openai")
        self.assertIsNone(provenance["route"]["reviewer"])

        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        build_packet(self.root, task_dir, "build")
        provenance = json.loads(packet_provenance_path(task_dir, "build").read_text(encoding="utf-8"))
        self.assertEqual(provenance["route"]["builder"]["lane"], "deep_builder")
        self.assertEqual(provenance["route"]["reviewer"]["lane"], "independent_reviewer")
        self.assertEqual(provenance["route"]["gate"]["lane"], "release_gate")

    def test_contract_fingerprint_excludes_generated_and_volatile_state(self) -> None:
        task_dir = self.low_risk_task()
        task = load_task(task_dir)
        before = task_contract_fingerprint(task)
        task["status"] = "in-progress"
        task["builder_attempts"] = 3
        task["controller"] = {"ownership_confirmed_at": "2026-01-01T00:00:00+00:00"}
        self.assertEqual(task_contract_fingerprint(task), before)

    def test_seal_still_reachable_through_a_refreshed_contract(self) -> None:
        task_dir = self.low_risk_task()
        confirm_task_ownership(self.root, task_dir, ["docs/note.py"])
        build_packet(self.root, task_dir, "build")
        (self.root / "docs" / "note.py").write_text("NOTE = 9\n", encoding="utf-8")
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "peer", "fine")
        self.assertTrue(check_task(self.root, task_dir)["ok"])


if __name__ == "__main__":
    unittest.main()
