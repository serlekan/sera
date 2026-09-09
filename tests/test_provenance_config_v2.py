"""Config v1-to-v2 compatibility translation tests."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import MappingProxyType

from sera import provenance
from sera.core import DEFAULT_CONFIG, SeraError, initialize, load_config
from sera.provenance import (
    ConfigV2View,
    RouteConfigView,
    normalize_route_config,
    translate_config,
)


CANONICAL_ROLES = {
    "implementation_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
}
CANONICAL_STAGE_ORDER = (
    "implementation_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
)
IDENTITY_EVIDENCE_CLASSES = (
    "unknown",
    "manual_assertion",
    "controller_observed",
    "adapter_observed",
    "provider_attested",
)
MODERN_ROUTE_LANES = (
    "fast_builder",
    "deep_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
)


def stage_policy(*, enabled: bool = True) -> dict[str, object]:
    return {
        "enabled": enabled,
        "required_modes": ["standard", "assured"],
        "required_risks": ["medium", "high"],
    }


def state_policy(evidence_class: str = "CHECKPOINT_OBSERVED") -> dict[str, object]:
    return {
        "minimum_evidence_class": evidence_class,
        "accepted_source_registration_hashes": ["ab" * 32],
        "accepted_source_types": ["registered_external_runner"],
        "required_capabilities": ["immutable_git_target"],
        "accepted_verification_methods": ["signed_manifest"],
    }


def identity_policy(mapping: dict[str, str] | None = None) -> dict[str, object]:
    """A four-stage identity-evidence policy keyed by canonical stage names."""
    classes = {
        "implementation_builder": "provider_attested",
        "implementation_validator": "adapter_observed",
        "independent_reviewer": "controller_observed",
        "release_gate": "manual_assertion",
    }
    if mapping is not None:
        classes = mapping
    return {
        stage: {"required_identity_evidence_class": value}
        for stage, value in classes.items()
    }


def modern_config() -> dict[str, object]:
    aliases = ("builder", "validator", "independent", "gate")
    return {
        "schema_version": 2,
        "repository_id": "sera-repository-v2",
        "stage_policies": {alias: stage_policy() for alias in aliases},
        "provenance_requirements": {
            "minimum_repository_identity_strength": "derived",
            "require_execution_receipts": True,
            "allow_legacy_provenance": False,
        },
        "execution_state_policy": {alias: state_policy() for alias in aliases},
        "identity_evidence_policy": identity_policy(),
        "substitution_rules": {
            "allow_approved_fallbacks": True,
            "allow_manual_substitution": False,
        },
        "knowledge_policy": {
            "source_paths": ["AGENTS.md", "docs/architecture.md"],
            "max_source_bytes": 120_000,
            "assessment_required": True,
        },
        "repository_identity_requirement": {"minimum_strength": "derived"},
        "context_budgets": {
            "token_budgets": {"fast": 5_000, "standard": 12_000, "assured": 24_000},
            "max_files": 10,
            "max_packet_chars": 40_000,
        },
    }


def route_lane(
    provider: str = "openai",
    model: str = "gpt-5.6-sol",
    *,
    enabled: bool = True,
    approved_fallbacks: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "provider": provider,
        "model": model,
        "enabled": enabled,
        "approved_fallbacks": [] if approved_fallbacks is None else approved_fallbacks,
    }


def modern_route_config() -> dict[str, object]:
    """A full modern config carrying both policy blocks and exact route lanes."""
    config = modern_config()
    config["lanes"] = {
        "planner": {"provider": "openai", "model": "gpt-5.6-sol", "enabled": True},
        "fast_builder": route_lane(
            "openai",
            "gpt-5.6-luna",
            approved_fallbacks=[
                {"provider": "anthropic", "model": "claude-sonnet-5"},
                {"provider": "openai", "model": "gpt-5.6-sol"},
            ],
        ),
        "deep_builder": route_lane("anthropic", "claude-sonnet-5"),
        "implementation_validator": route_lane("openai", "gpt-5.6-sol", enabled=True),
        "independent_reviewer": route_lane("anthropic", "claude-opus-5"),
        "release_gate": route_lane("openai", "gpt-5.6-sol"),
        "optional_fable": {
            "provider": "anthropic",
            "model": "claude-fable-5",
            "enabled": False,
            "allowed_uses": ["prototype"],
            "may_be_sole_release_gate": False,
        },
    }
    return config


class ConfigV1TranslationTests(unittest.TestCase):
    def test_default_config_becomes_a_complete_frozen_provider_neutral_view(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sera-config-v1-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        view = translate_config(copy.deepcopy(DEFAULT_CONFIG), root)

        self.assertIsInstance(view, ConfigV2View)
        self.assertEqual(view.source_schema, 1)
        self.assertEqual(set(view.stage_policies), CANONICAL_ROLES)
        self.assertEqual(set(view.execution_state_policy), CANONICAL_ROLES)
        self.assertTrue(view.stage_policies["implementation_builder"]["enabled"])
        self.assertFalse(view.stage_policies["implementation_validator"]["enabled"])
        self.assertEqual(view.context_budgets["token_budgets"]["assured"], 32_000)
        with self.assertRaises(FrozenInstanceError):
            view.source_schema = 2  # type: ignore[misc]
        with self.assertRaises(TypeError):
            view.stage_policies["builder"] = {}  # type: ignore[index]

    def test_legacy_aliases_terminate_at_the_translation_boundary(self) -> None:
        view = translate_config(copy.deepcopy(DEFAULT_CONFIG), Path.cwd())
        forbidden = {"builder", "validator", "independent", "gate", "fast_builder", "deep_builder"}
        self.assertTrue(forbidden.isdisjoint(view.stage_policies))
        self.assertTrue(forbidden.isdisjoint(view.execution_state_policy))
        self.assertTrue(forbidden.isdisjoint(view.identity_evidence_policy))

    def test_repeated_translation_does_not_change_v1_config_file_bytes(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="sera-config-bytes-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        config_dir = root / ".sera"
        config_dir.mkdir()
        path = config_dir / "config.json"
        original = (json.dumps(DEFAULT_CONFIG, indent=3, ensure_ascii=False) + "\r\n").encode()
        path.write_bytes(original)

        loaded = load_config(root)
        translate_config(loaded, root)
        translate_config(loaded, root)
        normalize_route_config(loaded)
        normalize_route_config(loaded)

        self.assertEqual(path.read_bytes(), original)


class ConfigV2TranslationTests(unittest.TestCase):
    def test_modern_config_normalizes_aliases_and_retains_policy_blocks(self) -> None:
        loaded = modern_config()
        view = translate_config(loaded, Path.cwd())

        self.assertEqual(view.source_schema, 2)
        self.assertEqual(set(view.stage_policies), CANONICAL_ROLES)
        self.assertEqual(set(view.execution_state_policy), CANONICAL_ROLES)
        self.assertEqual(view.repository_identity_requirement, {"minimum_strength": "derived"})
        self.assertEqual(view.substitution_rules["allow_manual_substitution"], False)
        self.assertEqual(view.knowledge_policy["source_paths"], ("AGENTS.md", "docs/architecture.md"))
        self.assertEqual(
            view.execution_state_policy["independent_reviewer"]["minimum_evidence_class"],
            "CHECKPOINT_OBSERVED",
        )
        self.assertEqual(view.context_budgets["max_packet_chars"], 40_000)

    def test_unknown_field_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["surprise"] = True
        with self.assertRaisesRegex(SeraError, "unknown field: surprise"):
            translate_config(loaded, Path.cwd())

    def test_explicit_null_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["knowledge_policy"] = None
        with self.assertRaisesRegex(SeraError, "knowledge_policy"):
            translate_config(loaded, Path.cwd())

    def test_null_in_a_known_legacy_compatibility_field_is_not_ignored(self) -> None:
        loaded = modern_config()
        loaded["default_mode"] = None
        with self.assertRaisesRegex(SeraError, "default_mode"):
            translate_config(loaded, Path.cwd())

    def test_malformed_role_policy_is_rejected_without_silent_default(self) -> None:
        loaded = modern_config()
        loaded["stage_policies"]["builder"]["required_modes"] = ["turbo"]  # type: ignore[index]
        with self.assertRaisesRegex(SeraError, "stage_policies.builder.required_modes"):
            translate_config(loaded, Path.cwd())

    def test_invalid_or_unbounded_values_are_rejected(self) -> None:
        loaded = modern_config()
        loaded["context_budgets"]["max_files"] = 0  # type: ignore[index]
        with self.assertRaisesRegex(SeraError, "context_budgets.max_files"):
            translate_config(loaded, Path.cwd())

    def test_unsupported_schema_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["schema_version"] = 3
        with self.assertRaisesRegex(SeraError, "unsupported config schema"):
            translate_config(loaded, Path.cwd())


class IdentityEvidencePolicyV2Tests(unittest.TestCase):
    def test_four_canonical_stage_policies_normalize_exactly(self) -> None:
        view = translate_config(modern_config(), Path.cwd())

        self.assertEqual(set(view.identity_evidence_policy), CANONICAL_ROLES)
        self.assertEqual(
            {stage: entry["required_identity_evidence_class"] for stage, entry in view.identity_evidence_policy.items()},
            {
                "implementation_builder": "provider_attested",
                "implementation_validator": "adapter_observed",
                "independent_reviewer": "controller_observed",
                "release_gate": "manual_assertion",
            },
        )
        for entry in view.identity_evidence_policy.values():
            self.assertEqual(set(entry), {"required_identity_evidence_class"})

    def test_every_identity_class_is_individually_accepted(self) -> None:
        for identity_class in IDENTITY_EVIDENCE_CLASSES:
            loaded = modern_config()
            loaded["identity_evidence_policy"] = identity_policy(
                {stage: identity_class for stage in CANONICAL_STAGE_ORDER}
            )
            view = translate_config(loaded, Path.cwd())
            self.assertEqual(
                {entry["required_identity_evidence_class"] for entry in view.identity_evidence_policy.values()},
                {identity_class},
            )

    def test_identity_block_is_not_nested_in_execution_state_policy(self) -> None:
        view = translate_config(modern_config(), Path.cwd())
        for entry in view.execution_state_policy.values():
            self.assertNotIn("required_identity_evidence_class", entry)

    def test_identity_mapping_is_immutable(self) -> None:
        view = translate_config(modern_config(), Path.cwd())
        self.assertIsInstance(view.identity_evidence_policy, MappingProxyType)
        with self.assertRaises(TypeError):
            view.identity_evidence_policy["release_gate"] = {}  # type: ignore[index]
        with self.assertRaises(TypeError):
            view.identity_evidence_policy["release_gate"]["required_identity_evidence_class"] = "unknown"  # type: ignore[index]

    def test_missing_canonical_stage_is_rejected(self) -> None:
        loaded = modern_config()
        del loaded["identity_evidence_policy"]["release_gate"]
        with self.assertRaisesRegex(SeraError, "identity_evidence_policy"):
            translate_config(loaded, Path.cwd())

    def test_missing_identity_block_entirely_is_rejected(self) -> None:
        loaded = modern_config()
        del loaded["identity_evidence_policy"]
        with self.assertRaisesRegex(SeraError, "identity_evidence_policy"):
            translate_config(loaded, Path.cwd())

    def test_unknown_stage_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"]["planner"] = {"required_identity_evidence_class": "manual_assertion"}
        with self.assertRaisesRegex(SeraError, "identity_evidence_policy"):
            translate_config(loaded, Path.cwd())

    def test_alias_stage_name_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"]["builder"] = {"required_identity_evidence_class": "manual_assertion"}
        with self.assertRaisesRegex(SeraError, "identity_evidence_policy"):
            translate_config(loaded, Path.cwd())

    def test_missing_required_identity_evidence_class_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"]["implementation_builder"] = {}
        with self.assertRaisesRegex(SeraError, "required_identity_evidence_class"):
            translate_config(loaded, Path.cwd())

    def test_unknown_nested_field_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"]["implementation_builder"]["notes"] = "extra"
        with self.assertRaisesRegex(SeraError, "unknown field"):
            translate_config(loaded, Path.cwd())

    def test_null_identity_class_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"]["implementation_builder"]["required_identity_evidence_class"] = None
        with self.assertRaisesRegex(SeraError, "identity_evidence_policy|null"):
            translate_config(loaded, Path.cwd())

    def test_unsupported_identity_class_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"]["implementation_builder"]["required_identity_evidence_class"] = "provider_trusted"
        with self.assertRaisesRegex(SeraError, "required_identity_evidence_class"):
            translate_config(loaded, Path.cwd())

    def test_non_string_identity_class_is_rejected(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"]["implementation_builder"]["required_identity_evidence_class"] = 3
        with self.assertRaisesRegex(SeraError, "required_identity_evidence_class"):
            translate_config(loaded, Path.cwd())

    def test_identity_class_is_not_inferred_from_execution_state_policy(self) -> None:
        loaded = modern_config()
        loaded["identity_evidence_policy"] = identity_policy(
            {stage: "unknown" for stage in CANONICAL_STAGE_ORDER}
        )
        view = translate_config(loaded, Path.cwd())
        self.assertEqual(
            {entry["required_identity_evidence_class"] for entry in view.identity_evidence_policy.values()},
            {"unknown"},
        )


class IdentityEvidencePolicyV1Tests(unittest.TestCase):
    def test_v1_translation_requires_manual_assertion_for_every_stage(self) -> None:
        view = translate_config(copy.deepcopy(DEFAULT_CONFIG), Path.cwd())

        self.assertEqual(set(view.identity_evidence_policy), CANONICAL_ROLES)
        for stage in CANONICAL_STAGE_ORDER:
            self.assertEqual(
                view.identity_evidence_policy[stage],
                {"required_identity_evidence_class": "manual_assertion"},
            )

    def test_v1_identity_mapping_is_immutable(self) -> None:
        view = translate_config(copy.deepcopy(DEFAULT_CONFIG), Path.cwd())
        with self.assertRaises(TypeError):
            view.identity_evidence_policy["implementation_builder"]["required_identity_evidence_class"] = "provider_attested"  # type: ignore[index]


class RouteConfigViewPositiveTests(unittest.TestCase):
    def test_modern_route_config_produces_a_frozen_view(self) -> None:
        view = normalize_route_config(modern_route_config())

        self.assertIsInstance(view, RouteConfigView)
        self.assertEqual(view.source_schema, 2)
        self.assertEqual(set(view.lanes), set(MODERN_ROUTE_LANES))
        with self.assertRaises(FrozenInstanceError):
            view.source_schema = 1  # type: ignore[misc]

    def test_route_view_is_not_a_config_v2_view(self) -> None:
        view = normalize_route_config(modern_route_config())
        self.assertNotIsInstance(view, ConfigV2View)
        self.assertFalse(hasattr(view, "stage_policies"))
        self.assertFalse(hasattr(view, "identity_evidence_policy"))

    def test_provider_model_and_enabled_are_preserved(self) -> None:
        view = normalize_route_config(modern_route_config())
        self.assertEqual(view.lanes["fast_builder"]["provider"], "openai")
        self.assertEqual(view.lanes["fast_builder"]["model"], "gpt-5.6-luna")
        self.assertEqual(view.lanes["deep_builder"]["provider"], "anthropic")
        self.assertEqual(view.lanes["independent_reviewer"]["model"], "claude-opus-5")
        self.assertIs(view.lanes["fast_builder"]["enabled"], True)

    def test_fallback_order_is_preserved_exactly(self) -> None:
        view = normalize_route_config(modern_route_config())
        self.assertEqual(
            view.lanes["fast_builder"]["approved_fallbacks"],
            (
                {"provider": "anthropic", "model": "claude-sonnet-5"},
                {"provider": "openai", "model": "gpt-5.6-sol"},
            ),
        )

    def test_zero_fallbacks_are_accepted(self) -> None:
        view = normalize_route_config(modern_route_config())
        self.assertEqual(view.lanes["release_gate"]["approved_fallbacks"], ())

    def test_eight_fallbacks_are_accepted(self) -> None:
        config = modern_route_config()
        config["lanes"]["fast_builder"]["approved_fallbacks"] = [
            {"provider": f"provider-{index}", "model": f"model-{index}"} for index in range(8)
        ]
        view = normalize_route_config(config)
        self.assertEqual(len(view.lanes["fast_builder"]["approved_fallbacks"]), 8)
        self.assertEqual(view.lanes["fast_builder"]["approved_fallbacks"][0], {"provider": "provider-0", "model": "model-0"})
        self.assertEqual(view.lanes["fast_builder"]["approved_fallbacks"][7], {"provider": "provider-7", "model": "model-7"})

    def test_explicit_implementation_validator_lane_is_accepted(self) -> None:
        view = normalize_route_config(modern_route_config())
        self.assertIn("implementation_validator", view.lanes)
        self.assertIs(view.lanes["implementation_validator"]["enabled"], True)
        self.assertEqual(view.lanes["implementation_validator"]["provider"], "openai")

    def test_boundary_length_provider_and_model_are_accepted(self) -> None:
        config = modern_route_config()
        config["lanes"]["deep_builder"]["provider"] = "p" * 128
        config["lanes"]["deep_builder"]["model"] = "m" * 128
        view = normalize_route_config(config)
        self.assertEqual(view.lanes["deep_builder"]["provider"], "p" * 128)
        self.assertEqual(view.lanes["deep_builder"]["model"], "m" * 128)

    def test_route_view_nested_state_is_immutable(self) -> None:
        view = normalize_route_config(modern_route_config())
        with self.assertRaises(TypeError):
            view.lanes["fast_builder"] = {}  # type: ignore[index]
        with self.assertRaises(TypeError):
            view.lanes["fast_builder"]["provider"] = "other"  # type: ignore[index]
        with self.assertRaises((TypeError, AttributeError)):
            view.lanes["fast_builder"]["approved_fallbacks"].append({"provider": "x", "model": "y"})  # type: ignore[attr-defined]
        with self.assertRaises(TypeError):
            view.lanes["fast_builder"]["approved_fallbacks"][0]["provider"] = "x"  # type: ignore[index]

    def test_same_captured_config_produces_both_views_without_a_reread(self) -> None:
        config = modern_route_config()
        snapshot = copy.deepcopy(config)

        original_load_config = provenance.load_config
        provenance.load_config = _forbidden_load_config
        try:
            policy_view = translate_config(config, Path.cwd())
            route_view = normalize_route_config(config)
        finally:
            provenance.load_config = original_load_config

        self.assertIsInstance(policy_view, ConfigV2View)
        self.assertIsInstance(route_view, RouteConfigView)
        self.assertEqual(config, snapshot)


def _forbidden_load_config(*args: object, **kwargs: object) -> object:
    raise AssertionError("config translation must not reload configuration")


class RouteConfigViewNegativeTests(unittest.TestCase):
    def _rejects(self, mutate) -> str:
        config = modern_route_config()
        mutate(config)
        with self.assertRaises(SeraError) as caught:
            normalize_route_config(config)
        return str(caught.exception)

    def test_nine_fallbacks_are_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [
                {"provider": f"p{index}", "model": f"m{index}"} for index in range(9)
            ]

        self.assertRegex(self._rejects(mutate), "approved_fallbacks")

    def test_duplicate_fallback_target_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [
                {"provider": "anthropic", "model": "claude-sonnet-5"},
                {"provider": "anthropic", "model": "claude-sonnet-5"},
            ]

        self.assertRegex(self._rejects(mutate), "duplicate|approved_fallbacks")

    def test_fallback_equal_to_lane_primary_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [
                {"provider": "openai", "model": "gpt-5.6-luna"},
            ]

        self.assertRegex(self._rejects(mutate), "primary|approved_fallbacks")

    def test_blank_fallback_provider_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [{"provider": "", "model": "claude-sonnet-5"}]

        self.assertRegex(self._rejects(mutate), "provider")

    def test_blank_fallback_model_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [{"provider": "anthropic", "model": ""}]

        self.assertRegex(self._rejects(mutate), "model")

    def test_overlong_fallback_provider_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [{"provider": "p" * 129, "model": "claude-sonnet-5"}]

        self.assertRegex(self._rejects(mutate), "provider")

    def test_overlong_fallback_model_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [{"provider": "anthropic", "model": "m" * 129}]

        self.assertRegex(self._rejects(mutate), "model")

    def test_missing_fallback_provider_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [{"model": "claude-sonnet-5"}]

        self.assertRegex(self._rejects(mutate), "provider")

    def test_unknown_fallback_field_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = [
                {"provider": "anthropic", "model": "claude-sonnet-5", "weight": 1},
            ]

        self.assertRegex(self._rejects(mutate), "unknown field|weight")

    def test_non_object_fallback_entry_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = ["anthropic/claude-sonnet-5"]

        self.assertRegex(self._rejects(mutate), "object|approved_fallbacks")

    def test_non_list_approved_fallbacks_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = {"provider": "anthropic", "model": "claude-sonnet-5"}

        self.assertRegex(self._rejects(mutate), "array|list|approved_fallbacks")

    def test_null_approved_fallbacks_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["approved_fallbacks"] = None

        self.assertRegex(self._rejects(mutate), "approved_fallbacks|null")

    def test_unknown_lane_field_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["temperature"] = 0.2

        self.assertRegex(self._rejects(mutate), "unknown field|temperature")

    def test_blank_lane_provider_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["provider"] = ""

        self.assertRegex(self._rejects(mutate), "provider")

    def test_overlong_lane_model_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["model"] = "m" * 129

        self.assertRegex(self._rejects(mutate), "model")

    def test_non_boolean_lane_enabled_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            config["lanes"]["fast_builder"]["enabled"] = "yes"

        self.assertRegex(self._rejects(mutate), "enabled")

    def test_missing_modern_route_lane_is_rejected(self) -> None:
        def mutate(config: dict[str, object]) -> None:
            del config["lanes"]["deep_builder"]

        self.assertRegex(self._rejects(mutate), "deep_builder|lane")

    def test_null_document_is_rejected(self) -> None:
        with self.assertRaises(SeraError):
            normalize_route_config(None)  # type: ignore[arg-type]

    def test_fallbacks_are_never_sorted_or_deduplicated_silently(self) -> None:
        config = modern_route_config()
        config["lanes"]["fast_builder"]["approved_fallbacks"] = [
            {"provider": "zeta", "model": "z"},
            {"provider": "alpha", "model": "a"},
        ]
        view = normalize_route_config(config)
        self.assertEqual(
            view.lanes["fast_builder"]["approved_fallbacks"],
            ({"provider": "zeta", "model": "z"}, {"provider": "alpha", "model": "a"}),
        )


class RouteConfigViewV1Tests(unittest.TestCase):
    def test_v1_route_view_disables_validator_and_empties_every_fallback(self) -> None:
        view = normalize_route_config(copy.deepcopy(DEFAULT_CONFIG))

        self.assertIsInstance(view, RouteConfigView)
        self.assertEqual(view.source_schema, 1)
        self.assertEqual(set(view.lanes), set(MODERN_ROUTE_LANES))
        self.assertIs(view.lanes["implementation_validator"]["enabled"], False)
        for lane in view.lanes.values():
            self.assertEqual(lane["approved_fallbacks"], ())

    def test_v1_route_view_preserves_default_lane_identity(self) -> None:
        view = normalize_route_config(copy.deepcopy(DEFAULT_CONFIG))
        self.assertEqual(view.lanes["fast_builder"]["provider"], "openai")
        self.assertEqual(view.lanes["fast_builder"]["model"], "gpt-5.6-luna")
        self.assertEqual(view.lanes["independent_reviewer"]["model"], "claude-opus-5")

    def test_enabled_optional_fable_grants_no_fallback_authority(self) -> None:
        config = copy.deepcopy(DEFAULT_CONFIG)
        config["lanes"]["optional_fable"]["enabled"] = True
        view = normalize_route_config(config)
        for lane in view.lanes.values():
            self.assertEqual(lane["approved_fallbacks"], ())
        self.assertNotIn("optional_fable", view.lanes)
        self.assertNotIn("planner", view.lanes)

    def test_v1_route_view_is_immutable(self) -> None:
        view = normalize_route_config(copy.deepcopy(DEFAULT_CONFIG))
        with self.assertRaises(TypeError):
            view.lanes["release_gate"]["enabled"] = False  # type: ignore[index]


class LegacyCompatibilityTests(unittest.TestCase):
    def _init_repo(self, prefix: str) -> Path:
        root = Path(tempfile.mkdtemp(prefix=prefix))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        initialize(root)
        return root

    def test_default_v1_config_file_bytes_survive_repeated_reads_and_both_views(self) -> None:
        root = self._init_repo("sera-legacy-bytes-")
        path = root / ".sera" / "config.json"
        original = path.read_bytes()

        for _ in range(3):
            loaded = load_config(root)
            translate_config(loaded, root)
            normalize_route_config(loaded)

        self.assertEqual(path.read_bytes(), original)

    def test_v1_default_config_yields_disabled_validator_and_empty_fallbacks(self) -> None:
        root = self._init_repo("sera-legacy-v1-")
        loaded = load_config(root)

        policy_view = translate_config(loaded, root)
        route_view = normalize_route_config(loaded)

        self.assertFalse(policy_view.stage_policies["implementation_validator"]["enabled"])
        self.assertIs(route_view.lanes["implementation_validator"]["enabled"], False)
        for lane in route_view.lanes.values():
            self.assertEqual(lane["approved_fallbacks"], ())
        for stage in CANONICAL_STAGE_ORDER:
            self.assertEqual(
                policy_view.identity_evidence_policy[stage]["required_identity_evidence_class"],
                "manual_assertion",
            )

    def test_modern_config_is_accepted_and_preserved_by_core_load_config(self) -> None:
        """core.validate_config already accepts the exact modern lane shape; prove it."""
        root = self._init_repo("sera-modern-load-")
        path = root / ".sera" / "config.json"
        modern_bytes = (json.dumps(modern_route_config(), indent=2) + "\n").encode("utf-8")
        path.write_bytes(modern_bytes)

        loaded = load_config(root)
        self.assertEqual(path.read_bytes(), modern_bytes)
        self.assertIn("implementation_validator", loaded["lanes"])
        self.assertEqual(
            loaded["lanes"]["fast_builder"]["approved_fallbacks"],
            [
                {"provider": "anthropic", "model": "claude-sonnet-5"},
                {"provider": "openai", "model": "gpt-5.6-sol"},
            ],
        )

        policy_view = translate_config(loaded, root)
        route_view = normalize_route_config(loaded)
        self.assertEqual(policy_view.source_schema, 2)
        self.assertEqual(route_view.source_schema, 2)
        self.assertEqual(set(route_view.lanes), set(MODERN_ROUTE_LANES))

    def test_legacy_aliases_do_not_escape_normalized_route_or_identity_output(self) -> None:
        policy_view = translate_config(modern_route_config(), Path.cwd())
        route_view = normalize_route_config(modern_route_config())
        forbidden = {"builder", "validator", "independent", "gate"}
        self.assertTrue(forbidden.isdisjoint(policy_view.identity_evidence_policy))
        self.assertTrue(forbidden.isdisjoint(route_view.lanes))


if __name__ == "__main__":
    unittest.main()
