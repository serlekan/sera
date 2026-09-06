"""Provider-neutral provenance primitives.

Only the sandbox protocol required by the I1-T04A capability gate lives here
for now. Repository-identity behavior belongs to I1-T05 and is deliberately
not implemented early.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


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
