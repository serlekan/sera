"""Routing and acceptance verified together across all five corrections."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.controller import auto_task, build_packet, confirm_task_ownership, next_action
from sera.core import (
    SEAL_REVIEW_MISMATCH,
    build_repo_map,
    check_task,
    create_seal,
    decide_route,
    initialize,
    load_task,
    record_evidence,
    record_review,
    task_fingerprint,
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


class CrossFindingTests(unittest.TestCase):
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

    def test_confirmed_high_risk_path_routes_gated_and_binds_reviews(self) -> None:
        # 1. A low-risk task drafted against a harmless file.
        task_dir, _ = auto_task(self.root, "low-risk task", files=["docs/note.py"])
        self.assertEqual(load_task(task_dir)["risk"], "low")
        self.assertEqual(load_task(task_dir)["mode"], "fast")

        # 2. Ownership is confirmed onto a high-risk path: policy must re-run.
        confirm_task_ownership(self.root, task_dir, ["critical/secret.py"])
        task = load_task(task_dir)
        self.assertEqual(task["risk"], "high")
        self.assertEqual(task["mode"], "assured")

        # 3. Routing now demands independent review and the release gate.
        decision = decide_route(self.root, task)
        self.assertEqual(decision.reviewer, "independent_reviewer")
        self.assertEqual(decision.gate, "release_gate")

        # 4. Hand off, implement, evidence, and review both required stages.
        build_packet(self.root, task_dir, "build")
        (self.root / "critical" / "secret.py").write_text("TOKEN = 'rotated'\n", encoding="utf-8")
        record_evidence(task_dir, "python -m unittest", 0, "tests passed")
        fingerprint = task_fingerprint(self.root, task_dir)
        record_review(task_dir, fingerprint, "ship", "independent-peer", "rotation is correct", "independent")
        record_review(task_dir, fingerprint, "ship", "release-gate", "acceptable to release", "gate")

        result = check_task(self.root, task_dir)
        self.assertTrue(result["ok"], msg=result["next_action"])
        self.assertEqual(sorted(result["reviews"]), ["gate", "independent"])

        # 5. The review packet represents the changed file with real material.
        _, packet = build_packet(self.root, task_dir, "review")
        self.assertIn("critical/secret.py", packet)
        self.assertIn("rotated", packet)

        # 6. Seal binds HEAD, tree, and the review ledger.
        seal = create_seal(self.root, task_dir)
        self.assertEqual(seal["schema_version"], 2)
        self.assertIn("review_ledger_fingerprint", seal)
        accepted = check_task(self.root, task_dir)
        self.assertFalse(accepted["seal_stale"])
        self.assertEqual(accepted["seal_status"], "current")
        self.assertEqual(next_action(self.root, task_dir)["state"], "accepted")

        # 7. Mutating an accepted review record breaks acceptance.
        reviews = task_dir / "reviews.jsonl"
        records = [json.loads(line) for line in reviews.read_text(encoding="utf-8").splitlines() if line.strip()]
        records[-1]["reviewer"] = "self-approved"
        reviews.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8"
        )
        mutated = check_task(self.root, task_dir)
        self.assertEqual(mutated["fingerprint"], accepted["fingerprint"])
        self.assertTrue(mutated["seal_stale"])
        self.assertEqual(mutated["seal_status"], "review_mismatch")
        self.assertIn(SEAL_REVIEW_MISMATCH, mutated["seal_stale_reasons"])


if __name__ == "__main__":
    unittest.main()
