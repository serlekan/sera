"""Freeze the legacy readiness surface before SERA 0.5.0 extraction.

These assertions are governed by design Sections 22.2 and 25. Intentional
0.5.0 changes must update the relevant assertion with the new invariant and a
migration explanation; refactoring alone must leave every value unchanged.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from sera.cli import main
from sera.controller import build_packet
from sera.core import (
    accept_review,
    check_task,
    create_seal,
    load_task,
    record_review,
    task_fingerprint,
)

from _fixtures import move_head, sera_task, temp_repo, working_directory


def run_cli(root: Path, *args: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with working_directory(root), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(list(args))
    return code, stdout.getvalue(), stderr.getvalue()


def run_json(root: Path, *args: str) -> tuple[int, dict]:
    code, stdout, stderr = run_cli(root, *args)
    if stderr:
        raise AssertionError(stderr)
    return code, json.loads(stdout)


class LegacyAssuranceCharacterizationTests(unittest.TestCase):
    def test_clean_task_cli_json_contracts_are_exact(self) -> None:
        # Design §22.2: compatibility facades retain existing output shapes.
        with temp_repo() as root:
            task_dir = sera_task(root)
            task_id = load_task(task_dir)["id"]

            next_code, next_report = run_json(root, "next", "--json")
            check_code, check_report = run_json(root, "check", "--json")
            status_code, status_report = run_json(root, "status", "--json")

            self.assertEqual(next_code, 0)
            self.assertEqual(
                {
                    "task_id": next_report["task_id"],
                    "state": next_report["state"],
                    "next_action": next_report["next_action"],
                    "command": next_report["command"],
                    "reason": next_report["reason"],
                    "required_stage": next_report["required_stage"],
                    "failed_reviews": next_report["failed_reviews"],
                },
                {
                    "task_id": task_id,
                    "state": "build_packet",
                    "next_action": "build_packet",
                    "command": "sera packet build",
                    "reason": "The task is specified but no builder handoff exists.",
                    "required_stage": "build",
                    "failed_reviews": [],
                },
            )
            self.assertEqual(check_code, 0)
            self.assertEqual(
                {
                    "ok": check_report["ok"],
                    "changed_files": check_report["changed_files"],
                    "out_of_scope": check_report["out_of_scope"],
                    "missing_verification": check_report["missing_verification"],
                    "required_review_stages": check_report["required_review_stages"],
                    "seal_status": check_report["seal_status"],
                    "seal_required_failure": check_report["seal_required_failure"],
                    "seal_required_failure_reasons": check_report["seal_required_failure_reasons"],
                    "next_action": check_report["next_action"],
                },
                {
                    "ok": True,
                    "changed_files": [],
                    "out_of_scope": [],
                    "missing_verification": [],
                    "required_review_stages": [],
                    "seal_status": "none",
                    "seal_required_failure": False,
                    "seal_required_failure_reasons": [],
                    "next_action": "Create the SERA Seal, then proceed to the separate commit decision.",
                },
            )
            self.assertEqual(status_code, 0)
            self.assertEqual(status_report, {key: value for key, value in check_report.items() if not key.startswith("seal_required_")})

    def test_out_of_scope_change_has_exact_blocking_actions(self) -> None:
        # Design §25: scope remains higher precedence than review dispatch.
        with temp_repo() as root:
            sera_task(root)
            (root / "README.md").write_text("outside\n", encoding="utf-8")

            check_code, check_report = run_json(root, "check", "--json")
            next_code, next_report = run_json(root, "next", "--json")

            self.assertEqual(check_code, 2)
            self.assertEqual(check_report["out_of_scope"], ["README.md"])
            self.assertEqual(check_report["next_action"], "Split or revert out-of-scope files before continuing.")
            self.assertEqual(next_code, 0)
            self.assertEqual(next_report["state"], "resolve_scope")
            self.assertEqual(
                next_report["reason"],
                "This task changed README.md outside its declared ownership. Split or revert the out-of-scope work, "
                "or declare ownership of it deliberately; review coverage cannot be complete while it stands.",
            )

    def test_missing_verification_has_exact_blocking_actions(self) -> None:
        # Design §25: required verification remains contract-bound.
        with temp_repo() as root:
            sera_task(root, verification=["python -m unittest"])
            (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

            check_code, check_report = run_json(root, "check", "--json")
            next_code, next_report = run_json(root, "next", "--json")

            self.assertEqual(check_code, 2)
            self.assertEqual(check_report["missing_verification"], ["python -m unittest"])
            self.assertEqual(
                check_report["next_action"],
                "Run `sera verify` or record the missing verification evidence.",
            )
            self.assertEqual(next_code, 0)
            self.assertEqual(
                (next_report["state"], next_report["command"], next_report["reason"]),
                ("build_packet", "sera packet build", "The task is specified but no builder handoff exists."),
            )

    def _review_task(self, root: Path, verdict: str = "ship") -> Path:
        task_dir = sera_task(root, mode="assured", risk="high")
        build_packet(root, task_dir, "build")
        (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        build_packet(root, task_dir, "review")
        accept_review(root, task_dir, verdict, "peer", "characterization", "independent")
        return task_dir

    def test_stale_review_after_head_move_is_exact(self) -> None:
        # Design §25: exact HEAD identity, not equal tree alone, controls currency.
        with temp_repo() as root:
            self._review_task(root)
            move_head(root)

            report = check_task(root, root / ".sera" / "tasks" / load_task_id(root))
            _, next_report = run_json(root, "next", "--json")

            self.assertEqual(report["stale_reviews"], ["independent"])
            self.assertEqual(report["stale_review_reasons"], {"independent": ["review_head_mismatch"]})
            self.assertEqual(
                report["next_action"],
                "HEAD moved after review; the accepted reviews describe a different commit. "
                "Regenerate the review packet and repeat the review at the current HEAD.",
            )
            self.assertEqual(next_report["state"], "review")
            self.assertEqual(
                next_report["reason"],
                "One or more required reviews no longer describe the current repository state "
                "(review_head_mismatch). Regenerate the review packet and repeat those stages.",
            )

    def test_failed_review_outranks_missing_gate(self) -> None:
        # Design §25: a current rejection blocks later-stage dispatch.
        with temp_repo() as root:
            self._review_task(root, verdict="fix-first")
            code, report = run_json(root, "next", "--json")

            self.assertEqual(code, 0)
            self.assertEqual(report["failed_reviews"], ["independent"])
            self.assertEqual(report["state"], "fix_first")
            self.assertEqual(
                report["reason"],
                "A current required review returned `fix-first` at the independent stage. "
                "Address the findings before any later stage.",
            )

    def test_manual_unbound_legacy_review_is_readable_but_stale(self) -> None:
        # Design §22.2: unversioned history is readable but never strengthened.
        with temp_repo() as root:
            task_dir = sera_task(root, mode="standard", risk="medium")
            (root / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
            record_review(task_dir, task_fingerprint(root, task_dir), "ship", "legacy-peer", "legacy")

            report = check_task(root, task_dir)

            self.assertEqual(report["stale_reviews"], ["independent"])
            self.assertEqual(report["stale_review_reasons"], {"independent": ["review_repository_unbound"]})
            self.assertEqual(
                report["next_action"],
                "A required review predates exact-HEAD binding and cannot satisfy 0.4.2 acceptance. "
                "Regenerate the review packet and repeat the review.",
            )

    def test_seal_cli_and_stale_missing_states_are_exact(self) -> None:
        # Design §22.2: legacy seal command output and exact-HEAD staleness remain stable.
        with temp_repo() as root:
            task_dir = sera_task(root)
            code, stdout, stderr = run_cli(root, "seal")
            seal = json.loads((task_dir / "seal.json").read_text(encoding="utf-8"))

            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            self.assertEqual(
                stdout,
                f"Sealed {seal['task_id']} at fingerprint {seal['fingerprint'][:16]}\n"
                f"Bound to HEAD {seal['repository_identity']['head_sha']}\n"
                f"Bound to HEAD tree {seal['repository_identity']['head_tree_sha']}\n"
                f"Bound to review ledger {seal['review_ledger_fingerprint'][:16]}\n",
            )
            self.assertEqual(check_task(root, task_dir)["seal_status"], "current")
            move_head(root)
            stale = check_task(root, task_dir)
            self.assertEqual(stale["seal_status"], "head_mismatch")
            self.assertEqual(stale["seal_stale_reasons"], ["seal_head_mismatch"])

        with temp_repo() as root:
            task_dir = sera_task(root)
            missing = check_task(root, task_dir)
            self.assertEqual((missing["seal"], missing["seal_status"], missing["seal_stale"]), (None, "none", False))

    def test_legacy_and_unsupported_seal_schemas_fail_closed_exactly(self) -> None:
        # Design §§6 and 22.2: legacy remains legacy; unsupported never becomes current.
        with temp_repo() as root:
            task_dir = sera_task(root)
            seal = create_seal(root, task_dir)
            legacy = {
                "schema_version": 1,
                "task_id": seal["task_id"],
                "sealed_at": seal["sealed_at"],
                "fingerprint": seal["fingerprint"],
                "evidence_records": seal["evidence_records"],
                "review_stages": seal["review_stages"],
                "changed_files": seal["changed_files"],
            }
            (task_dir / "seal.json").write_text(json.dumps(legacy, indent=2), encoding="utf-8")
            legacy_report = check_task(root, task_dir)
            self.assertEqual(
                (legacy_report["seal_status"], legacy_report["seal_stale"], legacy_report["seal_stale_reasons"]),
                ("legacy_unbound", True, ["seal_missing_head_identity"]),
            )

            legacy["schema_version"] = 999
            (task_dir / "seal.json").write_text(json.dumps(legacy, indent=2), encoding="utf-8")
            unsupported = check_task(root, task_dir)
            self.assertEqual(
                (unsupported["seal_status"], unsupported["seal_stale"], unsupported["seal_stale_reasons"]),
                ("schema_unsupported", True, ["seal_schema_unsupported"]),
            )


def load_task_id(root: Path) -> str:
    return (root / ".sera" / "latest-task").read_text(encoding="utf-8").strip()


if __name__ == "__main__":
    unittest.main()
