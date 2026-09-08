"""Provider-neutral repository and execution provenance primitives."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .core import RISK_LEVELS, VALID_MODES, SeraError, validate_config
from .schemas import (
    SchemaError,
    canonical_json,
    read_strict_json,
    require_bounded_str,
    require_hash,
    sha256_domain,
)


_MAX_REMOTE_CHARS = 4096
_MAX_REMOTE_IDENTITIES = 32
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_CONFIGURED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SCP_REMOTE_RE = re.compile(r"^(?:[^@/:\\\s]+@)?([^/:\\\s]+):(.+)$")
_CANONICAL_ROLES = (
    "implementation_builder",
    "implementation_validator",
    "independent_reviewer",
    "release_gate",
)
_ROLE_ALIASES = {
    "builder": "implementation_builder",
    "validator": "implementation_validator",
    "independent": "independent_reviewer",
    "gate": "release_gate",
    **{role: role for role in _CANONICAL_ROLES},
}
_EVIDENCE_CLASSES = {
    "LEGACY_PROVENANCE",
    "CHECKPOINT_OBSERVED",
    "EXECUTION_STATE_ENFORCED",
    "EXECUTION_STATE_ATTESTED",
}
_REPOSITORY_STRENGTHS = {"local_only", "derived", "configured"}

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
_ROLE_SPEC_KEYS = {key: _STAGE_POLICY_SPEC for key in _ROLE_ALIASES}
_EXECUTION_ROLE_SPEC_KEYS = {key: _EXECUTION_STATE_POLICY_SPEC for key in _ROLE_ALIASES}
_LANE_SPEC = {
    "provider": None,
    "model": None,
    "enabled": None,
    "allowed_uses": [None],
    "may_be_sole_release_gate": None,
}
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
        name: _LANE_SPEC
        for name in (
            "planner",
            "fast_builder",
            "deep_builder",
            "independent_reviewer",
            "release_gate",
            "optional_fable",
        )
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


@dataclass(frozen=True)
class ConfigV2View:
    """Immutable, provider-neutral candidate policy normalized from config."""

    source_schema: int
    stage_policies: Mapping[str, object]
    provenance_requirements: Mapping[str, object]
    execution_state_policy: Mapping[str, object]
    substitution_rules: Mapping[str, object]
    knowledge_policy: Mapping[str, object]
    repository_identity_requirement: Mapping[str, object]
    context_budgets: Mapping[str, object]


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
        canonical = _ROLE_ALIASES.get(role)
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
        substitution_rules=_freeze_mapping(values["substitution_rules"]),
        knowledge_policy=_freeze_mapping(values["knowledge_policy"]),
        repository_identity_requirement=_freeze_mapping(values["repository_identity_requirement"]),
        context_budgets=_freeze_mapping(values["context_budgets"]),
    )


def _translate_v2(loaded: dict[str, object]) -> ConfigV2View:
    try:
        strict = read_strict_json(canonical_json(loaded).encode("utf-8"), spec=_CONFIG_V2_SPEC)
    except SchemaError as exc:
        raise SeraError(str(exc)) from None
    _reject_nulls(strict)
    required = {
        "schema_version",
        "stage_policies",
        "provenance_requirements",
        "execution_state_policy",
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
