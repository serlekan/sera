"""Coherent route capture: pure selector seam, explicit validator, RouteSnapshotV1.

T09 extracts the one existing route-selection algorithm into a pure
captured-config seam, adds the explicit `implementation_validator` result, and
persists an immutable `RouteSnapshotV1` per selected stage in
`.sera/tasks/<task-id>/route-snapshots.jsonl`. It captures and binds authority;
it never selects evidence, dispatches a provider, or duplicates policy source
rules.
"""

from __future__ import annotations

import copy
import hashlib
import tempfile
import threading
import unittest
from pathlib import Path

from sera import provenance
from sera.core import (
    DEFAULT_CONFIG,
    RouteDecision,
    SeraError,
    _deep_update,
    build_repo_map,
    decide_route,
    decide_route_from_config,
    initialize,
    load_config,
    load_task,
    new_task,
    resolved_route_identity,
    route_fingerprint,
)
from sera.schemas import SchemaError, TaskLockGuard, canonical_json, task_lock
from tests.test_provenance_config_v2 import modern_config, modern_route_config

CANONICAL_STAGES = (
    "implementation_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
)
ROOT = Path(".")
REPO_MAP = {"files": []}


def merged(overrides: dict | None = None) -> dict:
    """A captured configuration exactly as `load_config` would return it."""
    config = copy.deepcopy(DEFAULT_CONFIG)
    if overrides:
        _deep_update(config, copy.deepcopy(overrides))
    return config


def modern_merged(mutate=None) -> dict:
    base = modern_route_config()
    if mutate is not None:
        mutate(base)
    return merged(base)


def task(**overrides) -> dict:
    values = {
        "id": "task-1",
        "mode": "standard",
        "risk": "medium",
        "uncertainty": 1,
        "use_case": "implementation",
        "allowed_files": ["src/app.py"],
        "verification": ["python -m unittest"],
    }
    values.update(overrides)
    values.setdefault("task_id", values["id"])
    return values


def policy_for(config: dict, the_task: dict, trigger: str = "explicit_policy_adoption") -> dict:
    view = provenance.translate_config(config, ROOT)
    return provenance.build_policy_snapshot(view, the_task, trigger)


def rehash(record: dict) -> dict:
    value = copy.deepcopy(record)
    payload = {key: item for key, item in value.items() if key != "snapshot_hash"}
    value["snapshot_hash"] = hashlib.sha256(
        b"route_snapshot\x1f" + canonical_json(payload).encode()
    ).hexdigest()
    return value


def route_snapshot(config: dict, the_task: dict, stage: str, *, policy: dict | None = None) -> dict:
    """End-to-end coherent capture for one selected stage from one config."""
    policy = policy or policy_for(config, the_task)
    decision = decide_route_from_config(config, the_task, REPO_MAP)
    view = provenance.normalize_route_config(config)
    identity = resolved_route_identity(config, decision)
    inputs = provenance.resolve_route_snapshot_inputs(view, identity, stage, policy)
    return provenance.build_route_snapshot(
        the_task["id"],
        inputs["stage"],
        policy,
        {"provider": inputs["requested_provider"], "model": inputs["requested_model"]},
        inputs["approved_fallbacks"],
    )


# --------------------------------------------------------------------------- #
# Group 1 — selector extraction                                               #
# --------------------------------------------------------------------------- #
class SelectorExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        import subprocess

        for args in (
            ("init", "-b", "main"),
            ("config", "user.name", "Test"),
            ("config", "user.email", "test@example.com"),
        ):
            subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
        initialize(self.root)
        subprocess.run(["git", "add", "."], cwd=self.root, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.root, check=True, capture_output=True)
        self.repo_map = build_repo_map(self.root)

    def _task(self, **over):
        values = dict(
            name="adjust",
            objective="Return the answer",
            mode="standard",
            risk="medium",
            allowed_files=["src/app.py"],
            constraints=[],
            verification=["python -m unittest"],
            uncertainty=1,
            use_case="prototype",
        )
        values.update(over)
        return load_task(new_task(self.root, **values))

    def _facade_equals_seam(self, the_task) -> RouteDecision:
        via_facade = decide_route(self.root, the_task, self.repo_map)
        via_seam = decide_route_from_config(load_config(self.root), the_task, self.repo_map)
        self.assertEqual(via_facade, via_seam)
        return via_facade

    def test_facade_equals_pure_seam_for_representative_routes(self) -> None:
        for over in (
            dict(mode="fast", risk="low", uncertainty=0, allowed_files=["src/app.py"]),
            dict(mode="standard", risk="medium"),
            dict(mode="assured", risk="high"),
            dict(mode="fast", risk="low", uncertainty=0, allowed_files=[f"src/f{i}.py" for i in range(9)]),
        ):
            with self.subTest(over=over):
                self._facade_equals_seam(self._task(**over))

    def test_fable_eligible_case_matches_between_facade_and_seam(self) -> None:
        config_path = self.root / ".sera" / "config.json"
        config = load_config(self.root)
        config["lanes"]["optional_fable"]["enabled"] = True
        config_path.write_text(canonical_json(config), encoding="utf-8")
        decision = self._facade_equals_seam(self._task(mode="fast", risk="low", uncertainty=0))
        self.assertTrue(decision.fable_eligible)

    def test_disabled_required_lane_fails_the_same_way_on_both_paths(self) -> None:
        config_path = self.root / ".sera" / "config.json"
        config = load_config(self.root)
        config["lanes"]["deep_builder"]["enabled"] = False
        config_path.write_text(canonical_json(config), encoding="utf-8")
        the_task = self._task(mode="assured", risk="high")
        with self.assertRaises(SeraError):
            decide_route(self.root, the_task, self.repo_map)
        with self.assertRaises(SeraError):
            decide_route_from_config(load_config(self.root), the_task, self.repo_map)

    def test_pure_seam_never_reads_configuration(self) -> None:
        import sera.core as core

        def boom(*args, **kwargs):
            raise AssertionError("seam reread config")

        original = core.load_config
        core.load_config = boom
        try:
            decision = decide_route_from_config(merged(), task(mode="fast", risk="low", uncertainty=0), REPO_MAP)
        finally:
            core.load_config = original
        self.assertEqual(decision.builder, "fast_builder")

    def test_thresholds_budget_and_context_are_unchanged(self) -> None:
        # fast + low + complexity < 5.5 -> fast lane, no reviewer for <= 2 files
        fast = decide_route_from_config(merged(), task(mode="fast", risk="low", uncertainty=0), REPO_MAP)
        self.assertEqual(fast.builder, "fast_builder")
        self.assertIsNone(fast.reviewer)
        self.assertIsNone(fast.gate)
        self.assertEqual(fast.budget_tokens, DEFAULT_CONFIG["token_budgets"]["fast"])
        # a single unmapped owned file estimates 8000 bytes -> 2000 tokens
        self.assertEqual(fast.estimated_context_tokens, 2000)
        self.assertEqual(fast.ownership_file_count, 1)
        self.assertEqual(fast.ownership_tokens, 2000)
        # uncertainty pushes complexity past the 5.5 threshold -> deep lane
        deep = decide_route_from_config(
            merged(), task(mode="fast", risk="low", uncertainty=4), REPO_MAP
        )
        self.assertEqual(deep.builder, "deep_builder")
        self.assertEqual(deep.reviewer, "independent_reviewer")
        # assured/high adds the gate; reason strings are preserved verbatim
        gated = decide_route_from_config(merged(), task(mode="assured", risk="high"), REPO_MAP)
        self.assertEqual(gated.gate, "release_gate")
        self.assertEqual(gated.budget_tokens, DEFAULT_CONFIG["token_budgets"]["assured"])
        self.assertIn("deeper implementation lane", gated.reason)
        self.assertEqual(fast.reason, "Low-risk, bounded work fits the fast lane.")


# --------------------------------------------------------------------------- #
# Group 2 — validator selection                                               #
# --------------------------------------------------------------------------- #
class ValidatorSelectionTests(unittest.TestCase):
    def test_config_v1_route_never_selects_a_validator(self) -> None:
        for over in (
            dict(mode="fast", risk="low", uncertainty=0),
            dict(mode="standard", risk="medium"),
            dict(mode="assured", risk="high"),
        ):
            decision = decide_route_from_config(merged(), task(**over), REPO_MAP)
            self.assertIsNone(decision.validator)

    def test_modern_required_validator_resolves_only_the_explicit_lane(self) -> None:
        decision = decide_route_from_config(modern_merged(), task(mode="assured", risk="high"), REPO_MAP)
        self.assertEqual(decision.validator, "implementation_validator")
        # never an alias of another selected stage
        self.assertNotEqual(decision.validator, decision.builder)
        self.assertNotEqual(decision.validator, decision.reviewer)
        self.assertNotEqual(decision.validator, decision.gate)

    def test_modern_validator_not_required_by_mode_or_risk_stays_none(self) -> None:
        # modern stage policy requires standard/assured or medium/high
        decision = decide_route_from_config(modern_merged(), task(mode="fast", risk="low"), REPO_MAP)
        self.assertIsNone(decision.validator)

    def test_required_validator_with_disabled_lane_fails_closed(self) -> None:
        config = modern_merged(lambda c: c["lanes"]["implementation_validator"].update(enabled=False))
        with self.assertRaises(SeraError):
            decide_route_from_config(config, task(mode="assured", risk="high"), REPO_MAP)

    def test_required_validator_with_missing_lane_fails_closed(self) -> None:
        # modern policy config with no route lanes block: the merged default has
        # no implementation_validator lane at all.
        config = merged(modern_config())
        with self.assertRaises(SeraError):
            decide_route_from_config(config, task(mode="assured", risk="high"), REPO_MAP)

    def test_required_validator_with_blank_provider_or_model_fails_closed(self) -> None:
        for field in ("provider", "model"):
            config = modern_merged(lambda c, f=field: c["lanes"]["implementation_validator"].update({f: "   "}))
            with self.assertRaises(SeraError):
                decide_route_from_config(config, task(mode="assured", risk="high"), REPO_MAP)

    def test_validator_never_reuses_builder_reviewer_gate_or_fable(self) -> None:
        def mutate(config):
            config["lanes"]["implementation_validator"]["enabled"] = False
            config["lanes"]["optional_fable"]["enabled"] = True
            config["lanes"]["optional_fable"]["allowed_uses"] = ["implementation"]

        config = modern_merged(mutate)
        with self.assertRaises(SeraError):
            decide_route_from_config(config, task(mode="assured", risk="high"), REPO_MAP)

    def test_validator_requirement_has_parity_with_policy_required_stages(self) -> None:
        cases = [
            (dict(mode="assured", risk="high"), True),
            (dict(mode="standard", risk="low"), True),   # required by mode
            (dict(mode="fast", risk="high"), True),       # required by risk
            (dict(mode="fast", risk="low"), False),
        ]
        for over, expected in cases:
            with self.subTest(over=over):
                config = modern_merged()
                the_task = task(**over)
                policy = policy_for(config, the_task)
                decision = decide_route_from_config(config, the_task, REPO_MAP)
                self.assertEqual("implementation_validator" in policy["required_stages"], expected)
                self.assertEqual(decision.validator is not None, expected)
        # disabled validator stage policy: never required, never selected
        disabled = modern_merged(lambda c: c["stage_policies"]["validator"].update(enabled=False))
        the_task = task(mode="assured", risk="high")
        self.assertNotIn("implementation_validator", policy_for(disabled, the_task)["required_stages"])
        self.assertIsNone(decide_route_from_config(disabled, the_task, REPO_MAP).validator)

    def test_config_alias_and_canonical_stage_policy_agree(self) -> None:
        the_task = task(mode="assured", risk="high")
        alias = modern_merged()  # stage_policies keyed by "validator"
        canonical = modern_merged(
            lambda c: c.__setitem__(
                "stage_policies",
                {
                    "implementation_builder": c["stage_policies"].pop("builder"),
                    "implementation_validator": c["stage_policies"].pop("validator"),
                    "independent_reviewer": c["stage_policies"].pop("independent"),
                    "release_gate": c["stage_policies"].pop("gate"),
                },
            )
        )
        self.assertEqual(
            decide_route_from_config(alias, the_task, REPO_MAP).validator,
            decide_route_from_config(canonical, the_task, REPO_MAP).validator,
        )

    def test_colliding_alias_and_canonical_stage_policy_fails_closed(self) -> None:
        def mutate(config):
            config["stage_policies"]["implementation_validator"] = config["stage_policies"]["validator"]

        config = modern_merged(mutate)
        with self.assertRaises(SeraError):
            decide_route_from_config(config, task(mode="assured", risk="high"), REPO_MAP)


# --------------------------------------------------------------------------- #
# Group 3 — route identity                                                    #
# --------------------------------------------------------------------------- #
class RouteIdentityTests(unittest.TestCase):
    def test_v1_route_identity_and_fingerprint_are_byte_identical_to_pre_t09(self) -> None:
        decision = decide_route_from_config(merged(), task(mode="assured", risk="high"), REPO_MAP)
        identity = resolved_route_identity(merged(), decision)
        self.assertEqual(set(identity), {"builder", "reviewer", "gate"})
        self.assertNotIn("validator", identity)
        legacy = {
            "builder": {"lane": "deep_builder", "provider": "anthropic", "model": "claude-sonnet-5"},
            "reviewer": {"lane": "independent_reviewer", "provider": "anthropic", "model": "claude-opus-5"},
            "gate": {"lane": "release_gate", "provider": "openai", "model": "gpt-5.6-sol"},
        }
        self.assertEqual(identity, legacy)
        # route_fingerprint is a pure hash of the identity JSON: with no
        # "validator" key present, T09 cannot have changed it for a v1 route.
        import json as _json

        self.assertEqual(
            route_fingerprint(merged(), decision),
            hashlib.sha256(
                _json.dumps(legacy, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            ).hexdigest(),
        )

    def test_modern_selected_validator_participates_in_route_identity(self) -> None:
        config = modern_merged()
        decision = decide_route_from_config(config, task(mode="assured", risk="high"), REPO_MAP)
        identity = resolved_route_identity(config, decision)
        self.assertIn("validator", identity)
        self.assertEqual(identity["validator"]["lane"], "implementation_validator")
        self.assertEqual(identity["builder"]["lane"], "deep_builder")

    def test_changing_selected_validator_target_changes_the_fingerprint(self) -> None:
        config = modern_merged()
        the_task = task(mode="assured", risk="high")
        before = route_fingerprint(config, decide_route_from_config(config, the_task, REPO_MAP))
        moved = modern_merged(lambda c: c["lanes"]["implementation_validator"].update(model="gpt-5.6-luna"))
        after = route_fingerprint(moved, decide_route_from_config(moved, the_task, REPO_MAP))
        self.assertNotEqual(before, after)

    def test_unused_lanes_do_not_affect_route_identity(self) -> None:
        config = modern_merged()
        the_task = task(mode="fast", risk="low", uncertainty=0)  # fast_builder, no validator/reviewer/gate
        base = route_fingerprint(config, decide_route_from_config(config, the_task, REPO_MAP))
        for mutate in (
            lambda c: c["lanes"]["deep_builder"].update(model="other"),
            lambda c: c["lanes"]["implementation_validator"].update(provider="other"),
            lambda c: c["lanes"]["independent_reviewer"].update(model="other"),
            lambda c: c["lanes"]["release_gate"].update(model="other"),
            lambda c: c["lanes"]["optional_fable"].update(enabled=True, model="other"),
            lambda c: c["lanes"]["planner"].update(model="other"),
            lambda c: c["lanes"]["fast_builder"]["approved_fallbacks"].append({"provider": "x", "model": "y"}),
        ):
            with self.subTest(mutate=mutate):
                other = modern_merged(mutate)
                self.assertEqual(
                    route_fingerprint(other, decide_route_from_config(other, the_task, REPO_MAP)), base
                )

    def test_selected_deep_builder_is_stable_against_fast_builder_mutation(self) -> None:
        config = modern_merged()
        the_task = task(mode="assured", risk="high")  # deep_builder
        base = route_fingerprint(config, decide_route_from_config(config, the_task, REPO_MAP))
        moved = modern_merged(lambda c: c["lanes"]["fast_builder"].update(model="changed", provider="changed"))
        self.assertEqual(
            route_fingerprint(moved, decide_route_from_config(moved, the_task, REPO_MAP)), base
        )

    def test_changing_the_selected_builder_target_changes_the_fingerprint(self) -> None:
        config = modern_merged()
        the_task = task(mode="fast", risk="low", uncertainty=0)
        base = route_fingerprint(config, decide_route_from_config(config, the_task, REPO_MAP))
        moved = modern_merged(lambda c: c["lanes"]["fast_builder"].update(model="changed"))
        self.assertNotEqual(route_fingerprint(moved, decide_route_from_config(moved, the_task, REPO_MAP)), base)


# --------------------------------------------------------------------------- #
# Group 4 — RouteSnapshot schema                                              #
# --------------------------------------------------------------------------- #
class RouteSnapshotSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = modern_merged()
        self.task = task(mode="assured", risk="high")
        self.policy = policy_for(self.config, self.task)

    def snap(self, stage="implementation_builder"):
        return route_snapshot(self.config, self.task, stage, policy=self.policy)

    def test_exact_field_set_and_schema_version(self) -> None:
        snapshot = self.snap()
        self.assertEqual(
            set(snapshot),
            {
                "schema_version",
                "task_id",
                "stage",
                "policy_hash",
                "requested_provider",
                "requested_model",
                "approved_fallbacks",
                "required_identity_evidence_class",
                "required_execution_state_evidence_class",
                "required_output_fields",
                "snapshot_hash",
            },
        )
        self.assertEqual(snapshot["schema_version"], 1)
        self.assertEqual(snapshot["task_id"], "task-1")

    def test_stage_is_canonical_and_provider_model_come_from_the_selected_lane(self) -> None:
        snapshot = self.snap()
        self.assertEqual(snapshot["stage"], "implementation_builder")
        self.assertEqual(snapshot["requested_provider"], "anthropic")
        self.assertEqual(snapshot["requested_model"], "claude-sonnet-5")
        validator = self.snap("implementation_validator")
        self.assertEqual(validator["stage"], "implementation_validator")
        self.assertEqual(validator["requested_provider"], "openai")

    def test_policy_hash_and_evidence_requirements_are_copied_from_the_bound_policy(self) -> None:
        snapshot = self.snap("independent_reviewer")
        self.assertEqual(snapshot["policy_hash"], self.policy["snapshot_hash"])
        self.assertEqual(
            snapshot["required_identity_evidence_class"],
            self.policy["identity_evidence_policy"]["independent_reviewer"]["required_identity_evidence_class"],
        )
        self.assertEqual(
            snapshot["required_execution_state_evidence_class"],
            self.policy["execution_state_policy"]["independent_reviewer"]["minimum_evidence_class"],
        )

    def test_no_timestamp_actual_or_source_authorization_fields(self) -> None:
        snapshot = self.snap()
        for forbidden in (
            "captured_at",
            "generated_at",
            "actual_provider",
            "actual_model",
            "source_id",
            "source_registration_hash",
            "accepted_source_registration_hashes",
            "accepted_source_types",
            "required_capabilities",
            "accepted_verification_methods",
            "verification_method",
            "execution_id",
            "receipt_hash",
        ):
            self.assertNotIn(forbidden, snapshot)

    def test_identical_semantic_inputs_produce_an_identical_hash(self) -> None:
        self.assertEqual(self.snap(), self.snap())
        self.assertEqual(
            route_snapshot(modern_merged(), task(mode="assured", risk="high"), "release_gate"),
            route_snapshot(modern_merged(), task(mode="assured", risk="high"), "release_gate"),
        )

    def test_snapshot_hash_excludes_only_itself_and_covers_every_field(self) -> None:
        snapshot = self.snap()
        for field in snapshot:
            altered = copy.deepcopy(snapshot)
            if field == "snapshot_hash":
                altered[field] = "0" * 64
                self.assertEqual(rehash(altered)["snapshot_hash"], snapshot["snapshot_hash"])
                continue
            altered[field] = altered[field] if isinstance(altered[field], str) else "mutated"
            altered[field] = "mutated-value"
            self.assertNotEqual(rehash(altered)["snapshot_hash"], snapshot["snapshot_hash"])


class RouteSnapshotOutputTableTests(unittest.TestCase):
    def test_each_stage_uses_the_exact_deterministic_output_fields(self) -> None:
        config = modern_merged()
        the_task = task(mode="assured", risk="high")
        policy = policy_for(config, the_task)
        expected = {
            "implementation_builder": ["raw_output_hash", "output_state"],
            "implementation_validator": ["raw_output_hash"],
            "independent_reviewer": ["raw_output_hash", "parsed_verdict"],
            "release_gate": ["raw_output_hash", "parsed_verdict"],
        }
        for stage, fields in expected.items():
            snapshot = route_snapshot(config, the_task, stage, policy=policy)
            self.assertEqual(snapshot["required_output_fields"], fields)

    def test_reader_rejects_a_swapped_output_table_for_every_stage(self) -> None:
        config = modern_merged()
        the_task = task(mode="assured", risk="high")
        policy = policy_for(config, the_task)
        wrong = {
            "implementation_builder": ["raw_output_hash", "parsed_verdict"],
            "implementation_validator": ["raw_output_hash", "output_state"],
            "independent_reviewer": ["raw_output_hash"],
            "release_gate": ["raw_output_hash", "output_state"],
        }
        for stage, bogus in wrong.items():
            snapshot = route_snapshot(config, the_task, stage, policy=policy)
            snapshot["required_output_fields"] = bogus
            with self.subTest(stage=stage):
                with self.assertRaises(SchemaError):
                    provenance._validate_route_snapshot(rehash(snapshot))


# --------------------------------------------------------------------------- #
# Group 5 — hash adversarial / persisted reader                               #
# --------------------------------------------------------------------------- #
class RouteSnapshotReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = modern_merged()
        self.task = task(mode="assured", risk="high")
        self.policy = policy_for(self.config, self.task)
        self.good = route_snapshot(self.config, self.task, "implementation_builder", policy=self.policy)

    def _rejects(self, mutate) -> None:
        altered = copy.deepcopy(self.good)
        mutate(altered)
        with self.assertRaises(SchemaError):
            provenance._validate_route_snapshot(rehash(altered))

    def test_rehashed_semantic_mutations_are_all_rejected(self) -> None:
        mutations = {
            "alias_stage": lambda s: s.__setitem__("stage", "builder"),
            "unknown_stage": lambda s: s.__setitem__("stage", "planner"),
            "missing_policy_hash": lambda s: s.__setitem__("policy_hash", ""),
            "short_hash_shape": lambda s: s.__setitem__("policy_hash", "abc"),
            "blank_provider": lambda s: s.__setitem__("requested_provider", ""),
            "blank_model": lambda s: s.__setitem__("requested_model", ""),
            "overlong_provider": lambda s: s.__setitem__("requested_provider", "p" * 129),
            "nine_fallbacks": lambda s: s.__setitem__(
                "approved_fallbacks", [{"provider": f"p{i}", "model": f"m{i}"} for i in range(9)]
            ),
            "duplicate_fallback": lambda s: s.__setitem__(
                "approved_fallbacks",
                [{"provider": "x", "model": "y"}, {"provider": "x", "model": "y"}],
            ),
            "primary_as_fallback": lambda s: s.__setitem__(
                "approved_fallbacks", [{"provider": s["requested_provider"], "model": s["requested_model"]}]
            ),
            "unknown_identity_class": lambda s: s.__setitem__("required_identity_evidence_class", "provider_trusted"),
            "unknown_state_class": lambda s: s.__setitem__("required_execution_state_evidence_class", "TOTALLY_FAKE"),
            "wrong_builder_table": lambda s: s.__setitem__("required_output_fields", ["raw_output_hash"]),
            "duplicate_output_fields": lambda s: s.__setitem__(
                "required_output_fields", ["raw_output_hash", "raw_output_hash"]
            ),
            "unknown_top_level_field": lambda s: s.__setitem__("actual_provider", "anthropic"),
            "null_field": lambda s: s.__setitem__("requested_model", None),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                self._rejects(mutate)

    def test_untampered_snapshot_round_trips_through_the_reader(self) -> None:
        self.assertEqual(provenance._validate_route_snapshot(dict(self.good)), self.good)

    def test_route_snapshot_construction_never_reads_configuration(self) -> None:
        import sera.core as core

        def boom(*args, **kwargs):
            raise AssertionError("route construction reread config")

        original = (core.load_config, provenance.load_config)
        core.load_config = boom
        provenance.load_config = boom
        try:
            snapshot = route_snapshot(self.config, self.task, "implementation_builder", policy=self.policy)
        finally:
            core.load_config, provenance.load_config = original
        self.assertEqual(snapshot, self.good)


# --------------------------------------------------------------------------- #
# Group 6 — fallbacks                                                         #
# --------------------------------------------------------------------------- #
class FallbackTests(unittest.TestCase):
    def _config(self, *, allow: bool, fallbacks):
        def mutate(config):
            config["substitution_rules"]["allow_approved_fallbacks"] = allow
            config["lanes"]["fast_builder"]["approved_fallbacks"] = fallbacks

        return modern_merged(mutate)

    def test_policy_permitted_fallbacks_are_captured_in_exact_order(self) -> None:
        ordered = [
            {"provider": "zeta", "model": "z"},
            {"provider": "alpha", "model": "a"},
            {"provider": "mid", "model": "m"},
        ]
        config = self._config(allow=True, fallbacks=ordered)
        the_task = task(mode="fast", risk="low", uncertainty=0)
        snapshot = route_snapshot(config, the_task, "implementation_builder")
        self.assertEqual(snapshot["approved_fallbacks"], ordered)

    def test_reordering_fallbacks_changes_the_snapshot_hash(self) -> None:
        the_task = task(mode="fast", risk="low", uncertainty=0)
        one = route_snapshot(
            self._config(allow=True, fallbacks=[{"provider": "a", "model": "1"}, {"provider": "b", "model": "2"}]),
            the_task,
            "implementation_builder",
        )
        two = route_snapshot(
            self._config(allow=True, fallbacks=[{"provider": "b", "model": "2"}, {"provider": "a", "model": "1"}]),
            the_task,
            "implementation_builder",
        )
        self.assertNotEqual(one["snapshot_hash"], two["snapshot_hash"])

    def test_policy_disallows_fallbacks_and_lane_is_empty_yields_empty_list(self) -> None:
        config = self._config(allow=False, fallbacks=[])
        the_task = task(mode="fast", risk="low", uncertainty=0)
        snapshot = route_snapshot(config, the_task, "implementation_builder")
        self.assertEqual(snapshot["approved_fallbacks"], [])

    def test_policy_disallows_fallbacks_but_lane_configures_them_fails_closed(self) -> None:
        config = self._config(allow=False, fallbacks=[{"provider": "x", "model": "y"}])
        the_task = task(mode="fast", risk="low", uncertainty=0)
        decision = decide_route_from_config(config, the_task, REPO_MAP)
        view = provenance.normalize_route_config(config)
        identity = resolved_route_identity(config, decision)
        policy = policy_for(config, the_task)
        with self.assertRaises(SchemaError):
            provenance.resolve_route_snapshot_inputs(view, identity, "implementation_builder", policy)

    def test_unused_lane_fallbacks_never_reach_the_selected_stage(self) -> None:
        def mutate(config):
            config["substitution_rules"]["allow_approved_fallbacks"] = True
            config["lanes"]["fast_builder"]["approved_fallbacks"] = []
            config["lanes"]["deep_builder"]["approved_fallbacks"] = [{"provider": "unused", "model": "u"}]

        config = modern_merged(mutate)
        the_task = task(mode="fast", risk="low", uncertainty=0)  # selects fast_builder
        snapshot = route_snapshot(config, the_task, "implementation_builder")
        self.assertEqual(snapshot["approved_fallbacks"], [])

    def test_enabled_optional_fable_grants_no_fallback_authority(self) -> None:
        def mutate(config):
            config["substitution_rules"]["allow_approved_fallbacks"] = True
            config["lanes"]["fast_builder"]["approved_fallbacks"] = []
            config["lanes"]["optional_fable"].update(enabled=True, allowed_uses=["prototype"])

        config = modern_merged(mutate)
        the_task = task(mode="fast", risk="low", uncertainty=0, use_case="prototype")
        decision = decide_route_from_config(config, the_task, REPO_MAP)
        self.assertTrue(decision.fable_eligible)
        snapshot = route_snapshot(config, the_task, "implementation_builder")
        self.assertEqual(snapshot["approved_fallbacks"], [])

    def test_required_primary_disabled_never_advances_to_a_fallback(self) -> None:
        for stage_lane, over in (
            ("deep_builder", dict(mode="assured", risk="high")),
            ("independent_reviewer", dict(mode="assured", risk="high")),
            ("release_gate", dict(mode="assured", risk="high")),
        ):
            def mutate(config, lane=stage_lane):
                config["substitution_rules"]["allow_approved_fallbacks"] = True
                config["lanes"][lane].update(enabled=False, approved_fallbacks=[{"provider": "fb", "model": "x"}])

            config = modern_merged(mutate)
            with self.subTest(lane=stage_lane):
                with self.assertRaises(SeraError):
                    decide_route_from_config(config, task(**over), REPO_MAP)

    def test_required_validator_disabled_never_advances_to_a_fallback(self) -> None:
        def mutate(config):
            config["lanes"]["implementation_validator"].update(
                enabled=False, approved_fallbacks=[{"provider": "fb", "model": "x"}]
            )

        config = modern_merged(mutate)
        with self.assertRaises(SeraError):
            decide_route_from_config(config, task(mode="assured", risk="high"), REPO_MAP)


# --------------------------------------------------------------------------- #
# Group 7 — ledger                                                            #
# --------------------------------------------------------------------------- #
class RouteLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.task_dir = self.root / ".sera" / "tasks" / "task-1"
        self.task_dir.mkdir(parents=True)
        self.path = self.task_dir / "route-snapshots.jsonl"
        self.config = modern_merged()
        self.task = task(id="task-1", mode="assured", risk="high")
        self.policy = policy_for(self.config, self.task)
        self.snapshot = route_snapshot(self.config, self.task, "implementation_builder", policy=self.policy)

    def append(self, snapshot, task_dir=None):
        target = task_dir or self.task_dir
        with task_lock(target) as guard:
            provenance.append_route_snapshot(target, snapshot, guard)

    def test_real_lock_append_then_strict_read(self) -> None:
        self.append(self.snapshot)
        self.assertEqual(provenance.read_route_snapshots(self.task_dir).records(), [self.snapshot])

    def test_fake_released_and_wrong_task_locks_cannot_append(self) -> None:
        fake = TaskLockGuard(self.task_dir / ".lock", self.task_dir, "task", threading.get_ident(), held=True)
        with task_lock(self.task_dir) as released:
            pass
        for guard in (None, fake, released):
            with self.assertRaises(SchemaError):
                provenance.append_route_snapshot(self.task_dir, self.snapshot, guard)
        other = self.task_dir.parent / "other"
        other.mkdir()
        with task_lock(other) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_route_snapshot(self.task_dir, self.snapshot, guard)
        self.assertFalse(self.path.exists())

    def test_foreign_new_record_with_real_destination_lock_is_rejected(self) -> None:
        destination = self.task_dir.parent / "task-B"
        destination.mkdir()
        with task_lock(destination) as guard:
            with self.assertRaises(SchemaError):
                provenance.append_route_snapshot(destination, self.snapshot, guard)
        self.assertFalse((destination / "route-snapshots.jsonl").exists())

    def test_foreign_history_is_rejected_without_changing_bytes(self) -> None:
        foreign_task = task(id="task-A", mode="assured", risk="high")
        foreign_policy = provenance.build_policy_snapshot(
            provenance.translate_config(self.config, ROOT), foreign_task, "explicit_policy_adoption"
        )
        foreign = route_snapshot(self.config, foreign_task, "implementation_builder", policy=foreign_policy)
        self.path.write_text(canonical_json(foreign) + "\n")
        before = self.path.read_bytes()
        with self.assertRaises(SchemaError):
            provenance.read_route_snapshots(self.task_dir).records()
        self.assertEqual(self.path.read_bytes(), before)

    def test_mixed_task_history_blocks_reader_and_new_append(self) -> None:
        foreign = rehash({**self.snapshot, "task_id": "task-A"})
        for records in ([self.snapshot, foreign], [foreign, self.snapshot], [self.snapshot, foreign, self.snapshot]):
            self.path.write_text("".join(canonical_json(r) + "\n" for r in records))
            before = self.path.read_bytes()
            with self.assertRaises(SchemaError):
                provenance.read_route_snapshots(self.task_dir).records()
            with self.assertRaises(SchemaError):
                self.append(self.snapshot)
            self.assertEqual(self.path.read_bytes(), before)

    def test_malformed_and_truncated_history_block_append_with_bytes_unchanged(self) -> None:
        line = canonical_json(self.snapshot) + "\n"
        for content in (line + "{bad}\n" + line, line.rstrip(), line.replace("raw_output_hash", "bogus")):
            self.path.write_text(content, encoding="utf-8")
            before = self.path.read_bytes()
            with self.assertRaises(SchemaError):
                provenance.read_route_snapshots(self.task_dir).records()
            with self.assertRaises(SchemaError):
                self.append(self.snapshot)
            self.assertEqual(self.path.read_bytes(), before)

    def test_rehashed_semantic_invalid_history_still_blocks(self) -> None:
        bad = rehash({**self.snapshot, "stage": "builder"})
        self.path.write_text(canonical_json(bad) + "\n")
        with self.assertRaises(SchemaError):
            provenance.read_route_snapshots(self.task_dir).records()

    def test_valid_duplicate_history_is_retained_and_fingerprint_is_order_sensitive(self) -> None:
        self.append(self.snapshot)
        reader = provenance.read_route_snapshots(self.task_dir)
        self.assertEqual(reader.schema_family, "route_snapshot")
        original = reader.fingerprint()
        self.append(self.snapshot)
        self.assertEqual(reader.records(), [self.snapshot, self.snapshot])
        self.assertNotEqual(reader.fingerprint(), original)
        other = route_snapshot(self.config, self.task, "release_gate", policy=self.policy)
        self.path.write_text(canonical_json(self.snapshot) + "\n" + canonical_json(other) + "\n")
        forward = reader.fingerprint()
        self.path.write_text(canonical_json(other) + "\n" + canonical_json(self.snapshot) + "\n")
        self.assertNotEqual(reader.fingerprint(), forward)

    def test_existing_malformed_line_blocks_a_new_append(self) -> None:
        self.path.write_text("{not json}\n")
        before = self.path.read_bytes()
        with self.assertRaises(SchemaError):
            self.append(self.snapshot)
        self.assertEqual(self.path.read_bytes(), before)


# --------------------------------------------------------------------------- #
# Group 8 — source-authority absence                                          #
# --------------------------------------------------------------------------- #
class SourceAuthorityAbsenceTests(unittest.TestCase):
    def test_route_serialization_carries_no_source_authorization(self) -> None:
        config = modern_merged()
        the_task = task(mode="assured", risk="high")
        for stage in CANONICAL_STAGES:
            snapshot = route_snapshot(config, the_task, stage)
            serialized = canonical_json(snapshot)
            for forbidden in (
                "accepted_source_registration_hashes",
                "accepted_source_types",
                "required_capabilities",
                "accepted_verification_methods",
            ):
                self.assertNotIn(forbidden, snapshot)
                self.assertNotIn(forbidden, serialized)

    def test_route_binds_the_requirement_not_the_evidence(self) -> None:
        config = modern_merged()
        the_task = task(mode="assured", risk="high")
        policy = policy_for(config, the_task)
        snapshot = route_snapshot(config, the_task, "implementation_builder", policy=policy)
        # exactly the class the policy requires, nothing derived from provider/model
        self.assertEqual(
            snapshot["required_identity_evidence_class"],
            policy["identity_evidence_policy"]["implementation_builder"]["required_identity_evidence_class"],
        )
        self.assertNotIn("identity_evidence", snapshot)


# --------------------------------------------------------------------------- #
# Group 9 — legacy vs modern end to end                                       #
# --------------------------------------------------------------------------- #
class LegacyConfigV1Tests(unittest.TestCase):
    def test_v1_route_snapshot_uses_legacy_defaults_and_no_manufactured_authority(self) -> None:
        config = merged()
        the_task = task(mode="standard", risk="medium")
        policy = policy_for(config, the_task)
        snapshot = route_snapshot(config, the_task, "implementation_builder", policy=policy)
        self.assertIsNone(decide_route_from_config(config, the_task, REPO_MAP).validator)
        self.assertEqual(snapshot["approved_fallbacks"], [])
        self.assertEqual(snapshot["required_identity_evidence_class"], "manual_assertion")
        self.assertEqual(snapshot["required_execution_state_evidence_class"], "LEGACY_PROVENANCE")

    def test_v1_route_view_keeps_validator_disabled(self) -> None:
        view = provenance.normalize_route_config(merged())
        self.assertIs(view.lanes["implementation_validator"]["enabled"], False)


class ModernConfigV2Tests(unittest.TestCase):
    def test_modern_selected_validator_snapshot_binds_exact_targets_and_policy_classes(self) -> None:
        config = modern_merged()
        the_task = task(mode="assured", risk="high")
        policy = policy_for(config, the_task)
        snapshot = route_snapshot(config, the_task, "implementation_validator", policy=policy)
        self.assertEqual(snapshot["requested_provider"], "openai")
        self.assertEqual(snapshot["requested_model"], "gpt-5.6-sol")
        self.assertEqual(snapshot["required_output_fields"], ["raw_output_hash"])
        self.assertEqual(
            snapshot["required_identity_evidence_class"],
            policy["identity_evidence_policy"]["implementation_validator"]["required_identity_evidence_class"],
        )

    def test_build_route_snapshot_rejects_a_policy_from_another_task(self) -> None:
        config = modern_merged()
        other_policy = provenance.build_policy_snapshot(
            provenance.translate_config(config, ROOT),
            task(id="task-2", mode="assured", risk="high"),
            "explicit_policy_adoption",
        )
        with self.assertRaises(SchemaError):
            provenance.build_route_snapshot(
                "task-1",
                "implementation_builder",
                other_policy,
                {"provider": "anthropic", "model": "claude-sonnet-5"},
                [],
            )


# --------------------------------------------------------------------------- #
# Group 10 — T09-001: fallback policy enforced at the constructor boundary     #
# --------------------------------------------------------------------------- #
class BuildRouteSnapshotFallbackAuthorityTests(unittest.TestCase):
    """`build_route_snapshot` independently refuses authority the bound policy forbids.

    Every case here bypasses `resolve_route_snapshot_inputs` and calls the
    authoritative constructor directly with a caller-supplied effective list.
    """

    def _policy(self, *, allow: bool) -> dict:
        config = modern_merged(
            lambda c: c["substitution_rules"].__setitem__("allow_approved_fallbacks", allow)
        )
        return policy_for(config, task(id="task-1", mode="assured", risk="high"))

    def _build(self, policy, fallbacks):
        return provenance.build_route_snapshot(
            "task-1",
            "implementation_builder",
            policy,
            {"provider": "primary", "model": "model"},
            fallbacks,
        )

    def test_disallowed_policy_with_non_empty_fallbacks_fails_at_construction(self) -> None:
        policy = self._policy(allow=False)
        self.assertIs(policy["substitution_rules"]["allow_approved_fallbacks"], False)
        with self.assertRaises(SchemaError):
            self._build(policy, [{"provider": "fallback", "model": "other"}])

    def test_disallowed_policy_produces_no_snapshot_hash_authority(self) -> None:
        policy = self._policy(allow=False)
        result = None
        try:
            result = self._build(policy, [{"provider": "fallback", "model": "other"}])
        except SchemaError:
            pass
        self.assertIsNone(result)  # nothing hashed, nothing returned

    def test_disallowed_policy_with_empty_fallbacks_builds_a_valid_snapshot(self) -> None:
        snapshot = self._build(self._policy(allow=False), [])
        self.assertEqual(snapshot["approved_fallbacks"], [])
        self.assertEqual(provenance._validate_route_snapshot(dict(snapshot)), snapshot)

    def test_allowed_policy_with_ordered_fallbacks_builds_and_preserves_order(self) -> None:
        ordered = [
            {"provider": "zeta", "model": "z"},
            {"provider": "alpha", "model": "a"},
        ]
        snapshot = self._build(self._policy(allow=True), copy.deepcopy(ordered))
        self.assertEqual(snapshot["approved_fallbacks"], ordered)
        self.assertEqual(provenance._validate_route_snapshot(dict(snapshot)), snapshot)

    def test_resolver_fail_closed_check_is_still_present(self) -> None:
        config = modern_merged(
            lambda c: (
                c["substitution_rules"].__setitem__("allow_approved_fallbacks", False),
                c["lanes"]["fast_builder"].__setitem__(
                    "approved_fallbacks", [{"provider": "x", "model": "y"}]
                ),
            )
        )
        the_task = task(mode="fast", risk="low", uncertainty=0)
        decision = decide_route_from_config(config, the_task, REPO_MAP)
        view = provenance.normalize_route_config(config)
        identity = resolved_route_identity(config, decision)
        policy = policy_for(config, the_task)
        with self.assertRaises(SchemaError):
            provenance.resolve_route_snapshot_inputs(view, identity, "implementation_builder", policy)


if __name__ == "__main__":
    unittest.main()
