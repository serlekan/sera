"""Provider-neutral repository and execution provenance primitives."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from .core import SeraError
from .schemas import sha256_domain


_MAX_REMOTE_CHARS = 4096
_MAX_REMOTE_IDENTITIES = 32
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_CONFIGURED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SCP_REMOTE_RE = re.compile(r"^(?:[^@/:\\\s]+@)?([^/:\\\s]+):(.+)$")


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
