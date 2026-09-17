"""TaskContractV2, TaskContractAdoptionV1, and the task-contracts.jsonl authority.

T11 defines the authoritative append-only history for modern task contracts
(spec Section 7.4): a native `TaskContractV2` line, or a `TaskContractAdoptionV1`
line embedding the complete new contract. It provides strict schema/hash
validation, full-history chain validation (`active_contract`), and the two
structural append primitives. The trusted `sera task contract --adopt`
operation is T12 and is not implemented here.
"""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from sera import provenance
from sera.core import (
    git_head_identity,
    load_config,
    task_contract_fingerprint,
    utc_now,
)
from sera.schemas import SchemaError, canonical_json, sha256_domain, task_lock


DUMMY_POLICY_HASH = "ab" * 32
OTHER_POLICY_HASH = "cd" * 32


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, encoding="utf-8", errors="replace"
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def make_repo() -> Path:
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")
    (root / ".sera").mkdir()
    (root / "src.py").write_text("value = 1\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "baseline")
    return temp, root


def repo_identity_for(root: Path) -> dict[str, object]:
    return provenance.repository_identity(root, load_config(root))


def contract_kwargs(task_id: str, root: Path, **overrides) -> dict[str, object]:
    values = dict(
        objective="Implement the thing",
        requested_mode=None,
        requested_risk=None,
        mode="standard",
        risk="low",
        risk_reasons=[],
        allowed_files=["src.py"],
        constraints=[],
        verification=["python -m unittest"],
        uncertainty=1,
        use_case="implementation",
        repository_identity=repo_identity_for(root),
        implementation_origin="sera_builder",
        active_policy_hash=DUMMY_POLICY_HASH,
    )
    values.update(overrides)
    return {"task_id": task_id, **values}


def make_contract(task_id: str, root: Path, **overrides) -> dict[str, object]:
    return provenance.build_task_contract_v2(**contract_kwargs(task_id, root, **overrides))


def rehash_contract(contract: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(contract)
    payload = {key: item for key, item in value.items() if key != "contract_hash"}
    value["contract_hash"] = __import__("hashlib").sha256(canonical_json(payload).encode()).hexdigest()
    return value


def rehash_adoption(adoption: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(adoption)
    payload = {key: item for key, item in value.items() if key != "adoption_hash"}
    value["adoption_hash"] = __import__("hashlib").sha256(canonical_json(payload).encode()).hexdigest()
    return value


def legacy_task_v1(task_id: str = "task-legacy") -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": task_id,
        "name": "legacy",
        "created_at": "2026-01-01T00:00:00+00:00",
        "mode": "standard",
        "mode_source": "default",
        "requested_mode": None,
        "risk": "low",
        "requested_risk": None,
        "risk_reasons": [],
        "uncertainty": 1,
        "use_case": "implementation",
        "objective": "legacy work",
        "allowed_files": ["src.py"],
        "constraints": [],
        "verification": [],
        "builder_attempts": 0,
        "status": "specified",
        "baseline_changes": {},
    }


def make_adoption(
    task_dir: Path,
    root: Path,
    *,
    previous,
    new_contract,
    bootstrap_limitations: dict | None = None,
) -> dict[str, object]:
    head = git_head_identity(root)
    return provenance.build_adoption_record(
        previous,
        new_contract,
        adoption_id=str(uuid.uuid4()),
        head_sha=head["head_sha"],
        tree_sha=head["head_tree_sha"],
        actor="tester",
        reason="adoption",
        adopted_at=utc_now(),
        bootstrap_limitations=bootstrap_limitations if bootstrap_limitations is not None else {},
    )


class TaskContractV2ShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp, self.root = make_repo()
        self.addCleanup(self.temp.cleanup)

    def test_exact_field_set_and_identity(self) -> None:
        contract = make_contract("task-1", self.root)
        self.assertEqual(
            set(contract),
            {
                "schema_version", "record_type", "task_id", "objective", "requested_mode", "requested_risk",
                "mode", "risk", "risk_reasons", "allowed_files", "constraints", "verification", "uncertainty",
                "use_case", "repository_identity", "implementation_origin", "active_policy_hash",
                "knowledge_sources", "knowledge_fingerprint", "knowledge_assessment_state", "provenance_class",
                "contract_hash",
            },
        )
        self.assertEqual(contract["schema_version"], 2)
        self.assertEqual(contract["record_type"], "task_contract")
        self.assertEqual(contract["knowledge_sources"], [])
        self.assertEqual(contract["knowledge_fingerprint"], provenance.EMPTY_KNOWLEDGE_FINGERPRINT)
        self.assertEqual(contract["knowledge_assessment_state"], "unassessed_by_pre_increment_2_runtime")
        self.assertEqual(contract["provenance_class"], "modern")
        self.assertEqual(task_contract_fingerprint(contract), contract["contract_hash"])

    def test_bootstrap_boundary_is_optional_and_omitted_when_absent(self) -> None:
        contract = make_contract("task-1", self.root)
        self.assertNotIn("bootstrap_boundary", contract)
        with_boundary = make_contract("task-1", self.root, bootstrap_boundary={"scope": "increment1"})
        self.assertEqual(with_boundary["bootstrap_boundary"], {"scope": "increment1"})

    def test_contract_hash_excludes_only_itself(self) -> None:
        contract = make_contract("task-1", self.root)
        mutated = copy.deepcopy(contract)
        mutated["objective"] = "A different objective entirely"
        mutated["contract_hash"] = provenance._contract_hash(mutated)
        self.assertNotEqual(mutated["contract_hash"], contract["contract_hash"])

        same_payload = copy.deepcopy(contract)
        same_payload["contract_hash"] = "00" * 32
        recomputed = provenance._contract_hash(same_payload)
        self.assertEqual(recomputed, contract["contract_hash"])

    def test_every_semantic_field_is_hash_bound(self) -> None:
        base = make_contract("task-1", self.root)
        for field, new_value in (
            ("mode", "assured"),
            ("risk", "high"),
            ("uncertainty", 2),
            ("use_case", "bugfix"),
            ("implementation_origin", "external"),
            ("active_policy_hash", OTHER_POLICY_HASH),
            ("provenance_class", "modern"),
        ):
            with self.subTest(field=field):
                mutated = copy.deepcopy(base)
                if mutated[field] == new_value:
                    continue
                mutated[field] = new_value
                self.assertNotEqual(provenance._contract_hash(mutated), base["contract_hash"])

    def test_unknown_field_is_rejected(self) -> None:
        contract = rehash_contract({**make_contract("task-1", self.root), "extra": "nope"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(contract)

    def test_missing_required_field_is_rejected(self) -> None:
        contract = make_contract("task-1", self.root)
        del contract["objective"]
        contract = rehash_contract(contract)
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(contract)

    def test_unsupported_schema_version_is_rejected(self) -> None:
        contract = rehash_contract({**make_contract("task-1", self.root), "schema_version": 3})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(contract)

    def test_wrong_record_type_is_rejected(self) -> None:
        contract = rehash_contract({**make_contract("task-1", self.root), "record_type": "task"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(contract)

    def test_foreign_task_is_rejected_by_ledger_binding(self) -> None:
        contract = make_contract("task-1", self.root)
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract(contract, "task-2")

    def test_requested_mode_and_risk_accept_null_or_valid_value(self) -> None:
        contract = make_contract("task-1", self.root, requested_mode="fast", requested_risk="medium")
        self.assertEqual(contract["requested_mode"], "fast")
        bad = rehash_contract({**contract, "requested_mode": "not-a-mode"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(bad)

    def test_uncertainty_out_of_range_is_rejected(self) -> None:
        bad = rehash_contract({**make_contract("task-1", self.root), "uncertainty": 4})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(bad)

    def test_allowed_files_must_be_normalized_and_duplicate_free(self) -> None:
        with self.assertRaises(Exception):
            make_contract("task-1", self.root, allowed_files=["./src.py"])
        with self.assertRaises(Exception):
            make_contract("task-1", self.root, allowed_files=["src.py", "src.py"])
        with self.assertRaises(Exception):
            make_contract("task-1", self.root, allowed_files=["../outside.py"])
        with self.assertRaises(Exception):
            make_contract("task-1", self.root, allowed_files=["/abs/path.py"])

    def test_knowledge_fingerprint_must_match_knowledge_sources(self) -> None:
        contract = make_contract("task-1", self.root)
        bad = rehash_contract({**contract, "knowledge_fingerprint": "00" * 32})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(bad)

    def test_provenance_class_must_be_modern(self) -> None:
        bad = rehash_contract({**make_contract("task-1", self.root), "provenance_class": "legacy"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(bad)

    def test_bootstrap_boundary_null_is_rejected(self) -> None:
        contract = make_contract("task-1", self.root)
        contract["bootstrap_boundary"] = None
        bad = rehash_contract(contract)
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(bad)

    def test_repository_identity_reuses_t05_validator(self) -> None:
        contract = make_contract("task-1", self.root)
        bad = rehash_contract({**contract, "repository_identity": {"schema_version": 1}})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(bad)

    def test_unsupported_implementation_origin_fails_closed(self) -> None:
        bad = rehash_contract({**make_contract("task-1", self.root), "implementation_origin": "hostile_claim"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_shape(bad)


class TaskContractAdoptionV1ShapeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp, self.root = make_repo()
        self.addCleanup(self.temp.cleanup)
        self.task_v1 = legacy_task_v1()
        self.new_contract = make_contract(
            "task-legacy", self.root, objective="legacy work", implementation_origin="pre_existing"
        )

    def build(self, **overrides) -> dict[str, object]:
        return make_adoption(None, self.root, previous=self.task_v1, new_contract=self.new_contract, **overrides)

    def test_valid_intrinsic_adoption_shape(self) -> None:
        adoption = self.build()
        self.assertEqual(adoption["schema_version"], 1)
        self.assertEqual(adoption["record_type"], "task_contract_adoption")
        self.assertEqual(adoption["previous_contract_schema"], 1)
        self.assertEqual(adoption["new_contract_schema"], 2)
        self.assertEqual(adoption["new_contract"], self.new_contract)
        self.assertEqual(adoption["new_contract_hash"], self.new_contract["contract_hash"])
        self.assertEqual(adoption["new_task_contract_fingerprint"], self.new_contract["contract_hash"])
        self.assertEqual(
            adoption["previous_contract_hash"], provenance.legacy_previous_contract_hash(self.task_v1)
        )
        self.assertEqual(
            adoption["previous_task_contract_fingerprint"], task_contract_fingerprint(self.task_v1)
        )

    def test_top_level_summaries_equal_embedded_contract(self) -> None:
        adoption = self.build()
        self.assertEqual(adoption["task_id"], self.new_contract["task_id"])
        self.assertEqual(adoption["repository_identity"], self.new_contract["repository_identity"])
        self.assertEqual(adoption["implementation_origin"], self.new_contract["implementation_origin"])
        self.assertEqual(adoption["policy_snapshot_hash"], self.new_contract["active_policy_hash"])
        self.assertEqual(adoption["knowledge_assessment_state"], self.new_contract["knowledge_assessment_state"])
        self.assertEqual(adoption["knowledge_sources"], self.new_contract["knowledge_sources"])
        self.assertEqual(adoption["knowledge_fingerprint"], self.new_contract["knowledge_fingerprint"])

    def test_mismatched_top_level_summary_is_rejected(self) -> None:
        adoption = self.build()
        for field, bad_value in (
            ("implementation_origin", "external"),
            ("policy_snapshot_hash", OTHER_POLICY_HASH),
            ("knowledge_assessment_state", "assessed"),
        ):
            with self.subTest(field=field):
                tampered = rehash_adoption({**adoption, field: bad_value})
                with self.assertRaises(SchemaError):
                    provenance._validate_task_contract_adoption_shape(tampered)

    def test_new_contract_hash_must_equal_embedded_contract_hash(self) -> None:
        adoption = self.build()
        tampered = rehash_adoption({**adoption, "new_contract_hash": "00" * 32})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption_shape(tampered)

    def test_bootstrap_limitations_must_equal_contract_boundary_when_present(self) -> None:
        contract_with_boundary = make_contract(
            "task-legacy", self.root, objective="legacy work", implementation_origin="pre_existing",
            bootstrap_boundary={"scope": "increment1"},
        )
        good = make_adoption(
            None, self.root, previous=self.task_v1, new_contract=contract_with_boundary,
            bootstrap_limitations={"scope": "increment1"},
        )
        self.assertEqual(good["bootstrap_limitations"], contract_with_boundary["bootstrap_boundary"])
        with self.assertRaises(SchemaError):
            make_adoption(
                None, self.root, previous=self.task_v1, new_contract=contract_with_boundary,
                bootstrap_limitations={"scope": "different"},
            )

    def test_adoption_hash_excludes_only_itself(self) -> None:
        adoption = self.build()
        mutated = copy.deepcopy(adoption)
        mutated["reason"] = "a totally different reason"
        recomputed = provenance._adoption_hash(mutated)
        self.assertNotEqual(recomputed, adoption["adoption_hash"])
        same_payload = copy.deepcopy(adoption)
        same_payload["adoption_hash"] = "00" * 32
        self.assertEqual(provenance._adoption_hash(same_payload), adoption["adoption_hash"])

    def test_unknown_field_is_rejected(self) -> None:
        tampered = rehash_adoption({**self.build(), "extra": "nope"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption_shape(tampered)

    def test_missing_field_is_rejected(self) -> None:
        adoption = self.build()
        del adoption["reason"]
        tampered = rehash_adoption(adoption)
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption_shape(tampered)

    def test_foreign_task_is_rejected_by_ledger_binding(self) -> None:
        adoption = self.build()
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption(adoption, "some-other-task")

    def test_bad_uuid_is_rejected(self) -> None:
        tampered = rehash_adoption({**self.build(), "adoption_id": "not-a-uuid"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption_shape(tampered)

    def test_bad_timestamp_is_rejected(self) -> None:
        tampered = rehash_adoption({**self.build(), "adopted_at": "not-a-timestamp"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption_shape(tampered)

    def test_bad_policy_result_is_rejected(self) -> None:
        tampered = rehash_adoption({**self.build(), "policy_result": "denied"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption_shape(tampered)

    def test_bad_head_or_tree_sha_is_rejected(self) -> None:
        tampered = rehash_adoption({**self.build(), "tree_sha": "not-a-sha"})
        with self.assertRaises(SchemaError):
            provenance._validate_task_contract_adoption_shape(tampered)


class ChainValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp, self.root = make_repo()
        self.addCleanup(self.temp.cleanup)

    def task_dir(self, task_id: str) -> Path:
        task_dir = self.root / ".sera" / "tasks" / task_id
        task_dir.mkdir(parents=True)
        return task_dir

    def test_native_contract_as_first_line_is_active(self) -> None:
        task_dir = self.task_dir("task-native")
        contract = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, contract, guard)
        self.assertEqual(provenance.active_contract(task_dir), contract)

    def test_valid_later_adoption_makes_embedded_contract_active(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        second = make_contract("task-native", self.root, objective="A revised objective")
        adoption = make_adoption(task_dir, self.root, previous=first, new_contract=second)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract_adoption(task_dir, adoption, guard)
        self.assertEqual(provenance.active_contract(task_dir), second)

    def test_second_raw_contract_is_rejected(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        second = make_contract("task-native", self.root, objective="Different scope")
        with self.assertRaises(SchemaError):
            with task_lock(task_dir) as guard:
                provenance.append_task_contract(task_dir, second, guard)
        # bytes unchanged: exactly one line remains, and it is the first contract.
        lines = (task_dir / "task-contracts.jsonl").read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)

    def test_bad_previous_hash_blocks_active_resolution(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        second = make_contract("task-native", self.root, objective="Different scope")
        adoption = make_adoption(task_dir, self.root, previous=first, new_contract=second)
        tampered = rehash_adoption({**adoption, "previous_contract_hash": "00" * 32})
        with task_lock(task_dir) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_task_contract_adoption(task_dir, tampered, guard)

    def test_bad_previous_fingerprint_blocks_active_resolution(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        second = make_contract("task-native", self.root, objective="Different scope")
        adoption = make_adoption(task_dir, self.root, previous=first, new_contract=second)
        tampered = rehash_adoption({**adoption, "previous_task_contract_fingerprint": "00" * 32})
        with task_lock(task_dir) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_task_contract_adoption(task_dir, tampered, guard)

    def test_mutated_middle_record_breaks_chain_validation(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        second = make_contract("task-native", self.root, objective="Second scope")
        adoption1 = make_adoption(task_dir, self.root, previous=first, new_contract=second)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract_adoption(task_dir, adoption1, guard)
        third = make_contract("task-native", self.root, objective="Third scope")
        adoption2 = make_adoption(task_dir, self.root, previous=second, new_contract=third)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract_adoption(task_dir, adoption2, guard)

        # Directly tamper with the middle (first adoption) physical line.
        path = task_dir / "task-contracts.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        middle = json.loads(lines[1])
        middle["reason"] = "tampered after the fact"
        lines[1] = canonical_json(middle)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        with self.assertRaises(SchemaError):
            provenance.active_contract(task_dir)

    def test_malformed_middle_line_fails_closed(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        second = make_contract("task-native", self.root, objective="Second scope")
        adoption1 = make_adoption(task_dir, self.root, previous=first, new_contract=second)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract_adoption(task_dir, adoption1, guard)
        path = task_dir / "task-contracts.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("not-json-at-all\n")
        with self.assertRaises(SchemaError):
            provenance.active_contract(task_dir)

    def test_truncated_final_line_fails_closed(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        path = task_dir / "task-contracts.jsonl"
        raw = path.read_bytes()
        path.write_bytes(raw[:-1])  # drop the trailing newline
        with self.assertRaises(SchemaError):
            provenance.active_contract(task_dir)

    def test_last_physical_line_with_invalid_previous_link_is_never_trusted(self) -> None:
        task_dir = self.task_dir("task-native")
        first = make_contract("task-native", self.root)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract(task_dir, first, guard)
        second = make_contract("task-native", self.root, objective="Second scope")
        # Build a syntactically valid adoption whose previous link is wrong,
        # and hand-append it directly (bypassing append_task_contract_adoption's
        # own chain check) to prove the *reader* also refuses to trust it.
        adoption = make_adoption(task_dir, self.root, previous=first, new_contract=second)
        tampered = rehash_adoption({**adoption, "previous_contract_hash": "11" * 32})
        with task_lock(task_dir) as guard:
            from sera.schemas import append_ledger_record

            append_ledger_record(task_dir / "task-contracts.jsonl", tampered, guard)
        with self.assertRaises(SchemaError):
            provenance.active_contract(task_dir)

    def test_foreign_task_record_copied_into_ledger_is_rejected(self) -> None:
        task_a = self.task_dir("task-a")
        task_b = self.task_dir("task-b")
        contract_a = make_contract("task-a", self.root)
        with task_lock(task_a) as guard:
            provenance.append_task_contract(task_a, contract_a, guard)

        # A byte-identical, correctly-hashed record for task-a copied verbatim
        # into task-b's ledger must still fail: the task_id inside it does not
        # match the owning directory.
        (task_b / "task-contracts.jsonl").write_text(canonical_json(contract_a) + "\n", encoding="utf-8")
        with self.assertRaises(SchemaError):
            provenance.active_contract(task_b)

    def test_legacy_task_with_no_ledger_has_no_active_contract(self) -> None:
        task_dir = self.task_dir("task-legacy")
        (task_dir / "task.json").write_text(json.dumps(legacy_task_v1("task-legacy")) + "\n", encoding="utf-8")
        self.assertIsNone(provenance.active_contract(task_dir))

    def test_legacy_task_with_raw_contract_as_first_line_is_rejected(self) -> None:
        task_dir = self.task_dir("task-legacy")
        (task_dir / "task.json").write_text(json.dumps(legacy_task_v1("task-legacy")) + "\n", encoding="utf-8")
        contract = make_contract("task-legacy", self.root, implementation_origin="pre_existing")
        with task_lock(task_dir) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_task_contract(task_dir, contract, guard)

    def test_legacy_task_with_valid_first_adoption_has_embedded_contract_active(self) -> None:
        task_dir = self.task_dir("task-legacy")
        task_v1 = legacy_task_v1("task-legacy")
        (task_dir / "task.json").write_text(json.dumps(task_v1) + "\n", encoding="utf-8")
        new_contract = make_contract("task-legacy", self.root, implementation_origin="pre_existing")
        adoption = make_adoption(task_dir, self.root, previous=task_v1, new_contract=new_contract)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract_adoption(task_dir, adoption, guard)
        self.assertEqual(provenance.active_contract(task_dir), new_contract)


class LockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp, self.root = make_repo()
        self.addCleanup(self.temp.cleanup)
        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        self.task_dir.mkdir(parents=True)

    def test_real_guard_permits_append(self) -> None:
        contract = make_contract("task-1", self.root)
        with task_lock(self.task_dir) as guard:
            provenance.append_task_contract(self.task_dir, contract, guard)
        self.assertTrue((self.task_dir / "task-contracts.jsonl").exists())

    def test_fake_guard_is_rejected(self) -> None:
        from sera.schemas import TaskLockGuard

        contract = make_contract("task-1", self.root)
        with task_lock(self.task_dir) as real_guard:
            fake = TaskLockGuard(
                lock_dir=real_guard.lock_dir,
                protected_root=real_guard.protected_root,
                kind=real_guard.kind,
                owner_thread_id=real_guard.owner_thread_id,
                held=True,
            )
            with self.assertRaises(SchemaError):
                provenance.append_task_contract(self.task_dir, contract, fake)
        self.assertFalse((self.task_dir / "task-contracts.jsonl").exists())

    def test_released_guard_is_rejected(self) -> None:
        contract = make_contract("task-1", self.root)
        with task_lock(self.task_dir) as guard:
            pass
        with self.assertRaises(SchemaError):
            provenance.append_task_contract(self.task_dir, contract, guard)
        self.assertFalse((self.task_dir / "task-contracts.jsonl").exists())

    def test_wrong_task_guard_is_rejected(self) -> None:
        other_dir = self.root / ".sera" / "tasks" / "task-2"
        other_dir.mkdir(parents=True)
        contract = make_contract("task-2", self.root)
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_task_contract(other_dir, contract, guard)
        self.assertFalse((other_dir / "task-contracts.jsonl").exists())

    def test_malformed_existing_history_blocks_append(self) -> None:
        path = self.task_dir / "task-contracts.jsonl"
        path.write_text("not-json\n", encoding="utf-8")
        contract = make_contract("task-1", self.root)
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_task_contract(self.task_dir, contract, guard)
        self.assertEqual(path.read_text(encoding="utf-8"), "not-json\n")

    def test_foreign_incoming_record_blocks_append(self) -> None:
        contract = make_contract("task-other", self.root)
        with task_lock(self.task_dir) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_task_contract(self.task_dir, contract, guard)
        self.assertFalse((self.task_dir / "task-contracts.jsonl").exists())


class LegacyCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp, self.root = make_repo()
        self.addCleanup(self.temp.cleanup)

    def test_legacy_task_json_is_never_rewritten_by_adoption_primitives(self) -> None:
        task_dir = self.root / ".sera" / "tasks" / "task-legacy"
        task_dir.mkdir(parents=True)
        task_v1 = legacy_task_v1("task-legacy")
        raw = json.dumps(task_v1, indent=2) + "\n"
        (task_dir / "task.json").write_text(raw, encoding="utf-8")
        new_contract = make_contract("task-legacy", self.root, implementation_origin="pre_existing")
        adoption = make_adoption(task_dir, self.root, previous=task_v1, new_contract=new_contract)
        with task_lock(task_dir) as guard:
            provenance.append_task_contract_adoption(task_dir, adoption, guard)
        self.assertEqual((task_dir / "task.json").read_text(encoding="utf-8"), raw)

    def test_legacy_task_contract_fingerprint_algorithm_is_unchanged(self) -> None:
        task_v1 = legacy_task_v1("task-legacy")
        payload = {
            field: task_v1.get(field)
            for field in (
                "objective", "requested_mode", "requested_risk", "mode", "risk", "risk_reasons",
                "allowed_files", "constraints", "verification", "uncertainty", "use_case",
            )
        }
        import hashlib
        import json as _json

        expected = hashlib.sha256(
            _json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        self.assertEqual(task_contract_fingerprint(task_v1), expected)

    def test_legacy_previous_contract_hash_uses_the_complete_v1_record(self) -> None:
        task_v1 = legacy_task_v1("task-legacy")
        task_v1["extra_runtime_field"] = "must not be dropped"
        expected = sha256_domain("task_contract:legacy_v1", canonical_json(task_v1).encode("utf-8"))
        self.assertEqual(provenance.legacy_previous_contract_hash(task_v1), expected)

        reduced = dict(task_v1)
        del reduced["extra_runtime_field"]
        self.assertNotEqual(
            provenance.legacy_previous_contract_hash(task_v1),
            provenance.legacy_previous_contract_hash(reduced),
        )

    def test_fingerprints_differ_across_adoption(self) -> None:
        task_v1 = legacy_task_v1("task-legacy")
        new_contract = make_contract("task-legacy", self.root, implementation_origin="pre_existing")
        legacy_fp = task_contract_fingerprint(task_v1)
        modern_fp = task_contract_fingerprint(new_contract)
        self.assertNotEqual(legacy_fp, modern_fp)
        self.assertEqual(modern_fp, new_contract["contract_hash"])

        task_dir = self.root / ".sera" / "tasks" / "task-legacy"
        task_dir.mkdir(parents=True)
        (task_dir / "task.json").write_text(json.dumps(task_v1) + "\n", encoding="utf-8")
        dyn_modern = provenance.dynamic_task_fingerprint(self.root, task_dir, modern_fp)
        self.assertNotEqual(dyn_modern, modern_fp)

    def test_no_automatic_promotion_from_reading_a_legacy_task(self) -> None:
        task_dir = self.root / ".sera" / "tasks" / "task-legacy"
        task_dir.mkdir(parents=True)
        task_v1 = legacy_task_v1("task-legacy")
        raw = json.dumps(task_v1, indent=2) + "\n"
        (task_dir / "task.json").write_text(raw, encoding="utf-8")
        for _ in range(3):
            self.assertIsNone(provenance.active_contract(task_dir))
        self.assertFalse((task_dir / "task-contracts.jsonl").exists())
        self.assertEqual((task_dir / "task.json").read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
