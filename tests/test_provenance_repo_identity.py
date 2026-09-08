"""RepositoryIdentityV1 resolution and credential-safety tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from sera.core import SeraError
from sera.provenance import canonicalize_remote, repository_identity


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def domain_hash(domain: str, *parts: str) -> str:
    payload = domain.encode() + b"\x1f" + b"\x1f".join(part.encode() for part in parts)
    return hashlib.sha256(payload).hexdigest()


class RemoteCanonicalizationTests(unittest.TestCase):
    def test_supported_https_and_ssh_forms_have_one_identity(self) -> None:
        expected = "github.com/serlekan/sera"
        self.assertEqual(canonicalize_remote("https://github.com/serlekan/sera.git"), expected)
        self.assertEqual(canonicalize_remote("ssh://git@github.com/serlekan/sera.git"), expected)
        self.assertEqual(canonicalize_remote("git@github.com:serlekan/sera.git"), expected)

    def test_credentials_query_and_fragment_are_removed(self) -> None:
        remote = "https://user:pass@Example.COM/owner/repo.git?token=top-secret#private"
        result = canonicalize_remote(remote)
        self.assertEqual(result, "example.com/owner/repo")
        for secret in ("user", "pass", "token", "top-secret", "private"):
            self.assertNotIn(secret, result)

    def test_non_default_port_is_preserved_for_its_transport(self) -> None:
        self.assertEqual(
            canonicalize_remote("https://example.com:22/owner/repo.git"),
            "example.com:22/owner/repo",
        )
        self.assertEqual(
            canonicalize_remote("ssh://git@example.com:443/owner/repo.git"),
            "example.com:443/owner/repo",
        )

    def test_malformed_remote_fails_without_echoing_secret_input(self) -> None:
        secret = "do-not-echo"
        with self.assertRaises(SeraError) as caught:
            canonicalize_remote(f"https://user:pass@example.com:{secret}/repo.git?token={secret}")
        error: BaseException | None = caught.exception
        while error is not None:
            self.assertNotIn(secret, str(error))
            self.assertNotIn(secret, repr(error))
            error = error.__cause__ or error.__context__


class RepositoryIdentityTests(unittest.TestCase):
    def make_repo(self, content: str = "one") -> Path:
        root = Path(tempfile.mkdtemp(prefix="sera-repo-identity-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        git(root, "init", "-q")
        git(root, "config", "user.email", "sera-tests@example.com")
        git(root, "config", "user.name", "SERA Tests")
        (root / "tracked.txt").write_text(content, encoding="utf-8")
        git(root, "add", "tracked.txt")
        git(root, "commit", "-q", "-m", "initial")
        return root

    def test_configured_identity_wins_and_uses_domain_separated_hash(self) -> None:
        root = self.make_repo()
        git(root, "remote", "add", "origin", "https://user:pass@example.com/ignored.git?token=nope")
        configured = "sera-repository-1234"

        result = repository_identity(root, {"schema_version": 2, "repository_id": configured})

        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "strategy": "configured",
                "logical_id": domain_hash("repo-identity:configured", configured),
                "strength": "configured",
                "components": {"configured_id_hash": domain_hash("repo-identity:configured-component", configured)},
            },
        )
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("user", serialized)
        self.assertNotIn("pass", serialized)
        self.assertNotIn("token", serialized)

    def test_remote_identity_is_order_independent_and_exactly_domain_separated(self) -> None:
        root = self.make_repo()
        first = "https://example.com/z/repo.git"
        second = "git@example.com:a/repo.git"
        git(root, "remote", "add", "alpha", first)
        git(root, "remote", "add", "omega", second)
        before = repository_identity(root, {"schema_version": 1})

        git(root, "remote", "set-url", "alpha", second)
        git(root, "remote", "set-url", "omega", first)
        after = repository_identity(root, {"schema_version": 1})

        root_commit = git(root, "rev-list", "--max-parents=0", "HEAD")
        self.assertEqual(before, after)
        self.assertEqual(before["strategy"], "git_remote_root")
        self.assertEqual(before["strength"], "derived")
        self.assertEqual(
            before["logical_id"],
            domain_hash("repo-identity:remote", "example.com/a/repo", "example.com/z/repo", root_commit),
        )

    def test_different_immutable_root_commit_changes_remote_identity(self) -> None:
        first = self.make_repo("first root")
        second = self.make_repo("second root")
        for root in (first, second):
            git(root, "remote", "add", "origin", "https://example.com/owner/repo.git")
        self.assertNotEqual(
            repository_identity(first, {"schema_version": 1})["logical_id"],
            repository_identity(second, {"schema_version": 1})["logical_id"],
        )

    def test_remote_components_and_errors_never_expose_credentials(self) -> None:
        root = self.make_repo()
        secrets = ("visible-user", "visible-pass", "query-secret", "fragment-secret")
        remote = (
            "https://visible-user:visible-pass@example.com/owner/repo.git"
            "?token=query-secret#fragment-secret"
        )
        git(root, "remote", "add", "origin", remote)

        serialized = json.dumps(repository_identity(root, {"schema_version": 1}), sort_keys=True)

        for secret in secrets:
            self.assertNotIn(secret, serialized)

    def test_no_remote_or_configured_id_uses_local_only_without_raw_path(self) -> None:
        root = self.make_repo()
        result = repository_identity(root, {"schema_version": 1})
        self.assertEqual(result["strategy"], "local_git_dir")
        self.assertEqual(result["strength"], "local_only")
        self.assertNotIn(str(root), json.dumps(result, sort_keys=True))

    def test_malformed_remote_fails_closed_without_secret_diagnostics(self) -> None:
        root = self.make_repo()
        secret = "remote-secret-value"
        git(root, "remote", "add", "origin", f"bad remote?token={secret}")
        with self.assertRaises(SeraError) as caught:
            repository_identity(root, {"schema_version": 1})
        self.assertNotIn(secret, str(caught.exception))

    def test_non_repository_and_unborn_repository_fail_closed(self) -> None:
        non_repo = Path(tempfile.mkdtemp(prefix="sera-not-a-repo-"))
        self.addCleanup(shutil.rmtree, non_repo, ignore_errors=True)
        with self.assertRaises(SeraError):
            repository_identity(non_repo, {"schema_version": 1})

        unborn = Path(tempfile.mkdtemp(prefix="sera-unborn-repo-"))
        self.addCleanup(shutil.rmtree, unborn, ignore_errors=True)
        git(unborn, "init", "-q")
        with self.assertRaises(SeraError):
            repository_identity(unborn, {"schema_version": 1})


if __name__ == "__main__":
    unittest.main()
