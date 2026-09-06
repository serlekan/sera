"""Strict append-only JSONL ledger behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sera.schemas import (
    EMPTY_LEDGER_FINGERPRINT,
    LedgerReader,
    SchemaError,
    append_ledger_record,
    canonical_json,
    ledger_fingerprint,
)


class _HeldLock:
    def __init__(self, root: Path, *, held: bool = True) -> None:
        self.root = root.resolve()
        self.held = held

    def protects(self, path: Path) -> bool:
        return path.resolve().is_relative_to(self.root)


class LedgerReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "history.jsonl"
        self.lock = _HeldLock(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_append_writes_one_canonical_utf8_record_and_newline(self) -> None:
        append_ledger_record(self.path, {"z": 1, "name": "café"}, self.lock)
        self.assertEqual(self.path.read_bytes(), b'{"name":"caf\xc3\xa9","z":1}\n')
        self.assertEqual(LedgerReader(self.path, "test").records(), [{"name": "café", "z": 1}])

    def test_records_remain_in_physical_order(self) -> None:
        for sequence in (1, 2, 3):
            append_ledger_record(self.path, {"sequence": sequence}, self.lock)
        self.assertEqual(
            LedgerReader(self.path, "test").records(),
            [{"sequence": 1}, {"sequence": 2}, {"sequence": 3}],
        )

    def test_record_mutation_and_order_change_the_fingerprint(self) -> None:
        original = [{"sequence": 1}, {"sequence": 2}]
        mutated = [{"sequence": 1}, {"sequence": 9}]
        reversed_records = list(reversed(original))
        self.assertNotEqual(ledger_fingerprint("test", original), ledger_fingerprint("test", mutated))
        self.assertNotEqual(ledger_fingerprint("test", original), ledger_fingerprint("test", reversed_records))

    def test_duplicate_physical_record_changes_fingerprint_and_is_retained(self) -> None:
        record = {"sequence": 1}
        single = ledger_fingerprint("test", [record])
        duplicate = ledger_fingerprint("test", [record, record])
        self.assertNotEqual(single, duplicate)
        self.path.write_bytes((canonical_json(record) + "\n" + canonical_json(record) + "\n").encode())
        self.assertEqual(LedgerReader(self.path, "test").records(), [record, record])

    def test_absent_and_empty_ledgers_have_the_canonical_empty_fingerprint(self) -> None:
        self.assertEqual(LedgerReader(self.path, "test").fingerprint(), EMPTY_LEDGER_FINGERPRINT)
        self.path.write_bytes(b"")
        self.assertEqual(LedgerReader(self.path, "another-family").fingerprint(), EMPTY_LEDGER_FINGERPRINT)
        self.assertEqual(ledger_fingerprint("test", []), EMPTY_LEDGER_FINGERPRINT)

    def test_json_spacing_and_platform_newlines_are_non_semantic(self) -> None:
        records = [{"a": 1, "b": 2}, {"text": "é"}]
        compact = b'{"a":1,"b":2}\n{"text":"\xc3\xa9"}\n'
        spaced_crlf = b'{ "b": 2, "a": 1 }\r\n{ "text": "\xc3\xa9" }\r\n'
        self.path.write_bytes(compact)
        compact_fingerprint = LedgerReader(self.path, "test").fingerprint()
        self.path.write_bytes(spaced_crlf)
        reader = LedgerReader(self.path, "test")
        self.assertEqual(reader.records(), records)
        self.assertEqual(reader.fingerprint(), compact_fingerprint)

    def test_normalizer_controls_semantic_record_form(self) -> None:
        self.path.write_text('{"legacy_name":"value"}\n', encoding="utf-8")
        reader = LedgerReader(
            self.path,
            "test",
            lambda record: {"name": record["legacy_name"], "schema_version": 1},
        )
        normalized = [{"name": "value", "schema_version": 1}]
        self.assertEqual(reader.records(), normalized)
        self.assertEqual(reader.fingerprint(), ledger_fingerprint("test", normalized))

    def test_malformed_middle_record_blocks_and_is_never_skipped(self) -> None:
        self.path.write_bytes(b'{"sequence":1}\nnot-json\n{"sequence":3}\n')
        reader = LedgerReader(self.path, "test")
        with self.assertRaisesRegex(SchemaError, "line 2"):
            reader.records()
        self.assertEqual(reader.malformed, "line 2 is invalid")

    def test_truncated_non_newline_final_record_blocks(self) -> None:
        self.path.write_bytes(b'{"sequence":1}\n{"sequence":2}')
        reader = LedgerReader(self.path, "test")
        with self.assertRaisesRegex(SchemaError, "incomplete final line"):
            reader.records()
        self.assertEqual(reader.malformed, "incomplete final line")

    def test_malformed_record_also_blocks_fingerprint(self) -> None:
        self.path.write_bytes(b'{"sequence":1}\n[]\n')
        reader = LedgerReader(self.path, "test")
        with self.assertRaisesRegex(SchemaError, "line 2"):
            reader.fingerprint()

    def test_indexed_hashes_surface_every_physical_record(self) -> None:
        records = [{"sequence": 1}, {"sequence": 1}]
        self.path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        entries = LedgerReader(self.path, "test").indexed_hashes()
        self.assertEqual([index for index, _ in entries], [0, 1])
        self.assertEqual(entries[0][1], entries[1][1])


class LedgerAppendLockContractTests(unittest.TestCase):
    def test_append_requires_a_held_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "history.jsonl"
            with self.assertRaisesRegex(SchemaError, "held lock"):
                append_ledger_record(path, {"sequence": 1}, None)
            with self.assertRaisesRegex(SchemaError, "held lock"):
                append_ledger_record(path, {"sequence": 1}, _HeldLock(root, held=False))
            self.assertFalse(path.exists())

    def test_append_rejects_a_lock_for_another_path(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            path = Path(first) / "history.jsonl"
            with self.assertRaisesRegex(SchemaError, "does not protect"):
                append_ledger_record(path, {"sequence": 1}, _HeldLock(Path(second)))
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
