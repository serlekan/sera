"""Strict serialization, validation, and hashing primitives.

This lowest-level domain module is deliberately standard-library-only.  It
must never import SERA orchestration or domain authorities above it.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


MAX_JSON_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 8
MAX_JSON_KEYS = 128
MAX_STRING_CHARS = 4096

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


SCHEMA_VERSIONS: dict[str, int] = {
    "config": 2,
    "task": 2,
    "task_contract": 2,
    "task_contract_adoption": 1,
    "policy_snapshot": 1,
    "route_snapshot": 1,
    "substitution_decision": 1,
    "receipt_import": 1,
    "execution_receipt": 1,
    "execution_repository_state": 1,
    "execution_target": 1,
    "execution_evidence_source": 1,
    "execution_binding": 1,
    "execution_state_evidence": 1,
    "review": 2,
    "verification": 2,
    "packet_provenance": 4,
    "packet_model": 1,
    "packet_lint": 1,
    "context_overflow_manifest": 1,
    "seal": 3,
    "seal_history": 1,
    "seal_pointer": 1,
}


class SchemaError(RuntimeError):
    """A fail-closed schema, canonicalization, or validation error."""


@dataclass(frozen=True)
class ReasonCode:
    code: str
    explanation: str
    stage: str
    blocking: bool
    evidence_refs: tuple[str, ...]
    action: str


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SchemaError("canonical JSON requires finite JSON values")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SchemaError("canonical JSON object keys must be strings")
            _validate_json_value(item)
        return
    raise SchemaError(f"canonical JSON does not support {type(value).__name__}")


def canonical_json(obj: Any) -> str:
    """Return the single canonical JSON text for a supported JSON value."""
    _validate_json_value(obj)
    try:
        return json.dumps(
            obj,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"canonical JSON failed: {exc}") from exc


def sha256_domain(domain: str, *parts: bytes) -> str:
    """Hash byte parts under an explicit, non-empty textual domain."""
    if not isinstance(domain, str) or not domain or any(ord(char) < 32 for char in domain):
        raise SchemaError("hash domain must be a non-empty control-free string")
    if not all(isinstance(part, bytes) for part in parts):
        raise SchemaError("domain hash parts must be bytes")
    return hashlib.sha256(domain.encode("utf-8") + b"\x1f" + b"\x1f".join(parts)).hexdigest()


def record_hash(
    domain: str,
    obj: Mapping[str, Any],
    exclude_key: str | Collection[str] | None = None,
) -> str:
    """Hash a record canonically, optionally omitting only named self fields."""
    if not isinstance(obj, Mapping):
        raise SchemaError("record hash input must be an object")
    if exclude_key is None:
        excluded: set[str] = set()
    elif isinstance(exclude_key, str):
        excluded = {exclude_key}
    else:
        excluded = set(exclude_key)
    payload = {key: value for key, value in obj.items() if key not in excluded}
    return sha256_domain(domain, canonical_json(payload).encode("utf-8"))


class _DuplicateKey(ValueError):
    pass


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise SchemaError(f"non-finite number is forbidden: {token}")


def _walk_limits(value: Any, *, depth: int, max_depth: int, counters: dict[str, int]) -> None:
    if depth > max_depth:
        raise SchemaError(f"JSON depth exceeds {max_depth}")
    if isinstance(value, str):
        if len(value) > MAX_STRING_CHARS:
            raise SchemaError(f"JSON string exceeds {MAX_STRING_CHARS} characters")
        if any(ord(char) < 32 for char in value):
            raise SchemaError("JSON strings must not contain control characters")
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise SchemaError("JSON contains a non-finite number")
    if isinstance(value, dict):
        counters["keys"] += len(value)
        if counters["keys"] > counters["max_keys"]:
            raise SchemaError(f"JSON contains more than {counters['max_keys']} object keys")
        for key, item in value.items():
            if not _KEY_RE.fullmatch(key):
                raise SchemaError(f"invalid object key: {key}")
            _walk_limits(item, depth=depth + 1, max_depth=max_depth, counters=counters)
    elif isinstance(value, list):
        for item in value:
            _walk_limits(item, depth=depth + 1, max_depth=max_depth, counters=counters)


def _validate_descriptor(value: Any, descriptor: Any, path: str = "") -> None:
    if descriptor is None:
        return
    if isinstance(descriptor, Mapping):
        if not isinstance(value, dict):
            label = path or "top level"
            raise SchemaError(f"{label} must be an object")
        for key in value:
            if key not in descriptor:
                field = f"{path}.{key}" if path else key
                raise SchemaError(f"unknown field: {field}")
        for key, nested in descriptor.items():
            if key in value:
                field = f"{path}.{key}" if path else key
                _validate_descriptor(value[key], nested, field)
        return
    if isinstance(descriptor, list) and len(descriptor) == 1:
        if not isinstance(value, list):
            raise SchemaError(f"{path} must be an array")
        for index, item in enumerate(value):
            _validate_descriptor(item, descriptor[0], f"{path}[{index}]")
        return
    raise SchemaError(f"invalid field descriptor at {path or 'top level'}")


def read_strict_json(
    raw: bytes,
    *,
    max_bytes: int = MAX_JSON_BYTES,
    max_depth: int = MAX_JSON_DEPTH,
    max_keys: int = MAX_JSON_KEYS,
    spec: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Parse one bounded UTF-8 JSON object and reject undeclared fields."""
    if not isinstance(raw, bytes):
        raise SchemaError("strict JSON input must be bytes")
    if len(raw) > max_bytes:
        raise SchemaError(f"JSON input exceeds {max_bytes} bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SchemaError("JSON input is not valid UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey as exc:
        raise SchemaError(f"duplicate object key: {exc.args[0]}") from exc
    except SchemaError:
        raise
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise SchemaError(f"invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SchemaError("JSON top level must be an object")
    counters = {"keys": 0, "max_keys": max_keys}
    _walk_limits(value, depth=1, max_depth=max_depth, counters=counters)
    _validate_descriptor(value, spec)
    return value


def require_hash(value: Any, field_name: str = "hash") -> str:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise SchemaError(f"{field_name} must be lowercase 64-character SHA-256 hex")
    return value


def require_timestamp(value: Any, field_name: str = "timestamp") -> str:
    if not isinstance(value, str) or not value:
        raise SchemaError(f"{field_name} must be a timezone-aware ISO 8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SchemaError(f"{field_name} must be a timezone-aware ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SchemaError(f"{field_name} must be a timezone-aware ISO 8601 timestamp")
    return value


def require_bounded_str(
    value: Any,
    field_name: str,
    *,
    max_length: int = MAX_STRING_CHARS,
    min_length: int = 1,
) -> str:
    if not isinstance(value, str) or not (min_length <= len(value) <= max_length):
        raise SchemaError(f"{field_name} must contain {min_length} to {max_length} characters")
    if any(ord(char) < 32 for char in value):
        raise SchemaError(f"{field_name} must not contain control characters")
    return value
