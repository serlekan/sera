"""Shared repository fixtures for SERA 0.5.0 tests."""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

from sera.core import build_repo_map, initialize, new_task


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


@contextlib.contextmanager
def temp_repo() -> Iterator[Path]:
    """Yield an initialized, committed one-file SERA repository."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        git(root, "init", "-b", "main")
        git(root, "config", "user.name", "SERA Test")
        git(root, "config", "user.email", "sera-test@example.com")
        (root / "src").mkdir()
        (root / "src" / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
        (root / "README.md").write_text("fixture\n", encoding="utf-8")
        initialize(root)
        commit(root, "baseline")
        build_repo_map(root)
        yield root


def sera_task(
    root: Path,
    *,
    mode: str = "fast",
    risk: str = "low",
    verification: list[str] | None = None,
) -> Path:
    """Create a deterministic task owning the fixture source file."""
    return new_task(
        root,
        "fixture task",
        "characterize assurance behavior",
        mode,
        risk,
        ["src/app.py"],
        [],
        verification or [],
        0,
        "implementation",
    )


def commit(root: Path, message: str) -> str:
    git(root, "add", ".")
    git(root, "commit", "-m", message)
    return git(root, "rev-parse", "HEAD")


def move_head(root: Path, message: str = "move head") -> str:
    git(root, "commit", "--allow-empty", "-m", message)
    return git(root, "rev-parse", "HEAD")


@contextlib.contextmanager
def working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
