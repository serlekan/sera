"""Config v1-to-v2 compatibility translation tests."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from sera.core import DEFAULT_CONFIG, SeraError, load_config
from sera.provenance import ConfigV2View, translate_config


CANONICAL_ROLES = {
    "implementation_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
}


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


if __name__ == "__main__":
    unittest.main()
