"""Explicit `sera task contract --adopt` — the only trusted Task v1 -> v2 transition.

Spec Section 7.5 (nine-step locked flow), Section 7.4 (one canonical append
embedding the complete new contract), Section 19 boundary 1 (capture, then
re-read every binding before the append), and Section 22 (a policy override
alone never promotes a legacy task). Identity-movement tests use real
repository/config/ledger mutations injected between capture and re-read.
"""

from __future__ import annotations

import ast
import io
import json
import subprocess
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sera import cli, provenance
from sera.controller import confirm_task_ownership
from sera.core import SeraError, check_task, load_task, new_task, task_contract_fingerprint, utc_now
from sera.schemas import LockHeld, SchemaError, append_ledger_record, canonical_json, task_lock


SOURCE_ROOT = Path(provenance.__file__).resolve().parent
LIMITATIONS = {
    "scope": "increment_1",
    "legacy_verification_allowed": True,
    "modern_stages_still_required": ["independent_reviewer", "release_gate"],
}


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class AdoptionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "sample.py").write_text("value = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.task_dir = new_task(
            self.root, "adopt", "Update sample", "standard", "low", ["sample.py"], [], ["python -m unittest"]
        )
        self.v1_bytes = (self.task_dir / "task.json").read_bytes()
        self.ledger = self.task_dir / "task-contracts.jsonl"
        self.policy_ledger = self.task_dir / "policy-snapshots.jsonl"

    def adopt(self, **overrides) -> dict:
        kwargs = dict(
            origin="pre_existing",
            actor="Operator",
            reason="Adopt the Increment 1 contract",
            bootstrap_limitations=LIMITATIONS,
        )
        kwargs.update(overrides)
        return provenance.adopt_task_contract(self.root, self.task_dir, **kwargs)

    def assert_rejected(self, code: str, **overrides) -> provenance.TaskContractError:
        before = self.ledger.read_bytes() if self.ledger.exists() else None
        with self.assertRaises(provenance.TaskContractError) as caught:
            self.adopt(**overrides)
        self.assertEqual(caught.exception.code, code, str(caught.exception))
        after = self.ledger.read_bytes() if self.ledger.exists() else None
        self.assertEqual(before, after, "a failed adoption must not append")
        self.assertEqual((self.task_dir / "task.json").read_bytes(), self.v1_bytes)
        return caught.exception

    def ledger_records(self) -> list[dict]:
        return provenance.read_task_contracts(self.task_dir).records()

    def mutate_during(self, name: str, mutation, nth: int = 1):
        """Run `mutation` once, right after the `nth` call to `provenance.<name>`.

        `git_head_identity` is first called while capturing (steps 1-2).
        `build_adoption_record` is first called by the pre-write dry
        construction and a second time for the real record after policy
        resolution (steps 3-5). Either way the mutation lands before the
        step-6 re-read.
        """
        real = getattr(provenance, name)
        calls = {"count": 0}

        def wrapper(*args, **kwargs):
            result = real(*args, **kwargs)
            calls["count"] += 1
            if calls["count"] == nth:
                mutation()
            return result

        return patch.object(provenance, name, side_effect=wrapper)


class PositiveAdoptionTests(AdoptionTestCase):
    def test_adoption_appends_exactly_one_record_embedding_a_valid_contract(self) -> None:
        result = self.adopt()
        records = self.ledger_records()
        self.assertEqual(len(records), 1)
        adoption = records[0]
        self.assertEqual(adoption["record_type"], "task_contract_adoption")
        self.assertEqual(adoption, result["adoption"])
        contract = provenance.validate_task_contract(adoption["new_contract"])
        self.assertEqual(provenance.active_contract(self.task_dir), contract)
        self.assertEqual(contract["implementation_origin"], "pre_existing")
        self.assertEqual(contract["knowledge_sources"], [])
        self.assertEqual(contract["knowledge_fingerprint"], provenance.EMPTY_KNOWLEDGE_FINGERPRINT)
        self.assertEqual(contract["knowledge_assessment_state"], "unassessed_by_pre_increment_2_runtime")
        self.assertEqual(contract["provenance_class"], "modern")
        self.assertEqual(contract["bootstrap_boundary"], LIMITATIONS)
        task_v1 = load_task(self.task_dir)
        for field in ("objective", "mode", "risk", "allowed_files", "constraints", "verification", "use_case"):
            self.assertEqual(contract[field], task_v1[field])
        self.assertEqual(adoption["previous_contract_schema"], 1)
        self.assertEqual(adoption["previous_contract_hash"], provenance.legacy_previous_contract_hash(task_v1))
        self.assertEqual(adoption["previous_task_contract_fingerprint"], task_contract_fingerprint(task_v1))
        self.assertEqual(result["task_contract_fingerprint"], contract["contract_hash"])

    def test_original_task_v1_bytes_are_preserved_and_projection_omitted(self) -> None:
        result = self.adopt()
        self.assertEqual((self.task_dir / "task.json").read_bytes(), self.v1_bytes)
        self.assertEqual(result["projection"], "omitted_legacy_task_v1_preserved")

    def test_contract_and_dynamic_fingerprints_both_change_across_adoption(self) -> None:
        legacy_fp = task_contract_fingerprint(load_task(self.task_dir))
        result = self.adopt()
        modern_fp = result["task_contract_fingerprint"]
        self.assertNotEqual(legacy_fp, modern_fp)
        self.assertNotEqual(
            provenance.dynamic_task_fingerprint(self.root, self.task_dir, legacy_fp),
            provenance.dynamic_task_fingerprint(self.root, self.task_dir, modern_fp),
        )

    def test_top_level_summary_equals_embedded_contract(self) -> None:
        adoption = self.adopt()["adoption"]
        contract = adoption["new_contract"]
        self.assertEqual(adoption["repository_identity"], contract["repository_identity"])
        self.assertEqual(adoption["implementation_origin"], contract["implementation_origin"])
        self.assertEqual(adoption["policy_snapshot_hash"], contract["active_policy_hash"])
        self.assertEqual(adoption["knowledge_sources"], contract["knowledge_sources"])
        self.assertEqual(adoption["knowledge_fingerprint"], contract["knowledge_fingerprint"])
        self.assertEqual(adoption["knowledge_assessment_state"], contract["knowledge_assessment_state"])
        self.assertEqual(adoption["bootstrap_limitations"], contract["bootstrap_boundary"])
        self.assertEqual(adoption["new_contract_hash"], contract["contract_hash"])
        self.assertEqual(adoption["new_task_contract_fingerprint"], contract["contract_hash"])
        self.assertEqual(adoption["actor"], "Operator")
        self.assertEqual(adoption["policy_result"], "permitted")

    def test_adoption_binds_a_policy_snapshot_present_in_the_task_ledger(self) -> None:
        result = self.adopt()
        contract = result["adoption"]["new_contract"]
        self.assertTrue(result["policy_snapshot_appended"])
        snapshot = provenance.active_policy_snapshot(self.task_dir, contract)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["snapshot_hash"], result["policy_snapshot_hash"])
        self.assertEqual(snapshot["trigger"], "task_contract_adoption")

    def test_existing_matching_policy_candidate_is_resolved_not_duplicated(self) -> None:
        candidate = provenance.adopt_policy_snapshot(self.root, self.task_dir, actor="Operator", reason="candidate")
        result = self.adopt()
        self.assertFalse(result["policy_snapshot_appended"])
        self.assertEqual(result["policy_snapshot_hash"], candidate["snapshot_hash"])
        self.assertEqual(len(provenance.read_policy_snapshots(self.task_dir).records()), 1)

    def test_empty_limitations_omit_the_contract_boundary(self) -> None:
        contract = self.adopt(bootstrap_limitations={})["adoption"]["new_contract"]
        self.assertNotIn("bootstrap_boundary", contract)

    def test_re_contract_chains_from_the_active_modern_contract(self) -> None:
        first = self.adopt()["adoption"]["new_contract"]
        second_result = self.adopt(origin="external", reason="Re-contract as external import")
        records = self.ledger_records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["previous_contract_schema"], 2)
        self.assertEqual(records[1]["previous_contract_hash"], first["contract_hash"])
        self.assertEqual(records[1]["previous_task_contract_fingerprint"], first["contract_hash"])
        self.assertEqual(provenance.active_contract(self.task_dir), second_result["adoption"]["new_contract"])
        self.assertEqual((self.task_dir / "task.json").read_bytes(), self.v1_bytes)

    def test_check_task_still_runs_after_adoption(self) -> None:
        self.adopt()
        result = check_task(self.root, self.task_dir)
        self.assertIn("ok", result)


class InputAndOriginGuardTests(AdoptionTestCase):
    def test_sera_builder_without_builder_evidence_is_rejected(self) -> None:
        self.assert_rejected(provenance.BUILDER_PROVENANCE_REQUIRED, origin="sera_builder")
        self.assertFalse(self.policy_ledger.exists(), "a doomed adoption must not append policy first")

    def test_unknown_origin_is_rejected(self) -> None:
        self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_INVALID, origin="imported")

    def test_actor_and_reason_are_explicit_and_nonblank(self) -> None:
        for overrides in ({"actor": " "}, {"reason": ""}, {"actor": None}, {"reason": 7}):
            with self.subTest(**{key: repr(value) for key, value in overrides.items()}):
                self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_INVALID, **overrides)
        self.assertFalse(self.policy_ledger.exists())

    def test_bootstrap_limitations_must_be_a_structured_object(self) -> None:
        for value in (None, "legacy boundary", ["scope"], {"bad": float("nan")}):
            with self.subTest(value=repr(value)):
                self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_INVALID, bootstrap_limitations=value)

    def test_foreign_task_id_in_task_v1_is_rejected(self) -> None:
        task_v1 = json.loads(self.v1_bytes)
        task_v1["id"] = "another-task"
        self.v1_bytes = (json.dumps(task_v1, indent=2) + "\n").encode("utf-8")
        (self.task_dir / "task.json").write_bytes(self.v1_bytes)
        self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_INVALID)

    def test_malformed_existing_history_is_rejected_without_append(self) -> None:
        self.ledger.write_text('{"record_type": "task_contract"\n', encoding="utf-8")
        self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_INVALID)

    def test_identical_re_contract_is_a_duplicate(self) -> None:
        self.adopt()
        self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_DUPLICATE)
        self.assertEqual(len(self.ledger_records()), 1)


class NoOrphanAndAuthorityGuardTests(AdoptionTestCase):
    """Review findings: validate before step 3; the ledger authority enforces the guards itself."""

    def assert_no_partial_write(self, code: str, **overrides) -> None:
        self.assert_rejected(code, **overrides)
        self.assertFalse(self.policy_ledger.exists(), "a doomed adoption must not append a policy snapshot")

    def test_inputs_that_cannot_form_a_valid_record_leave_no_orphan_policy_snapshot(self) -> None:
        cases = (
            {"actor": "a\nb"},
            {"reason": "a\x00b"},
            {"actor": "\ud800"},
            {"bootstrap_limitations": {"a": {"b": {"c": {"d": {"e": {"f": {"g": 1}}}}}}}},
            {"bootstrap_limitations": {f"k{index}": 1 for index in range(70)}},
            {"bootstrap_limitations": {"x": "\ud800"}},
        )
        for overrides in cases:
            with self.subTest(overrides=repr(overrides)[:60]):
                self.assert_no_partial_write(provenance.TASK_CONTRACT_ADOPTION_INVALID, **overrides)

    def test_legacy_task_that_cannot_form_a_contract_leaves_no_orphan(self) -> None:
        task_v1 = json.loads(self.v1_bytes)
        del task_v1["use_case"]
        self.v1_bytes = (json.dumps(task_v1, indent=2) + "\n").encode("utf-8")
        (self.task_dir / "task.json").write_bytes(self.v1_bytes)
        self.assert_no_partial_write(provenance.TASK_CONTRACT_ADOPTION_INVALID)

    def test_append_primitive_refuses_builder_origin_on_a_v1_adoption(self) -> None:
        task_v1 = load_task(self.task_dir)
        contract = provenance.build_task_contract_v2(
            self.task_dir.name,
            **{field: task_v1[field] for field in provenance._CARRIED_CONTRACT_FIELDS},
            repository_identity=provenance.repository_identity(self.root, {}),
            implementation_origin="sera_builder",
            active_policy_hash="ab" * 32,
        )
        record = provenance.build_adoption_record(
            task_v1, contract, adoption_id=str(uuid.uuid4()), head_sha=git(self.root, "rev-parse", "HEAD"),
            tree_sha=git(self.root, "rev-parse", "HEAD^{tree}"), actor="Operator", reason="claim builder",
            adopted_at=utc_now(), bootstrap_limitations={},
        )
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(provenance.TaskContractError) as caught:
                provenance.append_task_contract_adoption(self.task_dir, record, guard)
        self.assertEqual(caught.exception.code, provenance.BUILDER_PROVENANCE_REQUIRED)
        self.assertFalse(self.ledger.exists())

    def test_reader_refuses_a_hand_appended_builder_origin_v1_adoption(self) -> None:
        self.test_append_primitive_refuses_builder_origin_on_a_v1_adoption()
        task_v1 = load_task(self.task_dir)
        contract = provenance.build_task_contract_v2(
            self.task_dir.name,
            **{field: task_v1[field] for field in provenance._CARRIED_CONTRACT_FIELDS},
            repository_identity=provenance.repository_identity(self.root, {}),
            implementation_origin="sera_builder",
            active_policy_hash="ab" * 32,
        )
        record = provenance.build_adoption_record(
            task_v1, contract, adoption_id=str(uuid.uuid4()), head_sha=git(self.root, "rev-parse", "HEAD"),
            tree_sha=git(self.root, "rev-parse", "HEAD^{tree}"), actor="Operator", reason="forged",
            adopted_at=utc_now(), bootstrap_limitations={},
        )
        with task_lock(self.task_dir) as guard:
            append_ledger_record(self.ledger, record, guard)
        with self.assertRaises(provenance.TaskContractError) as caught:
            provenance.active_contract(self.task_dir)
        self.assertEqual(caught.exception.code, provenance.BUILDER_PROVENANCE_REQUIRED)

    def test_append_primitive_refuses_an_identical_contract_with_a_fresh_adoption_id(self) -> None:
        active = self.adopt()["adoption"]["new_contract"]
        record = provenance.build_adoption_record(
            active, active, adoption_id=str(uuid.uuid4()), head_sha=git(self.root, "rev-parse", "HEAD"),
            tree_sha=git(self.root, "rev-parse", "HEAD^{tree}"), actor="Operator", reason="no-op",
            adopted_at=utc_now(), bootstrap_limitations=active.get("bootstrap_boundary", {}),
        )
        before = self.ledger.read_bytes()
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(provenance.TaskContractError) as caught:
                provenance.append_task_contract_adoption(self.task_dir, record, guard)
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_ADOPTION_DUPLICATE)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_coded_reread_failure_keeps_its_own_code(self) -> None:
        real = provenance.repository_identity
        calls = {"count": 0}

        def wrapper(root, config):
            calls["count"] += 1
            if calls["count"] > 1:
                raise provenance.TaskContractError(provenance.TASK_CONTRACT_REPOSITORY_MISMATCH, "unresolvable")
            return real(root, config)

        with patch.object(provenance, "repository_identity", side_effect=wrapper):
            self.assert_rejected(provenance.TASK_CONTRACT_REPOSITORY_MISMATCH)

    def test_auto_drafted_task_with_unconfirmed_ownership_cannot_be_adopted(self) -> None:
        task_v1 = json.loads(self.v1_bytes)
        task_v1["controller"] = {"auto_drafted": True, "ownership_confirmed": False}
        self.v1_bytes = (json.dumps(task_v1, indent=2) + "\n").encode("utf-8")
        (self.task_dir / "task.json").write_bytes(self.v1_bytes)
        self.assert_no_partial_write(provenance.TASK_CONTRACT_ADOPTION_INVALID)
        confirm_task_ownership(self.root, self.task_dir, ["sample.py"])
        self.v1_bytes = (self.task_dir / "task.json").read_bytes()
        self.adopt()
        self.assertIsNotNone(provenance.active_contract(self.task_dir))

    def test_oversize_limitations_file_is_rejected_without_reading_it_whole(self) -> None:
        path = self.root / "big.json"
        path.write_bytes(b'{"a": "' + b"x" * (2 * 1024 * 1024) + b'"}')
        with self.assertRaises(provenance.TaskContractError):
            provenance.load_bootstrap_limitations(path)

    def test_projection_staging_symlink_is_replaced_not_followed(self) -> None:
        native = self.root / ".sera" / "tasks" / "task-native"
        native.mkdir(parents=True)
        victim = self.root / "victim.txt"
        victim.write_text("do not touch\n", encoding="utf-8")
        try:
            (native / ".task.json.projection").symlink_to(victim)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlinks unavailable: {exc}")
        contract = provenance.build_task_contract_v2(
            "task-native", objective="o", requested_mode=None, requested_risk=None, mode="standard", risk="low",
            risk_reasons=[], allowed_files=["sample.py"], constraints=[], verification=[], uncertainty=1,
            use_case="implementation", repository_identity=provenance.repository_identity(self.root, {}),
            implementation_origin="external", active_policy_hash="ab" * 32,
        )
        provenance._write_contract_projection(native, contract)
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not touch\n")


class TocTouTests(AdoptionTestCase):
    def test_head_movement_between_capture_and_reread_is_stale(self) -> None:
        def move_head() -> None:
            (self.root / "sample.py").write_text("value = 2\n", encoding="utf-8")
            git(self.root, "commit", "-am", "moved during adoption")

        with self.mutate_during("git_head_identity", move_head):
            self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_STALE)

    def test_tree_movement_with_same_head_is_stale(self) -> None:
        real = provenance.git_head_identity
        calls = {"count": 0}

        def wrapper(root):
            identity = dict(real(root))
            calls["count"] += 1
            if calls["count"] > 1:
                identity["head_tree_sha"] = "f" * 40
            return identity

        with patch.object(provenance, "git_head_identity", side_effect=wrapper):
            self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_STALE)

    def test_repository_identity_movement_is_a_repository_mismatch(self) -> None:
        real = provenance.repository_identity
        calls = {"count": 0}

        def wrapper(root, config):
            identity = dict(real(root, config))
            calls["count"] += 1
            if calls["count"] > 1:
                identity["logical_id"] = "0" * 64
            return identity

        with patch.object(provenance, "repository_identity", side_effect=wrapper):
            self.assert_rejected(provenance.TASK_CONTRACT_REPOSITORY_MISMATCH)

    def test_policy_movement_between_capture_and_reread_is_stale(self) -> None:
        def move_policy() -> None:
            (self.root / ".sera" / "config.json").write_text(
                json.dumps({"max_packet_chars": 12_345}) + "\n", encoding="utf-8"
            )

        with self.mutate_during("build_adoption_record", move_policy, nth=2):
            self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_STALE)

    def test_legacy_task_json_change_between_capture_and_reread_is_stale(self) -> None:
        def edit_v1() -> None:
            task_v1 = json.loads(self.v1_bytes)
            task_v1["objective"] = "silently widened objective"
            self.v1_bytes = (json.dumps(task_v1, indent=2) + "\n").encode("utf-8")
            (self.task_dir / "task.json").write_bytes(self.v1_bytes)

        with self.mutate_during("git_head_identity", edit_v1):
            self.assert_rejected(provenance.TASK_CONTRACT_ADOPTION_STALE)

    def test_contract_history_movement_between_capture_and_reread_is_stale(self) -> None:
        first = self.adopt()["adoption"]["new_contract"]

        def race_recontract() -> None:
            # A writer that ignores the lock appends a valid re-contract.
            racing = provenance.build_task_contract_v2(
                **{key: first[key] for key in (
                    "task_id", "objective", "requested_mode", "requested_risk", "mode", "risk", "risk_reasons",
                    "allowed_files", "constraints", "verification", "uncertainty", "use_case",
                    "repository_identity", "active_policy_hash",
                )},
                implementation_origin="external",
            )
            record = provenance.build_adoption_record(
                first, racing, adoption_id=str(uuid.uuid4()), head_sha=git(self.root, "rev-parse", "HEAD"),
                tree_sha=git(self.root, "rev-parse", "HEAD^{tree}"), actor="racer", reason="race",
                adopted_at=utc_now(), bootstrap_limitations={},
            )
            with self.ledger.open("a", encoding="utf-8") as handle:
                handle.write(canonical_json(record) + "\n")

        with self.mutate_during("git_head_identity", race_recontract):
            with self.assertRaises(provenance.TaskContractError) as caught:
                self.adopt(bootstrap_limitations={}, reason="second")
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_ADOPTION_STALE)
        self.assertEqual(len(self.ledger_records()), 2, "only the racing line exists; ours was never appended")

    def test_held_task_lock_blocks_adoption(self) -> None:
        with task_lock(self.task_dir):
            with self.assertRaises(LockHeld):
                self.adopt()
        self.assertFalse(self.ledger.exists())
        self.assertFalse(self.policy_ledger.exists())


class LedgerAuthorityTests(AdoptionTestCase):
    def adoption_record(self) -> dict:
        self.adopt()
        return self.ledger_records()[0]

    def test_exact_duplicate_record_is_refused_by_the_append_primitive(self) -> None:
        record = self.adoption_record()
        before = self.ledger.read_bytes()
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(provenance.TaskContractError) as caught:
                provenance.append_task_contract_adoption(self.task_dir, record, guard)
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_ADOPTION_DUPLICATE)
        self.assertEqual(self.ledger.read_bytes(), before)

    def test_exact_duplicate_line_in_history_is_reported_as_duplicate(self) -> None:
        record = self.adoption_record()
        with task_lock(self.task_dir) as guard:
            append_ledger_record(self.ledger, record, guard)
        with self.assertRaises(provenance.TaskContractError) as caught:
            provenance.active_contract(self.task_dir)
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_ADOPTION_DUPLICATE)

    def test_same_adoption_id_with_different_content_conflicts(self) -> None:
        record = self.adoption_record()
        payload = {key: value for key, value in record.items() if key != "adoption_hash"}
        payload["reason"] = "a different event reusing the same id"
        conflicting = {**payload, "adoption_hash": provenance._adoption_hash(payload)}
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(provenance.TaskContractError) as caught:
                provenance.append_task_contract_adoption(self.task_dir, conflicting, guard)
            self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_ADOPTION_CONFLICT)
            append_ledger_record(self.ledger, conflicting, guard)
        with self.assertRaises(provenance.TaskContractError) as caught:
            provenance.active_contract(self.task_dir)
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_ADOPTION_CONFLICT)

    def test_stale_previous_contract_hash_is_stale(self) -> None:
        first = self.adoption_record()["new_contract"]
        second = provenance.build_task_contract_v2(
            **{key: first[key] for key in (
                "task_id", "objective", "requested_mode", "requested_risk", "mode", "risk", "risk_reasons",
                "allowed_files", "constraints", "verification", "uncertainty", "use_case",
                "repository_identity", "active_policy_hash",
            )},
            implementation_origin="external",
        )
        stale = provenance.build_adoption_record(
            load_task(self.task_dir), second, adoption_id=str(uuid.uuid4()),
            head_sha=git(self.root, "rev-parse", "HEAD"), tree_sha=git(self.root, "rev-parse", "HEAD^{tree}"),
            actor="Operator", reason="stale base", adopted_at=utc_now(), bootstrap_limitations={},
        )
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(provenance.TaskContractError) as caught:
                provenance.append_task_contract_adoption(self.task_dir, stale, guard)
        self.assertIn(caught.exception.code, {provenance.TASK_CONTRACT_ADOPTION_STALE})

    def test_wrong_previous_task_contract_fingerprint_is_a_fingerprint_mismatch(self) -> None:
        first = self.adoption_record()["new_contract"]
        second = provenance.build_task_contract_v2(
            **{key: first[key] for key in (
                "task_id", "objective", "requested_mode", "requested_risk", "mode", "risk", "risk_reasons",
                "allowed_files", "constraints", "verification", "uncertainty", "use_case",
                "repository_identity", "active_policy_hash",
            )},
            implementation_origin="external",
        )
        good = provenance.build_adoption_record(
            first, second, adoption_id=str(uuid.uuid4()),
            head_sha=git(self.root, "rev-parse", "HEAD"), tree_sha=git(self.root, "rev-parse", "HEAD^{tree}"),
            actor="Operator", reason="re-contract", adopted_at=utc_now(), bootstrap_limitations={},
        )
        payload = {key: value for key, value in good.items() if key != "adoption_hash"}
        payload["previous_task_contract_fingerprint"] = "0" * 64
        tampered = {**payload, "adoption_hash": provenance._adoption_hash(payload)}
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(provenance.TaskContractError) as caught:
                provenance.append_task_contract_adoption(self.task_dir, tampered, guard)
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_FINGERPRINT_MISMATCH)

    def test_top_level_repository_disagreement_is_a_repository_mismatch(self) -> None:
        record = self.adoption_record()
        payload = {key: value for key, value in record.items() if key != "adoption_hash"}
        payload["adoption_id"] = str(uuid.uuid4())
        payload["repository_identity"] = {**payload["repository_identity"], "logical_id": "0" * 64}
        tampered = {**payload, "adoption_hash": provenance._adoption_hash(payload)}
        with self.assertRaises(provenance.TaskContractError) as caught:
            provenance._validate_task_contract_adoption_shape(tampered)
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_REPOSITORY_MISMATCH)

    def test_new_contract_fingerprint_disagreement_is_a_fingerprint_mismatch(self) -> None:
        record = self.adoption_record()
        payload = {key: value for key, value in record.items() if key != "adoption_hash"}
        payload["new_task_contract_fingerprint"] = "0" * 64
        tampered = {**payload, "adoption_hash": provenance._adoption_hash(payload)}
        with self.assertRaises(provenance.TaskContractError) as caught:
            provenance._validate_task_contract_adoption_shape(tampered)
        self.assertEqual(caught.exception.code, provenance.TASK_CONTRACT_FINGERPRINT_MISMATCH)


class NoSilentPromotionTests(AdoptionTestCase):
    def test_policy_only_adoption_never_performs_contract_adoption(self) -> None:
        with patch("sera.cli.find_repo_root", return_value=self.root), redirect_stdout(io.StringIO()):
            code = cli.main(["task", "policy", self.task_dir.name, "--adopt", "--actor", "Op", "--reason", "policy"])
        self.assertEqual(code, 0)
        self.assertFalse(self.ledger.exists())
        self.assertIsNone(provenance.active_contract(self.task_dir))
        self.assertEqual((self.task_dir / "task.json").read_bytes(), self.v1_bytes)

    def test_adopt_task_contract_is_the_only_production_adoption_writer(self) -> None:
        callers: dict[str, set[str]] = {"append_task_contract_adoption": set(), "append_task_contract": set()}
        for path in sorted(SOURCE_ROOT.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(function):
                    if isinstance(node, ast.Call):
                        name = getattr(node.func, "id", getattr(node.func, "attr", None))
                        if name in callers:
                            callers[name].add(f"{path.stem}.{function.name}")
        self.assertEqual(callers["append_task_contract_adoption"], {"provenance.adopt_task_contract"})
        self.assertEqual(callers["append_task_contract"], set(), "native modern creation is not integrated yet")

    def test_ownership_confirmation_cannot_rewrite_an_adopted_task_v1(self) -> None:
        self.adopt()
        with self.assertRaises(SeraError):
            confirm_task_ownership(self.root, self.task_dir, ["other.py"])
        self.assertEqual((self.task_dir / "task.json").read_bytes(), self.v1_bytes)
        self.assertIsNotNone(provenance.active_contract(self.task_dir))

    def test_reading_and_checking_never_adopt(self) -> None:
        check_task(self.root, self.task_dir)
        provenance.active_contract(self.task_dir)
        self.assertFalse(self.ledger.exists())


class ProjectionTests(unittest.TestCase):
    """Step 9: a derived v2 projection is written last and never rolls back history."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "sample.py").write_text("value = 1\n", encoding="utf-8")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.task_dir = self.root / ".sera" / "tasks" / "task-native"
        self.task_dir.mkdir(parents=True)
        native = provenance.build_task_contract_v2(
            "task-native",
            objective="Native modern task",
            requested_mode=None,
            requested_risk=None,
            mode="standard",
            risk="low",
            risk_reasons=[],
            allowed_files=["sample.py"],
            constraints=[],
            verification=[],
            uncertainty=1,
            use_case="implementation",
            repository_identity=provenance.repository_identity(self.root, {}),
            implementation_origin="external",
            active_policy_hash="ab" * 32,
        )
        with task_lock(self.task_dir) as guard:
            provenance.append_task_contract(self.task_dir, native, guard)
        self.native = native

    def recontract(self) -> dict:
        return provenance.adopt_task_contract(
            self.root, self.task_dir, origin="pre_existing", actor="Operator", reason="re-contract",
            bootstrap_limitations={},
        )

    def test_projection_is_written_from_the_ledger_after_the_append(self) -> None:
        result = self.recontract()
        self.assertEqual(result["projection"], "written")
        projected = json.loads((self.task_dir / "task.json").read_text(encoding="utf-8"))
        self.assertEqual(projected, provenance.active_contract(self.task_dir))
        self.assertEqual(task_contract_fingerprint(projected), result["task_contract_fingerprint"])

    def test_projection_failure_never_rolls_back_the_authoritative_append(self) -> None:
        with patch.object(provenance, "_write_contract_projection", side_effect=OSError("disk full")):
            result = self.recontract()
        self.assertTrue(result["projection"].startswith("failed"))
        self.assertEqual(len(provenance.read_task_contracts(self.task_dir).records()), 2)
        self.assertEqual(provenance.active_contract(self.task_dir), result["adoption"]["new_contract"])
        self.assertFalse((self.task_dir / "task.json").exists())


class ContractAdoptCliTests(AdoptionTestCase):
    def run_cli(self, *options: str) -> tuple[int, str, str]:
        with patch("sera.cli.find_repo_root", return_value=self.root), redirect_stdout(io.StringIO()) as out, \
                redirect_stderr(io.StringIO()) as err:
            code = cli.main(["task", "contract", self.task_dir.name, "--adopt", *options])
        return code, out.getvalue(), err.getvalue()

    def limitations_file(self, content: str) -> str:
        path = self.root / "limitations.json"
        path.write_text(content, encoding="utf-8")
        return str(path)

    def test_cli_adopts_with_explicit_limitations_file(self) -> None:
        code, out, _ = self.run_cli(
            "--origin", "pre_existing", "--actor", "Operator", "--reason", "Increment 1 adoption",
            "--bootstrap-limitations", self.limitations_file(json.dumps(LIMITATIONS)),
        )
        self.assertEqual(code, 0)
        contract = provenance.active_contract(self.task_dir)
        self.assertIsNotNone(contract)
        self.assertEqual(contract["bootstrap_boundary"], LIMITATIONS)
        self.assertIn(contract["contract_hash"], out)
        self.assertIn("legacy", out.lower())
        self.assertEqual((self.task_dir / "task.json").read_bytes(), self.v1_bytes)

    def test_cli_accepts_explicit_absence_of_limitations(self) -> None:
        code, _, _ = self.run_cli(
            "--origin", "external", "--actor", "Operator", "--reason", "import", "--no-bootstrap-limitations"
        )
        self.assertEqual(code, 0)
        self.assertNotIn("bootstrap_boundary", provenance.active_contract(self.task_dir))

    def test_cli_requires_every_explicit_input(self) -> None:
        base = ["--origin", "pre_existing", "--actor", "Operator", "--reason", "r", "--no-bootstrap-limitations"]
        for drop in ("--origin", "--actor", "--reason", "--no-bootstrap-limitations"):
            index = base.index(drop)
            options = base[:index] + base[index + (1 if drop.startswith("--no-") else 2):]
            with self.subTest(missing=drop), self.assertRaises(SystemExit):
                self.run_cli(*options)
        with self.assertRaises(SystemExit):
            self.run_cli(*base, "--bootstrap-limitations", self.limitations_file("{}"))
        self.assertFalse(self.ledger.exists())

    def test_cli_rejects_builder_origin_and_invalid_limitations_without_append(self) -> None:
        code, _, err = self.run_cli(
            "--origin", "sera_builder", "--actor", "Operator", "--reason", "r", "--no-bootstrap-limitations"
        )
        self.assertEqual(code, 2)
        self.assertIn("BUILDER_PROVENANCE_REQUIRED", err)
        for content in ("not json", "[1, 2]", '{"a": 1, "a": 2}'):
            with self.subTest(content=content):
                code, _, _ = self.run_cli(
                    "--origin", "pre_existing", "--actor", "Operator", "--reason", "r",
                    "--bootstrap-limitations", self.limitations_file(content),
                )
                self.assertEqual(code, 2)
        self.assertFalse(self.ledger.exists())


if __name__ == "__main__":
    unittest.main()
