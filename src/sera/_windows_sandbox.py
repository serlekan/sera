"""Bounded Windows AppContainer + Bound File System sandbox adapter.

The module implements only the public ``SBOX`` 0.1.0 FlatBuffer fields needed
by SERA. It has no generic table/field dispatch and no runtime dependency on a
FlatBuffers package. Unknown policy versions and path overlap fail closed.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import platform
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .provenance import SandboxLaunchRequest, SandboxLaunchResult


BACKEND_NAME = "windows_appcontainer_bfs_v1"
SBOX_SCHEMA_VERSION = "0.1.0"
SBOX_FILE_IDENTIFIER = b"SBOX"
SBOX_CONTRACT_FINGERPRINT = hashlib.sha256(
    b"SBOX:0.1.0:SandboxSpec[version=0,app_container=1,disallow_win32k=3,"
    b"least_privilege=5,fs_read_write=7,fs_read_only=8,network_policy=9];"
    b"NetworkPolicy[egress=1];EndpointPolicy[default_action=0:DENY]"
).hexdigest()

CAP_CREATE_PROCESS = 0x1
CAP_FILESYSTEM_DENY = 0x2
CAP_NETWORK_PROXY = 0x4
REQUIRED_CAPABILITIES = CAP_CREATE_PROCESS | CAP_FILESYSTEM_DENY | CAP_NETWORK_PROXY


def _normal_path(path: Path) -> str:
    if not path.is_absolute():
        raise ValueError(f"sandbox root must be absolute: {path}")
    value = os.path.normpath(str(path.absolute()))
    if "\0" in value:
        raise ValueError("sandbox root cannot contain NUL")
    return value


def _paths_intersect(left: str, right: str) -> bool:
    try:
        common = os.path.commonpath((left, right)).casefold()
    except ValueError:
        return False
    return common in {left.casefold(), right.casefold()}


@dataclass(frozen=True)
class WindowsSandboxPolicy:
    """The complete and closed SERA subset of ``SandboxSpec``."""

    read_only_roots: tuple[Path, ...] = ()
    read_write_roots: tuple[Path, ...] = ()
    schema_version: str = SBOX_SCHEMA_VERSION
    app_container: bool = True
    disallow_win32k: bool = True
    least_privilege: bool = True
    network_default_deny: bool = True

    def __post_init__(self) -> None:
        if self.schema_version != SBOX_SCHEMA_VERSION:
            raise ValueError(f"unsupported SBOX schema version: {self.schema_version!r}")
        if not (self.app_container and self.disallow_win32k and self.least_privilege):
            raise ValueError("the strong backend requires AppContainer, Win32k denial, and least privilege")
        if not self.network_default_deny:
            raise ValueError("the T04A backend supports only default-deny network policy")
        read_only = {_normal_path(path).casefold() for path in self.read_only_roots}
        read_write = {_normal_path(path).casefold() for path in self.read_write_roots}
        overlap = {
            f"{read_path} <-> {write_path}"
            for read_path in read_only
            for write_path in read_write
            if _paths_intersect(read_path, write_path)
        }
        if overlap:
            raise ValueError(f"sandbox roots cannot be both read-only and read-write: {sorted(overlap)!r}")

    def canonical_grants(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "schema_fingerprint": SBOX_CONTRACT_FINGERPRINT,
            "app_container": self.app_container,
            "disallow_win32k": self.disallow_win32k,
            "least_privilege": self.least_privilege,
            "network_default": "deny",
            "read_only_roots": sorted((_normal_path(path) for path in self.read_only_roots), key=str.casefold),
            "read_write_roots": sorted((_normal_path(path) for path in self.read_write_roots), key=str.casefold),
        }


def _append_string(buffer: bytearray, value: str) -> int:
    encoded = value.encode("utf-8")
    start = len(buffer)
    buffer.extend(struct.pack("<I", len(encoded)))
    buffer.extend(encoded)
    buffer.append(0)
    while len(buffer) % 4:
        buffer.append(0)
    return start


def _append_string_vector(buffer: bytearray, values: tuple[str, ...]) -> tuple[int, list[int]]:
    start = len(buffer)
    buffer.extend(struct.pack("<I", len(values)))
    fields: list[int] = []
    for _ in values:
        fields.append(len(buffer))
        buffer.extend(b"\0\0\0\0")
    return start, fields


def encode_sbox_policy(policy: WindowsSandboxPolicy) -> bytes:
    """Encode the exact SBOX 0.1.0 subset; no unknown-field path exists."""

    grants = policy.canonical_grants()
    read_only = tuple(grants["read_only_roots"])
    read_write = tuple(grants["read_write_roots"])

    # Root offset + identifier, root vtable, root table, NetworkPolicy vtable
    # and table, then an empty EndpointPolicy table (default action DENY).
    buffer = bytearray.fromhex(
        "2000000053424f58"
        "1800180014001300000012000000110000000c0008000400"
        "180000000000000000000000000000000001010100000000"
        "08000800000004000800000008000000"
        "0400040004000000"
    )
    read_only_vector, read_only_fields = _append_string_vector(buffer, read_only)
    read_write_vector, read_write_fields = _append_string_vector(buffer, read_write)

    read_only_strings = [_append_string(buffer, value) for value in read_only]
    read_write_strings = [_append_string(buffer, value) for value in read_write]
    version_string = _append_string(buffer, policy.schema_version)

    def put_uoffset(field: int, target: int) -> None:
        if target <= field:
            raise ValueError("SBOX encoder produced a non-forward offset")
        struct.pack_into("<I", buffer, field, target - field)

    put_uoffset(36, 64)  # SandboxSpec.network_policy
    put_uoffset(40, read_only_vector)
    put_uoffset(44, read_write_vector)
    put_uoffset(52, version_string)
    for field, target in zip(read_only_fields, read_only_strings, strict=True):
        put_uoffset(field, target)
    for field, target in zip(read_write_fields, read_write_strings, strict=True):
        put_uoffset(field, target)
    return bytes(buffer)


def sandbox_policy_hash(policy: WindowsSandboxPolicy) -> str:
    return hashlib.sha256(b"sera:sbox-policy:v1\0" + encode_sbox_policy(policy)).hexdigest()


@dataclass(frozen=True)
class WindowsSandboxCapability:
    backend: str
    available: bool
    reason: str
    os_identity: str
    dll_path: str
    dll_size: int | None
    dll_sha256: str | None
    dll_mtime_ns: int | None
    create_process_export: bool
    query_support_export: bool
    query_succeeded: bool
    capabilities: int
    last_error: int
    schema_version: str = SBOX_SCHEMA_VERSION
    schema_fingerprint: str = SBOX_CONTRACT_FINGERPRINT


def _system_dll_path() -> Path:
    if os.name != "nt":
        return Path(r"C:\Windows\System32\processmodel.dll")
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    get_system_directory = kernel.GetSystemDirectoryW
    get_system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_system_directory.restype = wintypes.UINT
    buffer = ctypes.create_unicode_buffer(32768)
    length = get_system_directory(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError(ctypes.get_last_error(), "GetSystemDirectoryW failed")
    return Path(buffer.value) / "processmodel.dll"


def probe_windows_sandbox() -> WindowsSandboxCapability:
    """Query the real host API; this function never claims a fallback."""

    dll_path = _system_dll_path()
    os_identity = f"{platform.platform()}|{platform.version()}"
    if os.name != "nt":
        return WindowsSandboxCapability(
            BACKEND_NAME, False, "Windows is required", os_identity, str(dll_path), None, None, None,
            False, False, False, 0, 0,
        )
    try:
        payload = dll_path.read_bytes()
        stat = dll_path.stat()
        dll = ctypes.WinDLL(str(dll_path), use_last_error=True)
    except OSError as exc:
        return WindowsSandboxCapability(
            BACKEND_NAME, False, f"processmodel.dll unavailable: {exc}", os_identity, str(dll_path), None, None,
            None, False, False, False, 0, getattr(exc, "winerror", 0) or 0,
        )
    create_export = hasattr(dll, "Experimental_CreateProcessInSandbox")
    query_export = hasattr(dll, "Experimental_QuerySandboxSupport")
    capabilities = ctypes.c_uint64(0)
    query_succeeded = False
    last_error = 0
    if query_export:
        query = dll.Experimental_QuerySandboxSupport
        query.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
        query.restype = ctypes.c_int
        query_succeeded = bool(query(ctypes.byref(capabilities)))
        last_error = ctypes.get_last_error()
    available = (
        create_export
        and query_export
        and query_succeeded
        and capabilities.value & REQUIRED_CAPABILITIES == REQUIRED_CAPABILITIES
    )
    reason = "required exports and capability bits are present" if available else (
        f"required API unavailable: exports=create:{create_export},query:{query_export}; "
        f"query={query_succeeded}; capabilities=0x{capabilities.value:x}; last_error={last_error}"
    )
    return WindowsSandboxCapability(
        BACKEND_NAME,
        available,
        reason,
        os_identity,
        str(dll_path),
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        stat.st_mtime_ns,
        create_export,
        query_export,
        query_succeeded,
        capabilities.value,
        last_error,
    )


if os.name == "nt":
    from ctypes import wintypes

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]


def _bounded_environment(values: tuple[tuple[str, str], ...]) -> ctypes.Array[ctypes.c_wchar]:
    if len(values) > 64:
        raise ValueError("sandbox environment exceeds 64-entry allowlist bound")
    seen: set[str] = set()
    entries: list[str] = []
    for key, value in sorted(values, key=lambda item: item[0].casefold()):
        if not key or len(key) > 256 or len(value) > 32768 or "=" in key or "\0" in key or "\0" in value:
            raise ValueError("invalid environment entry")
        folded = key.casefold()
        if folded in seen:
            raise ValueError(f"duplicate environment key: {key}")
        seen.add(folded)
        entries.append(f"{key}={value}")
    block = "\0".join(entries) + "\0\0"
    if len(block) > 1024 * 1024:
        raise ValueError("sandbox environment exceeds one-megacharacter bound")
    return ctypes.create_unicode_buffer(block)


def _appcontainer_sid(identity: str) -> str | None:
    if os.name != "nt":
        return None
    userenv = ctypes.WinDLL("userenv.dll", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32.dll", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    sid = ctypes.c_void_p()
    derive = userenv.DeriveAppContainerSidFromAppContainerName
    derive.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    derive.restype = ctypes.c_long
    if derive(identity, ctypes.byref(sid)) != 0:
        return None
    value = wintypes.LPWSTR()
    convert = advapi.ConvertSidToStringSidW
    convert.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    convert.restype = wintypes.BOOL
    free_sid = advapi.FreeSid
    free_sid.argtypes = [ctypes.c_void_p]
    free_sid.restype = ctypes.c_void_p
    local_free = kernel.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p
    try:
        if not convert(sid, ctypes.byref(value)):
            return None
        return value.value
    finally:
        if value:
            local_free(ctypes.cast(value, ctypes.c_void_p))
        free_sid(sid)


def _delete_appcontainer_profile(identity: str) -> str:
    if os.name != "nt":
        return "not-applicable"
    userenv = ctypes.WinDLL("userenv.dll", use_last_error=True)
    delete = userenv.DeleteAppContainerProfile
    delete.argtypes = [wintypes.LPCWSTR]
    delete.restype = ctypes.c_long
    result = int(delete(identity))
    return "deleted" if result == 0 else f"DeleteAppContainerProfile HRESULT=0x{result & 0xFFFFFFFF:08x}"


def _hold_root_handles(roots: tuple[Path, ...]) -> list[int]:
    """Hold non-inheritable root handles without DELETE sharing during a run."""

    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handles: list[int] = []
    invalid = ctypes.c_void_p(-1).value
    for root in roots:
        # GENERIC_READ (rather than FILE_READ_ATTRIBUTES alone) makes the
        # no-FILE_SHARE_DELETE contract effective for directory renames.
        handle = create_file(str(root), 0x80000000, 0x1 | 0x2, None, 3, 0x02000000, None)
        if handle == invalid:
            error = ctypes.get_last_error()
            for opened in handles:
                close_handle(opened)
            raise OSError(error, f"could not hold sandbox root identity: {root}")
        handles.append(handle)
    return handles


def _close_handles(handles: list[int]) -> None:
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    for handle in handles:
        kernel.CloseHandle(handle)


class WindowsSandboxBackend:
    """Launch exact argv through ``Experimental_CreateProcessInSandbox``."""

    backend_name = BACKEND_NAME

    def launch(self, request: SandboxLaunchRequest) -> SandboxLaunchResult:
        capability = probe_windows_sandbox()
        if not capability.available:
            return SandboxLaunchResult(
                BACKEND_NAME, "INDETERMINATE", False, False, None, None, None, None,
                capability.reason, "not-attempted",
            )
        if not request.argv or request.timeout_seconds <= 0:
            raise ValueError("sandbox request requires argv and a positive timeout")
        policy = WindowsSandboxPolicy(request.read_only_roots, request.read_write_roots)
        encoded = encode_sbox_policy(policy)
        policy_hash = sandbox_policy_hash(policy)
        command_line = subprocess.list2cmdline(list(request.argv))
        command_buffer = ctypes.create_unicode_buffer(command_line)
        environment = _bounded_environment(request.environment)
        spec = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(_STARTUPINFOW)
        process = _PROCESS_INFORMATION()

        try:
            root_handles = _hold_root_handles(request.read_only_roots + request.read_write_roots)
        except OSError as exc:
            return SandboxLaunchResult(
                BACKEND_NAME, "INDETERMINATE", False, False, None, None, policy_hash, None,
                str(exc), "not-created",
            )

        dll = ctypes.WinDLL(capability.dll_path, use_last_error=True)
        create = dll.Experimental_CreateProcessInSandbox
        create.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_wchar),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOW),
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_uint32,
            ctypes.POINTER(_PROCESS_INFORMATION),
        ]
        create.restype = wintypes.BOOL
        creation_flags = 0x00000004 | 0x00000400 | 0x08000000
        # CREATE_SUSPENDED closes the descendant race before later Job-control
        # integration; T04A resumes only after the OS has returned valid handles.
        ok = bool(
            create(
                None,
                command_buffer,
                None,
                None,
                False,
                creation_flags,
                ctypes.cast(environment, ctypes.c_void_p),
                str(request.working_directory),
                ctypes.byref(startup),
                request.identity,
                spec,
                len(encoded),
                ctypes.byref(process),
            )
        )
        if not ok:
            error = ctypes.get_last_error()
            kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            if process.hThread:
                kernel.CloseHandle(process.hThread)
            if process.hProcess:
                kernel.CloseHandle(process.hProcess)
            _close_handles(root_handles)
            teardown = _delete_appcontainer_profile(request.identity)
            return SandboxLaunchResult(
                BACKEND_NAME, "INDETERMINATE", False, False, None, None, policy_hash, None,
                f"Experimental_CreateProcessInSandbox failed with Win32 error {error}", teardown,
            )

        kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.TerminateProcess.restype = wintypes.BOOL
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        sandbox_identity = _appcontainer_sid(request.identity)
        exit_code = wintypes.DWORD(0)
        error_text: str | None = None
        try:
            if kernel.ResumeThread(process.hThread) == 0xFFFFFFFF:
                error_text = f"ResumeThread failed with Win32 error {ctypes.get_last_error()}"
                kernel.TerminateProcess(process.hProcess, 0xDEAD)
            wait_ms = min(int(request.timeout_seconds * 1000), 0xFFFFFFFE)
            wait_result = kernel.WaitForSingleObject(process.hProcess, wait_ms)
            if wait_result != 0:
                kernel.TerminateProcess(process.hProcess, 0xDEAD)
                kernel.WaitForSingleObject(process.hProcess, 5000)
                error_text = f"sandboxed process wait failed or timed out: 0x{wait_result:08x}"
            if not kernel.GetExitCodeProcess(process.hProcess, ctypes.byref(exit_code)):
                error_text = f"GetExitCodeProcess failed with Win32 error {ctypes.get_last_error()}"
        finally:
            kernel.CloseHandle(process.hThread)
            kernel.CloseHandle(process.hProcess)
            _close_handles(root_handles)
        teardown = _delete_appcontainer_profile(request.identity)
        if error_text is None and exit_code.value != 0:
            error_text = f"sandboxed process exited with code {exit_code.value}"
        status = "COMPLETED" if error_text is None and exit_code.value == 0 else "INDETERMINATE"
        return SandboxLaunchResult(
            BACKEND_NAME,
            status,
            True,
            False,
            int(process.dwProcessId),
            int(exit_code.value),
            policy_hash,
            sandbox_identity,
            error_text,
            teardown,
        )


@dataclass(frozen=True)
class WeakExecutionClassification:
    status: str = "INDETERMINATE"
    maximum_assurance: str = "CHECKPOINT_OBSERVED"
    os_sandbox_applied: bool = False


def weak_execution_classification() -> WeakExecutionClassification:
    return WeakExecutionClassification()


@dataclass(frozen=True)
class ProbeAttack:
    name: str
    passed: bool
    observation: str


@dataclass(frozen=True)
class EnforcementPreflightReport:
    backend: str
    p0_implementation: str
    backend_status: str
    effective_status: str
    proof_property: str
    fallback_used: bool
    capability: WindowsSandboxCapability
    policy_hash: str | None
    sandbox_identity: str | None
    process_id: int | None
    exit_code: int | None
    target_identity: dict[str, object]
    workspace_identity: dict[str, object]
    child_argv: str
    child_environment: str
    grants: str
    attacks: tuple[ProbeAttack, ...]
    diagnostics: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return (
            self.p0_implementation == "PASS"
            and self.backend_status == "IMPLEMENTABLE"
            and self.effective_status == "VERIFIED"
            and not self.fallback_used
            and bool(self.attacks)
            and all(attack.passed for attack in self.attacks)
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _probe_script(encoded_configuration: str) -> str:
    return f'''from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

CONFIG = json.loads(base64.b64decode({encoded_configuration!r}).decode("utf-8"))

def attempt(name, operation):
    try:
        value = operation()
        return {{"name": name, "succeeded": True, "value": str(value)}}
    except Exception as exc:
        return {{"name": name, "succeeded": False, "error_type": type(exc).__name__,
                "winerror": getattr(exc, "winerror", None), "errno": getattr(exc, "errno", None)}}

def attacks():
    original = os.getcwd()
    values = [
        attempt("baseline_read_target", lambda: Path(CONFIG["target_marker"]).read_text(encoding="utf-8")),
        attempt("change_cwd_ungranted", lambda: os.chdir(CONFIG["ungranted_cwd"])),
        attempt("open_governed_checkout", lambda: Path(CONFIG["governed_marker"]).read_text(encoding="utf-8")),
        attempt("open_separate_clone", lambda: Path(CONFIG["clone_marker"]).read_text(encoding="utf-8")),
        attempt("read_other_repository", lambda: Path(CONFIG["other_marker"]).read_text(encoding="utf-8")),
        attempt("connect_unregistered_network", lambda: socket.create_connection(("127.0.0.1", CONFIG["network_port"]), timeout=2).close()),
        attempt("access_external_alias", lambda: Path(CONFIG["alias_marker"]).read_text(encoding="utf-8")),
        attempt("mutate_non_mutating_target", lambda: Path(CONFIG["target_marker"]).write_text("mutated", encoding="utf-8")),
        attempt("write_approved_output", lambda: Path(CONFIG["write_probe"]).write_text("ok", encoding="utf-8")),
    ]
    os.chdir(original)
    return values

if len(sys.argv) == 2 and sys.argv[1] == "--child":
    Path(CONFIG["child_result"]).write_text(json.dumps(attacks(), sort_keys=True), encoding="utf-8")
else:
    Path(CONFIG["ready"]).write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 15
    while not Path(CONFIG["go"]).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    parent_results = attacks()
    child = subprocess.run([sys.executable, __file__, "--child"], check=False)
    Path(CONFIG["parent_result"]).write_text(
        json.dumps({{"attacks": parent_results, "child_exit_code": child.returncode}}, sort_keys=True),
        encoding="utf-8",
    )
'''


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_identity(path: Path) -> str:
    state = path.stat(follow_symlinks=False)
    return f"volume:{state.st_dev}:file:{state.st_ino}"


def _directory_content_hash(path: Path) -> str:
    digest = hashlib.sha256(b"sera:directory-content:v1\0")
    files = sorted(
        (candidate for candidate in path.rglob("*") if candidate.is_file() and not candidate.is_symlink()),
        key=lambda candidate: candidate.relative_to(path).as_posix(),
    )
    for candidate in files:
        relative = candidate.relative_to(path).as_posix().encode("utf-8")
        payload = candidate.read_bytes()
        digest.update(struct.pack(">I", len(relative)))
        digest.update(relative)
        digest.update(struct.pack(">Q", len(payload)))
        digest.update(payload)
    return digest.hexdigest()


def _git_value(root: Path, expression: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", expression],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _denied(observation: dict[str, object]) -> bool:
    return not observation.get("succeeded", False) and (
        observation.get("error_type") == "PermissionError"
        or observation.get("winerror") in {5, 1260}
        or observation.get("errno") == 13
    )


def _network_denied(observation: dict[str, object]) -> bool:
    return not observation.get("succeeded", False) and (
        observation.get("winerror") == 10013 or observation.get("errno") == 13
    )


def _prepare_git_fixture(path: Path, marker_name: str) -> Path:
    path.mkdir(parents=True)
    marker = path / marker_name
    marker.write_text(f"fixture:{marker_name}\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "init", "--quiet"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "add", "--", marker.name], check=True, capture_output=True)
    subprocess.run(
        [
            "git", "-C", str(path), "-c", "user.name=SERA Preflight", "-c",
            "user.email=sera-preflight.invalid", "commit", "--quiet", "-m", "fixture",
        ],
        check=True,
        capture_output=True,
    )
    return marker


def _has_reparse_point(root: Path) -> bool:
    def is_reparse(path: Path) -> bool:
        try:
            return bool(os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except (AttributeError, OSError):
            return path.is_symlink()

    if is_reparse(root):
        return True
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        if any(is_reparse(base / name) for name in [*names, *files]):
            return True
    return False


def validate_execution_root(root: Path) -> None:
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("execution root must be an existing absolute directory")
    if _has_reparse_point(root):
        raise ValueError("execution root contains a reparse point")


def _create_directory_alias(link: Path, target: Path) -> tuple[bool, str]:
    try:
        os.symlink(target, link, target_is_directory=True)
        return True, "symbolic link"
    except OSError as symlink_error:
        if os.name != "nt":
            return False, f"symlink failed: {symlink_error}"
        command = [str(_system_dll_path().parent / "cmd.exe"), "/d", "/c", "mklink", "/J", str(link), str(target)]
        completed = subprocess.run(command, check=False, capture_output=True, text=True, shell=False)
        if completed.returncode == 0 and link.exists():
            return True, "directory junction"
        return False, f"symlink={getattr(symlink_error, 'winerror', None)};junction_exit={completed.returncode}"


def _failure_report(
    capability: WindowsSandboxCapability,
    diagnostics: list[str],
    attacks: list[ProbeAttack] | None = None,
    launch: SandboxLaunchResult | None = None,
) -> EnforcementPreflightReport:
    return EnforcementPreflightReport(
        BACKEND_NAME,
        "FAIL",
        "PLAN_SPEC_BLOCKER",
        "INDETERMINATE",
        "AppContainer+BFS confinement was not proven",
        False if launch is None else launch.fallback_used,
        capability,
        None if launch is None else launch.policy_hash,
        None if launch is None else launch.sandbox_identity,
        None if launch is None else launch.process_id,
        None if launch is None else launch.exit_code,
        {},
        {},
        "",
        "",
        "",
        tuple(attacks or ()),
        tuple(diagnostics),
    )


def run_enforcement_preflight(governed_checkout: Path) -> EnforcementPreflightReport:
    """Execute the real T04A denial matrix and return a fail-closed report."""

    capability = probe_windows_sandbox()
    if not capability.available:
        return _failure_report(capability, [capability.reason])
    governed_checkout = governed_checkout.resolve(strict=True)
    diagnostics: list[str] = []
    attacks: list[ProbeAttack] = []
    with tempfile.TemporaryDirectory(prefix="sera-enforcement-") as temporary:
        root = Path(temporary)
        target = root / "target"
        workspace = root / "workspace"
        clone = root / "separate-clone"
        other = root / "other-repository"
        ungranted_cwd = root / "ungranted-cwd"
        alias = root / "external-target-alias"
        for directory in (workspace, ungranted_cwd):
            directory.mkdir()
        target_marker = _prepare_git_fixture(target, "target-marker.txt")
        subprocess.run(
            ["git", "clone", "--quiet", "--no-hardlinks", str(target), str(clone)],
            check=True,
            capture_output=True,
        )
        clone_marker = clone / target_marker.name
        other_marker = _prepare_git_fixture(other, "other-marker.txt")
        governed_marker = governed_checkout / "pyproject.toml"
        if not governed_marker.is_file():
            return _failure_report(capability, ["governed checkout marker is unavailable"])
        target_hash_before = _hash_file(target_marker)
        target_directory_before = _directory_identity(target)
        workspace_directory_before = _directory_identity(workspace)
        target_head_before = _git_value(target, "HEAD")
        target_tree_before = _git_value(target, "HEAD^{tree}")
        network_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        network_listener.bind(("127.0.0.1", 0))
        network_listener.listen(4)

        alias_created, alias_kind = _create_directory_alias(alias, target)
        if not alias_created:
            diagnostics.append(f"external alias creation failed: {alias_kind}")

        reparse_root = root / "reparse-substitution"
        reparse_root.mkdir()
        reparse_detected = False
        reparse_created, reparse_kind = _create_directory_alias(reparse_root / "escape", governed_checkout)
        if reparse_created:
            try:
                validate_execution_root(reparse_root)
            except ValueError:
                reparse_detected = True
        else:
            diagnostics.append(f"reparse substitution creation failed: {reparse_kind}")
        attacks.append(ProbeAttack("reparse_point_substitution", reparse_detected, f"{reparse_kind} detected" if reparse_detected else "not executed or not detected"))

        validate_execution_root(target)
        validate_execution_root(workspace)
        configuration = {
            "target_marker": str(target_marker),
            "governed_marker": str(governed_marker),
            "clone_marker": str(clone_marker),
            "other_marker": str(other_marker),
            "network_port": network_listener.getsockname()[1],
            "ungranted_cwd": str(ungranted_cwd),
            "alias_marker": str(alias / target_marker.name),
            "write_probe": str(workspace / "write-probe.txt"),
            "ready": str(workspace / "ready"),
            "go": str(workspace / "go"),
            "parent_result": str(workspace / "parent-result.json"),
            "child_result": str(workspace / "child-result.json"),
        }
        encoded_configuration = base64.b64encode(
            json.dumps(configuration, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        script = target / "probe.py"
        script.write_text(_probe_script(encoded_configuration), encoding="utf-8")
        target_content_before = _directory_content_hash(target)

        python_executable = Path(sys.executable).resolve(strict=True)
        python_root = python_executable.parent.parent
        system_root = _system_dll_path().parent.parent.resolve(strict=True)
        environment = (
            ("ALLUSERSPROFILE", str(workspace)),
            ("APPDATA", str(workspace)),
            ("ComSpec", str(system_root / "System32" / "cmd.exe")),
            ("HOMEDRIVE", workspace.drive),
            ("HOMEPATH", str(workspace)[len(workspace.drive) :]),
            ("LOCALAPPDATA", str(workspace)),
            ("PATH", os.pathsep.join((str(python_executable.parent), str(system_root / "System32")))),
            ("ProgramData", str(workspace)),
            ("PYTHONIOENCODING", "utf-8"),
            ("SystemDrive", system_root.drive),
            ("SystemRoot", str(system_root)),
            ("TEMP", str(workspace)),
            ("TMP", str(workspace)),
            ("WINDIR", str(system_root)),
        )
        request = SandboxLaunchRequest(
            argv=(str(python_executable), "-I", "-S", str(script)),
            working_directory=target,
            read_only_roots=(target, python_root, system_root),
            read_write_roots=(workspace,),
            environment=environment,
            identity=f"sera-t04a-{uuid.uuid4().hex}",
            timeout_seconds=30,
        )
        policy = WindowsSandboxPolicy(request.read_only_roots, request.read_write_roots)
        child_argv = json.dumps(request.argv, separators=(",", ":"))
        child_environment = json.dumps(dict(request.environment), separators=(",", ":"), sort_keys=True)
        grants = json.dumps(policy.canonical_grants(), separators=(",", ":"), sort_keys=True)

        launch_box: list[SandboxLaunchResult] = []
        thread = threading.Thread(target=lambda: launch_box.append(WindowsSandboxBackend().launch(request)), daemon=True)
        thread.start()
        ready = workspace / "ready"
        deadline = time.monotonic() + 12
        while not ready.exists() and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)

        renamed = root / "renamed-target"
        rename_prevented = False
        rename_error: int | None = None
        if ready.exists():
            try:
                target.rename(renamed)
                renamed.rename(target)
            except OSError as exc:
                rename_error = getattr(exc, "winerror", None)
                rename_prevented = rename_error == 32
            (workspace / "go").write_text("go", encoding="utf-8")
        else:
            diagnostics.append("sandboxed process did not publish its ready observation")
        thread.join(timeout=35)
        if thread.is_alive() or not launch_box:
            return _failure_report(capability, diagnostics + ["sandbox launch did not complete"], attacks)
        launch = launch_box[0]
        attacks.append(ProbeAttack("renamed_alternate_root_after_launch", rename_prevented, f"rename prevented with sharing violation {rename_error}" if rename_prevented else "rename succeeded or was not prevented by the held-root contract"))

        parent_result_path = workspace / "parent-result.json"
        child_result_path = workspace / "child-result.json"
        if launch.status != "COMPLETED" or not parent_result_path.is_file() or not child_result_path.is_file():
            diagnostics.append(launch.error or "sandboxed probe did not produce both result files")
            return _failure_report(capability, diagnostics, attacks, launch)
        parent_payload = json.loads(parent_result_path.read_text(encoding="utf-8"))
        child_payload = json.loads(child_result_path.read_text(encoding="utf-8"))
        parent_observations = {item["name"]: item for item in parent_payload["attacks"]}
        child_observations = {item["name"]: item for item in child_payload}
        network_listener.close()

        attacks.extend(
            (
                ProbeAttack("change_cwd_ungranted", _denied(parent_observations["change_cwd_ungranted"]), "denied" if _denied(parent_observations["change_cwd_ungranted"]) else "succeeded"),
                ProbeAttack("open_governed_checkout", _denied(parent_observations["open_governed_checkout"]), "denied" if _denied(parent_observations["open_governed_checkout"]) else "succeeded"),
                ProbeAttack("open_separate_clone", _denied(parent_observations["open_separate_clone"]), "denied" if _denied(parent_observations["open_separate_clone"]) else "succeeded"),
                ProbeAttack("read_other_repository", _denied(parent_observations["read_other_repository"]), "denied" if _denied(parent_observations["read_other_repository"]) else "succeeded"),
                ProbeAttack("descendant_repeats_denials", parent_payload["child_exit_code"] == 0 and all(_denied(child_observations[name]) for name in ("change_cwd_ungranted", "open_governed_checkout", "open_separate_clone", "read_other_repository")), "child remained confined"),
                ProbeAttack("unregistered_network", _network_denied(parent_observations["connect_unregistered_network"]), "denied" if _network_denied(parent_observations["connect_unregistered_network"]) else "network connection was not access-denied"),
                ProbeAttack("descendant_repeats_network_denial", _network_denied(child_observations["connect_unregistered_network"]), "child network access denied" if _network_denied(child_observations["connect_unregistered_network"]) else "child network connection was not access-denied"),
            )
        )
        alias_observation = parent_observations["access_external_alias"]
        alias_same_identity = alias_created and os.path.samefile(alias / target_marker.name, target_marker)
        alias_passed = _denied(alias_observation) or bool(alias_observation.get("succeeded")) and alias_same_identity
        attacks.append(ProbeAttack("unintended_external_alias", alias_passed, "denied" if _denied(alias_observation) else "same file identity proven" if alias_same_identity else "unproven alias access"))
        mutation_denied = _denied(parent_observations["mutate_non_mutating_target"])
        target_unchanged = target_marker.is_file() and _hash_file(target_marker) == target_hash_before
        attacks.append(ProbeAttack("mutate_non_mutating_target", mutation_denied and target_unchanged, f"denied={mutation_denied};post_state_unchanged={target_unchanged}"))
        baseline_read = bool(parent_observations["baseline_read_target"].get("succeeded"))
        output_write = bool(parent_observations["write_approved_output"].get("succeeded"))
        attacks.append(ProbeAttack("explicit_grants_operational", baseline_read and output_write, f"read_target={baseline_read};write_workspace={output_write}"))
        attacks.append(ProbeAttack("cwd_only_classification", weak_execution_classification().status == "INDETERMINATE", "INDETERMINATE/CHECKPOINT_OBSERVED"))

        no_checkout_disclosure = all(str(governed_checkout) not in value for value in (child_argv, child_environment, grants))
        attacks.append(ProbeAttack("governed_path_not_in_launch_channels", no_checkout_disclosure, "argv/environment/grants checked"))
        identity_observed = bool(launch.sandbox_identity)
        attacks.append(ProbeAttack("appcontainer_identity_observed", identity_observed, launch.sandbox_identity or "missing"))
        attacks.append(
            ProbeAttack(
                "sandbox_teardown",
                launch.teardown_observation == "deleted",
                launch.teardown_observation,
            )
        )
        target_identity = {
            "path_hash": hashlib.sha256(str(target).encode("utf-8")).hexdigest(),
            "directory_identity_before": target_directory_before,
            "directory_identity_after": _directory_identity(target),
            "git_head_before": target_head_before,
            "git_head_after": _git_value(target, "HEAD"),
            "git_tree_before": target_tree_before,
            "git_tree_after": _git_value(target, "HEAD^{tree}"),
            "marker_hash_before": target_hash_before,
            "marker_hash_after": _hash_file(target_marker),
            "content_hash_before": target_content_before,
            "content_hash_after": _directory_content_hash(target),
        }
        workspace_identity = {
            "path_hash": hashlib.sha256(str(workspace).encode("utf-8")).hexdigest(),
            "directory_identity_before": workspace_directory_before,
            "directory_identity_after": _directory_identity(workspace),
            "post_files": sorted(path.name for path in workspace.iterdir()),
        }
        identities_stable = (
            target_identity["directory_identity_before"] == target_identity["directory_identity_after"]
            and target_identity["git_head_before"] == target_identity["git_head_after"]
            and target_identity["git_tree_before"] == target_identity["git_tree_after"]
            and target_identity["marker_hash_before"] == target_identity["marker_hash_after"]
            and target_identity["content_hash_before"] == target_identity["content_hash_after"]
            and workspace_identity["directory_identity_before"] == workspace_identity["directory_identity_after"]
        )
        attacks.append(
            ProbeAttack(
                "target_workspace_identity_revalidated",
                identities_stable,
                "directory, commit/tree, and target marker identities revalidated",
            )
        )
        passed = all(attack.passed for attack in attacks) and launch.os_sandbox_applied and not launch.fallback_used
        return EnforcementPreflightReport(
            BACKEND_NAME,
            "PASS" if passed else "FAIL",
            "IMPLEMENTABLE" if passed else "PLAN_SPEC_BLOCKER",
            "VERIFIED" if passed else "INDETERMINATE",
            "registered AppContainer+BFS process and descendants could access only exact explicit roots",
            launch.fallback_used,
            capability,
            launch.policy_hash,
            launch.sandbox_identity,
            launch.process_id,
            launch.exit_code,
            target_identity,
            workspace_identity,
            child_argv,
            child_environment,
            grants,
            tuple(attacks),
            tuple(diagnostics),
        )
