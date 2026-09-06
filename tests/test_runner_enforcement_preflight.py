"""Real implementation gate for the Windows AppContainer + BFS backend."""

from __future__ import annotations

import os
import struct
import unittest
from pathlib import Path

from sera._windows_sandbox import (
    BACKEND_NAME,
    SBOX_SCHEMA_VERSION,
    WindowsSandboxBackend,
    WindowsSandboxPolicy,
    _bounded_environment,
    encode_sbox_policy,
    probe_windows_sandbox,
    run_enforcement_preflight,
    weak_execution_classification,
)
from sera.provenance import SandboxBackend


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_SBOX_0_1_0 = bytes.fromhex(
    "2000000053424f581800180014001300000012000000110000000c0008000400"
    "180000001c000000280000002c000000000101014c0000000800080000000400"
    "08000000080000000400040004000000010000000c0000000100000014000000"
    "09000000433a5c534552415c5400000009000000433a5c534552415c57000000"
    "05000000302e312e30000000"
)


def decode_sbox_fixture(payload: bytes) -> dict[str, object]:
    """Small independent decoder for only the slots asserted by this fixture."""

    root = struct.unpack_from("<I", payload, 0)[0]
    vtable = root - struct.unpack_from("<i", payload, root)[0]

    def field(table: int, table_vtable: int, slot: int) -> int | None:
        vtable_size = struct.unpack_from("<H", payload, table_vtable)[0]
        entry = table_vtable + 4 + slot * 2
        if entry + 2 > table_vtable + vtable_size:
            return None
        offset = struct.unpack_from("<H", payload, entry)[0]
        return table + offset if offset else None

    def target(position: int) -> int:
        return position + struct.unpack_from("<I", payload, position)[0]

    def string(position: int) -> str:
        start = target(position)
        size = struct.unpack_from("<I", payload, start)[0]
        return payload[start + 4 : start + 4 + size].decode("utf-8")

    def strings(position: int) -> list[str]:
        vector = target(position)
        size = struct.unpack_from("<I", payload, vector)[0]
        return [string(vector + 4 + index * 4) for index in range(size)]

    network_position = field(root, vtable, 9)
    assert network_position is not None
    network = target(network_position)
    network_vtable = network - struct.unpack_from("<i", payload, network)[0]
    egress_position = field(network, network_vtable, 1)
    assert egress_position is not None
    egress = target(egress_position)
    egress_vtable = egress - struct.unpack_from("<i", payload, egress)[0]
    default_action = field(egress, egress_vtable, 0)
    return {
        "identifier": payload[4:8],
        "version": string(field(root, vtable, 0)),
        "app_container": bool(payload[field(root, vtable, 1)]),
        "disallow_win32k": bool(payload[field(root, vtable, 3)]),
        "least_privilege": bool(payload[field(root, vtable, 5)]),
        "read_write": strings(field(root, vtable, 7)),
        "read_only": strings(field(root, vtable, 8)),
        "egress_default": 0 if default_action is None else payload[default_action],
    }


class SboxPolicyEncodingTests(unittest.TestCase):
    def test_matches_public_schema_compiler_golden_buffer(self) -> None:
        policy = WindowsSandboxPolicy(
            read_only_roots=(Path(r"C:\SERA\T"),),
            read_write_roots=(Path(r"C:\SERA\W"),),
        )

        encoded = encode_sbox_policy(policy)

        self.assertEqual(encoded, GOLDEN_SBOX_0_1_0)
        self.assertEqual(encoded[4:8], b"SBOX")
        self.assertEqual(policy.schema_version, SBOX_SCHEMA_VERSION)
        self.assertEqual(
            decode_sbox_fixture(encoded),
            {
                "identifier": b"SBOX",
                "version": "0.1.0",
                "app_container": True,
                "disallow_win32k": True,
                "least_privilege": True,
                "read_write": [r"C:\SERA\W"],
                "read_only": [r"C:\SERA\T"],
                "egress_default": 0,
            },
        )

    def test_policy_is_closed_to_unknown_versions_and_relative_roots(self) -> None:
        with self.assertRaises(ValueError):
            WindowsSandboxPolicy(schema_version="0.2.0")
        with self.assertRaises(ValueError):
            WindowsSandboxPolicy(read_only_roots=(Path("relative"),))

    def test_policy_rejects_overlapping_read_only_and_read_write_grants(self) -> None:
        root = Path(r"C:\SERA\T")
        with self.assertRaises(ValueError):
            WindowsSandboxPolicy(read_only_roots=(root,), read_write_roots=(root,))
        with self.assertRaises(ValueError):
            WindowsSandboxPolicy(
                read_only_roots=(Path(r"C:\SERA"),),
                read_write_roots=(Path(r"C:\SERA\W"),),
            )

    def test_environment_allowlist_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            _bounded_environment(tuple((f"SERA_{index}", "x") for index in range(65)))
        with self.assertRaises(ValueError):
            _bounded_environment((("SERA_VALUE", "x" * 32769),))


class BackendContractTests(unittest.TestCase):
    def test_adapter_satisfies_provider_neutral_protocol(self) -> None:
        self.assertIsInstance(WindowsSandboxBackend(), SandboxBackend)

    def test_worktree_plus_cwd_is_never_strong_execution(self) -> None:
        result = weak_execution_classification()
        self.assertEqual(result.status, "INDETERMINATE")
        self.assertEqual(result.maximum_assurance, "CHECKPOINT_OBSERVED")
        self.assertFalse(result.os_sandbox_applied)


@unittest.skipUnless(os.name == "nt", "the strong backend exists only on Windows")
class RealWindowsEnforcementGateTests(unittest.TestCase):
    def test_host_exposes_the_exact_required_api_and_capabilities(self) -> None:
        capability = probe_windows_sandbox()
        self.assertTrue(capability.available, capability.reason)
        self.assertEqual(capability.backend, BACKEND_NAME)
        self.assertTrue(capability.create_process_export)
        self.assertTrue(capability.query_support_export)
        self.assertEqual(capability.capabilities & 0x7, 0x7)

    def test_real_appcontainer_bfs_denies_every_mandatory_attack(self) -> None:
        report = run_enforcement_preflight(REPOSITORY_ROOT)

        self.assertEqual(report.backend, BACKEND_NAME)
        self.assertFalse(report.fallback_used)
        self.assertTrue(report.passed, report.to_json())
        self.assertEqual(report.p0_implementation, "PASS")
        self.assertEqual(report.backend_status, "IMPLEMENTABLE")
        self.assertEqual(report.effective_status, "VERIFIED")
        self.assertTrue(all(attack.passed for attack in report.attacks), report.to_json())
        self.assertIn("unregistered_network", {attack.name for attack in report.attacks})
        self.assertIn("descendant_repeats_network_denial", {attack.name for attack in report.attacks})
        self.assertIn("sandbox_teardown", {attack.name for attack in report.attacks})
        self.assertEqual(
            report.target_identity["directory_identity_before"],
            report.target_identity["directory_identity_after"],
        )
        self.assertEqual(report.target_identity["marker_hash_before"], report.target_identity["marker_hash_after"])
        self.assertEqual(report.target_identity["content_hash_before"], report.target_identity["content_hash_after"])
        self.assertEqual(
            report.workspace_identity["directory_identity_before"],
            report.workspace_identity["directory_identity_after"],
        )
        self.assertNotIn(str(REPOSITORY_ROOT), report.child_argv)
        self.assertNotIn(str(REPOSITORY_ROOT), report.child_environment)
        self.assertNotIn(str(REPOSITORY_ROOT), report.grants)
        self.assertNotIn(str(REPOSITORY_ROOT), "\n".join(report.diagnostics))


if __name__ == "__main__":
    unittest.main()
