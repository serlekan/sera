"""Immutable policy capture, strict history, and candidate-only adoption."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from sera import cli, provenance
from sera.controller import confirm_task_ownership
from sera.core import DEFAULT_CONFIG, load_task, new_task
from sera.schemas import LedgerReader, SchemaError, TaskLockGuard, canonical_json, task_lock
from tests.test_controller import git
from tests.test_provenance_config_v2 import (
    CANONICAL_ROLES,
    CANONICAL_STAGE_ORDER,
    IDENTITY_EVIDENCE_CLASSES,
    identity_policy,
    modern_config,
)


MODERN_IDENTITY_POLICY = {
    "implementation_builder": {"required_identity_evidence_class": "provider_attested"},
    "implementation_validator": {"required_identity_evidence_class": "adapter_observed"},
    "independent_reviewer": {"required_identity_evidence_class": "controller_observed"},
    "release_gate": {"required_identity_evidence_class": "manual_assertion"},
}


def signed(record):
    value = copy.deepcopy(record)
    payload = {key: item for key, item in value.items() if key != "snapshot_hash"}
    value["snapshot_hash"] = hashlib.sha256(
        b"policy_snapshot\x1f" + canonical_json(payload).encode()
    ).hexdigest()
    return value


class PolicySnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        self.task_dir.mkdir(parents=True)
        self.path = self.task_dir / "policy-snapshots.jsonl"
        self.config = modern_config()
        self.task = {"id": "task-1", "mode": "assured", "risk": "high", "verification": ["python -m unittest"]}

    def build(self, trigger="explicit_policy_adoption", view=None):
        self.assertTrue(callable(getattr(provenance, "build_policy_snapshot", None)), "T08 builder is missing")
        return provenance.build_policy_snapshot(
            view or provenance.translate_config(self.config, self.root), self.task, trigger
        )

    def append(self, snapshot):
        with task_lock(self.task_dir) as guard:
            provenance.append_policy_snapshot(self.task_dir, snapshot, guard)

    def test_exact_shape_and_normalized_task_policy(self):
        snapshot = self.build()
        self.assertEqual(set(snapshot), {
            "schema_version", "source_schema", "task_id", "captured_at", "trigger", "mode", "risk",
            "required_stages", "implementation_origin_rules", "provenance_requirements",
            "identity_evidence_policy", "execution_state_policy", "substitution_rules",
            "independence_requirements", "verification_requirements", "context_budgets",
            "knowledge_policy", "repository_identity_requirement", "snapshot_hash",
        })
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["source_schema"], 2)
        self.assertEqual(snapshot["task_id"], "task-1")
        self.assertEqual(set(snapshot["required_stages"]), CANONICAL_ROLES)
        self.assertEqual(set(snapshot["execution_state_policy"]), CANONICAL_ROLES)
        self.assertEqual(set(snapshot["identity_evidence_policy"]), CANONICAL_ROLES)
        self.assertEqual(snapshot["identity_evidence_policy"], MODERN_IDENTITY_POLICY)
        for entry in snapshot["identity_evidence_policy"].values():
            self.assertEqual(set(entry), {"required_identity_evidence_class"})
        for policy in snapshot["execution_state_policy"].values():
            self.assertEqual(policy, {
                "minimum_evidence_class": "CHECKPOINT_OBSERVED",
                "accepted_source_registration_hashes": ["ab" * 32],
                "accepted_source_types": ["registered_external_runner"],
                "required_capabilities": ["immutable_git_target"],
                "accepted_verification_methods": ["signed_manifest"],
            })
        self.assertEqual(snapshot["substitution_rules"], {"allow_approved_fallbacks": True, "allow_manual_substitution": False})
        self.assertEqual(snapshot["context_budgets"], {"token_budget": 24000, "max_files": 10, "max_packet_chars": 40000})
        self.assertEqual(snapshot["knowledge_policy"], {"source_paths": ["AGENTS.md", "docs/architecture.md"], "max_source_bytes": 120000, "assessment_required": True})
        self.assertEqual(snapshot["repository_identity_requirement"], {"minimum_strength": "derived"})
        self.assertEqual(snapshot["verification_requirements"], ["python -m unittest"])
        self.assertEqual(snapshot["implementation_origin_rules"]["sera_builder"], ["implementation_builder"])
        self.assertEqual(snapshot["independence_requirements"], {"distinct_execution_ids": True, "distinct_receipt_hashes": True})
        self.assertEqual(snapshot, signed(snapshot))

    def test_hash_covers_every_field_and_excludes_only_itself(self):
        snapshot = self.build()
        for field in snapshot:
            altered = copy.deepcopy(snapshot)
            altered[field] = "mutated"
            with self.subTest(field=field):
                if field == "snapshot_hash":
                    self.assertEqual(signed(altered)["snapshot_hash"], snapshot["snapshot_hash"])
                else:
                    self.assertNotEqual(signed(altered)["snapshot_hash"], snapshot["snapshot_hash"])
                    self.path.write_text(canonical_json(altered) + "\n", encoding="utf-8")
                    with self.assertRaises(SchemaError):
                        provenance.read_policy_snapshots(self.task_dir).records()

    def test_trigger_vocabulary_and_temporal_determinism(self):
        with patch("sera.provenance.utc_now", return_value="2026-09-08T12:00:00Z", create=True):
            self.assertEqual(self.build(), self.build())
        for trigger in ("task_created", "ownership_confirmed", "explicit_policy_adoption", "task_contract_adoption"):
            self.assertEqual(self.build(trigger)["trigger"], trigger)
        for trigger in ("adopt", "created", "ownership_confirmation", ""):
            with self.assertRaises((SchemaError, provenance.SeraError)):
                self.build(trigger)

    def test_legacy_view_retains_source_schema_and_override(self):
        snapshot = self.build(view=provenance.translate_config(copy.deepcopy(DEFAULT_CONFIG), self.root))
        self.assertEqual(snapshot["source_schema"], 1)
        self.assertEqual(snapshot["legacy_override"], {"allow_legacy_provenance": True})
        self.assertEqual(snapshot, signed(snapshot))
        self.assertNotIn("implementation_validator", snapshot["required_stages"])
        self.assertEqual(set(snapshot["identity_evidence_policy"]), CANONICAL_ROLES)
        for stage in CANONICAL_STAGE_ORDER:
            self.assertEqual(
                snapshot["identity_evidence_policy"][stage],
                {"required_identity_evidence_class": "manual_assertion"},
            )

    def test_required_stage_uses_mode_or_risk_and_enabled(self):
        self.task.update(mode="fast", risk="low")
        self.assertEqual(self.build()["required_stages"], [])
        self.task["risk"] = "high"
        self.config["stage_policies"]["gate"]["enabled"] = False
        self.assertEqual(set(self.build()["required_stages"]), CANONICAL_ROLES - {"release_gate"})

    def test_captured_view_survives_config_movement_and_history_never_reloads_config(self):
        view = provenance.translate_config(self.config, self.root)
        self.config["execution_state_policy"]["builder"]["accepted_source_registration_hashes"] = ["cd" * 32]
        config_path = self.root / ".sera" / "config.json"
        config_path.write_text(json.dumps(self.config))
        snapshot = self.build(view=view)
        config_path.write_text("config moved between capture and append")
        self.append(snapshot)
        config_path.write_text("not even valid JSON anymore")
        self.assertEqual(provenance.read_policy_snapshots(self.task_dir).records(), [snapshot])
        self.assertEqual(snapshot["execution_state_policy"]["implementation_builder"]["accepted_source_registration_hashes"], ["ab" * 32])
        snapshot["knowledge_policy"]["source_paths"].clear()
        self.assertEqual(list(view.knowledge_policy["source_paths"]), ["AGENTS.md", "docs/architecture.md"])

    def test_strict_ledger_blocks_malformed_middle_truncation_and_hash_mutation(self):
        snapshot = self.build()
        line = canonical_json(snapshot) + "\n"
        for content in (line + "{bad}\n" + line, line.rstrip(), line.replace('"risk":"high"', '"risk":"low"')):
            with self.subTest(content=content[-50:]):
                self.path.write_text(content, encoding="utf-8")
                before = self.path.read_bytes()
                with self.assertRaises(SchemaError):
                    provenance.read_policy_snapshots(self.task_dir).records()
                with self.assertRaises(SchemaError):
                    self.append(snapshot)
                self.assertEqual(self.path.read_bytes(), before)

    def test_duplicates_order_and_valid_persisted_edit_affect_fingerprint(self):
        first = self.build()
        second = signed({**first, "risk": "medium"})
        self.append(first)
        reader = provenance.read_policy_snapshots(self.task_dir)
        self.assertIsInstance(reader, LedgerReader)
        self.assertEqual(reader.schema_family, "policy_snapshot")
        original = reader.fingerprint()
        self.append(first)
        self.assertEqual(reader.records(), [first, first])
        self.assertNotEqual(reader.fingerprint(), original)
        self.path.write_text(canonical_json(first) + "\n" + canonical_json(second) + "\n")
        ordered = reader.fingerprint()
        self.path.write_text(canonical_json(second) + "\n" + canonical_json(first) + "\n")
        self.assertNotEqual(reader.fingerprint(), ordered)
        self.path.write_text(canonical_json(second) + "\n")
        self.assertNotEqual(reader.fingerprint(), original)

    def test_fake_released_and_wrong_task_locks_cannot_append(self):
        snapshot = self.build()
        fake = TaskLockGuard(self.task_dir / ".lock", self.task_dir, "task", threading.get_ident(), held=True)
        with task_lock(self.task_dir) as released:
            pass
        for guard in (None, fake, released):
            with self.assertRaises(SchemaError):
                provenance.append_policy_snapshot(self.task_dir, snapshot, guard)
        other = self.task_dir.parent / "other"
        other.mkdir()
        with task_lock(other) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_policy_snapshot(self.task_dir, snapshot, guard)
        self.assertFalse(self.path.exists())

    def test_rehashed_invalid_schema_and_nested_policy_still_block(self):
        snapshot = self.build()
        mutations = [
            {**snapshot, "schema_version": True},
            {**snapshot, "source_schema": 3},
            {**snapshot, "trigger": "adopt"},
            {**snapshot, "required_stages": ["builder"]},
            {**snapshot, "actor": "unapproved schema extension"},
            {**snapshot, "context_budgets": {"token_budget": 0, "max_files": 10, "max_packet_chars": 40000}},
        ]
        bad_source = copy.deepcopy(snapshot)
        bad_source["execution_state_policy"]["implementation_builder"]["accepted_source_registration_hashes"] = ["not-a-hash"]
        mutations.append(bad_source)
        bad_role = copy.deepcopy(snapshot)
        bad_role["execution_state_policy"]["builder"] = bad_role["execution_state_policy"].pop("implementation_builder")
        mutations.append(bad_role)
        legacy = self.build(view=provenance.translate_config(copy.deepcopy(DEFAULT_CONFIG), self.root))
        legacy["legacy_override"]["allow_legacy_provenance"] = 1
        mutations.append(legacy)
        for altered in mutations:
            with self.subTest(altered=altered):
                self.path.write_text(canonical_json(signed(altered)) + "\n")
                with self.assertRaises(SchemaError):
                    provenance.read_policy_snapshots(self.task_dir).records()

    def test_active_selection_validates_entire_history_after_bound_record(self):
        snapshot = self.build()
        self.append(snapshot)
        with self.path.open("ab") as handle:
            handle.write(b'{"invalid":true}\n')
        contract = {"schema_version": 2, "record_type": "task_contract", "task_id": "task-1", "active_policy_hash": snapshot["snapshot_hash"]}
        for candidate_contract in (contract, {"schema_version": 1}):
            with self.assertRaises(SchemaError):
                provenance.active_policy_snapshot(self.task_dir, candidate_contract)

    def test_exact_bound_hash_only_and_legacy_candidates_stay_inactive(self):
        first = self.build()
        self.append(first)
        second = self.build("ownership_confirmed")
        self.append(second)
        for legacy in (None, {"schema_version": 1, "active_policy_hash": second["snapshot_hash"]}):
            self.assertIsNone(provenance.active_policy_snapshot(self.task_dir, legacy))
        contract = {"schema_version": 2, "record_type": "task_contract", "task_id": "task-1", "active_policy_hash": first["snapshot_hash"]}
        self.assertEqual(provenance.active_policy_snapshot(self.task_dir, contract), first)
        contract["active_policy_hash"] = "ef" * 32
        self.assertIsNone(provenance.active_policy_snapshot(self.task_dir, contract))
        contract.update(active_policy_hash=first["snapshot_hash"], task_id="wrong-task")
        with self.assertRaises(SchemaError):
            provenance.active_policy_snapshot(self.task_dir, contract)

    def test_foreign_snapshot_with_real_destination_lock_is_rejected(self):
        foreign = self.build()
        destination = self.task_dir.parent / "task-B"
        destination.mkdir()
        with task_lock(destination) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_policy_snapshot(destination, foreign, guard)
        self.assertFalse((destination / "policy-snapshots.jsonl").exists())

    def test_foreign_history_is_rejected_without_changing_bytes(self):
        foreign = signed({**self.build(), "task_id": "task-A"})
        self.path.write_text(canonical_json(foreign) + "\n")
        before = self.path.read_bytes()
        with self.assertRaises(SchemaError):
            provenance.read_policy_snapshots(self.task_dir).records()
        self.assertEqual(self.path.read_bytes(), before)

    def test_same_task_snapshot_with_real_lock_appends_and_reads(self):
        snapshot = self.build()
        self.append(snapshot)
        self.assertEqual(provenance.read_policy_snapshots(self.task_dir).records(), [snapshot])

    def test_mixed_history_blocks_reader_fingerprint_and_new_append(self):
        local = self.build()
        foreign = signed({**local, "task_id": "task-A"})
        for records in ([local, foreign], [foreign, local], [local, foreign, local]):
            with self.subTest(task_ids=[record["task_id"] for record in records]):
                self.path.write_text("".join(canonical_json(record) + "\n" for record in records))
                before = self.path.read_bytes()
                reader = provenance.read_policy_snapshots(self.task_dir)
                with self.assertRaises(SchemaError):
                    reader.records()
                with self.assertRaises(SchemaError):
                    reader.fingerprint()
                with self.assertRaises(SchemaError):
                    self.append(local)
                self.assertEqual(self.path.read_bytes(), before)

    def test_foreign_history_cannot_activate_even_with_matching_hash_and_task(self):
        foreign = signed({**self.build(), "task_id": "task-A"})
        self.path.write_text(canonical_json(foreign) + "\n")
        contract = {
            "schema_version": 2, "record_type": "task_contract",
            "task_id": "task-A", "active_policy_hash": foreign["snapshot_hash"],
        }
        with self.assertRaises(SchemaError):
            provenance.active_policy_snapshot(self.task_dir, contract)


class PolicySnapshotIdentityPolicyTests(unittest.TestCase):
    """T08-R: the four-stage identity-evidence policy is persisted and hash-bound."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        self.task_dir.mkdir(parents=True)
        self.path = self.task_dir / "policy-snapshots.jsonl"
        self.config = modern_config()
        self.task = {"id": "task-1", "mode": "assured", "risk": "high", "verification": ["python -m unittest"]}

    def build(self, view=None, trigger="explicit_policy_adoption"):
        return provenance.build_policy_snapshot(
            view or provenance.translate_config(self.config, self.root), self.task, trigger
        )

    def append(self, snapshot):
        with task_lock(self.task_dir) as guard:
            provenance.append_policy_snapshot(self.task_dir, snapshot, guard)

    def test_identity_policy_shape_and_values_match_the_captured_view(self):
        snapshot = self.build()
        self.assertEqual(set(snapshot["identity_evidence_policy"]), CANONICAL_ROLES)
        self.assertEqual(snapshot["identity_evidence_policy"], MODERN_IDENTITY_POLICY)
        for entry in snapshot["identity_evidence_policy"].values():
            self.assertEqual(set(entry), {"required_identity_evidence_class"})

    def test_disabled_stage_identity_requirement_is_still_persisted(self):
        self.task.update(mode="fast", risk="low")
        snapshot = self.build()
        self.assertEqual(snapshot["required_stages"], [])
        self.assertEqual(set(snapshot["identity_evidence_policy"]), CANONICAL_ROLES)

    def test_every_identity_class_round_trips_through_the_ledger(self):
        for identity_class in IDENTITY_EVIDENCE_CLASSES:
            with self.subTest(identity_class=identity_class):
                self.config = modern_config()
                self.config["identity_evidence_policy"] = identity_policy(
                    {stage: identity_class for stage in CANONICAL_STAGE_ORDER}
                )
                snapshot = self.build()
                self.assertEqual(
                    {entry["required_identity_evidence_class"] for entry in snapshot["identity_evidence_policy"].values()},
                    {identity_class},
                )
                if self.path.exists():
                    self.path.unlink()
                self.append(snapshot)
                self.assertEqual(provenance.read_policy_snapshots(self.task_dir).records(), [snapshot])

    def test_each_stage_identity_change_changes_the_snapshot_hash(self):
        snapshot = self.build()
        for stage in CANONICAL_STAGE_ORDER:
            with self.subTest(stage=stage):
                altered = copy.deepcopy(snapshot)
                altered["identity_evidence_policy"][stage]["required_identity_evidence_class"] = "unknown"
                self.assertNotEqual(signed(altered)["snapshot_hash"], snapshot["snapshot_hash"])

    def test_identity_policy_cannot_be_excluded_from_the_hash(self):
        snapshot = self.build()
        altered = copy.deepcopy(snapshot)
        altered["identity_evidence_policy"]["release_gate"]["required_identity_evidence_class"] = "unknown"
        # record_hash omits exactly {"snapshot_hash"}; the identity block is covered.
        self.assertNotEqual(
            provenance.record_hash("policy_snapshot", altered, "snapshot_hash"),
            snapshot["snapshot_hash"],
        )
        other = provenance.build_policy_snapshot(
            provenance.translate_config(
                {**modern_config(), "identity_evidence_policy": identity_policy(
                    {stage: "unknown" for stage in CANONICAL_STAGE_ORDER}
                )},
                self.root,
            ),
            self.task,
            "explicit_policy_adoption",
        )
        self.assertNotEqual(other["snapshot_hash"], snapshot["snapshot_hash"])

    def test_rehashed_identity_mutations_are_rejected_by_the_reader(self):
        snapshot = self.build()

        def variant(mutate):
            altered = copy.deepcopy(snapshot)
            mutate(altered["identity_evidence_policy"])
            return altered

        def drop_block(altered):
            del altered["identity_evidence_policy"]

        mutations = {
            "missing_whole_block": drop_block,
            "missing_stage": lambda block: block.pop("release_gate"),
            "unknown_stage": lambda block: block.__setitem__("planner", {"required_identity_evidence_class": "unknown"}),
            "alias_stage": lambda block: block.__setitem__("builder", {"required_identity_evidence_class": "manual_assertion"}),
            "missing_class": lambda block: block.__setitem__("implementation_builder", {}),
            "unknown_nested_field": lambda block: block["implementation_builder"].__setitem__("note", "x"),
            "null_class": lambda block: block["implementation_builder"].__setitem__("required_identity_evidence_class", None),
            "unsupported_class": lambda block: block["implementation_builder"].__setitem__("required_identity_evidence_class", "provider_trusted"),
            "non_string_class": lambda block: block["implementation_builder"].__setitem__("required_identity_evidence_class", 5),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                if name == "missing_whole_block":
                    altered = copy.deepcopy(snapshot)
                    drop_block(altered)
                else:
                    altered = variant(mutate)
                self.path.write_text(canonical_json(signed(altered)) + "\n", encoding="utf-8")
                with self.assertRaises(SchemaError):
                    provenance.read_policy_snapshots(self.task_dir).records()

    def test_persisted_alias_stage_is_never_normalized_away(self):
        snapshot = self.build()
        altered = copy.deepcopy(snapshot)
        altered["identity_evidence_policy"]["gate"] = altered["identity_evidence_policy"].pop("release_gate")
        self.path.write_text(canonical_json(signed(altered)) + "\n", encoding="utf-8")
        with self.assertRaises(SchemaError):
            provenance.read_policy_snapshots(self.task_dir).records()

    def test_captured_identity_policy_survives_later_config_movement(self):
        view = provenance.translate_config(self.config, self.root)
        config_path = self.root / ".sera" / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        self.config["identity_evidence_policy"] = identity_policy(
            {stage: "unknown" for stage in CANONICAL_STAGE_ORDER}
        )
        config_path.write_text(json.dumps(self.config))
        snapshot = self.build(view=view)
        config_path.write_text("config moved between capture and append")
        self.append(snapshot)
        config_path.write_text("not even valid JSON anymore")

        records = provenance.read_policy_snapshots(self.task_dir).records()
        self.assertEqual(records, [snapshot])
        self.assertEqual(records[0]["identity_evidence_policy"], MODERN_IDENTITY_POLICY)

    def test_snapshot_identity_is_a_json_detached_copy_of_the_frozen_view(self):
        view = provenance.translate_config(self.config, self.root)
        snapshot = self.build(view=view)

        self.assertEqual(
            snapshot["identity_evidence_policy"],
            {stage: dict(entry) for stage, entry in view.identity_evidence_policy.items()},
        )
        snapshot["identity_evidence_policy"]["release_gate"]["required_identity_evidence_class"] = "unknown"
        self.assertEqual(
            view.identity_evidence_policy["release_gate"]["required_identity_evidence_class"],
            "manual_assertion",
        )
        with self.assertRaises(TypeError):
            view.identity_evidence_policy["release_gate"] = {}  # type: ignore[index]
        with self.assertRaises(TypeError):
            view.identity_evidence_policy["release_gate"]["required_identity_evidence_class"] = "x"  # type: ignore[index]

    def test_mutating_source_config_after_capture_cannot_change_the_snapshot(self):
        view = provenance.translate_config(self.config, self.root)
        self.config["identity_evidence_policy"]["implementation_builder"]["required_identity_evidence_class"] = "unknown"
        snapshot = self.build(view=view)
        self.assertEqual(
            snapshot["identity_evidence_policy"]["implementation_builder"],
            {"required_identity_evidence_class": "provider_attested"},
        )


class PolicyAdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-b", "main")
        git(self.root, "config", "user.name", "Test")
        git(self.root, "config", "user.email", "test@example.com")
        (self.root / "sample.py").write_text("value = 1\n")
        git(self.root, "add", ".")
        git(self.root, "commit", "-m", "baseline")
        self.task_dir = new_task(self.root, "policy-test", "Update sample", "standard", "low", ["sample.py"], [], ["python -m unittest"])
        self.path = self.task_dir / "policy-snapshots.jsonl"

    def run_cli(self, *options):
        self.assertTrue(callable(getattr(provenance, "build_policy_snapshot", None)), "T08 adoption is missing")
        with patch("sera.cli.find_repo_root", return_value=self.root), redirect_stdout(io.StringIO()) as output:
            result = cli.main(["task", "policy", self.task_dir.name, "--adopt", *options])
        return result, output.getvalue()

    def test_adoption_requires_actor_and_reason_without_inference(self):
        for options in (("--actor", "Test"), ("--reason", "capture"), ()):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.run_cli(*options)
        for actor, reason in ((" ", "capture"), ("Test", " ")):
            with redirect_stderr(io.StringIO()):
                code, _ = self.run_cli("--actor", actor, "--reason", reason)
            self.assertEqual(code, 2)
        self.assertFalse(self.path.exists())

    def test_adoption_appends_one_candidate_leaves_task_and_contract_bytes_unchanged(self):
        before = (self.task_dir / "task.json").read_bytes()
        code, output = self.run_cli("--actor", "Test", "--reason", "Capture current policy")
        self.assertEqual(code, 0)
        self.assertIn("candidate", output.lower())
        self.assertIn("TASK_CONTRACT_ADOPTION_REQUIRED", output)
        self.assertEqual((self.task_dir / "task.json").read_bytes(), before)
        self.assertFalse((self.task_dir / "task-contracts.jsonl").exists())
        records = provenance.read_policy_snapshots(self.task_dir).records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["trigger"], "explicit_policy_adoption")
        self.assertNotIn("actor", records[0])
        self.assertNotIn("reason", records[0])
        self.assertEqual(set(records[0]["identity_evidence_policy"]), CANONICAL_ROLES)
        for entry in records[0]["identity_evidence_policy"].values():
            self.assertEqual(entry, {"required_identity_evidence_class": "manual_assertion"})
        self.assertIsNone(provenance.active_policy_snapshot(self.task_dir, load_task(self.task_dir)))
        history = b'{"sentinel":"future contract history"}\n'
        (self.task_dir / "task-contracts.jsonl").write_bytes(history)
        first_line = self.path.read_bytes()
        self.run_cli("--actor", "Test", "--reason", "Another candidate")
        self.assertTrue(self.path.read_bytes().startswith(first_line))
        self.assertEqual((self.task_dir / "task-contracts.jsonl").read_bytes(), history)
        self.assertEqual((self.task_dir / "task.json").read_bytes(), before)

    def test_adoption_and_confirmation_obey_real_lock(self):
        self.assertTrue(callable(getattr(provenance, "build_policy_snapshot", None)))
        before = (self.task_dir / "task.json").read_bytes()
        with task_lock(self.task_dir):
            with redirect_stderr(io.StringIO()):
                code, _ = self.run_cli("--actor", "Test", "--reason", "capture")
            self.assertEqual(code, 2)
            from sera.schemas import LockHeld
            with self.assertRaises(LockHeld):
                confirm_task_ownership(self.root, self.task_dir, ["security.py"])
        self.assertFalse(self.path.exists())
        self.assertEqual((self.task_dir / "task.json").read_bytes(), before)

    def test_ownership_confirmation_appends_second_snapshot_with_new_risk(self):
        self.run_cli("--actor", "Test", "--reason", "capture")
        first_line = self.path.read_bytes()
        confirmed = confirm_task_ownership(self.root, self.task_dir, ["auth/security.py"])
        self.assertTrue(self.path.read_bytes().startswith(first_line))
        records = provenance.read_policy_snapshots(self.task_dir).records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["trigger"], "ownership_confirmed")
        self.assertEqual(records[1]["risk"], confirmed["risk"])
        self.assertEqual(records[1]["risk"], "high")
        self.assertIn("release_gate", records[1]["required_stages"])
        for record in records:
            self.assertEqual(set(record["identity_evidence_policy"]), CANONICAL_ROLES)
            for entry in record["identity_evidence_policy"].values():
                self.assertEqual(entry, {"required_identity_evidence_class": "manual_assertion"})

    def test_malformed_history_blocks_confirmation_before_task_mutation(self):
        self.assertTrue(callable(getattr(provenance, "build_policy_snapshot", None)))
        self.path.write_bytes(b'{"broken":')
        before = (self.task_dir / "task.json").read_bytes()
        with self.assertRaises(SchemaError):
            confirm_task_ownership(self.root, self.task_dir, ["auth/security.py"])
        self.assertEqual((self.task_dir / "task.json").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
