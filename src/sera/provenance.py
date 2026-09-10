"""Provider-neutral repository and execution provenance primitives."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .core import (
    RISK_LEVELS,
    UNBORN_HEAD,
    VALID_MODES,
    SeraError,
    git_head_identity,
    load_config,
    load_task,
    task_contract_fingerprint,
    task_fingerprint,
    task_review_coverage,
    utc_now,
    validate_config,
)
from .schemas import (
    LedgerReader,
    SchemaError,
    TaskLockGuard,
    append_ledger_record,
    canonical_json,
    read_strict_json,
    record_hash,
    require_bounded_str,
    require_hash,
    require_timestamp,
    sha256_domain,
    task_lock,
)


_MAX_REMOTE_CHARS = 4096
_MAX_REMOTE_IDENTITIES = 32
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_CONFIGURED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SCP_REMOTE_RE = re.compile(r"^(?:[^@/:\\\s]+@)?([^/:\\\s]+):(.+)$")
# --- Canonical roles and implementation origin (spec Section 9) ----------------
# One authoritative public vocabulary. T06/T08 and every downstream consumer
# resolve roles and origins through these names; the private spellings below only
# delegate to this authority and never form a second normative list.
CANONICAL_ROLES: tuple[str, ...] = (
    "implementation_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
)
# Section 9 domain input aliases. `builder`, `independent`, and `gate` are the
# only permitted aliases; every canonical role resolves to itself. `validator`
# is deliberately NOT a domain alias (see `_CONFIG_ROLE_ALIASES`).
ROLE_ALIASES: Mapping[str, str] = MappingProxyType(
    {
        "builder": "implementation_builder",
        "independent": "independent_reviewer",
        "gate": "release_gate",
        **{role: role for role in CANONICAL_ROLES},
    }
)
# The Config v2 compatibility translator (T06) accepts one extra historical,
# config-oriented alias, `validator`, inside its own normalization boundary. That
# is not a Section 9 domain alias; it is derived from `ROLE_ALIASES` so the two
# vocabularies cannot drift.
_CONFIG_ROLE_ALIASES: Mapping[str, str] = MappingProxyType(
    {**ROLE_ALIASES, "validator": "implementation_validator"}
)
# Backwards-compatible private spelling used by this module's config/policy code;
# the same object as the public authority, not a copy.
_CANONICAL_ROLES = CANONICAL_ROLES
# Implementation origins (spec Sections 7.1 and 9). Exactly three; an unknown
# origin fails closed with `IMPLEMENTATION_ORIGIN_UNSUPPORTED`.
IMPLEMENTATION_ORIGINS: tuple[str, ...] = ("sera_builder", "external", "pre_existing")
# T10 reason codes, following the reason-code string-constant pattern from T02.
VALIDATOR_CANNOT_SATISFY_BUILDER = "VALIDATOR_CANNOT_SATISFY_BUILDER"
IMPLEMENTATION_ORIGIN_UNSUPPORTED = "IMPLEMENTATION_ORIGIN_UNSUPPORTED"
BUILDER_PROVENANCE_REQUIRED = "BUILDER_PROVENANCE_REQUIRED"
_EVIDENCE_CLASSES = {
    "LEGACY_PROVENANCE",
    "CHECKPOINT_OBSERVED",
    "EXECUTION_STATE_ENFORCED",
    "EXECUTION_STATE_ATTESTED",
}
# Identity-evidence mechanisms, NOT a total ordering. T06-R only validates and
# normalizes the candidate policy; class comparison/satisfaction belongs to T20.
_IDENTITY_EVIDENCE_CLASSES = {
    "unknown",
    "manual_assertion",
    "controller_observed",
    "adapter_observed",
    "provider_attested",
}
_V1_IDENTITY_EVIDENCE_CLASS = "manual_assertion"
_REPOSITORY_STRENGTHS = {"local_only", "derived", "configured"}

# The modern Config v2 route-relevant stage lanes. `planner` and `optional_fable`
# remain accepted for existing 0.4.2 purposes but are never canonical-stage
# routes and never carry approved-fallback authority.
_MODERN_ROUTE_LANES = (
    "fast_builder",
    "deep_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
)
# The physically present route lanes in a Config v1 file. A v1 file has no
# validator lane; its absence is compatibility, not an error.
_V1_ROUTE_LANES = ("fast_builder", "deep_builder", "independent_reviewer", "release_gate")
_MAX_ROUTE_STR = 128
_MAX_APPROVED_FALLBACKS = 8
# A captured configuration is the repository's own reviewed policy merged over
# ``DEFAULT_CONFIG`` (already parsed and validated by ``core.load_config``), not
# an untrusted external payload. Its object-key count comfortably exceeds the
# external-evidence default, so the strict descriptor pass over a config uses a
# wider bound; ``read_strict_json`` defaults for evidence paths are untouched.
_CONFIG_STRICT_MAX_KEYS = 1024

_STAGE_POLICY_SPEC = {
    "enabled": None,
    "required_modes": [None],
    "required_risks": [None],
}
_EXECUTION_STATE_POLICY_SPEC = {
    "minimum_evidence_class": None,
    "accepted_source_registration_hashes": [None],
    "accepted_source_types": [None],
    "required_capabilities": [None],
    "accepted_verification_methods": [None],
}
_ROLE_SPEC_KEYS = {key: _STAGE_POLICY_SPEC for key in _CONFIG_ROLE_ALIASES}
_EXECUTION_ROLE_SPEC_KEYS = {key: _EXECUTION_STATE_POLICY_SPEC for key in _CONFIG_ROLE_ALIASES}
_LANE_SPEC = {
    "provider": None,
    "model": None,
    "enabled": None,
    "allowed_uses": [None],
    "may_be_sole_release_gate": None,
}
# Exact modern route lane shape. Unknown lane-entry or fallback-entry fields are
# rejected; order of `approved_fallbacks` is normative.
_ROUTE_LANE_SPEC = {
    "provider": None,
    "model": None,
    "enabled": None,
    "approved_fallbacks": [{"provider": None, "model": None}],
}
_IDENTITY_STAGE_SPEC = {"required_identity_evidence_class": None}
_CONFIG_V2_SPEC = {
    "schema_version": None,
    "repository_id": None,
    "stage_policies": _ROLE_SPEC_KEYS,
    "provenance_requirements": {
        "minimum_repository_identity_strength": None,
        "require_execution_receipts": None,
        "allow_legacy_provenance": None,
    },
    "execution_state_policy": _EXECUTION_ROLE_SPEC_KEYS,
    "identity_evidence_policy": {role: _IDENTITY_STAGE_SPEC for role in _CANONICAL_ROLES},
    "substitution_rules": {
        "allow_approved_fallbacks": None,
        "allow_manual_substitution": None,
    },
    "knowledge_policy": {
        "source_paths": [None],
        "max_source_bytes": None,
        "assessment_required": None,
    },
    "repository_identity_requirement": {"minimum_strength": None},
    "context_budgets": {
        "token_budgets": {mode: None for mode in VALID_MODES},
        "max_files": None,
        "max_packet_chars": None,
    },
    # Config v2 adds the modern blocks without breaking the v1 loader. These
    # legacy routing/settings keys remain valid input, but none enter the
    # provider-neutral normalized policy view below.
    "default_mode": None,
    "max_builder_attempts": None,
    "max_file_bytes": None,
    "max_packet_chars": None,
    "exclude_dirs": [None],
    "token_budgets": {mode: None for mode in VALID_MODES},
    "lanes": {
        "planner": _LANE_SPEC,
        "optional_fable": _LANE_SPEC,
        **{name: _ROUTE_LANE_SPEC for name in _MODERN_ROUTE_LANES},
    },
    "verification": [None],
    "controller": {
        "context_max_files": None,
        "context_min_score": None,
        "enforce_context_budget": None,
        "auto_risk": None,
    },
    "risk_policy": {"high_risk_terms": [None], "high_risk_paths": [None]},
    "rules": {
        "builders_may_commit": None,
        "review_after_every_post_review_change": None,
        "cross_provider_review_preferred": None,
        "draft_pull_requests": None,
    },
}


# --- T10 role/origin domain semantics (spec Section 9) -------------------------
#
# T10 fixes the internal vocabulary only. It does not route executions, select
# or validate receipts, interpret identity evidence, or create any persisted
# artifact. `provenance.py` owns these semantics; `core`/`controller`/`cli`
# remain facades and never decide role/origin questions.

_STRICT_EVIDENCE_CLASSES: tuple[str, ...] = (
    "EXECUTION_STATE_ENFORCED",
    "EXECUTION_STATE_ATTESTED",
)

# Strict modern execution-state defaults (spec Section 9). This is a default
# *requirement* table, never evidence: nothing here marks any execution as
# ENFORCED or ATTESTED, chooses an evidence source, or derives assurance.
# Actual policy acceptance of ATTESTED still comes from a bound PolicySnapshotV1.
STRICT_MODERN_EXECUTION_STATE_DEFAULTS: Mapping[str, Mapping[str, object]] = MappingProxyType(
    {
        "implementation_builder": MappingProxyType(
            {
                "required_when": "implementation_origin=sera_builder",
                "evidence_scope": "governed_input",
                "accepted_evidence_classes": _STRICT_EVIDENCE_CLASSES,
                "attested_requires_policy_acceptance": True,
            }
        ),
        "implementation_validator": MappingProxyType(
            {
                "required_when": "always",
                "evidence_scope": "stage_execution",
                "accepted_evidence_classes": _STRICT_EVIDENCE_CLASSES,
                "attested_requires_policy_acceptance": True,
            }
        ),
        "independent_reviewer": MappingProxyType(
            {
                "required_when": "always",
                "evidence_scope": "stage_execution",
                "accepted_evidence_classes": _STRICT_EVIDENCE_CLASSES,
                "attested_requires_policy_acceptance": True,
            }
        ),
        "release_gate": MappingProxyType(
            {
                "required_when": "always",
                "evidence_scope": "stage_execution",
                "accepted_evidence_classes": _STRICT_EVIDENCE_CLASSES,
                "attested_requires_policy_acceptance": True,
            }
        ),
    }
)


class ProvenanceRoleError(SeraError):
    """Fail-closed role/origin domain error carrying a stable reason-code leaf."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def canonical_role(value: object) -> str:
    """Resolve a Section 9 role or domain alias to its canonical role name.

    Canonical roles resolve to themselves. ``builder``, ``independent``, and
    ``gate`` are the only accepted input aliases. Every other value fails closed:
    blank, ``None``, a non-string, an unknown string, a routing lane name, a
    legacy presentation label such as ``supplementary``, or a config-only alias
    such as ``validator``. No case-folding or whitespace trimming is performed
    and malformed persisted values are never coerced into validity.
    """
    if not isinstance(value, str):
        raise SeraError(f"canonical_role requires a string role, got {type(value).__name__}")
    resolved = ROLE_ALIASES.get(value)
    if resolved is None:
        raise SeraError(f"unsupported provenance role {value!r}")
    return resolved


def validator_satisfies_builder() -> bool:
    """Return ``False`` unconditionally.

    An ``implementation_validator`` receipt never satisfies an
    ``implementation_builder`` requirement (spec Section 9). No same provider,
    same model, same execution, same content, same output hash, manual override,
    or legacy-compatibility path changes this; the stable reason is
    ``VALIDATOR_CANNOT_SATISFY_BUILDER``. T10 does not implement receipt
    selection.
    """
    return False


def _origin_rules(policy: Mapping[str, object]) -> dict[str, frozenset[str]]:
    if not isinstance(policy, Mapping):
        raise SchemaError("origin_requirements requires a policy mapping")
    raw = policy.get("implementation_origin_rules")
    if not isinstance(raw, Mapping) or set(raw) != set(IMPLEMENTATION_ORIGINS):
        raise SchemaError("policy implementation_origin_rules must key exactly the canonical origins")
    rules: dict[str, frozenset[str]] = {}
    for name in IMPLEMENTATION_ORIGINS:
        entry = raw[name]
        if not isinstance(entry, (list, tuple)):
            raise SchemaError(f"implementation_origin_rules.{name} must be a list of role strings")
        entry_list = list(entry)
        if any(role not in CANONICAL_ROLES for role in entry_list):
            raise SchemaError(f"implementation_origin_rules.{name} contains a non-canonical role")
        if len(set(entry_list)) != len(entry_list):
            raise SchemaError(f"implementation_origin_rules.{name} repeats a role")
        rules[name] = frozenset(entry_list)
    # Section 9 invariants, checked for every query so a malformed block fails
    # closed regardless of which origin is asked about. These mirror the T08
    # persisted-policy reader and are never broadened here.
    if rules["sera_builder"] != frozenset({"implementation_builder"}):
        raise SchemaError("implementation_origin_rules.sera_builder must require exactly implementation_builder")
    for name in ("external", "pre_existing"):
        if not rules[name] <= frozenset({"implementation_validator"}):
            raise SchemaError(f"implementation_origin_rules.{name} may only require implementation_validator")
    return rules


def _required_stage_set(policy: Mapping[str, object]) -> frozenset[str]:
    raw = policy.get("required_stages")
    if not isinstance(raw, (list, tuple)) or any(role not in CANONICAL_ROLES for role in raw):
        raise SchemaError("policy required_stages must be a list of canonical roles")
    return frozenset(raw)


def _assert_validator_policy_consistency(
    required_stages: Collection[str],
    origin_rules: Mapping[str, Collection[str]],
) -> None:
    """Fail closed unless both ``external`` and ``pre_existing`` require the
    validator stage exactly when ``required_stages`` does.

    This is the single authority for the captured-fact consistency invariant
    (spec Section 9): for each of those two origins,

        "implementation_validator" in implementation_origin_rules[origin]
        IFF
        "implementation_validator" in required_stages

    Every origin is checked regardless of which one a caller asked about, so one
    contradictory bound policy never yields a partial trusted answer. It reads
    only these two captured policy facts and never consults configuration.
    ``sera_builder`` is intentionally excluded: its rule is always
    ``["implementation_builder"]`` and is not derived from ``required_stages``.
    """
    stage_has_validator = "implementation_validator" in set(required_stages)
    for origin in ("external", "pre_existing"):
        rule_has_validator = "implementation_validator" in set(origin_rules[origin])
        if rule_has_validator != stage_has_validator:
            raise SchemaError(
                f"implementation_origin_rules.{origin} validator requirement disagrees "
                "with required_stages"
            )


def origin_requirements(origin: object, policy: Mapping[str, object]) -> dict[str, object]:
    """Return the implementation-provenance role requirements for an origin.

    Pure in-memory semantic mapping over one already-bound policy. It never
    reads or translates current configuration, never inspects provider/model,
    receipts, or route snapshots, and never mutates ``policy``. The bound policy
    is authoritative for whether the validator stage is required; this function
    consumes the captured PolicySnapshotV1 facts ``implementation_origin_rules``
    and ``required_stages`` (spec Section 8.2) rather than recomputing a second,
    possibly disagreeing answer.

    Returned keys (minimal and deterministic):

    - ``origin``: the canonical origin (one of ``IMPLEMENTATION_ORIGINS``)
    - ``required_roles``: canonical roles required for this origin, ordered by
      ``CANONICAL_ROLES``
    - ``builder_authorship_claim``: ``True`` only for ``sera_builder``
    - ``reason``: ``BUILDER_PROVENANCE_REQUIRED`` for ``sera_builder`` else
      ``None``

    Fails closed with ``ProvenanceRoleError`` (code
    ``IMPLEMENTATION_ORIGIN_UNSUPPORTED``) on an unknown origin and with
    ``SchemaError`` on malformed or internally contradictory policy input.
    """
    if not isinstance(origin, str) or origin not in IMPLEMENTATION_ORIGINS:
        raise ProvenanceRoleError(
            IMPLEMENTATION_ORIGIN_UNSUPPORTED, f"unknown implementation origin {origin!r}"
        )
    rules = _origin_rules(policy)
    required_stages = _required_stage_set(policy)
    # Validate the complete captured relationship before returning anything: a
    # policy whose external or pre_existing rule disagrees with required_stages
    # is rejected even when the caller asked about a different, consistent origin.
    _assert_validator_policy_consistency(required_stages, rules)
    origin_rule = rules[origin]
    required_roles = tuple(role for role in CANONICAL_ROLES if role in origin_rule)
    return {
        "origin": origin,
        "required_roles": required_roles,
        "builder_authorship_claim": origin == "sera_builder",
        "reason": BUILDER_PROVENANCE_REQUIRED if origin == "sera_builder" else None,
    }


@dataclass(frozen=True)
class ConfigV2View:
    """Immutable, provider-neutral candidate policy normalized from config."""

    source_schema: int
    stage_policies: Mapping[str, object]
    provenance_requirements: Mapping[str, object]
    execution_state_policy: Mapping[str, object]
    identity_evidence_policy: Mapping[str, object]
    substitution_rules: Mapping[str, object]
    knowledge_policy: Mapping[str, object]
    repository_identity_requirement: Mapping[str, object]
    context_budgets: Mapping[str, object]


@dataclass(frozen=True)
class RouteConfigView:
    """Immutable in-memory normalization of mutable provider/model route mechanics.

    Never persisted, never policy authority, never assurance evidence, and never
    nested in ``ConfigV2View``. It carries only normalized lane identity,
    provider, model, enabled state, and each lane's own ordered explicit
    ``approved_fallbacks``. Whether a fallback is policy-permitted is decided
    later (T09) against the active ``PolicySnapshotV1``; T06-R never gates it.
    """

    source_schema: int
    lanes: Mapping[str, Mapping[str, object]]


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({key: _freeze(item) for key, item in value.items()})


def _required_object(container: dict[str, object], key: str, path: str = "") -> dict[str, object]:
    value = container.get(key)
    label = f"{path}.{key}" if path else key
    if not isinstance(value, dict):
        raise SeraError(f"{label} must be an object.")
    return value


def _require_exact_fields(value: dict[str, object], fields: set[str], path: str) -> None:
    missing = sorted(fields - set(value))
    if missing:
        raise SeraError(f"{path} is missing required field: {missing[0]}")


def _reject_nulls(value: object, path: str = "config") -> None:
    if value is None:
        raise SeraError(f"{path} must not be null.")
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_nulls(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_nulls(item, f"{path}[{index}]")


def _require_bool_value(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise SeraError(f"{path} must be true or false.")
    return value


def _require_bounded_int(value: object, path: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (1 <= value <= maximum):
        raise SeraError(f"{path} must be an integer from 1 to {maximum}.")
    return value


def _require_enum_list(value: object, path: str, allowed: set[str]) -> list[str]:
    if not isinstance(value, list) or len(value) > len(allowed):
        raise SeraError(f"{path} must be a bounded list.")
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise SeraError(f"{path} contains an unsupported value.")
    if len(value) != len(set(value)):
        raise SeraError(f"{path} must not contain duplicates.")
    return list(value)


def _require_string_list(
    value: object,
    path: str,
    *,
    maximum_items: int = 32,
    maximum_chars: int = 256,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise SeraError(f"{path} must be a bounded list of strings.")
    result: list[str] = []
    for item in value:
        try:
            result.append(require_bounded_str(item, path, max_length=maximum_chars))
        except SchemaError as exc:
            raise SeraError(str(exc)) from None
    if len(result) != len(set(result)):
        raise SeraError(f"{path} must not contain duplicates.")
    return result


def _normalize_role_map(
    value: dict[str, object],
    path: str,
    validator: Callable[[object, str], dict[str, object]],
) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for role, policy in value.items():
        canonical = _CONFIG_ROLE_ALIASES.get(role)
        if canonical is None:
            raise SeraError(f"{path} contains unsupported role {role!r}.")
        if canonical in normalized:
            raise SeraError(f"{path} defines {canonical!r} more than once.")
        normalized[canonical] = validator(policy, f"{path}.{role}")
    missing = [role for role in _CANONICAL_ROLES if role not in normalized]
    if missing:
        raise SeraError(f"{path} is missing canonical role {missing[0]!r}.")
    return {role: normalized[role] for role in _CANONICAL_ROLES}


def _validate_stage_policy(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SeraError(f"{path} must be an object.")
    _require_exact_fields(value, set(_STAGE_POLICY_SPEC), path)
    return {
        "enabled": _require_bool_value(value["enabled"], f"{path}.enabled"),
        "required_modes": _require_enum_list(
            value["required_modes"], f"{path}.required_modes", set(VALID_MODES)
        ),
        "required_risks": _require_enum_list(
            value["required_risks"], f"{path}.required_risks", set(RISK_LEVELS)
        ),
    }


def _normalize_identity_evidence_policy(value: object, path: str = "identity_evidence_policy") -> dict[str, object]:
    """Normalize the explicit four-stage identity-evidence candidate policy.

    This is the only configuration candidate for the required provider/model
    identity class. It is never inferred from execution-state policy, a
    provider/model string, a source display name, or a receipt claim, and T06-R
    performs no class comparison or ranking.
    """
    if not isinstance(value, dict):
        raise SeraError(f"{path} must be an object.")
    _require_exact_fields(value, set(_CANONICAL_ROLES), path)
    unsupported = sorted(set(value) - set(_CANONICAL_ROLES))
    if unsupported:
        raise SeraError(f"{path} contains an unsupported stage: {unsupported[0]}")
    normalized: dict[str, object] = {}
    for role in _CANONICAL_ROLES:
        entry = value[role]
        entry_path = f"{path}.{role}"
        if not isinstance(entry, dict):
            raise SeraError(f"{entry_path} must be an object.")
        _require_exact_fields(entry, {"required_identity_evidence_class"}, entry_path)
        extra = sorted(set(entry) - {"required_identity_evidence_class"})
        if extra:
            raise SeraError(f"{entry_path} has an unknown field: {extra[0]}")
        identity_class = entry["required_identity_evidence_class"]
        if not isinstance(identity_class, str) or identity_class not in _IDENTITY_EVIDENCE_CLASSES:
            raise SeraError(f"{entry_path}.required_identity_evidence_class is unsupported.")
        normalized[role] = {"required_identity_evidence_class": identity_class}
    return {role: normalized[role] for role in _CANONICAL_ROLES}


def _require_route_str(value: object, path: str) -> str:
    try:
        return require_bounded_str(value, path, max_length=_MAX_ROUTE_STR)
    except SchemaError as exc:
        raise SeraError(str(exc)) from None


def _normalize_approved_fallbacks(
    value: object, path: str, primary: tuple[str, str]
) -> tuple[dict[str, str], ...]:
    """Validate and normalize one lane's explicit ordered fallback candidates.

    Order is normative: entries are never sorted, never deduplicated, and never
    silently discarded. Whether these candidates are policy-permitted is a
    later (T09) decision against the active ``PolicySnapshotV1``.
    """
    if not isinstance(value, list):
        raise SeraError(f"{path} must be a list.")
    if len(value) > _MAX_APPROVED_FALLBACKS:
        raise SeraError(f"{path} accepts at most {_MAX_APPROVED_FALLBACKS} entries.")
    seen: list[tuple[str, str]] = []
    normalized: list[dict[str, str]] = []
    for index, entry in enumerate(value):
        entry_path = f"{path}[{index}]"
        if not isinstance(entry, dict):
            raise SeraError(f"{entry_path} must be an object.")
        _require_exact_fields(entry, {"provider", "model"}, entry_path)
        extra = sorted(set(entry) - {"provider", "model"})
        if extra:
            raise SeraError(f"{entry_path} has an unknown field: {extra[0]}")
        provider = _require_route_str(entry.get("provider"), f"{entry_path}.provider")
        model = _require_route_str(entry.get("model"), f"{entry_path}.model")
        target = (provider, model)
        if target == primary:
            raise SeraError(f"{entry_path} must not repeat the lane primary provider/model.")
        if target in seen:
            raise SeraError(f"{entry_path} is a duplicate approved fallback target.")
        seen.append(target)
        normalized.append({"provider": provider, "model": model})
    return tuple(normalized)


def _normalize_route_lane(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SeraError(f"{path} must be an object.")
    _require_exact_fields(value, set(_ROUTE_LANE_SPEC), path)
    extra = sorted(set(value) - set(_ROUTE_LANE_SPEC))
    if extra:
        raise SeraError(f"{path} has an unknown field: {extra[0]}")
    provider = _require_route_str(value.get("provider"), f"{path}.provider")
    model = _require_route_str(value.get("model"), f"{path}.model")
    enabled = _require_bool_value(value.get("enabled"), f"{path}.enabled")
    fallbacks = _normalize_approved_fallbacks(
        value.get("approved_fallbacks"), f"{path}.approved_fallbacks", (provider, model)
    )
    return {"provider": provider, "model": model, "enabled": enabled, "approved_fallbacks": fallbacks}


def _validate_execution_state_policy(value: object, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SeraError(f"{path} must be an object.")
    _require_exact_fields(value, set(_EXECUTION_STATE_POLICY_SPEC), path)
    evidence_class = value["minimum_evidence_class"]
    if not isinstance(evidence_class, str) or evidence_class not in _EVIDENCE_CLASSES:
        raise SeraError(f"{path}.minimum_evidence_class is unsupported.")
    hashes = _require_string_list(
        value["accepted_source_registration_hashes"],
        f"{path}.accepted_source_registration_hashes",
    )
    for item in hashes:
        try:
            require_hash(item, f"{path}.accepted_source_registration_hashes")
        except SchemaError as exc:
            raise SeraError(str(exc)) from None
    return {
        "minimum_evidence_class": evidence_class,
        "accepted_source_registration_hashes": hashes,
        "accepted_source_types": _require_string_list(
            value["accepted_source_types"], f"{path}.accepted_source_types"
        ),
        "required_capabilities": _require_string_list(
            value["required_capabilities"], f"{path}.required_capabilities"
        ),
        "accepted_verification_methods": _require_string_list(
            value["accepted_verification_methods"], f"{path}.accepted_verification_methods"
        ),
    }


def _translate_v1(loaded: dict[str, object]) -> ConfigV2View:
    validate_config(loaded)
    lanes = loaded["lanes"]
    assert isinstance(lanes, dict)
    controller = loaded["controller"]
    assert isinstance(controller, dict)
    token_budgets = loaded["token_budgets"]
    assert isinstance(token_budgets, dict)

    def enabled(name: str) -> bool:
        lane = lanes.get(name, {})
        return isinstance(lane, dict) and lane.get("enabled") is True

    stage_policies = {
        "implementation_builder": {
            "enabled": enabled("fast_builder") or enabled("deep_builder"),
            "required_modes": list(VALID_MODES),
            "required_risks": list(RISK_LEVELS),
        },
        "implementation_validator": {"enabled": False, "required_modes": [], "required_risks": []},
        "independent_reviewer": {
            "enabled": enabled("independent_reviewer"),
            "required_modes": ["standard", "assured"],
            "required_risks": ["medium", "high"],
        },
        "release_gate": {
            "enabled": enabled("release_gate"),
            "required_modes": ["assured"],
            "required_risks": ["high"],
        },
    }
    legacy_state = {
        "minimum_evidence_class": "LEGACY_PROVENANCE",
        "accepted_source_registration_hashes": [],
        "accepted_source_types": [],
        "required_capabilities": [],
        "accepted_verification_methods": [],
    }
    values = {
        "stage_policies": stage_policies,
        "provenance_requirements": {
            "minimum_repository_identity_strength": "local_only",
            "require_execution_receipts": False,
            "allow_legacy_provenance": True,
        },
        "execution_state_policy": {role: dict(legacy_state) for role in _CANONICAL_ROLES},
        # Compatibility only: an explicitly limited legacy default. It does not
        # make a v1 task modern, strong, strict-assured, or release-grade, and it
        # is never persisted into the v1 config file.
        "identity_evidence_policy": {
            role: {"required_identity_evidence_class": _V1_IDENTITY_EVIDENCE_CLASS}
            for role in _CANONICAL_ROLES
        },
        "substitution_rules": {
            "allow_approved_fallbacks": False,
            "allow_manual_substitution": False,
        },
        "knowledge_policy": {
            "source_paths": [],
            "max_source_bytes": loaded["max_file_bytes"],
            "assessment_required": False,
        },
        "repository_identity_requirement": {"minimum_strength": "local_only"},
        "context_budgets": {
            "token_budgets": dict(token_budgets),
            "max_files": controller["context_max_files"],
            "max_packet_chars": loaded["max_packet_chars"],
        },
    }
    return ConfigV2View(
        source_schema=1,
        stage_policies=_freeze_mapping(values["stage_policies"]),
        provenance_requirements=_freeze_mapping(values["provenance_requirements"]),
        execution_state_policy=_freeze_mapping(values["execution_state_policy"]),
        identity_evidence_policy=_freeze_mapping(values["identity_evidence_policy"]),
        substitution_rules=_freeze_mapping(values["substitution_rules"]),
        knowledge_policy=_freeze_mapping(values["knowledge_policy"]),
        repository_identity_requirement=_freeze_mapping(values["repository_identity_requirement"]),
        context_budgets=_freeze_mapping(values["context_budgets"]),
    )


def _translate_v2(loaded: dict[str, object]) -> ConfigV2View:
    try:
        strict = read_strict_json(
            canonical_json(loaded).encode("utf-8"),
            spec=_CONFIG_V2_SPEC,
            max_keys=_CONFIG_STRICT_MAX_KEYS,
        )
    except SchemaError as exc:
        raise SeraError(str(exc)) from None
    _reject_nulls(strict)
    required = {
        "schema_version",
        "stage_policies",
        "provenance_requirements",
        "execution_state_policy",
        "identity_evidence_policy",
        "substitution_rules",
        "knowledge_policy",
        "repository_identity_requirement",
        "context_budgets",
    }
    _require_exact_fields(strict, required, "config v2")
    if "repository_id" in strict:
        repository_id = strict["repository_id"]
        if not isinstance(repository_id, str) or not _CONFIGURED_ID_RE.fullmatch(repository_id):
            raise SeraError("repository_id must be a bounded non-secret identifier.")

    stage_policies = _normalize_role_map(
        _required_object(strict, "stage_policies"), "stage_policies", _validate_stage_policy
    )
    provenance = _required_object(strict, "provenance_requirements")
    _require_exact_fields(provenance, set(_CONFIG_V2_SPEC["provenance_requirements"]), "provenance_requirements")  # type: ignore[arg-type]
    minimum_strength = provenance["minimum_repository_identity_strength"]
    if minimum_strength not in _REPOSITORY_STRENGTHS:
        raise SeraError("provenance_requirements.minimum_repository_identity_strength is unsupported.")
    provenance_requirements = {
        "minimum_repository_identity_strength": minimum_strength,
        "require_execution_receipts": _require_bool_value(
            provenance["require_execution_receipts"],
            "provenance_requirements.require_execution_receipts",
        ),
        "allow_legacy_provenance": _require_bool_value(
            provenance["allow_legacy_provenance"],
            "provenance_requirements.allow_legacy_provenance",
        ),
    }
    execution_state_policy = _normalize_role_map(
        _required_object(strict, "execution_state_policy"),
        "execution_state_policy",
        _validate_execution_state_policy,
    )
    identity_evidence_policy = _normalize_identity_evidence_policy(
        _required_object(strict, "identity_evidence_policy")
    )

    substitution = _required_object(strict, "substitution_rules")
    _require_exact_fields(substitution, set(_CONFIG_V2_SPEC["substitution_rules"]), "substitution_rules")  # type: ignore[arg-type]
    substitution_rules = {
        key: _require_bool_value(value, f"substitution_rules.{key}")
        for key, value in substitution.items()
    }

    knowledge = _required_object(strict, "knowledge_policy")
    _require_exact_fields(knowledge, set(_CONFIG_V2_SPEC["knowledge_policy"]), "knowledge_policy")  # type: ignore[arg-type]
    source_paths = _require_string_list(
        knowledge["source_paths"], "knowledge_policy.source_paths", maximum_items=64, maximum_chars=512
    )
    for source_path in source_paths:
        normalized = source_path.replace("\\", "/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in normalized.split("/"):
            raise SeraError("knowledge_policy.source_paths must be contained repository-relative paths.")
    knowledge_policy = {
        "source_paths": source_paths,
        "max_source_bytes": _require_bounded_int(
            knowledge["max_source_bytes"], "knowledge_policy.max_source_bytes", maximum=10_000_000
        ),
        "assessment_required": _require_bool_value(
            knowledge["assessment_required"], "knowledge_policy.assessment_required"
        ),
    }

    identity_requirement = _required_object(strict, "repository_identity_requirement")
    _require_exact_fields(identity_requirement, {"minimum_strength"}, "repository_identity_requirement")
    if identity_requirement["minimum_strength"] not in _REPOSITORY_STRENGTHS:
        raise SeraError("repository_identity_requirement.minimum_strength is unsupported.")
    repository_identity_requirement = {"minimum_strength": identity_requirement["minimum_strength"]}

    budgets = _required_object(strict, "context_budgets")
    _require_exact_fields(budgets, set(_CONFIG_V2_SPEC["context_budgets"]), "context_budgets")  # type: ignore[arg-type]
    token_budgets = _required_object(budgets, "token_budgets", "context_budgets")
    _require_exact_fields(token_budgets, set(VALID_MODES), "context_budgets.token_budgets")
    context_budgets = {
        "token_budgets": {
            mode: _require_bounded_int(
                token_budgets[mode], f"context_budgets.token_budgets.{mode}", maximum=10_000_000
            )
            for mode in VALID_MODES
        },
        "max_files": _require_bounded_int(
            budgets["max_files"], "context_budgets.max_files", maximum=10_000
        ),
        "max_packet_chars": _require_bounded_int(
            budgets["max_packet_chars"], "context_budgets.max_packet_chars", maximum=10_000_000
        ),
    }
    return ConfigV2View(
        source_schema=2,
        stage_policies=_freeze_mapping(stage_policies),
        provenance_requirements=_freeze_mapping(provenance_requirements),
        execution_state_policy=_freeze_mapping(execution_state_policy),
        identity_evidence_policy=_freeze_mapping(identity_evidence_policy),
        substitution_rules=_freeze_mapping(substitution_rules),
        knowledge_policy=_freeze_mapping(knowledge_policy),
        repository_identity_requirement=_freeze_mapping(repository_identity_requirement),
        context_budgets=_freeze_mapping(context_budgets),
    )


def translate_config(loaded: dict[str, object], root: Path) -> ConfigV2View:
    """Normalize config in memory without authorizing or rewriting history."""
    del root  # The candidate view is intentionally independent of repository state.
    if not isinstance(loaded, dict):
        raise SeraError("configuration must be an object.")
    schema_version = loaded.get("schema_version")
    if isinstance(schema_version, bool) or schema_version not in {1, 2}:
        raise SeraError(f"unsupported config schema: {schema_version!r}")
    if schema_version == 1:
        return _translate_v1(loaded)
    return _translate_v2(loaded)


def _route_v1(loaded: dict[str, object]) -> RouteConfigView:
    validate_config(loaded)
    lanes = loaded.get("lanes")
    if not isinstance(lanes, dict):
        raise SeraError("lanes must be an object.")
    normalized: dict[str, object] = {}
    for lane_name in _V1_ROUTE_LANES:
        lane = lanes.get(lane_name)
        if not isinstance(lane, dict):
            raise SeraError(f"lanes.{lane_name} must be an object for route normalization.")
        normalized[lane_name] = {
            "provider": _require_route_str(lane.get("provider"), f"lanes.{lane_name}.provider"),
            "model": _require_route_str(lane.get("model"), f"lanes.{lane_name}.model"),
            "enabled": _require_bool_value(lane.get("enabled"), f"lanes.{lane_name}.enabled"),
            # No v1 field grants fallback authority; `optional_fable` never does.
            "approved_fallbacks": (),
        }
    # A v1 file has no validator lane. Its absence is compatibility, not an error:
    # the translated validator route is disabled and carries no fallbacks.
    normalized["implementation_validator"] = {
        "provider": None,
        "model": None,
        "enabled": False,
        "approved_fallbacks": (),
    }
    return RouteConfigView(
        source_schema=1,
        lanes=_freeze_mapping({name: normalized[name] for name in _MODERN_ROUTE_LANES}),
    )


def _route_v2(loaded: dict[str, object]) -> RouteConfigView:
    try:
        strict = read_strict_json(
            canonical_json(loaded).encode("utf-8"),
            spec=_CONFIG_V2_SPEC,
            max_keys=_CONFIG_STRICT_MAX_KEYS,
        )
    except SchemaError as exc:
        raise SeraError(str(exc)) from None
    _reject_nulls(strict)
    lanes = strict.get("lanes")
    if not isinstance(lanes, dict):
        raise SeraError("lanes must be an object for a modern route configuration.")
    normalized: dict[str, object] = {}
    for lane_name in _MODERN_ROUTE_LANES:
        if lane_name not in lanes:
            raise SeraError(f"lanes is missing the modern route lane {lane_name!r}.")
        normalized[lane_name] = _normalize_route_lane(lanes[lane_name], f"lanes.{lane_name}")
    return RouteConfigView(
        source_schema=2,
        lanes=_freeze_mapping({name: normalized[name] for name in _MODERN_ROUTE_LANES}),
    )


def normalize_route_config(loaded: dict[str, object]) -> RouteConfigView:
    """Normalize mutable route mechanics from one captured config, in memory only.

    Operates on the supplied configuration object: it never rereads
    ``.sera/config.json``, never calls ``load_config``, and never writes. The
    result is not persisted, not policy authority, and not assurance evidence.
    """
    if not isinstance(loaded, dict):
        raise SeraError("configuration must be an object.")
    schema_version = loaded.get("schema_version")
    if isinstance(schema_version, bool) or schema_version not in {1, 2}:
        raise SeraError(f"unsupported config schema: {schema_version!r}")
    if schema_version == 1:
        return _route_v1(loaded)
    return _route_v2(loaded)


_POLICY_TRIGGERS = {
    "task_created", "ownership_confirmed", "explicit_policy_adoption", "task_contract_adoption",
}
_POLICY_SNAPSHOT_SPEC = {
    "schema_version": None,
    "source_schema": None,
    "task_id": None,
    "captured_at": None,
    "trigger": None,
    "mode": None,
    "risk": None,
    "required_stages": [None],
    "implementation_origin_rules": {origin: [None] for origin in IMPLEMENTATION_ORIGINS},
    "provenance_requirements": _CONFIG_V2_SPEC["provenance_requirements"],
    "identity_evidence_policy": _CONFIG_V2_SPEC["identity_evidence_policy"],
    "execution_state_policy": {role: _EXECUTION_STATE_POLICY_SPEC for role in _CANONICAL_ROLES},
    "legacy_override": {"allow_legacy_provenance": None},
    "substitution_rules": _CONFIG_V2_SPEC["substitution_rules"],
    "independence_requirements": {"distinct_execution_ids": None, "distinct_receipt_hashes": None},
    "verification_requirements": [None],
    "context_budgets": {"token_budget": None, "max_files": None, "max_packet_chars": None},
    "knowledge_policy": _CONFIG_V2_SPEC["knowledge_policy"],
    "repository_identity_requirement": _CONFIG_V2_SPEC["repository_identity_requirement"],
    "snapshot_hash": None,
}


def _policy_value(value: object) -> object:
    """Detach JSON values from one frozen view without consulting configuration."""
    if isinstance(value, Mapping):
        return {key: _policy_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_policy_value(item) for item in value]
    return value


def _validate_policy_snapshot(record: dict[str, object]) -> dict[str, object]:
    """Validate persisted meaning as written; never normalize aliases or reload config."""
    snapshot = read_strict_json(canonical_json(record).encode("utf-8"), spec=_POLICY_SNAPSHOT_SPEC)
    try:
        _require_exact_fields(snapshot, set(_POLICY_SNAPSHOT_SPEC) - {"legacy_override"}, "policy snapshot")
        _reject_nulls(snapshot, "policy snapshot")
        if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
            raise SchemaError("unsupported policy snapshot schema")
        if type(snapshot["source_schema"]) is not int or snapshot["source_schema"] not in (1, 2):
            raise SchemaError("unsupported policy source schema")
        require_bounded_str(snapshot["task_id"], "task_id", max_length=256)
        require_timestamp(snapshot["captured_at"], "captured_at")
        if snapshot["trigger"] not in _POLICY_TRIGGERS:
            raise SchemaError("unsupported policy snapshot trigger")
        if snapshot["mode"] not in VALID_MODES or snapshot["risk"] not in RISK_LEVELS:
            raise SchemaError("unsupported policy mode or risk")
        _require_enum_list(snapshot["required_stages"], "required_stages", set(_CANONICAL_ROLES))
        origins = snapshot["implementation_origin_rules"]
        _require_exact_fields(origins, set(IMPLEMENTATION_ORIGINS), "implementation_origin_rules")
        for origin, stages in origins.items():
            allowed = {"implementation_builder"} if origin == "sera_builder" else {"implementation_validator"}
            _require_enum_list(stages, f"implementation_origin_rules.{origin}", allowed)
        if origins["sera_builder"] != ["implementation_builder"]:
            raise SchemaError("sera_builder provenance requires an implementation_builder receipt")
        # Stricter semantic validation of the existing captured facts (no new
        # field, no schema change): external and pre_existing must require the
        # validator stage exactly when required_stages does. Same single
        # authority used by origin_requirements.
        _assert_validator_policy_consistency(snapshot["required_stages"], origins)
        provenance = snapshot["provenance_requirements"]
        _require_exact_fields(provenance, set(_CONFIG_V2_SPEC["provenance_requirements"]), "provenance_requirements")
        if provenance["minimum_repository_identity_strength"] not in _REPOSITORY_STRENGTHS:
            raise SchemaError("unsupported provenance repository identity strength")
        for key in ("require_execution_receipts", "allow_legacy_provenance"):
            _require_bool_value(provenance[key], f"provenance_requirements.{key}")
        execution = snapshot["execution_state_policy"]
        _require_exact_fields(execution, set(_CANONICAL_ROLES), "execution_state_policy")
        for role, policy in execution.items():
            _validate_execution_state_policy(policy, f"execution_state_policy.{role}")
        # Persisted identity-evidence policy is read exactly as written: the
        # complete four-stage block is required, aliases are rejected rather than
        # normalized, and the class must be one of the five accepted mechanisms.
        # This is an independent policy dimension; nothing here compares it to
        # execution-state evidence, provider/model, or receipt claims.
        _normalize_identity_evidence_policy(snapshot["identity_evidence_policy"])
        if "legacy_override" in snapshot:
            if snapshot["legacy_override"].get("allow_legacy_provenance") is not True:
                raise SchemaError("legacy_override must explicitly permit legacy provenance")
            if provenance["allow_legacy_provenance"] is not True:
                raise SchemaError("legacy_override disagrees with provenance requirements")
        elif provenance["allow_legacy_provenance"]:
            raise SchemaError("legacy provenance requires an explicit captured override")
        for key in ("substitution_rules", "independence_requirements"):
            block = snapshot[key]
            _require_exact_fields(block, set(_POLICY_SNAPSHOT_SPEC[key]), key)
            for field, value in block.items():
                _require_bool_value(value, f"{key}.{field}")
        if not all(snapshot["independence_requirements"].values()):
            raise SchemaError("independent stages require distinct execution IDs and receipt hashes")
        _require_string_list(snapshot["verification_requirements"], "verification_requirements", maximum_items=128, maximum_chars=4096)
        budgets = snapshot["context_budgets"]
        _require_exact_fields(budgets, set(_POLICY_SNAPSHOT_SPEC["context_budgets"]), "context_budgets")
        for key, value in budgets.items():
            _require_bounded_int(value, f"context_budgets.{key}", maximum=10_000 if key == "max_files" else 10_000_000)
        knowledge = snapshot["knowledge_policy"]
        _require_exact_fields(knowledge, set(_POLICY_SNAPSHOT_SPEC["knowledge_policy"]), "knowledge_policy")
        paths = _require_string_list(knowledge["source_paths"], "knowledge_policy.source_paths", maximum_items=64, maximum_chars=512)
        for path in paths:
            normalized = path.replace("\\", "/")
            if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in normalized.split("/"):
                raise SchemaError("knowledge source paths must be repository-relative")
        _require_bounded_int(knowledge["max_source_bytes"], "knowledge_policy.max_source_bytes", maximum=10_000_000)
        _require_bool_value(knowledge["assessment_required"], "knowledge_policy.assessment_required")
        identity = snapshot["repository_identity_requirement"]
        _require_exact_fields(identity, {"minimum_strength"}, "repository_identity_requirement")
        if identity["minimum_strength"] not in _REPOSITORY_STRENGTHS:
            raise SchemaError("unsupported repository identity requirement")
        require_hash(snapshot["snapshot_hash"], "snapshot_hash")
        if record_hash("policy_snapshot", snapshot, "snapshot_hash") != snapshot["snapshot_hash"]:
            raise SchemaError("policy snapshot hash mismatch")
    except (SeraError, TypeError, KeyError) as exc:
        raise SchemaError(f"invalid policy snapshot: {exc}") from exc
    return snapshot


def build_policy_snapshot(config_view: ConfigV2View, task: Mapping[str, object], trigger: str) -> dict:
    """Capture one normalized candidate; configured source hashes confer no registry trust.

    Origin receipt rules and structural independence capture Sections 9 and 13.
    Verification comes from the task contract, where task creation resolved it.
    Neither origin selection nor modern contract adoption is performed here.
    """
    if not isinstance(config_view, ConfigV2View):
        raise SchemaError("policy capture requires one normalized ConfigV2View")
    if set(config_view.stage_policies) != set(_CANONICAL_ROLES):
        raise SchemaError("policy capture requires canonical stage roles")
    mode, risk = task.get("mode"), task.get("risk")
    if mode not in VALID_MODES or risk not in RISK_LEVELS:
        raise SchemaError("policy capture requires resolved task mode and risk")
    required = []
    for role in _CANONICAL_ROLES:
        policy = config_view.stage_policies[role]
        if policy["enabled"] and (mode in policy["required_modes"] or risk in policy["required_risks"]):
            required.append(role)
    validator = ["implementation_validator"] if "implementation_validator" in required else []
    snapshot = {
        "schema_version": 1,
        "source_schema": config_view.source_schema,
        "task_id": task.get("task_id", task.get("id")),
        "captured_at": utc_now(),
        "trigger": trigger,
        "mode": mode,
        "risk": risk,
        "required_stages": required,
        "implementation_origin_rules": {
            "sera_builder": ["implementation_builder"], "external": list(validator), "pre_existing": list(validator),
        },
        "provenance_requirements": _policy_value(config_view.provenance_requirements),
        # Required policy copied from the already-captured frozen view (Section
        # 8.2): the four-stage identity requirement, hash-bound below. Not an
        # observed effective class; never inferred from provider/model or
        # execution-state policy, and never a config reread.
        "identity_evidence_policy": _policy_value(config_view.identity_evidence_policy),
        "execution_state_policy": _policy_value(config_view.execution_state_policy),
        "substitution_rules": _policy_value(config_view.substitution_rules),
        "independence_requirements": {"distinct_execution_ids": True, "distinct_receipt_hashes": True},
        "verification_requirements": _policy_value(task.get("verification_requirements", task.get("verification", []))),
        "context_budgets": {
            "token_budget": config_view.context_budgets["token_budgets"][mode],
            "max_files": config_view.context_budgets["max_files"],
            "max_packet_chars": config_view.context_budgets["max_packet_chars"],
        },
        "knowledge_policy": _policy_value(config_view.knowledge_policy),
        "repository_identity_requirement": _policy_value(config_view.repository_identity_requirement),
    }
    if config_view.provenance_requirements["allow_legacy_provenance"]:
        snapshot["legacy_override"] = {"allow_legacy_provenance": True}
    snapshot["snapshot_hash"] = record_hash("policy_snapshot", snapshot, "snapshot_hash")
    return _validate_policy_snapshot(snapshot)


def _validate_task_policy_snapshot(record: dict, task_id: str) -> dict:
    snapshot = _validate_policy_snapshot(record)
    if snapshot["task_id"] != task_id:
        raise SchemaError("policy snapshot belongs to another task")
    return snapshot


def read_policy_snapshots(task_dir: Path) -> LedgerReader:
    task_dir = Path(task_dir).resolve()

    def validate(record: dict) -> dict:
        return _validate_task_policy_snapshot(record, task_dir.name)

    return LedgerReader(task_dir / "policy-snapshots.jsonl", "policy_snapshot", validate)


def append_policy_snapshot(task_dir: Path, snapshot: dict, lock: TaskLockGuard) -> None:
    """Append under a genuine live task guard, refusing any invalid existing history."""
    task_dir = Path(task_dir).resolve()
    validated = _validate_task_policy_snapshot(snapshot, task_dir.name)
    read_policy_snapshots(task_dir).records()
    append_ledger_record(Path(task_dir) / "policy-snapshots.jsonl", validated, lock)


def active_policy_snapshot(task_dir: Path, contract: Mapping[str, object] | None) -> dict | None:
    """Resolve the exact hash from a caller-supplied active modern contract.

    T11/T12 own contract validation and active-contract authority. This function
    never derives either from task.json, candidates, or current configuration.
    """
    records = read_policy_snapshots(task_dir).records()
    if contract is None or contract.get("schema_version") == 1:
        return None
    if contract.get("schema_version") != 2 or contract.get("record_type") != "task_contract":
        raise SchemaError("active policy selection requires a modern task contract")
    bound_hash = require_hash(contract.get("active_policy_hash"), "active_policy_hash")
    task_id = require_bounded_str(contract.get("task_id"), "task_id", max_length=256)
    for snapshot in records:
        if snapshot["snapshot_hash"] == bound_hash:
            if snapshot["task_id"] != task_id:
                raise SchemaError("bound policy snapshot belongs to another task")
            return snapshot
    return None


def adopt_policy_snapshot(root: Path, task_dir: Path, *, actor: str, reason: str) -> dict:
    """Capture a candidate only; actor/reason are explicit command preconditions.

    Section 8.2 defines no normative actor/reason fields or adoption audit record.
    They are required here but are deliberately not added to PolicySnapshotV1.
    """
    require_bounded_str(actor, "actor", max_length=256)
    require_bounded_str(reason, "reason", max_length=4096)
    if not actor.strip() or not reason.strip():
        raise SchemaError("policy adoption requires nonblank actor and reason")
    with task_lock(task_dir) as lock:
        task = load_task(task_dir)
        view = translate_config(load_config(root), root)
        snapshot = build_policy_snapshot(view, task, "explicit_policy_adoption")
        append_policy_snapshot(task_dir, snapshot, lock)
    return snapshot


# --- RouteSnapshotV1 + route-snapshots.jsonl (spec Section 8.3) ----------------
#
# Every packet binds one immutable route snapshot resolved from one captured
# configuration under the validated active policy. `core.decide_route_from_config`
# remains the sole route selector; this module only maps its already-resolved
# result to the four canonical stages, applies the active policy's fallback gate,
# copies the two independent evidence requirements from the bound policy, and
# hashes the record. It never reads configuration, never selects fast/deep,
# reviewer, gate, or the validator requirement, never evaluates evidence, never
# dispatches a provider, and never carries source authorization.

EXECUTION_ROUTE_MISMATCH = "EXECUTION_ROUTE_MISMATCH"

_ROUTE_SNAPSHOT_SCHEMA_VERSION = 1
_MAX_ROUTE_TARGET_STR = _MAX_ROUTE_STR  # 128
_MAX_EFFECTIVE_FALLBACKS = _MAX_APPROVED_FALLBACKS  # 8

# Deterministic receipt semantics for a successful counted stage (spec Section
# 8.3), not configurable route policy. Order is normative.
_ROUTE_OUTPUT_FIELDS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "implementation_builder": ("raw_output_hash", "output_state"),
        "implementation_validator": ("raw_output_hash",),
        "independent_reviewer": ("raw_output_hash", "parsed_verdict"),
        "release_gate": ("raw_output_hash", "parsed_verdict"),
    }
)

# Canonical stage -> the `RouteDecision` / `resolved_route_identity` key carrying
# the lane the selector already chose. `implementation_validator` maps to the
# distinct `validator` result and never to `builder`, `reviewer`, or `gate`.
_STAGE_IDENTITY_KEY: Mapping[str, str] = MappingProxyType(
    {
        "implementation_builder": "builder",
        "implementation_validator": "validator",
        "independent_reviewer": "reviewer",
        "release_gate": "gate",
    }
)

_ROUTE_SNAPSHOT_SPEC = {
    "schema_version": None,
    "task_id": None,
    "stage": None,
    "policy_hash": None,
    "requested_provider": None,
    "requested_model": None,
    "approved_fallbacks": [{"provider": None, "model": None}],
    "required_identity_evidence_class": None,
    "required_execution_state_evidence_class": None,
    "required_output_fields": [None],
    "snapshot_hash": None,
}


def _require_route_target(value: object, path: str) -> str:
    return require_bounded_str(value, path, max_length=_MAX_ROUTE_TARGET_STR)


def _normalize_effective_fallbacks(
    value: object, primary: tuple[str, str]
) -> list[dict[str, str]]:
    """Validate the effective ordered fallback list against one selected primary.

    Order is normative: never sorted, grouped, or deduplicated. A fallback may
    not repeat the selected primary and no `(provider, model)` pair may recur.
    """
    if not isinstance(value, (list, tuple)):
        raise SchemaError("approved_fallbacks must be a list")
    if len(value) > _MAX_EFFECTIVE_FALLBACKS:
        raise SchemaError(f"approved_fallbacks accepts at most {_MAX_EFFECTIVE_FALLBACKS} entries")
    seen: list[tuple[str, str]] = []
    normalized: list[dict[str, str]] = []
    for index, entry in enumerate(value):
        entry_path = f"approved_fallbacks[{index}]"
        if not isinstance(entry, Mapping) or set(entry) != {"provider", "model"}:
            raise SchemaError(f"{entry_path} must be an object with exactly provider and model")
        provider = _require_route_target(entry["provider"], f"{entry_path}.provider")
        model = _require_route_target(entry["model"], f"{entry_path}.model")
        target = (provider, model)
        if target == primary:
            raise SchemaError(f"{entry_path} must not repeat the selected primary provider/model")
        if target in seen:
            raise SchemaError(f"{entry_path} is a duplicate fallback target")
        seen.append(target)
        normalized.append({"provider": provider, "model": model})
    return normalized


def _validate_route_snapshot(record: dict[str, object]) -> dict[str, object]:
    """Validate one persisted RouteSnapshotV1 exactly as written.

    Persisted history is read strictly: canonical stage names only (no alias
    normalization), the exact deterministic output-field table for that stage,
    supported evidence classes on both independent dimensions, and a recomputed
    `snapshot_hash`. A rehashed but semantically invalid record is still
    rejected.
    """
    snapshot = read_strict_json(canonical_json(record).encode("utf-8"), spec=_ROUTE_SNAPSHOT_SPEC)
    try:
        _require_exact_fields(snapshot, set(_ROUTE_SNAPSHOT_SPEC), "route snapshot")
        _reject_nulls(snapshot, "route snapshot")
        if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != _ROUTE_SNAPSHOT_SCHEMA_VERSION:
            raise SchemaError("unsupported route snapshot schema")
        require_bounded_str(snapshot["task_id"], "task_id", max_length=256)
        stage = snapshot["stage"]
        if stage not in _CANONICAL_ROLES:
            raise SchemaError("route snapshot stage must be a canonical role")
        require_hash(snapshot["policy_hash"], "policy_hash")
        primary = (
            _require_route_target(snapshot["requested_provider"], "requested_provider"),
            _require_route_target(snapshot["requested_model"], "requested_model"),
        )
        _normalize_effective_fallbacks(snapshot["approved_fallbacks"], primary)
        if snapshot["required_identity_evidence_class"] not in _IDENTITY_EVIDENCE_CLASSES:
            raise SchemaError("unsupported required identity-evidence class")
        if snapshot["required_execution_state_evidence_class"] not in _EVIDENCE_CLASSES:
            raise SchemaError("unsupported required execution-state evidence class")
        if snapshot["required_output_fields"] != list(_ROUTE_OUTPUT_FIELDS[stage]):
            raise SchemaError("route snapshot output fields do not match the deterministic stage table")
        require_hash(snapshot["snapshot_hash"], "snapshot_hash")
        if record_hash("route_snapshot", snapshot, "snapshot_hash") != snapshot["snapshot_hash"]:
            raise SchemaError("route snapshot hash mismatch")
    except (SeraError, TypeError, KeyError) as exc:
        raise SchemaError(f"invalid route snapshot: {exc}") from exc
    return snapshot


def _policy_allows_approved_fallbacks(active_policy_snapshot: Mapping[str, object]) -> bool:
    policy = _validate_policy_snapshot(dict(active_policy_snapshot))
    return bool(policy["substitution_rules"]["allow_approved_fallbacks"])


def resolve_route_snapshot_inputs(
    route_view: RouteConfigView,
    route_identity: Mapping[str, object],
    canonical_stage: str,
    active_policy_snapshot: Mapping[str, object],
) -> dict[str, object]:
    """Map one already-selected stage to its captured route mechanics.

    Pure and selection-free: it maps the canonical stage to the lane the
    `RouteDecision` already chose (via `core.resolved_route_identity`), reads
    that exact lane's normalized provider/model and explicit ordered
    `approved_fallbacks` from the captured `RouteConfigView`, and applies the
    active policy's fallback gate. It never chooses fast vs deep, decides the
    reviewer/gate/validator requirement, reads configuration, inspects receipts,
    interprets evidence, or dispatches a provider.

    When `substitution_rules.allow_approved_fallbacks` is false the effective
    list is empty; a non-empty selected-lane list under that policy is a
    contradictory capture and fails closed (spec Section 8.3) rather than being
    silently dropped.
    """
    if canonical_stage not in _CANONICAL_ROLES:
        raise SchemaError("route snapshot stage must be a canonical role")
    if not isinstance(route_view, RouteConfigView):
        raise SchemaError("route snapshot inputs require a normalized RouteConfigView")
    identity = (
        route_identity.get(_STAGE_IDENTITY_KEY[canonical_stage])
        if isinstance(route_identity, Mapping)
        else None
    )
    if not isinstance(identity, Mapping) or not identity.get("lane"):
        raise SchemaError(
            f"{EXECUTION_ROUTE_MISMATCH}: {canonical_stage} is not a selected route stage"
        )
    lane_name = identity["lane"]
    lane = route_view.lanes.get(lane_name)
    if lane is None:
        raise SchemaError(
            f"{EXECUTION_ROUTE_MISMATCH}: selected lane {lane_name!r} is absent from the captured route view"
        )
    provider = _require_route_target(lane["provider"], "requested_provider")
    model = _require_route_target(lane["model"], "requested_model")
    # The RouteConfigView and the RouteDecision identity were both normalized
    # from the SAME captured configuration; disagreement means two configs were
    # mixed between capture and route construction.
    if identity.get("provider") != provider or identity.get("model") != model:
        raise SchemaError(
            f"{EXECUTION_ROUTE_MISMATCH}: route identity disagrees with the captured route view"
        )
    lane_fallbacks = [dict(entry) for entry in lane["approved_fallbacks"]]
    if not _policy_allows_approved_fallbacks(active_policy_snapshot):
        if lane_fallbacks:
            raise SchemaError(
                f"{EXECUTION_ROUTE_MISMATCH}: active policy disallows approved fallbacks but the "
                f"selected lane {lane_name!r} configures {len(lane_fallbacks)}"
            )
        effective: list[dict[str, str]] = []
    else:
        effective = lane_fallbacks
    return {
        "stage": canonical_stage,
        "requested_provider": provider,
        "requested_model": model,
        "approved_fallbacks": effective,
    }


def build_route_snapshot(
    task_id: object,
    stage: object,
    active_policy_snapshot: Mapping[str, object],
    resolved_primary: Mapping[str, object],
    effective_fallbacks: object,
) -> dict[str, object]:
    """Construct and hash one immutable RouteSnapshotV1 without reading config.

    Each argument has exactly one authority. `policy_hash`, the required
    identity-evidence class, the required execution-state evidence class, and the
    deterministic output fields are all derived here from their authoritative
    inputs — the validated active `PolicySnapshotV1` and the locked stage table —
    so a caller cannot supply a second, disagreeing copy. A receipt, provider, or
    model can influence none of them. `snapshot_hash` excludes only itself.
    """
    policy = _validate_policy_snapshot(dict(active_policy_snapshot))
    task_id = require_bounded_str(task_id, "task_id", max_length=256)
    if policy["task_id"] != task_id:
        raise SchemaError(
            f"{EXECUTION_ROUTE_MISMATCH}: route task_id does not match the bound policy snapshot"
        )
    try:
        canonical_stage = canonical_role(stage)
    except SeraError as exc:
        raise SchemaError(f"route snapshot stage is not a canonical stage: {exc}") from None
    if not isinstance(resolved_primary, Mapping) or set(resolved_primary) != {"provider", "model"}:
        raise SchemaError("resolved primary route must carry exactly provider and model")
    provider = _require_route_target(resolved_primary["provider"], "requested_provider")
    model = _require_route_target(resolved_primary["model"], "requested_model")
    fallbacks = _normalize_effective_fallbacks(effective_fallbacks, (provider, model))
    snapshot = {
        "schema_version": _ROUTE_SNAPSHOT_SCHEMA_VERSION,
        "task_id": task_id,
        "stage": canonical_stage,
        "policy_hash": policy["snapshot_hash"],
        "requested_provider": provider,
        "requested_model": model,
        "approved_fallbacks": fallbacks,
        "required_identity_evidence_class": policy["identity_evidence_policy"][canonical_stage][
            "required_identity_evidence_class"
        ],
        "required_execution_state_evidence_class": policy["execution_state_policy"][canonical_stage][
            "minimum_evidence_class"
        ],
        "required_output_fields": list(_ROUTE_OUTPUT_FIELDS[canonical_stage]),
    }
    snapshot["snapshot_hash"] = record_hash("route_snapshot", snapshot, "snapshot_hash")
    return _validate_route_snapshot(snapshot)


def _validate_task_route_snapshot(record: dict, task_id: str) -> dict:
    snapshot = _validate_route_snapshot(record)
    if snapshot["task_id"] != task_id:
        raise SchemaError(f"{EXECUTION_ROUTE_MISMATCH}: route snapshot belongs to another task")
    return snapshot


def read_route_snapshots(task_dir: Path) -> LedgerReader:
    """Strictly read one task's append-only route-snapshot ledger.

    Every physical non-empty record is validated, including that its `task_id`
    equals the owning task directory name (the T08 contextual-binding lesson).
    Nothing is skipped, repaired, or filtered.
    """
    task_dir = Path(task_dir).resolve()

    def validate(record: dict) -> dict:
        return _validate_task_route_snapshot(record, task_dir.name)

    return LedgerReader(task_dir / "route-snapshots.jsonl", "route_snapshot", validate)


def append_route_snapshot(task_dir: Path, snapshot: dict, lock: TaskLockGuard) -> None:
    """Append one route snapshot under a genuine live task guard.

    The incoming record is fully validated and bound to this task, the entire
    existing ledger is reread and validated, and only then is one canonical line
    appended. Any malformed or foreign existing history blocks the append with
    the ledger bytes unchanged.
    """
    task_dir = Path(task_dir).resolve()
    validated = _validate_task_route_snapshot(snapshot, task_dir.name)
    read_route_snapshots(task_dir).records()
    append_ledger_record(Path(task_dir) / "route-snapshots.jsonl", validated, lock)


def canonicalize_remote(url: str) -> str:
    """Return a credential-free logical identity for a supported Git remote."""
    if (
        not isinstance(url, str)
        or not url
        or len(url) > _MAX_REMOTE_CHARS
        or any(ord(char) < 32 for char in url)
    ):
        raise SeraError("Git remote identity is malformed or unsupported.")

    candidate = url.strip()
    scp_match = _SCP_REMOTE_RE.fullmatch(candidate)
    if "://" not in candidate and scp_match:
        candidate = f"ssh://{scp_match.group(1)}/{scp_match.group(2)}"

    parsed = None
    scheme = ""
    host = None
    port = None
    parsed_ok = False
    try:
        parsed = urlsplit(candidate)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        port = parsed.port
        parsed_ok = True
    except ValueError:
        pass
    if not parsed_ok or parsed is None:
        raise SeraError("Git remote identity is malformed or unsupported.")

    if scheme not in {"http", "https", "ssh", "git"} or not host:
        raise SeraError("Git remote identity is malformed or unsupported.")
    path = "/".join(segment for segment in parsed.path.replace("\\", "/").split("/") if segment)
    if not path or path in {".", ".."}:
        raise SeraError("Git remote identity is malformed or unsupported.")
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not path:
        raise SeraError("Git remote identity is malformed or unsupported.")

    normalized_host = host.lower()
    if ":" in normalized_host:
        normalized_host = f"[{normalized_host}]"
    default_port = {"http": 80, "https": 443, "ssh": 22, "git": 9418}[scheme]
    if port is not None and port != default_port:
        normalized_host = f"{normalized_host}:{port}"
    return f"{normalized_host}/{path}"


def _git_output(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, ValueError) as exc:
        raise SeraError("Git repository identity could not be resolved.") from exc
    if result.returncode != 0:
        raise SeraError("Git repository identity could not be resolved.")
    return result.stdout


def _immutable_root_commit(root: Path) -> str:
    roots = sorted(set(_git_output(root, "rev-list", "--max-parents=0", "HEAD").splitlines()))
    if len(roots) != 1 or not _GIT_OBJECT_RE.fullmatch(roots[0]):
        raise SeraError("Git repository identity has no single immutable root commit.")
    return roots[0]


def _remote_identities(root: Path) -> list[str]:
    identities: set[str] = set()
    for line in _git_output(root, "remote", "-v").splitlines():
        try:
            _name, remainder = line.split("\t", 1)
        except ValueError as exc:
            raise SeraError("Git remote identity is malformed or unsupported.") from exc
        match = re.fullmatch(r"(.+) \((?:fetch|push)\)", remainder)
        if match is None:
            raise SeraError("Git remote identity is malformed or unsupported.")
        identities.add(canonicalize_remote(match.group(1)))
        if len(identities) > _MAX_REMOTE_IDENTITIES:
            raise SeraError("Git repository has too many remote identities.")
    return sorted(identities)


def repository_identity(root: Path, config: dict[str, object]) -> dict[str, object]:
    """Resolve the bounded, non-secret RepositoryIdentityV1 snapshot."""
    configured_id = config.get("repository_id") if config.get("schema_version") == 2 else None
    if configured_id is not None:
        if not isinstance(configured_id, str) or not _CONFIGURED_ID_RE.fullmatch(configured_id):
            raise SeraError("Configured repository_id must be a bounded non-secret identifier.")
        return {
            "schema_version": 1,
            "strategy": "configured",
            "logical_id": sha256_domain("repo-identity:configured", configured_id.encode("utf-8")),
            "strength": "configured",
            "components": {
                "configured_id_hash": sha256_domain(
                    "repo-identity:configured-component", configured_id.encode("utf-8")
                )
            },
        }

    root = Path(root).resolve()
    root_commit = _immutable_root_commit(root)
    remotes = _remote_identities(root)
    if remotes:
        return {
            "schema_version": 1,
            "strategy": "git_remote_root",
            "logical_id": sha256_domain(
                "repo-identity:remote",
                *(identity.encode("utf-8") for identity in remotes),
                root_commit.encode("ascii"),
            ),
            "strength": "derived",
            "components": {
                "remote_count": len(remotes),
                "remote_identity_hashes": [
                    sha256_domain("repo-identity:remote-component", identity.encode("utf-8"))
                    for identity in remotes
                ],
                "root_commit": root_commit,
            },
        }

    common_value = _git_output(root, "rev-parse", "--git-common-dir").strip()
    if not common_value or "\n" in common_value or "\r" in common_value:
        raise SeraError("Git common-directory identity could not be resolved.")
    common_path = Path(common_value)
    if not common_path.is_absolute():
        common_path = root / common_path
    try:
        common_path = common_path.resolve(strict=True)
    except OSError as exc:
        raise SeraError("Git common-directory identity could not be resolved.") from exc
    common_identity = str(common_path).replace("\\", "/").casefold()
    return {
        "schema_version": 1,
        "strategy": "local_git_dir",
        "logical_id": sha256_domain(
            "repo-identity:local",
            common_identity.encode("utf-8"),
            root_commit.encode("ascii"),
        ),
        "strength": "local_only",
        "components": {
            "git_common_dir_hash": sha256_domain(
                "repo-identity:git-common-dir", common_identity.encode("utf-8")
            ),
            "root_commit": root_commit,
        },
    }


EXECUTION_HEAD_MISMATCH = "EXECUTION_HEAD_MISMATCH"
EXECUTION_TREE_MISMATCH = "EXECUTION_TREE_MISMATCH"
EXECUTION_INPUT_STATE_MISMATCH = "EXECUTION_INPUT_STATE_MISMATCH"
EXECUTION_OUTPUT_STATE_MISSING = "EXECUTION_OUTPUT_STATE_MISSING"
EXECUTION_REPOSITORY_MISMATCH = "EXECUTION_REPOSITORY_MISMATCH"

_REPOSITORY_IDENTITY_SPEC = {
    "schema_version": None,
    "strategy": None,
    "logical_id": None,
    "strength": None,
    "components": None,
}
_REPOSITORY_STATE_SPEC = {
    "schema_version": None,
    "repository_identity": _REPOSITORY_IDENTITY_SPEC,
    "head_sha": None,
    "tree_sha": None,
    "task_contract_fingerprint": None,
    "task_fingerprint": None,
    "state_kind": None,
    "review_change_fingerprint": None,
}


class RepositoryStateError(SeraError):
    """Fail-closed repository-state error with a stable reason-code leaf."""

    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(f"{code}: {detail}")


def _state_error(detail: str, code: str = EXECUTION_INPUT_STATE_MISMATCH) -> RepositoryStateError:
    return RepositoryStateError(code, detail)


def _validated_hash(value: object, field: str) -> str:
    try:
        return require_hash(value, field)
    except SchemaError as exc:
        raise _state_error(str(exc)) from None


def _validated_git_id(value: object, field: str, *, allow_unborn: bool = True) -> str:
    if value == UNBORN_HEAD and allow_unborn:
        return UNBORN_HEAD
    if not isinstance(value, str) or not _GIT_OBJECT_RE.fullmatch(value):
        raise _state_error(f"{field} must be a lowercase immutable Git object ID.")
    return value


def _validate_repository_identity(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != set(_REPOSITORY_IDENTITY_SPEC):
        raise _state_error("repository_identity has an invalid field set.")
    if value.get("schema_version") != 1 or isinstance(value.get("schema_version"), bool):
        raise _state_error("repository_identity uses an unsupported schema.")
    strategy = value.get("strategy")
    strength = value.get("strength")
    if not isinstance(strategy, str):
        raise _state_error("repository_identity.strategy is invalid.")
    expected_strength = {
        "configured": "configured",
        "git_remote_root": "derived",
        "local_git_dir": "local_only",
    }.get(strategy)
    if expected_strength is None or strength != expected_strength:
        raise _state_error("repository_identity strategy and strength are inconsistent.")
    logical_id = _validated_hash(value.get("logical_id"), "repository_identity.logical_id")
    components = value.get("components")
    if not isinstance(components, dict):
        raise _state_error("repository_identity.components must be an object.")

    if strategy == "configured":
        if set(components) != {"configured_id_hash"}:
            raise _state_error("configured repository identity components are invalid.")
        normalized_components: dict[str, object] = {
            "configured_id_hash": _validated_hash(
                components["configured_id_hash"], "repository_identity.components.configured_id_hash"
            )
        }
    elif strategy == "git_remote_root":
        if set(components) != {"remote_count", "remote_identity_hashes", "root_commit"}:
            raise _state_error("remote repository identity components are invalid.")
        count = components["remote_count"]
        hashes = components["remote_identity_hashes"]
        if isinstance(count, bool) or not isinstance(count, int) or not (1 <= count <= _MAX_REMOTE_IDENTITIES):
            raise _state_error("repository_identity.components.remote_count is invalid.")
        if not isinstance(hashes, list) or len(hashes) != count:
            raise _state_error("repository_identity.components.remote_identity_hashes are invalid.")
        validated_hashes = [
            _validated_hash(item, "repository_identity.components.remote_identity_hashes")
            for item in hashes
        ]
        if len(set(validated_hashes)) != len(validated_hashes):
            raise _state_error("repository_identity.components.remote_identity_hashes are invalid.")
        normalized_components = {
            "remote_count": count,
            "remote_identity_hashes": validated_hashes,
            "root_commit": _validated_git_id(
                components["root_commit"],
                "repository_identity.components.root_commit",
                allow_unborn=False,
            ),
        }
    else:
        if set(components) != {"git_common_dir_hash", "root_commit"}:
            raise _state_error("local repository identity components are invalid.")
        normalized_components = {
            "git_common_dir_hash": _validated_hash(
                components["git_common_dir_hash"],
                "repository_identity.components.git_common_dir_hash",
            ),
            "root_commit": _validated_git_id(
                components["root_commit"],
                "repository_identity.components.root_commit",
                allow_unborn=False,
            ),
        }
    return {
        "schema_version": 1,
        "strategy": strategy,
        "logical_id": logical_id,
        "strength": strength,
        "components": normalized_components,
    }


def validate_repository_state(
    obj: object,
    *,
    root: Path | None = None,
    task_dir: Path | None = None,
    contract_fp: str | None = None,
    dynamic_fp: str | None = None,
    require_committed: bool = False,
    output_required: bool = False,
) -> dict[str, object]:
    """Validate intrinsic state and, when supplied, its current context."""
    if obj is None:
        code = EXECUTION_OUTPUT_STATE_MISSING if output_required else EXECUTION_INPUT_STATE_MISMATCH
        raise _state_error("repository state is missing.", code)
    if not isinstance(obj, dict):
        raise _state_error("repository state must be an object.")
    try:
        strict = read_strict_json(canonical_json(obj).encode("utf-8"), spec=_REPOSITORY_STATE_SPEC)
    except SchemaError as exc:
        raise _state_error(str(exc)) from None
    required = set(_REPOSITORY_STATE_SPEC) - {"review_change_fingerprint"}
    if not required.issubset(strict):
        missing = sorted(required - set(strict))[0]
        raise _state_error(f"repository state is missing required field {missing}.")
    schema_version = strict["schema_version"]
    if schema_version != 1 or isinstance(schema_version, bool):
        raise _state_error("repository state uses an unsupported schema.")
    state_kind = strict["state_kind"]
    if not isinstance(state_kind, str) or state_kind not in {"committed", "working_tree"}:
        raise _state_error("state_kind must be committed or working_tree.")
    head_sha = _validated_git_id(strict["head_sha"], "head_sha")
    tree_sha = _validated_git_id(strict["tree_sha"], "tree_sha")
    if (head_sha == UNBORN_HEAD) != (tree_sha == UNBORN_HEAD):
        raise _state_error("head_sha and tree_sha must agree on unborn state.")
    if state_kind == "committed" and head_sha == UNBORN_HEAD:
        raise _state_error("an unborn repository cannot satisfy committed state.")
    if require_committed and state_kind != "committed":
        raise _state_error("the validation context requires committed state.")

    review_fingerprint = strict.get("review_change_fingerprint")
    if state_kind == "working_tree" and review_fingerprint is None:
        raise _state_error("working_tree state requires review_change_fingerprint.")
    if review_fingerprint is not None:
        review_fingerprint = _validated_hash(review_fingerprint, "review_change_fingerprint")

    normalized: dict[str, object] = {
        "schema_version": 1,
        "repository_identity": _validate_repository_identity(strict["repository_identity"]),
        "head_sha": head_sha,
        "tree_sha": tree_sha,
        "task_contract_fingerprint": _validated_hash(
            strict["task_contract_fingerprint"], "task_contract_fingerprint"
        ),
        "task_fingerprint": _validated_hash(strict["task_fingerprint"], "task_fingerprint"),
        "state_kind": state_kind,
    }
    if review_fingerprint is not None:
        normalized["review_change_fingerprint"] = review_fingerprint

    root = Path(root).resolve() if root is not None else None
    if root is not None:
        try:
            current_identity = repository_identity(root, load_config(root))
            current_head = git_head_identity(root)
        except (OSError, ValueError, SeraError):
            raise _state_error("current repository context could not be resolved.") from None
        if normalized["repository_identity"] != current_identity:
            raise _state_error(
                "repository identity does not match current context.", EXECUTION_REPOSITORY_MISMATCH
            )
        if head_sha != current_head["head_sha"]:
            raise _state_error("HEAD does not match current context.", EXECUTION_HEAD_MISMATCH)
        if tree_sha != current_head["head_tree_sha"]:
            raise _state_error("tree does not match current context.", EXECUTION_TREE_MISMATCH)

    if task_dir is not None:
        if root is None:
            raise _state_error("task_dir context requires root.")
        try:
            task = load_task(Path(task_dir))
            current_contract_fp = task_contract_fingerprint(task)
            current_dynamic_fp = task_fingerprint(root, Path(task_dir))
        except (OSError, ValueError, SeraError):
            raise _state_error("current task context could not be resolved.") from None
        if contract_fp is None:
            contract_fp = current_contract_fp
        if dynamic_fp is None:
            dynamic_fp = current_dynamic_fp
    if contract_fp is not None:
        expected_contract_fp = _validated_hash(contract_fp, "expected task_contract_fingerprint")
        if normalized["task_contract_fingerprint"] != expected_contract_fp:
            raise _state_error("task contract fingerprint does not match validation context.")
    if dynamic_fp is not None:
        expected_dynamic_fp = _validated_hash(dynamic_fp, "expected task_fingerprint")
        if normalized["task_fingerprint"] != expected_dynamic_fp:
            raise _state_error("dynamic task fingerprint does not match validation context.")
    return normalized


def repository_state(
    root: Path,
    task_dir: Path,
    *,
    state_kind: str,
    contract_fp: str,
    dynamic_fp: str,
) -> dict[str, object]:
    """Capture one coherent ExecutionRepositoryStateV1 from current state."""
    if not isinstance(state_kind, str) or state_kind not in {"committed", "working_tree"}:
        raise _state_error("state_kind must be committed or working_tree.")
    contract_fp = _validated_hash(contract_fp, "task_contract_fingerprint")
    dynamic_fp = _validated_hash(dynamic_fp, "task_fingerprint")
    root = Path(root).resolve()
    task_dir = Path(task_dir).resolve()
    try:
        config = load_config(root)
        identity_before = repository_identity(root, config)
        head_before = git_head_identity(root)
        if state_kind == "committed" and head_before["head_sha"] == UNBORN_HEAD:
            raise _state_error("an unborn repository cannot satisfy committed state.")
        task = load_task(task_dir)
        coverage = task_review_coverage(root, task, int(config["max_packet_chars"]))
        identity_after = repository_identity(root, config)
        head_after = git_head_identity(root)
    except RepositoryStateError:
        raise
    except (OSError, ValueError, KeyError, SeraError):
        raise _state_error("current repository state could not be captured.") from None

    if identity_before != identity_after:
        raise _state_error(
            "repository identity moved during capture.", EXECUTION_REPOSITORY_MISMATCH
        )
    if head_before["head_sha"] != head_after["head_sha"]:
        raise _state_error("HEAD moved during capture.", EXECUTION_HEAD_MISMATCH)
    if head_before["head_tree_sha"] != head_after["head_tree_sha"]:
        raise _state_error("tree moved during capture.", EXECUTION_TREE_MISMATCH)

    if state_kind == "committed" and any(
        entry.get("staged") or entry.get("unstaged") for entry in coverage["entries"]
    ):
        raise _state_error("task-relevant working-tree changes cannot be captured as committed state.")

    state: dict[str, object] = {
        "schema_version": 1,
        "repository_identity": identity_before,
        "head_sha": head_before["head_sha"],
        "tree_sha": head_before["head_tree_sha"],
        "task_contract_fingerprint": contract_fp,
        "task_fingerprint": dynamic_fp,
        "state_kind": state_kind,
    }
    if state_kind == "working_tree":
        state["review_change_fingerprint"] = coverage["change_fingerprint"]
    return validate_repository_state(state)


@dataclass(frozen=True)
class SandboxLaunchRequest:
    """A bounded, provider-neutral request for one sandboxed process."""

    argv: tuple[str, ...]
    working_directory: Path
    read_only_roots: tuple[Path, ...]
    read_write_roots: tuple[Path, ...]
    environment: tuple[tuple[str, str], ...]
    identity: str
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class SandboxLaunchResult:
    """Machine observations returned by an enforcement backend."""

    backend: str
    status: str
    os_sandbox_applied: bool
    fallback_used: bool
    process_id: int | None
    exit_code: int | None
    policy_hash: str | None
    sandbox_identity: str | None
    error: str | None
    teardown_observation: str


@runtime_checkable
class SandboxBackend(Protocol):
    """Interface consumed by the registered runner introduced in I1-T18."""

    backend_name: str

    def launch(self, request: SandboxLaunchRequest) -> SandboxLaunchResult:
        """Launch exactly ``request.argv`` or return a fail-closed result."""
