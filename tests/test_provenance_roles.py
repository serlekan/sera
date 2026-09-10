"""Canonical role vocabulary and implementation-origin semantics (spec Section 9).

T10 fixes one internal vocabulary for roles, implementation origins, and
role/origin requirements. It does not route executions, validate receipts, or
create any persisted artifact.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from sera import provenance
from sera.core import SeraError
from sera.schemas import SchemaError, canonical_json
from tests.test_provenance_config_v2 import modern_config


ROOT = Path(".")
TASK = {"id": "task-1", "mode": "assured", "risk": "high", "verification": ["python -m unittest"]}

# The exact canonical roles and their required order (spec Section 9).
CANONICAL_ORDER = (
    "implementation_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
)
# Routing lanes and legacy presentation names that MUST NOT be canonical roles.
NON_ROLES = (
    "planner",
    "reviewer",
    "supplementary",
    "fast_builder",
    "deep_builder",
    "optional_fable",
    "validator",
    "builder",
    "independent",
    "gate",
)


def policy_snapshot(*, validator_required: bool) -> dict:
    config = modern_config()
    if not validator_required:
        config["stage_policies"]["validator"]["enabled"] = False
    view = provenance.translate_config(config, ROOT)
    return provenance.build_policy_snapshot(view, TASK, "explicit_policy_adoption")


class CanonicalRoleTests(unittest.TestCase):
    def test_canonical_roles_are_exactly_the_four_in_order(self) -> None:
        self.assertEqual(tuple(provenance.CANONICAL_ROLES), CANONICAL_ORDER)

    def test_canonical_roles_collection_is_immutable_and_stable(self) -> None:
        first = tuple(provenance.CANONICAL_ROLES)
        with self.assertRaises((TypeError, AttributeError)):
            provenance.CANONICAL_ROLES[0] = "x"  # type: ignore[index]
        self.assertEqual(tuple(provenance.CANONICAL_ROLES), first)

    def test_canonical_role_resolves_to_itself(self) -> None:
        for role in CANONICAL_ORDER:
            self.assertEqual(provenance.canonical_role(role), role)

    def test_section_9_domain_aliases_resolve(self) -> None:
        self.assertEqual(provenance.canonical_role("builder"), "implementation_builder")
        self.assertEqual(provenance.canonical_role("independent"), "independent_reviewer")
        self.assertEqual(provenance.canonical_role("gate"), "release_gate")

    def test_validator_is_not_a_section_9_domain_alias(self) -> None:
        # T06 config compatibility recognizes `validator` inside its own
        # normalization boundary; that must not leak into the domain vocabulary.
        with self.assertRaises(SeraError):
            provenance.canonical_role("validator")

    def test_unknown_blank_and_non_string_roles_fail_closed(self) -> None:
        for bad in ("", "  ", "implementation_reviewer", "release-gate", "BUILDER"):
            with self.subTest(bad=bad), self.assertRaises(SeraError):
                provenance.canonical_role(bad)
        for bad in (None, 0, 1, True, ["builder"], {"role": "builder"}):
            with self.subTest(bad=bad), self.assertRaises(SeraError):
                provenance.canonical_role(bad)  # type: ignore[arg-type]

    def test_routing_lane_names_and_legacy_presentation_names_are_rejected(self) -> None:
        for bad in NON_ROLES:
            if bad in {"builder", "independent", "gate"}:
                continue  # these three are the permitted Section 9 aliases
            with self.subTest(bad=bad), self.assertRaises(SeraError):
                provenance.canonical_role(bad)

    def test_role_aliases_authority_excludes_validator_and_lanes(self) -> None:
        aliases = dict(provenance.ROLE_ALIASES)
        self.assertEqual(set(aliases.values()), set(CANONICAL_ORDER))
        self.assertNotIn("validator", aliases)
        for lane in ("fast_builder", "deep_builder", "planner", "optional_fable", "supplementary"):
            self.assertNotIn(lane, aliases)
        with self.assertRaises((TypeError, AttributeError)):
            provenance.ROLE_ALIASES["x"] = "y"  # type: ignore[index]

    def test_no_case_folding_or_trimming(self) -> None:
        for bad in (" builder", "builder ", "Builder", "GATE", "Independent"):
            with self.subTest(bad=bad), self.assertRaises(SeraError):
                provenance.canonical_role(bad)


class PersistedAliasBoundaryTests(unittest.TestCase):
    def test_canonicalization_precedes_a_hypothetical_persistence_boundary(self) -> None:
        # A modern record is built from canonical roles only; an alias supplied
        # at the domain boundary is resolved before it could be persisted.
        record = {"role": provenance.canonical_role("builder")}
        self.assertEqual(record["role"], "implementation_builder")
        self.assertNotIn(record["role"], {"builder", "independent", "gate"})

    def test_t08_strict_reader_still_rejects_a_persisted_role_alias(self) -> None:
        snapshot = policy_snapshot(validator_required=True)
        aliased = copy.deepcopy(snapshot)
        aliased["required_stages"] = ["builder"]
        with self.assertRaises(SchemaError):
            provenance._validate_policy_snapshot(aliased)
        aliased_origin = copy.deepcopy(snapshot)
        aliased_origin["implementation_origin_rules"]["sera_builder"] = ["builder"]
        with self.assertRaises(SchemaError):
            provenance._validate_policy_snapshot(aliased_origin)
        # canonical_json round-trips the untampered snapshot unchanged.
        self.assertEqual(canonical_json(snapshot), canonical_json(copy.deepcopy(snapshot)))


class ImplementationOriginTests(unittest.TestCase):
    def test_implementation_origins_are_exactly_the_three_in_order(self) -> None:
        self.assertEqual(
            tuple(provenance.IMPLEMENTATION_ORIGINS),
            ("sera_builder", "external", "pre_existing"),
        )

    def test_origins_collection_is_immutable(self) -> None:
        with self.assertRaises((TypeError, AttributeError)):
            provenance.IMPLEMENTATION_ORIGINS[0] = "x"  # type: ignore[index]

    def test_known_origins_are_accepted(self) -> None:
        policy = policy_snapshot(validator_required=True)
        for origin in ("sera_builder", "external", "pre_existing"):
            self.assertEqual(provenance.origin_requirements(origin, policy)["origin"], origin)

    def test_unknown_origin_fails_closed_with_reason(self) -> None:
        policy = policy_snapshot(validator_required=True)
        for bad in ("legacy", "manual", "imported", "third_party", "unknown", "", None, 3):
            with self.subTest(bad=bad):
                with self.assertRaises(provenance.ProvenanceRoleError) as caught:
                    provenance.origin_requirements(bad, policy)  # type: ignore[arg-type]
                self.assertEqual(caught.exception.code, provenance.IMPLEMENTATION_ORIGIN_UNSUPPORTED)
                self.assertTrue(
                    str(caught.exception).startswith(f"{provenance.IMPLEMENTATION_ORIGIN_UNSUPPORTED}:")
                )


class OriginRequirementsTests(unittest.TestCase):
    RETURN_KEYS = {"origin", "required_roles", "builder_authorship_claim", "reason"}

    def test_return_shape_is_minimal_and_deterministic(self) -> None:
        policy = policy_snapshot(validator_required=True)
        result = provenance.origin_requirements("sera_builder", policy)
        self.assertEqual(set(result), self.RETURN_KEYS)
        self.assertIsInstance(result["required_roles"], tuple)

    def test_sera_builder_always_requires_implementation_builder(self) -> None:
        for validator_required in (True, False):
            policy = policy_snapshot(validator_required=validator_required)
            result = provenance.origin_requirements("sera_builder", policy)
            self.assertEqual(result["required_roles"], ("implementation_builder",))
            self.assertIs(result["builder_authorship_claim"], True)
            self.assertEqual(result["reason"], provenance.BUILDER_PROVENANCE_REQUIRED)

    def test_external_requires_validator_only_when_policy_requires_the_stage(self) -> None:
        required = provenance.origin_requirements("external", policy_snapshot(validator_required=True))
        self.assertEqual(required["required_roles"], ("implementation_validator",))
        self.assertIs(required["builder_authorship_claim"], False)
        self.assertIsNone(required["reason"])

        not_required = provenance.origin_requirements(
            "external", policy_snapshot(validator_required=False)
        )
        self.assertEqual(not_required["required_roles"], ())
        self.assertIs(not_required["builder_authorship_claim"], False)

    def test_pre_existing_requires_validator_only_when_policy_requires_the_stage(self) -> None:
        required = provenance.origin_requirements(
            "pre_existing", policy_snapshot(validator_required=True)
        )
        self.assertEqual(required["required_roles"], ("implementation_validator",))
        self.assertIs(required["builder_authorship_claim"], False)

        not_required = provenance.origin_requirements(
            "pre_existing", policy_snapshot(validator_required=False)
        )
        self.assertEqual(not_required["required_roles"], ())

    def test_external_and_pre_existing_never_claim_builder_authorship(self) -> None:
        for validator_required in (True, False):
            policy = policy_snapshot(validator_required=validator_required)
            for origin in ("external", "pre_existing"):
                result = provenance.origin_requirements(origin, policy)
                self.assertNotIn("implementation_builder", result["required_roles"])
                self.assertIs(result["builder_authorship_claim"], False)

    def test_does_not_read_current_configuration(self) -> None:
        policy = policy_snapshot(validator_required=True)
        with patch.object(provenance, "load_config", side_effect=AssertionError("config reread")):
            for origin in ("sera_builder", "external", "pre_existing"):
                provenance.origin_requirements(origin, policy)

    def test_does_not_mutate_the_input_policy(self) -> None:
        policy = policy_snapshot(validator_required=True)
        before = canonical_json(policy)
        for origin in ("sera_builder", "external", "pre_existing"):
            provenance.origin_requirements(origin, policy)
        self.assertEqual(canonical_json(policy), before)

    def test_consumes_captured_policy_facts_not_a_second_answer(self) -> None:
        # A hand-built policy whose origin rule disagrees with required_stages is
        # internally contradictory and fails closed.
        contradictory = {
            "required_stages": ["implementation_builder"],
            "implementation_origin_rules": {
                "sera_builder": ["implementation_builder"],
                "external": ["implementation_validator"],
                "pre_existing": [],
            },
        }
        with self.assertRaises(SchemaError):
            provenance.origin_requirements("external", contradictory)

    def test_malformed_policy_fails_closed(self) -> None:
        for bad in (
            None,
            {},
            {"implementation_origin_rules": {}, "required_stages": []},
            {"required_stages": ["implementation_builder"]},
            {
                "required_stages": ["implementation_builder"],
                "implementation_origin_rules": {
                    "sera_builder": ["implementation_validator"],
                    "external": [],
                    "pre_existing": [],
                },
            },
            {
                "required_stages": ["implementation_builder"],
                "implementation_origin_rules": {
                    "sera_builder": ["implementation_builder"],
                    "external": ["implementation_builder"],
                    "pre_existing": [],
                },
            },
        ):
            with self.subTest(bad=bad), self.assertRaises(SchemaError):
                provenance.origin_requirements("external", bad)  # type: ignore[arg-type]


class ValidatorVersusBuilderTests(unittest.TestCase):
    def test_validator_never_satisfies_builder(self) -> None:
        self.assertIs(provenance.validator_satisfies_builder(), False)

    def test_result_is_unconditional(self) -> None:
        # The helper takes no arguments; there is no provider/model/policy/hash
        # input that could flip it.
        results = {provenance.validator_satisfies_builder() for _ in range(8)}
        self.assertEqual(results, {False})

    def test_reason_code_spelling_is_stable(self) -> None:
        self.assertEqual(
            provenance.VALIDATOR_CANNOT_SATISFY_BUILDER, "VALIDATOR_CANNOT_SATISFY_BUILDER"
        )
        self.assertIn(
            "VALIDATOR_CANNOT_SATISFY_BUILDER",
            provenance.validator_satisfies_builder.__doc__ or "",
        )


class ReasonCodeTests(unittest.TestCase):
    def test_t10_reason_codes_spelling(self) -> None:
        self.assertEqual(
            provenance.VALIDATOR_CANNOT_SATISFY_BUILDER, "VALIDATOR_CANNOT_SATISFY_BUILDER"
        )
        self.assertEqual(
            provenance.IMPLEMENTATION_ORIGIN_UNSUPPORTED, "IMPLEMENTATION_ORIGIN_UNSUPPORTED"
        )
        self.assertEqual(provenance.BUILDER_PROVENANCE_REQUIRED, "BUILDER_PROVENANCE_REQUIRED")


class StrictModernDefaultsTests(unittest.TestCase):
    def test_matrix_matches_section_9_exactly(self) -> None:
        defaults = provenance.STRICT_MODERN_EXECUTION_STATE_DEFAULTS
        self.assertEqual(tuple(defaults), CANONICAL_ORDER)
        accepted = ("EXECUTION_STATE_ENFORCED", "EXECUTION_STATE_ATTESTED")
        for role, entry in defaults.items():
            self.assertEqual(tuple(entry["accepted_evidence_classes"]), accepted)
            self.assertIs(entry["attested_requires_policy_acceptance"], True)
        self.assertEqual(
            defaults["implementation_builder"]["required_when"],
            "implementation_origin=sera_builder",
        )
        self.assertEqual(
            defaults["implementation_builder"]["evidence_scope"], "governed_input"
        )
        for role in ("implementation_validator", "independent_reviewer", "release_gate"):
            self.assertEqual(defaults[role]["required_when"], "always")

    def test_defaults_are_immutable_and_create_no_evidence(self) -> None:
        defaults = provenance.STRICT_MODERN_EXECUTION_STATE_DEFAULTS
        with self.assertRaises((TypeError, AttributeError)):
            defaults["implementation_builder"] = {}  # type: ignore[index]
        with self.assertRaises((TypeError, AttributeError)):
            defaults["implementation_builder"]["required_when"] = "always"  # type: ignore[index]
        # No entry asserts an observed/effective evidence class.
        for entry in defaults.values():
            self.assertNotIn("EXECUTION_STATE_ENFORCED", {entry["required_when"], entry["evidence_scope"]})


if __name__ == "__main__":
    unittest.main()
