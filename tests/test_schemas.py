"""Strict schema and canonicalization primitives for SERA 0.5.0."""

from __future__ import annotations

import hashlib
import json
import math
import unittest

from sera.core import TASK_CONTRACT_FIELDS, task_contract_fingerprint
from sera.schemas import (
    MAX_JSON_BYTES,
    ReasonCode,
    SCHEMA_VERSIONS,
    SchemaError,
    canonical_json,
    read_strict_json,
    record_hash,
    require_bounded_str,
    require_hash,
    require_timestamp,
    sha256_domain,
)


class CanonicalJsonTests(unittest.TestCase):
    def test_key_insertion_order_does_not_change_canonical_bytes(self) -> None:
        left = {"z": [3, 2, 1], "a": "café"}
        right = {"a": "café", "z": [3, 2, 1]}
        self.assertEqual(canonical_json(left), '{"a":"café","z":[3,2,1]}')
        self.assertEqual(canonical_json(left), canonical_json(right))

    def test_array_order_remains_semantic(self) -> None:
        self.assertNotEqual(canonical_json({"items": [1, 2]}), canonical_json({"items": [2, 1]}))

    def test_non_finite_numbers_are_rejected_at_any_depth(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaisesRegex(SchemaError, "finite JSON values"):
                canonical_json({"outer": [{"value": value}]})

    def test_domain_hash_and_record_hash_are_lowercase_sha256(self) -> None:
        digest = sha256_domain("sera:test", b"alpha", b"beta")
        expected = hashlib.sha256(b"sera:test\x1falpha\x1fbeta").hexdigest()
        self.assertEqual(digest, expected)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(
            record_hash("sera:record", {"record_hash": "ignored", "name": "é"}, "record_hash"),
            sha256_domain("sera:record", b'{"name":"\xc3\xa9"}'),
        )

    def test_legacy_task_contract_serialization_is_byte_compatible(self) -> None:
        task = {
            "objective": "Preserve café output",
            "requested_mode": "standard",
            "requested_risk": "medium",
            "mode": "standard",
            "risk": "medium",
            "risk_reasons": ["explicit"],
            "allowed_files": ["src/z.py", "src/a.py"],
            "constraints": ["unicode ✓"],
            "verification": ["python -m unittest"],
            "uncertainty": 1,
            "use_case": "implementation",
        }
        payload = {field: task.get(field) for field in TASK_CONTRACT_FIELDS}
        legacy = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self.assertEqual(canonical_json(payload).encode("utf-8"), legacy.encode("utf-8"))
        self.assertEqual(hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest(), task_contract_fingerprint(task))


class StrictJsonReaderTests(unittest.TestCase):
    def test_exact_object_schema_is_accepted(self) -> None:
        raw = '{"metadata":{"count":2},"name":"café"}'.encode()
        self.assertEqual(
            read_strict_json(raw, spec={"name": None, "metadata": {"count": None}}),
            {"name": "café", "metadata": {"count": 2}},
        )

    def test_preparse_limit_rejects_one_mib_plus_one_byte(self) -> None:
        raw = b"{" + (b" " * (MAX_JSON_BYTES - 1)) + b"}"
        self.assertEqual(len(raw), MAX_JSON_BYTES + 1)
        with self.assertRaisesRegex(SchemaError, "exceeds 1048576 bytes"):
            read_strict_json(raw, spec={})

    def test_depth_nine_is_rejected_when_limit_is_eight(self) -> None:
        value: object = 0
        for _ in range(9):
            value = [value]
        with self.assertRaisesRegex(SchemaError, "depth exceeds 8"):
            read_strict_json(json.dumps({"value": value}).encode(), spec=None, max_depth=8)

    def test_aggregate_object_key_limit_is_enforced(self) -> None:
        raw = json.dumps({"outer": {f"key_{index}": index for index in range(5)}}).encode()
        with self.assertRaisesRegex(SchemaError, "more than 5 object keys"):
            read_strict_json(raw, spec=None, max_keys=5)

    def test_unknown_top_level_and_nested_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(SchemaError, "unknown field: extra"):
            read_strict_json(b'{"name":"ok","extra":1}', spec={"name": None})
        with self.assertRaisesRegex(SchemaError, "unknown field: metadata.extra"):
            read_strict_json(
                b'{"metadata":{"count":1,"extra":2}}',
                spec={"metadata": {"count": None}},
            )

    def test_non_object_root_and_invalid_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(SchemaError, "top level must be an object"):
            read_strict_json(b"[]", spec={})
        with self.assertRaisesRegex(SchemaError, "invalid object key: bad key"):
            read_strict_json(b'{"bad key":1}', spec={"bad key": None})

    def test_nan_and_infinity_tokens_are_rejected(self) -> None:
        for token in (b"NaN", b"Infinity", b"-Infinity"):
            with self.subTest(token=token), self.assertRaisesRegex(SchemaError, "non-finite number"):
                read_strict_json(b'{"value":' + token + b"}", spec={"value": None})

    def test_duplicate_object_keys_are_rejected(self) -> None:
        with self.assertRaisesRegex(SchemaError, "duplicate object key: name"):
            read_strict_json(b'{"name":"first","name":"second"}', spec={"name": None})


class PrimitiveValidatorTests(unittest.TestCase):
    def test_hash_requires_lowercase_sha256_hex(self) -> None:
        valid = "ab" * 32
        self.assertEqual(require_hash(valid, "payload_hash"), valid)
        for value in ("AB" * 32, "0" * 63, "g" * 64, 7):
            with self.subTest(value=value), self.assertRaisesRegex(SchemaError, "payload_hash"):
                require_hash(value, "payload_hash")

    def test_timestamp_requires_timezone_aware_iso8601(self) -> None:
        self.assertEqual(require_timestamp("2026-09-05T12:34:56Z", "created_at"), "2026-09-05T12:34:56Z")
        self.assertEqual(
            require_timestamp("2026-09-05T13:34:56+01:00", "created_at"),
            "2026-09-05T13:34:56+01:00",
        )
        for value in ("2026-09-05T12:34:56", "not-a-time", 1):
            with self.subTest(value=value), self.assertRaisesRegex(SchemaError, "created_at"):
                require_timestamp(value, "created_at")

    def test_bounded_string_rejects_empty_control_and_oversized_values(self) -> None:
        self.assertEqual(require_bounded_str("actor", "actor_id", max_length=5), "actor")
        for value in ("", "bad\nvalue", "abcdef", 1):
            with self.subTest(value=value), self.assertRaisesRegex(SchemaError, "actor_id"):
                require_bounded_str(value, "actor_id", max_length=5)

    def test_schema_registry_and_reason_code_shape_are_stable(self) -> None:
        self.assertEqual(SCHEMA_VERSIONS["task_contract"], 2)
        self.assertEqual(SCHEMA_VERSIONS["execution_receipt"], 1)
        self.assertEqual(SCHEMA_VERSIONS["seal"], 3)
        reason = ReasonCode("RECEIPT_INVALID", "Receipt is invalid.", "review", True, ("hash",), "Re-import.")
        self.assertEqual(
            reason,
            ReasonCode(
                code="RECEIPT_INVALID",
                explanation="Receipt is invalid.",
                stage="review",
                blocking=True,
                evidence_refs=("hash",),
                action="Re-import.",
            ),
        )


if __name__ == "__main__":
    unittest.main()
