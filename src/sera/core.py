from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__

STATE_DIR = ".sera"
# `.sera/**` holds two different kinds of file. These subtrees and files are
# machine-generated SERA runtime state; everything else under `.sera/` is
# ordinary repository content, reviewed like any other tracked file.
SERA_RUNTIME_DIRS = ("cache", "tasks")
SERA_RUNTIME_FILES = ("latest-task",)
VALID_MODES = ("fast", "standard", "assured")
MODE_RANK = {"fast": 1, "standard": 2, "assured": 3}
BUILTIN_DEFAULT_MODE = "standard"
RISK_LEVELS = ("low", "medium", "high")
RISK_RANK = {"low": 1, "medium": 2, "high": 3}

SEAL_SCHEMA_VERSION = 2
SEAL_LEGACY_SCHEMA_VERSION = 1
SEAL_FINGERPRINT_MISMATCH = "seal_fingerprint_mismatch"
SEAL_HEAD_MISMATCH = "seal_head_mismatch"
SEAL_HEAD_TREE_MISMATCH = "seal_head_tree_mismatch"
SEAL_MISSING_HEAD_IDENTITY = "seal_missing_head_identity"
SEAL_REVIEW_MISMATCH = "seal_review_mismatch"
SEAL_SCHEMA_UNSUPPORTED = "seal_schema_unsupported"
SEAL_SCHEMA_INCONSISTENT = "seal_schema_inconsistent"
SEAL_V2_REQUIRED_FIELDS = ("task_id", "sealed_at", "fingerprint", "repository_identity", "review_ledger_fingerprint")
SEAL_V2_ONLY_FIELDS = ("repository_identity", "review_ledger_fingerprint")
UNBORN_HEAD = "unborn"
# Git's canonical empty tree. A task whose baseline predates the first commit
# compares against this rather than inventing a commit that never existed.
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

REVIEW_DIFF_BUDGET_INSUFFICIENT = "review_diff_budget_insufficient"
# Every changed file is guaranteed at least this many characters of real patch
# body before any remaining review-diff budget is distributed by relevance.
MIN_FILE_DIFF_CHARS = 320
BLOCK_SEPARATOR = "\n\n"

# Why an accepted review no longer describes the current repository state.
REVIEW_FINGERPRINT_MISMATCH = "review_fingerprint_mismatch"
REVIEW_HEAD_MISMATCH = "review_head_mismatch"
REVIEW_HEAD_TREE_MISMATCH = "review_head_tree_mismatch"
REVIEW_REPOSITORY_UNBOUND = "review_repository_unbound"
# Why a task's cumulative change set cannot be derived or represented completely.
REVIEW_BASELINE_UNBOUND = "review_baseline_unbound"
REVIEW_BASELINE_UNREACHABLE = "review_baseline_unreachable"
REVIEW_SCOPE_UNRESOLVED = "review_scope_unresolved"
REVIEW_EVIDENCE_INCOMPLETE = "review_evidence_incomplete"

# Sources a single changed file's evidence can come from, oldest first.
CHANGE_SOURCES = ("committed", "staged", "unstaged")

PACKET_SCHEMA_VERSION = 3
# 0.4.1 packets. They carry no repository identity and no change-set binding,
# so they can never satisfy 0.4.2 exact-head review dispatch.
PACKET_LEGACY_SCHEMA_VERSIONS = (2,)
PACKET_MISSING = "packet_missing"
PACKET_UNBOUND = "packet_unbound"
PACKET_LEGACY_SCHEMA = "packet_legacy_schema"
PACKET_STALE_CONTRACT = "packet_stale_contract"
PACKET_STALE_HEAD = "packet_stale_head"
PACKET_STALE_HEAD_TREE = "packet_stale_head_tree"
PACKET_STALE_STATE = "packet_stale_state"
PACKET_STALE_CHANGE_SET = "packet_stale_change_set"
PACKET_COVERAGE_INCOMPLETE = "packet_coverage_incomplete"
PACKET_STALE_ROUTE = "packet_stale_route"
PACKET_CONTENT_MISMATCH = "packet_content_mismatch"
BOOTSTRAP_EXCEPTION_SCHEMA_VERSION = 1
BOOTSTRAP_EXCEPTION_TYPE = "historical_workflow_bootstrap_exception"
BOOTSTRAP_EXCEPTION_INVALID = "bootstrap_exception_invalid"
BOOTSTRAP_EXCEPTION_NOT_APPLICABLE = "bootstrap_exception_not_applicable"
BOOTSTRAP_EXCEPTION_REQUIRED_WORKFLOW = (
    "builder",
    "packet",
    "independent_review",
    "gate",
    "seal",
)
BOOTSTRAP_EXCEPTION_AUDIT_MESSAGE = (
    "Builder handoff history is unavailable and has been explicitly preserved as missing. "
    "Workflow progression continues under a documented bootstrap exception. "
    "This does not assert that the builder stage occurred."
)
# Semantic task-contract fields. Generated artifacts are bound to a hash of
# these, never to their own contents, so the binding cannot become circular.
TASK_CONTRACT_FIELDS = (
    "objective",
    "requested_mode",
    "requested_risk",
    "mode",
    "risk",
    "risk_reasons",
    "allowed_files",
    "constraints",
    "verification",
    "uncertainty",
    "use_case",
)

HIGH_RISK_TERMS = {
    "auth", "authentication", "authorization", "balance", "cryptography", "deploy", "deployment",
    "ledger", "migration", "money", "password", "payment", "payout", "permission", "production",
    "secret", "session", "settlement", "transaction", "treasury", "wallet",
}

MEDIUM_RISK_TERMS = {
    "api", "concurrency", "database", "idempotency", "idempotent", "integration", "schema", "state",
    "webhook",
}

DEFAULT_EXCLUDES = {
    ".git",
    ".sera",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "vendor",
    "__pycache__",
    ".next",
    ".turbo",
}
TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".json",
    ".md", ".mdx", ".toml", ".yaml", ".yml", ".sh", ".ps1", ".go", ".rs",
    ".java", ".kt", ".kts", ".php", ".rb", ".c", ".h", ".cpp", ".hpp",
    ".css", ".scss", ".html", ".sql", ".graphql", ".gql", ".xml", ".txt",
}
LANGUAGE_BY_SUFFIX = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".php": "PHP", ".rb": "Ruby", ".c": "C", ".h": "C/C++", ".cpp": "C++",
    ".hpp": "C++", ".css": "CSS", ".scss": "SCSS", ".html": "HTML", ".sql": "SQL",
    ".md": "Markdown", ".mdx": "MDX", ".json": "JSON", ".toml": "TOML",
    ".yaml": "YAML", ".yml": "YAML", ".sh": "Shell", ".ps1": "PowerShell",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "default_mode": "standard",
    "max_builder_attempts": 2,
    "max_file_bytes": 300_000,
    "max_packet_chars": 48_000,
    "exclude_dirs": sorted(DEFAULT_EXCLUDES),
    "token_budgets": {"fast": 6_000, "standard": 16_000, "assured": 32_000},
    "lanes": {
        "planner": {"provider": "openai", "model": "gpt-5.6-sol", "enabled": True},
        "fast_builder": {"provider": "openai", "model": "gpt-5.6-luna", "enabled": True},
        "deep_builder": {"provider": "anthropic", "model": "claude-sonnet-5", "enabled": True},
        "independent_reviewer": {"provider": "anthropic", "model": "claude-opus-5", "enabled": True},
        "release_gate": {"provider": "openai", "model": "gpt-5.6-sol", "enabled": True},
        "optional_fable": {
            "provider": "anthropic",
            "model": "claude-fable-5",
            "enabled": False,
            "allowed_uses": ["prototype", "creative-ui", "second-attempt", "supplementary-review"],
            "may_be_sole_release_gate": False,
        },
    },
    "verification": [],
    "controller": {
        "context_max_files": 12,
        "context_min_score": 2,
        "enforce_context_budget": True,
        "auto_risk": True,
    },
    "risk_policy": {
        "high_risk_terms": [],
        "high_risk_paths": [],
    },
    "rules": {
        "builders_may_commit": False,
        "review_after_every_post_review_change": True,
        "cross_provider_review_preferred": True,
        "draft_pull_requests": True,
    },
}


class SeraError(RuntimeError):
    pass


@dataclass(frozen=True)
class RouteDecision:
    builder: str
    reviewer: str | None
    gate: str | None
    reason: str
    estimated_context_tokens: int
    budget_tokens: int
    fable_eligible: bool
    ownership_file_count: int = 0
    ownership_tokens: int = 0


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "task"


def estimate_tokens(text_or_chars: str | int) -> int:
    chars = len(text_or_chars) if isinstance(text_or_chars, str) else text_or_chars
    return max(1, math.ceil(chars / 4))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def normalize_repo_path(value: str) -> str:
    """Normalize a repository-relative path to POSIX form without a leading `./`."""
    normalized = value.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def is_sera_runtime_path(path: str) -> bool:
    """True when a repository path is SERA runtime state rather than content.

    Excluding all of `.sera/**` was too coarse: it hid a project's own reviewed
    policy — `.sera/config.json`, `.sera/POLICY.md`, and anything else a team
    chooses to track there — from ownership, change detection, scope checking,
    and review evidence. Only generated runtime state is excluded now: the
    repository-map cache, per-task capsules, packets, ledgers, seals, and the
    latest-task pointer. Nothing under `.sera/tasks/**` or `.sera/cache/**` can
    reach a review packet.
    """
    normalized = normalize_repo_path(path)
    if normalized == STATE_DIR:
        return True
    if not normalized.startswith(f"{STATE_DIR}/"):
        return False
    remainder = normalized[len(STATE_DIR) + 1 :]
    if not remainder:
        return True
    return remainder.split("/", 1)[0] in SERA_RUNTIME_DIRS or remainder in SERA_RUNTIME_FILES


ZERO_SHA = "0" * 7


def normalize_runtime_boundary(
    record: dict[str, Any],
    *,
    is_copy: bool,
    deleted_status: str,
    added_status: str,
) -> list[dict[str, Any]]:
    """Apply SERA runtime-boundary semantics to one Git change record.

    Runtime classification applies to each identity of a change *independently*.
    Classifying a rename by its destination alone discards the other half of the
    change: a project file renamed into `.sera/tasks/**` has genuinely
    disappeared from project content, and a project file that appears out of
    runtime state has genuinely been added. Excluding runtime state must never
    be a way to make project changes invisible to scope and review.

        project -> project   rename/copy   kept unchanged
        project -> runtime   rename        => deletion of the project source
        project -> runtime   copy          => nothing; the source still stands
        runtime -> project   rename/copy   => addition of the project destination
        runtime -> runtime   anything      => nothing

    The runtime side is never turned into review content by any of these paths;
    only the project-visible side of the change survives.
    """
    path = record["path"]
    old_path = record.get("old_path")
    destination_runtime = is_sera_runtime_path(path)
    if not old_path:
        return [] if destination_runtime else [record]

    source_runtime = is_sera_runtime_path(old_path)
    if source_runtime and destination_runtime:
        return []
    if not source_runtime and not destination_runtime:
        return [record]

    if destination_runtime:
        if is_copy:
            # A copy leaves its source in place, so synthesizing a deletion here
            # would invent a change that never happened.
            return []
        removed = dict(record)
        removed.update(
            {
                "status": deleted_status,
                "path": old_path,
                "old_path": None,
                "new_sha": ZERO_SHA,
                # Rendering must not re-pair this with its runtime counterpart,
                # which would print the runtime path into review evidence.
                "boundary": True,
            }
        )
        return [removed]

    added = dict(record)
    added.update({"status": added_status, "old_path": None, "old_sha": ZERO_SHA, "boundary": True})
    return [added]


def project_visible_records(
    records: list[dict[str, Any]],
    *,
    deleted_status: str,
    added_status: str,
) -> list[dict[str, Any]]:
    """Normalize a batch of Git change records to their project-visible form."""
    visible: list[dict[str, Any]] = []
    for record in records:
        visible.extend(
            normalize_runtime_boundary(
                record,
                is_copy="C" in str(record.get("status", "")),
                deleted_status=deleted_status,
                added_status=added_status,
            )
        )
    return visible


def text_tokens(value: str) -> list[str]:
    """Split text into ordered lowercase alphanumeric tokens.

    Order is preserved so multi-word phrases can be matched as contiguous runs.
    """
    return re.findall(r"[a-z0-9]+", value.lower())


def phrase_matches(phrase: str, text: str) -> bool:
    """Return True when `phrase` appears in `text` as a contiguous token run.

    Matching is case-insensitive and token-aware, never a raw substring search:
    the term `order` matches `place order` and `order.py` but not `reorder`.
    No stemming is applied, so `payment` does not match `payments`.
    """
    needle = text_tokens(phrase)
    if not needle:
        return False
    haystack = text_tokens(text)
    span = len(needle)
    return any(haystack[index : index + span] == needle for index in range(len(haystack) - span + 1))


_PATH_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _translate_path_pattern(pattern: str) -> str:
    parts = ["\\A"]
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            run = index
            while run < length and pattern[run] == "*":
                run += 1
            if run - index >= 2:
                if run < length and pattern[run] == "/":
                    parts.append("(?:.*/)?")
                    index = run + 1
                else:
                    parts.append(".*")
                    index = run
            else:
                parts.append("[^/]*")
                index = run
        elif char == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(char))
            index += 1
    parts.append("\\Z")
    return "".join(parts)


def path_matches(path: str, pattern: str) -> bool:
    """Match a repository-relative path against a documented glob pattern.

    Syntax: `**` matches across directory separators, `*` matches within one
    path segment, `?` matches a single character within one segment. Everything
    else is literal. `src/payments/**` matches `src/payments/gateway.py` and
    `src/payments/eu/sepa.py`, but not `src/payments_legacy.py`.
    """
    compiled = _PATH_PATTERN_CACHE.get(pattern)
    if compiled is None:
        compiled = re.compile(_translate_path_pattern(normalize_repo_path(pattern)))
        _PATH_PATTERN_CACHE[pattern] = compiled
    return bool(compiled.match(normalize_repo_path(path)))


def resolve_mode(config: dict[str, Any] | None, explicit_mode: str | None = None) -> tuple[str, str]:
    """Resolve the effective mode. This is the single canonical precedence resolver.

    Precedence: explicit CLI mode > configured `default_mode` > built-in fallback.
    `None` and `"auto"` both mean "no explicit override". Returns
    `(mode, source)` where source is `explicit`, `config`, or `builtin`.
    Invalid values fail closed rather than degrading to a fallback.
    """
    if explicit_mode is not None and explicit_mode != "auto":
        if explicit_mode not in VALID_MODES:
            raise SeraError(f"Invalid mode {explicit_mode!r}. Valid modes: {', '.join(VALID_MODES)}.")
        return explicit_mode, "explicit"
    configured = (config or {}).get("default_mode")
    if configured is None:
        return BUILTIN_DEFAULT_MODE, "builtin"
    if not isinstance(configured, str) or configured not in VALID_MODES:
        raise SeraError(
            f"Configured default_mode {configured!r} is invalid. Valid modes: {', '.join(VALID_MODES)}."
        )
    return configured, "config"


def max_risk(first: str, second: str) -> str:
    return first if RISK_RANK[first] >= RISK_RANK[second] else second


def escalate_mode_for_risk(mode: str, risk: str) -> str:
    """High risk never routes below `assured`."""
    if risk == "high" and MODE_RANK[mode] < MODE_RANK["assured"]:
        return "assured"
    return mode


def classify_builtin_risk(objective: str, files: list[str] | None = None) -> tuple[str, list[dict[str, str]]]:
    """SERA's built-in, project-neutral risk vocabulary."""
    haystack = " ".join([objective, *(files or [])])
    words = set(text_tokens(haystack))
    high = sorted(term for term in HIGH_RISK_TERMS if term in words)
    if high:
        return "high", [{"type": "builtin_term", "value": term, "level": "high"} for term in high]
    medium = sorted(term for term in MEDIUM_RISK_TERMS if term in words)
    if medium:
        return "medium", [{"type": "builtin_term", "value": term, "level": "medium"} for term in medium]
    return "low", []


def risk_policy_terms(config: dict[str, Any] | None) -> list[str]:
    policy = (config or {}).get("risk_policy") or {}
    return [term for term in policy.get("high_risk_terms", []) if isinstance(term, str) and term.strip()]


def risk_policy_paths(config: dict[str, Any] | None) -> list[str]:
    policy = (config or {}).get("risk_policy") or {}
    return [item for item in policy.get("high_risk_paths", []) if isinstance(item, str) and item.strip()]


def assess_risk(
    config: dict[str, Any] | None,
    objective: str,
    files: list[str] | None = None,
    explicit_risk: str | None = None,
) -> dict[str, Any]:
    """Compose effective risk and explain every escalation.

    Effective risk is the maximum severity implied by the built-in classifier,
    project-defined high-risk terms, project-defined high-risk paths, and any
    explicit user risk. Explicit input can raise the level but never silently
    lowers an automatically detected one; a rejected downgrade is recorded as
    an auditable `explicit_risk_not_applied` reason.
    """
    config = config or {}
    controller = config.get("controller", {}) if isinstance(config.get("controller"), dict) else {}
    paths = [normalize_repo_path(item) for item in (files or [])]
    reasons: list[dict[str, str]] = []
    level = "low"

    if controller.get("auto_risk", True):
        builtin_level, builtin_reasons = classify_builtin_risk(objective, paths)
        level = max_risk(level, builtin_level)
        reasons.extend(builtin_reasons)
    elif explicit_risk in (None, "auto"):
        level = max_risk(level, "medium")
        reasons.append({"type": "auto_risk_disabled", "value": "medium"})

    for term in risk_policy_terms(config):
        if phrase_matches(term, objective) or any(phrase_matches(term, path) for path in paths):
            level = "high"
            reasons.append({"type": "project_term", "value": term})

    for pattern in risk_policy_paths(config):
        matched = [path for path in paths if path_matches(path, pattern)]
        if matched:
            level = "high"
            reasons.append({"type": "project_path", "value": pattern, "matched": matched[0]})

    if explicit_risk not in (None, "auto"):
        if explicit_risk not in RISK_LEVELS:
            raise SeraError(f"Risk must be {', '.join(RISK_LEVELS)}.")
        if RISK_RANK[explicit_risk] < RISK_RANK[level]:
            reasons.append({"type": "explicit_risk_not_applied", "value": explicit_risk})
        else:
            level = explicit_risk
            reasons.append({"type": "explicit_risk", "value": explicit_risk})

    if not reasons:
        reasons.append({"type": "no_signal", "value": "no high- or medium-risk signal detected"})
    return {"risk": level, "reasons": reasons}


def resolve_task_policy(config: dict[str, Any] | None, task: dict[str, Any]) -> dict[str, Any]:
    """Derive effective mode and risk from a task's *current* contract.

    This is the single policy evaluation shared by task creation and ownership
    confirmation, so changing confirmed ownership cannot bypass risk policy.

    Derivation always starts from the task's persisted *requested* inputs —
    `requested_mode` and `requested_risk` — never from a previously derived
    value. That keeps a transiently confirmed high-risk path from making risk
    permanently sticky, while an explicit `requested_risk` remains a floor that
    survives any later ownership change.
    """
    requested_mode = task.get("requested_mode")
    requested_risk = task.get("requested_risk")
    resolved_mode, mode_source = resolve_mode(config, requested_mode)
    assessment = assess_risk(
        config,
        task.get("objective", ""),
        list(task.get("allowed_files", [])),
        explicit_risk=requested_risk,
    )
    risk = assessment["risk"]
    mode = escalate_mode_for_risk(resolved_mode, risk)
    reasons = list(assessment["reasons"])
    if mode != resolved_mode:
        reasons.append({"type": "mode_escalation", "value": mode})
    return {"mode": mode, "mode_source": mode_source, "risk": risk, "risk_reasons": reasons}


def apply_task_policy(config: dict[str, Any] | None, task: dict[str, Any]) -> dict[str, Any]:
    """Recompute and write effective policy onto a task contract in place."""
    policy = resolve_task_policy(config, task)
    task["mode"] = policy["mode"]
    task["mode_source"] = policy["mode_source"]
    task["risk"] = policy["risk"]
    task["risk_reasons"] = policy["risk_reasons"]
    return policy


def format_risk_reason(reason: dict[str, str]) -> str:
    kind = reason.get("type", "")
    value = reason.get("value", "")
    if kind == "builtin_term":
        return f"{reason.get('level', 'high')}-risk term: {value}"
    if kind == "project_term":
        return f"project high-risk term: {value}"
    if kind == "project_path":
        matched = reason.get("matched")
        return f"project high-risk path: {value}" + (f" (matched {matched})" if matched else "")
    if kind == "explicit_risk":
        return f"explicit risk: {value}"
    if kind == "explicit_risk_not_applied":
        return f"explicit risk {value} rejected; automatic assessment is more severe"
    if kind == "auto_risk_disabled":
        return f"automatic risk classification disabled; baseline {value}"
    if kind == "mode_escalation":
        return f"mode escalated to {value} by high risk"
    return f"{kind}: {value}"


def run_git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, encoding="utf-8", errors="replace"
    )
    if check and result.returncode != 0:
        raise SeraError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def find_repo_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=start, text=True, capture_output=True
    )
    if result.returncode == 0:
        return Path(result.stdout.strip()).resolve()
    current = start
    while True:
        if (current / STATE_DIR).exists() or (current / "pyproject.toml").exists():
            return current
        if current.parent == current:
            raise SeraError("Not inside a Git repository. Run this command from the project root.")
        current = current.parent


def _rev_parse_verified(root: Path, revision: str) -> str | None:
    """Resolve a revision to an immutable object ID, or `None` if it cannot be.

    Git's *exit status* is the authority here, not its stdout. `git rev-parse
    HEAD` in a repository with no commits exits non-zero but still prints the
    literal string `HEAD`, so trusting stdout stores a symbolic expression where
    an object ID belongs — and that expression silently re-resolves to whatever
    HEAD becomes later.
    """
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", revision],
        cwd=root,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    value = (result.stdout or "").strip()
    if result.returncode != 0 or not value:
        return None
    return value


def git_head_identity(root: Path) -> dict[str, str]:
    """Return the exact commit and tree identity acceptance is bound to.

    Every value is either an immutable resolved Git object ID or the explicit
    sentinel `unborn`. A symbolic revision is never stored: it would resolve
    differently later and quietly collapse a task's baseline into its own
    result. A repository with no commits yet reports `unborn` for both fields so
    comparison stays total and never treats two absences as a match.
    """
    head = _rev_parse_verified(root, "HEAD^{commit}")
    if head is None:
        return {"head_sha": UNBORN_HEAD, "head_tree_sha": UNBORN_HEAD}
    tree = _rev_parse_verified(root, f"{head}^{{tree}}")
    if tree is None:
        # A commit that cannot produce a tree means a damaged object store.
        # Failing closed is the only safe answer; a sentinel here would be read
        # as "no commits yet".
        raise SeraError(f"Commit {head} exists but its tree cannot be resolved; the repository is damaged.")
    return {"head_sha": head, "head_tree_sha": tree}


def state_path(root: Path) -> Path:
    return root / STATE_DIR


def config_path(root: Path) -> Path:
    return state_path(root) / "config.json"


def load_config(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    _deep_update(merged, loaded)
    validate_config(merged)
    return merged


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SeraError(f"{label} must be an object, got {type(value).__name__}.")
    return value


def _require_int(container: dict[str, Any], key: str, label: str, minimum: int = 1) -> None:
    if key not in container:
        return
    value = container[key]
    # bool is a subclass of int; a flag is never a valid numeric setting.
    if isinstance(value, bool) or not isinstance(value, int):
        raise SeraError(f"{label} must be an integer, got {type(value).__name__}.")
    if value < minimum:
        raise SeraError(f"{label} must be >= {minimum}, got {value}.")


def _require_bool(container: dict[str, Any], key: str, label: str) -> None:
    if key in container and not isinstance(container[key], bool):
        raise SeraError(f"{label} must be true or false, got {type(container[key]).__name__}.")


def _require_string_list(container: dict[str, Any], key: str, label: str) -> None:
    value = container.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SeraError(f"{label} must be a list of strings.")


def validate_config(config: dict[str, Any]) -> None:
    """Fail closed on configuration SERA cannot honor deterministically.

    Every expected mistake must surface as a `SeraError` on the normal CLI error
    path. An explicit `null` is a malformed value, not an omission: omit a key
    entirely to accept its default.
    """
    _require_mapping(config, "configuration")
    resolve_mode(config)
    _require_int(config, "schema_version", "schema_version", minimum=1)
    _require_int(config, "max_builder_attempts", "max_builder_attempts", minimum=1)
    _require_int(config, "max_file_bytes", "max_file_bytes", minimum=1)
    _require_int(config, "max_packet_chars", "max_packet_chars", minimum=1)
    _require_string_list(config, "exclude_dirs", "exclude_dirs")
    _require_string_list(config, "verification", "verification")

    controller = _require_mapping(config.get("controller", {}), "controller")
    _require_int(controller, "context_max_files", "controller.context_max_files", minimum=1)
    _require_int(controller, "context_min_score", "controller.context_min_score", minimum=0)
    _require_bool(controller, "enforce_context_budget", "controller.enforce_context_budget")
    _require_bool(controller, "auto_risk", "controller.auto_risk")

    policy = _require_mapping(config.get("risk_policy", {}), "risk_policy")
    _require_string_list(policy, "high_risk_terms", "risk_policy.high_risk_terms")
    _require_string_list(policy, "high_risk_paths", "risk_policy.high_risk_paths")

    budgets = _require_mapping(config.get("token_budgets", {}), "token_budgets")
    for mode in VALID_MODES:
        if mode not in budgets:
            raise SeraError(f"token_budgets is missing the {mode!r} mode budget.")
        _require_int(budgets, mode, f"token_budgets.{mode}", minimum=1)

    lanes = _require_mapping(config.get("lanes", {}), "lanes")
    for name, lane in lanes.items():
        _require_mapping(lane, f"lanes.{name}")
        _require_bool(lane, "enabled", f"lanes.{name}.enabled")
        _require_bool(lane, "may_be_sole_release_gate", f"lanes.{name}.may_be_sole_release_gate")
        for field in ("provider", "model"):
            if field in lane and not isinstance(lane[field], str):
                raise SeraError(f"lanes.{name}.{field} must be a string.")
        _require_string_list(lane, "allowed_uses", f"lanes.{name}.allowed_uses")

    rules = _require_mapping(config.get("rules", {}), "rules")
    for name, value in rules.items():
        if not isinstance(value, bool):
            raise SeraError(f"rules.{name} must be true or false.")


def _deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def initialize(root: Path, force: bool = False) -> list[Path]:
    state = state_path(root)
    created: list[Path] = []
    for path in (state, state / "cache", state / "tasks"):
        path.mkdir(parents=True, exist_ok=True)
    cfg = config_path(root)
    if cfg.exists() and not force:
        raise SeraError(f"{cfg.relative_to(root)} already exists. Use --force to replace it.")
    cfg.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
    created.append(cfg)
    readme = state / "README.md"
    if not readme.exists() or force:
        readme.write_text(
            "# Local SERA state\n\n"
            "This directory contains generated task capsules, compact repository maps, and evidence ledgers.\n"
            "Commit `.sera/config.json` when it represents team policy. Ignore `.sera/cache/` and task runtime data.\n",
            encoding="utf-8",
        )
        created.append(readme)
    ensure_gitignore(root)
    return created


def ensure_gitignore(root: Path) -> None:
    path = root / ".gitignore"
    marker = "# SERA runtime\n.sera/cache/\n.sera/tasks/*/packet-*\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if ".sera/cache/" not in existing:
        with path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("\n" + marker)


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        sample = path.read_bytes()[:2048]
    except OSError:
        return False
    return b"\x00" not in sample


def extract_symbols(text: str, suffix: str) -> list[str]:
    patterns: list[re.Pattern[str]] = []
    if suffix in {".py", ".pyi"}:
        patterns = [re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", re.M), re.compile(r"^\s*class\s+([A-Za-z_]\w*)", re.M)]
    elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        patterns = [
            re.compile(r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"),
            re.compile(r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"),
            re.compile(r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\("),
        ]
    elif suffix == ".go":
        patterns = [re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)", re.M), re.compile(r"^type\s+([A-Za-z_]\w*)", re.M)]
    elif suffix == ".rs":
        patterns = [re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)", re.M), re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)", re.M)]
    elif suffix in {".java", ".kt", ".kts", ".php", ".rb", ".c", ".cpp", ".h", ".hpp"}:
        patterns = [re.compile(r"\b(?:class|interface|struct|enum|trait)\s+([A-Za-z_]\w*)")]
    found: list[str] = []
    for pattern in patterns:
        for match in pattern.finditer(text):
            name = match.group(1)
            if name not in found:
                found.append(name)
            if len(found) >= 16:
                return found
    return found


def iter_repo_files(root: Path, excludes: set[str], max_bytes: int) -> Iterable[Path]:
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in excludes and not d.startswith(".cache"))
        current_path = Path(current)
        for name in sorted(files):
            path = current_path / name
            try:
                if path.is_symlink() or path.stat().st_size > max_bytes:
                    continue
            except OSError:
                continue
            if is_probably_text(path):
                yield path


def build_repo_map(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config(root)
    excludes = set(config.get("exclude_dirs", [])) | DEFAULT_EXCLUDES
    max_bytes = int(config.get("max_file_bytes", 300_000))
    entries: list[dict[str, Any]] = []
    languages: dict[str, int] = {}
    fingerprint_parts: list[str] = []
    for path in iter_repo_files(root, excludes, max_bytes):
        relative = path.relative_to(root).as_posix()
        data = path.read_bytes()
        digest = sha256_bytes(data)
        text = data.decode("utf-8", errors="replace")
        suffix = path.suffix.lower()
        language = LANGUAGE_BY_SUFFIX.get(suffix, "Text")
        languages[language] = languages.get(language, 0) + 1
        entry = {
            "path": relative,
            "bytes": len(data),
            "lines": text.count("\n") + (1 if text else 0),
            "sha256": digest,
            "mtime_ns": path.stat().st_mtime_ns,
            "language": language,
            "symbols": extract_symbols(text, suffix),
        }
        entries.append(entry)
        fingerprint_parts.append(f"{relative}\0{digest}")
    fingerprint = sha256_text("\n".join(fingerprint_parts))
    result = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "repo_root": root.name,
        "head_sha": _rev_parse_verified(root, "HEAD^{commit}"),
        "fingerprint": fingerprint,
        "file_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "estimated_full_source_tokens": estimate_tokens(sum(item["bytes"] for item in entries)),
        "languages": dict(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
        "files": entries,
    }
    cache = state_path(root) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "repo-map.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    markdown = render_repo_map_markdown(result)
    (cache / "repo-map.md").write_text(markdown, encoding="utf-8")
    return result



def update_repo_map(root: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refresh the repository map while reusing unchanged file metadata.

    The filesystem is still walked to detect adds/deletes, but unchanged files
    are not reread or re-parsed when their size/mtime and Git state are stable.
    """
    config = config or load_config(root)
    previous_path = state_path(root) / "cache" / "repo-map.json"
    if not previous_path.exists():
        result = build_repo_map(root, config)
        result["reused_files"] = 0
        result["rescanned_files"] = result["file_count"]
        return result
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    previous_index = {item["path"]: item for item in previous.get("files", [])}
    excludes = set(config.get("exclude_dirs", [])) | DEFAULT_EXCLUDES
    max_bytes = int(config.get("max_file_bytes", 300_000))
    current_head = _rev_parse_verified(root, "HEAD^{commit}")
    previous_head = previous.get("head_sha")
    forced: set[str] = set()
    if previous_head and current_head and previous_head != current_head:
        changed = run_git(root, "diff", "--name-only", previous_head, current_head, check=False)
        forced.update(line.replace("\\", "/") for line in changed.splitlines() if line.strip())
    forced.update(working_tree_snapshot(root))

    entries: list[dict[str, Any]] = []
    languages: dict[str, int] = {}
    fingerprint_parts: list[str] = []
    reused = 0
    rescanned = 0
    for path in iter_repo_files(root, excludes, max_bytes):
        relative = path.relative_to(root).as_posix()
        stat = path.stat()
        old = previous_index.get(relative)
        if (
            old
            and relative not in forced
            and old.get("bytes") == stat.st_size
            and old.get("mtime_ns") == stat.st_mtime_ns
            and old.get("sha256")
        ):
            entry = old
            reused += 1
        else:
            data = path.read_bytes()
            digest = sha256_bytes(data)
            text = data.decode("utf-8", errors="replace")
            suffix = path.suffix.lower()
            entry = {
                "path": relative,
                "bytes": len(data),
                "lines": text.count("\n") + (1 if text else 0),
                "sha256": digest,
                "mtime_ns": stat.st_mtime_ns,
                "language": LANGUAGE_BY_SUFFIX.get(suffix, "Text"),
                "symbols": extract_symbols(text, suffix),
            }
            rescanned += 1
        languages[entry["language"]] = languages.get(entry["language"], 0) + 1
        entries.append(entry)
        fingerprint_parts.append(f"{relative}\0{entry['sha256']}")

    result = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "repo_root": root.name,
        "head_sha": current_head,
        "fingerprint": sha256_text("\n".join(fingerprint_parts)),
        "file_count": len(entries),
        "total_bytes": sum(item["bytes"] for item in entries),
        "estimated_full_source_tokens": estimate_tokens(sum(item["bytes"] for item in entries)),
        "languages": dict(sorted(languages.items(), key=lambda item: (-item[1], item[0]))),
        "files": entries,
        "reused_files": reused,
        "rescanned_files": rescanned,
    }
    cache = state_path(root) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "repo-map.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (cache / "repo-map.md").write_text(render_repo_map_markdown(result), encoding="utf-8")
    return result

def render_repo_map_markdown(repo_map: dict[str, Any]) -> str:
    lines = [
        "# Compact repository map",
        "",
        f"Fingerprint: `{repo_map['fingerprint']}`",
        f"Files: {repo_map['file_count']}",
        f"Source bytes indexed: {repo_map['total_bytes']}",
        f"Estimated tokens avoided by reusing this map: ~{repo_map['estimated_full_source_tokens']:,}",
        "",
        "## Languages",
        "",
    ]
    for language, count in repo_map["languages"].items():
        lines.append(f"- {language}: {count}")
    lines.extend(["", "## Files and exported symbols", ""])
    for item in repo_map["files"]:
        symbols = ", ".join(item["symbols"]) if item["symbols"] else "—"
        lines.append(f"- `{item['path']}` · {item['language']} · {item['lines']} lines · symbols: {symbols}")
    return "\n".join(lines) + "\n"


def load_repo_map(root: Path, rebuild_if_missing: bool = True) -> dict[str, Any]:
    path = state_path(root) / "cache" / "repo-map.json"
    if not path.exists():
        if not rebuild_if_missing:
            raise SeraError("Repository map is missing. Run `sera map`.")
        return build_repo_map(root)
    return json.loads(path.read_text(encoding="utf-8"))


def new_task(
    root: Path,
    name: str,
    objective: str,
    mode: str | None = None,
    risk: str | None = None,
    allowed_files: list[str] | None = None,
    constraints: list[str] | None = None,
    verification: list[str] | None = None,
    uncertainty: int = 1,
    use_case: str = "implementation",
) -> Path:
    """Create a task capsule with resolved mode and composed risk.

    Mode and risk are resolved here so every creation path — `sera task new`,
    `sera task auto`, and `sera run` — shares one precedence and one escalation
    rule instead of duplicating them per entry point.
    """
    if not objective.strip():
        raise SeraError("Objective cannot be empty.")
    config = load_config(root)
    owned = sorted({normalize_repo_path(path) for path in (allowed_files or [])})
    requested_mode = None if mode in (None, "auto") else mode
    requested_risk = None if risk in (None, "auto") else risk
    # Validate explicit inputs eagerly so a bad flag fails at creation.
    resolve_mode(config, requested_mode)
    if requested_risk is not None and requested_risk not in RISK_LEVELS:
        raise SeraError(f"Risk must be {', '.join(RISK_LEVELS)}.")
    draft = {
        "objective": objective.strip(),
        "allowed_files": owned,
        "requested_mode": requested_mode,
        "requested_risk": requested_risk,
    }
    policy = resolve_task_policy(config, draft)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tasks_root = state_path(root) / "tasks"
    base_id = f"{timestamp}-{slugify(name)}"
    # Task IDs carry second resolution, so two tasks drafted in the same second
    # with the same name would otherwise collide.
    task_id = base_id
    suffix = 2
    while (tasks_root / task_id).exists():
        task_id = f"{base_id}-{suffix}"
        suffix += 1
    task_dir = tasks_root / task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    task = {
        "schema_version": 1,
        "id": task_id,
        "name": name,
        "created_at": utc_now(),
        "mode": policy["mode"],
        "mode_source": policy["mode_source"],
        "requested_mode": requested_mode,
        "risk": policy["risk"],
        "requested_risk": requested_risk,
        "risk_reasons": policy["risk_reasons"],
        "uncertainty": max(0, min(3, int(uncertainty))),
        "use_case": use_case,
        "objective": objective.strip(),
        "allowed_files": owned,
        "constraints": constraints or [],
        "verification": verification or list(config.get("verification", [])),
        "builder_attempts": 0,
        "status": "specified",
        "baseline_changes": working_tree_snapshot(root),
        # Engineering state established when the task begins. Committed review
        # coverage is derived from this baseline, never re-inferred from a later
        # HEAD, so a task cannot retroactively disown what it committed.
        "baseline_repository_identity": git_head_identity(root),
    }
    (task_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    (task_dir / "ledger.jsonl").write_text("", encoding="utf-8")
    write_task_capsule(task_dir, task)
    (state_path(root) / "latest-task").write_text(task_id + "\n", encoding="utf-8")
    return task_dir


def task_contract_fingerprint(task: dict[str, Any]) -> str:
    """Deterministic identity of a task's semantic contract.

    Covers only what a handoff artifact is derived from — objective, requested
    and derived policy, confirmed ownership, constraints, verification. It
    deliberately excludes generated artifacts, timestamps, evidence, and
    worktree state, so binding an artifact to it can never be circular.
    """
    payload = {field: task.get(field) for field in TASK_CONTRACT_FIELDS}
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def _lane_identity(config: dict[str, Any], lane: str | None) -> dict[str, Any] | None:
    if lane is None:
        return None
    value = config.get("lanes", {}).get(lane, {})
    return {"lane": lane, "provider": value.get("provider"), "model": value.get("model")}


def resolved_route_identity(config: dict[str, Any], decision: RouteDecision) -> dict[str, Any]:
    """The lane, provider, and model actually selected for each required stage.

    Only stages this task genuinely requires are bound, so changing an unrelated
    or unused lane never invalidates a packet.
    """
    return {
        "builder": _lane_identity(config, decision.builder),
        "reviewer": _lane_identity(config, decision.reviewer),
        "gate": _lane_identity(config, decision.gate),
    }


def route_fingerprint(config: dict[str, Any], decision: RouteDecision) -> str:
    identity = resolved_route_identity(config, decision)
    return sha256_text(json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def packet_provenance_path(task_dir: Path, stage: str) -> Path:
    return task_dir / f"packet-{stage}.provenance.json"


def write_packet_provenance(
    task_dir: Path,
    stage: str,
    task: dict[str, Any],
    route: dict[str, Any] | None = None,
    state_fingerprint: str | None = None,
    route_identity_fingerprint: str | None = None,
    content_sha256: str | None = None,
    repository_identity: dict[str, str] | None = None,
    review_change_fingerprint: str | None = None,
    coverage_complete: bool | None = None,
) -> dict[str, Any]:
    provenance = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "packet_type": stage,
        "task_id": task["id"],
        "task_contract_fingerprint": task_contract_fingerprint(task),
        "state_fingerprint": state_fingerprint,
        "route_fingerprint": route_identity_fingerprint,
        "content_sha256": content_sha256,
        # The exact repository the packet was generated against. Authoritative
        # for review handoffs; recorded for build handoffs as provenance only,
        # because a builder committing its own work must not stale its packet.
        "repository_identity": repository_identity,
        "review_change_fingerprint": review_change_fingerprint,
        "coverage_complete": coverage_complete,
        # Diagnostics only. Freshness never trusts this object; the route is
        # independently re-resolved and re-fingerprinted at validation time.
        "route": route or {},
        "generated_at": utc_now(),
    }
    packet_provenance_path(task_dir, stage).write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return provenance


def read_packet_provenance(task_dir: Path, stage: str) -> dict[str, Any] | None:
    path = packet_provenance_path(task_dir, stage)
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _valid_identity(value: Any) -> bool:
    """True when a stored repository identity carries both required fields."""
    return isinstance(value, dict) and bool(value.get("head_sha")) and bool(value.get("head_tree_sha"))


def packet_state(
    root: Path,
    task_dir: Path,
    stage: str,
    task: dict[str, Any],
    state_fingerprint: str | None = None,
    repo_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide whether a generated packet may still be dispatched.

    Existence is never sufficient, and recorded metadata is never trusted on its
    own. The task contract, the repository identity, the represented change set,
    the resolved route, and the packet's own bytes are all recomputed from
    current state and compared, so editing the stored route strings, the
    embedded Markdown checksum, or the packet body cannot make a stale packet
    look current.

    A review packet additionally binds the exact HEAD commit and tree it was
    generated against. Moving HEAD — including by an empty commit that leaves
    the tree and the working delta untouched — makes it stale, because the
    reviewer inspected a different repository than the one now standing. Build
    packets record identity but are not invalidated by it: committing an
    implementation must not stale the handoff that produced it.

    Validation is ordered and every failure is a closed one:

        missing -> unbound -> contract -> head -> tree -> state
                -> coverage -> change set -> route -> content -> current
    """
    packet = task_dir / f"packet-{stage}.md"
    if not packet.exists():
        return {"exists": False, "current": False, "reason": PACKET_MISSING}
    provenance = read_packet_provenance(task_dir, stage)
    if provenance and provenance.get("schema_version") in PACKET_LEGACY_SCHEMA_VERSIONS:
        # A 0.4.1 packet predates identity and change-set binding entirely. It
        # must never be mistaken for an exact-head-bound 0.4.2 packet.
        return {"exists": True, "current": False, "reason": PACKET_LEGACY_SCHEMA}
    if (
        not provenance
        or provenance.get("schema_version") != PACKET_SCHEMA_VERSION
        or provenance.get("packet_type") != stage
        or provenance.get("task_id") != task.get("id")
        or not provenance.get("task_contract_fingerprint")
        or not provenance.get("route_fingerprint")
        or not provenance.get("content_sha256")
        or not _valid_identity(provenance.get("repository_identity"))
        or (stage == "review" and not provenance.get("review_change_fingerprint"))
    ):
        return {"exists": True, "current": False, "reason": PACKET_UNBOUND}
    if provenance["task_contract_fingerprint"] != task_contract_fingerprint(task):
        return {"exists": True, "current": False, "reason": PACKET_STALE_CONTRACT}
    if stage == "review":
        bound = provenance["repository_identity"]
        current = git_head_identity(root)
        if bound.get("head_sha") != current.get("head_sha"):
            return {"exists": True, "current": False, "reason": PACKET_STALE_HEAD}
        if bound.get("head_tree_sha") != current.get("head_tree_sha"):
            return {"exists": True, "current": False, "reason": PACKET_STALE_HEAD_TREE}
    if state_fingerprint is not None and provenance.get("state_fingerprint") != state_fingerprint:
        return {"exists": True, "current": False, "reason": PACKET_STALE_STATE}
    if stage == "review":
        coverage = task_review_coverage(root, task, int(load_config(root)["max_packet_chars"]))
        if provenance.get("coverage_complete") is not True or not coverage["coverage_complete"]:
            return {"exists": True, "current": False, "reason": PACKET_COVERAGE_INCOMPLETE}
        if provenance["review_change_fingerprint"] != coverage["change_fingerprint"]:
            return {"exists": True, "current": False, "reason": PACKET_STALE_CHANGE_SET}
    try:
        decision = decide_route(root, task, repo_map)
        current_route = route_fingerprint(load_config(root), decision)
    except SeraError:
        # The route cannot be resolved at all now, so nothing built from the
        # previous one may be dispatched.
        return {"exists": True, "current": False, "reason": PACKET_STALE_ROUTE}
    if provenance["route_fingerprint"] != current_route:
        return {"exists": True, "current": False, "reason": PACKET_STALE_ROUTE}
    try:
        content = packet.read_bytes()
    except OSError:
        return {"exists": True, "current": False, "reason": PACKET_CONTENT_MISMATCH}
    if provenance["content_sha256"] != sha256_bytes(content):
        return {"exists": True, "current": False, "reason": PACKET_CONTENT_MISMATCH}
    return {"exists": True, "current": True, "reason": None}


def bootstrap_exception_state(
    root: Path,
    task_dir: Path,
    task: dict[str, Any],
    state_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Return the read-only state of a historical builder-handoff exception."""
    path = task_dir / "bootstrap-exception.json"
    result: dict[str, Any] = {
        "exists": path.exists(),
        "applicable": False,
        "accepted": False,
        "reason": None,
        "validation_errors": [],
        "missing_stage": None,
        "implementation_identity": {"head_sha": None, "head_tree_sha": None},
        "review_packet": None,
        "coverage_complete": None,
        "audit_message": None,
    }
    if not result["exists"]:
        return result
    if (task_dir / "packet-build.md").exists():
        result.update(applicable=False, reason=BOOTSTRAP_EXCEPTION_NOT_APPLICABLE)
        return result
    try:
        exception = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result.update(applicable=True, reason=BOOTSTRAP_EXCEPTION_INVALID, validation_errors=["exception_unreadable"])
        return result
    if not isinstance(exception, dict):
        result.update(applicable=True, reason=BOOTSTRAP_EXCEPTION_INVALID, validation_errors=["exception_unreadable"])
        return result
    errors: list[str] = []
    if (
        type(exception.get("schema_version")) is not int
        or exception.get("schema_version") != BOOTSTRAP_EXCEPTION_SCHEMA_VERSION
    ):
        errors.append("schema_version_invalid")
    if exception.get("type") != BOOTSTRAP_EXCEPTION_TYPE:
        errors.append("type_invalid")
    if not isinstance(exception.get("reason"), str) or not exception["reason"].strip():
        errors.append("reason_invalid")
    if exception.get("missing_stage") != "builder_handoff":
        errors.append("missing_stage_invalid")
    if exception.get("no_fabricated_evidence") is not True:
        errors.append("no_fabricated_evidence_invalid")
    if not isinstance(exception.get("implementation_head_sha"), str) or not exception["implementation_head_sha"]:
        errors.append("implementation_head_sha_invalid")
    if not isinstance(exception.get("implementation_tree_sha"), str) or not exception["implementation_tree_sha"]:
        errors.append("implementation_tree_sha_invalid")
    if exception.get("future_workflow_required") != list(BOOTSTRAP_EXCEPTION_REQUIRED_WORKFLOW):
        errors.append("future_workflow_required_invalid")
    if packet_provenance_path(task_dir, "build").exists():
        errors.append("builder_provenance_present")
    try:
        if not task_changed_files(root, task):
            errors.append("implementation_change_missing")
    except (OSError, SeraError, KeyError, TypeError, ValueError):
        errors.append("implementation_change_validation_failed")

    review_packet: dict[str, Any] | None = None
    try:
        fingerprint = state_fingerprint if state_fingerprint is not None else task_fingerprint(root, task_dir)
        repo_map = load_repo_map(root, rebuild_if_missing=False)
        if not isinstance(repo_map, dict) or not isinstance(repo_map.get("files"), list) or any(
            not isinstance(item, dict) or not isinstance(item.get("path"), str) for item in repo_map.get("files", [])
        ):
            raise SeraError("Repository map cache is invalid. Run `sera map`.")
        packet = packet_state(root, task_dir, "review", task, fingerprint, repo_map=repo_map)
        review_packet = {
            "exists": bool(packet["exists"]),
            "current": bool(packet["current"]),
            "reason": packet["reason"],
        }
        if not review_packet["current"]:
            errors.append("review_packet_not_current")
    except (OSError, json.JSONDecodeError, SeraError, KeyError, ValueError):
        review_packet = {"exists": False, "current": False, "reason": "validation_failed"}
        errors.append("review_packet_validation_failed")

    coverage_complete: bool | None = None
    try:
        coverage = task_review_coverage(root, task, int(load_config(root)["max_packet_chars"]))
        coverage_complete = coverage.get("coverage_complete") is True
        if coverage_complete is not True:
            errors.append("coverage_incomplete")
    except (OSError, json.JSONDecodeError, SeraError, KeyError, TypeError, ValueError):
        errors.append("coverage_validation_failed")

    try:
        provenance = read_packet_provenance(task_dir, "review")
        identity = provenance.get("repository_identity") if provenance else None
        if not _valid_identity(identity):
            errors.append("review_packet_identity_invalid")
        else:
            if exception.get("implementation_head_sha") != identity["head_sha"]:
                errors.append("implementation_head_mismatch")
            if exception.get("implementation_tree_sha") != identity["head_tree_sha"]:
                errors.append("implementation_tree_mismatch")
    except (OSError, json.JSONDecodeError, SeraError, KeyError, TypeError, ValueError):
        errors.append("review_packet_identity_invalid")
    result.update(
        applicable=True,
        accepted=not errors,
        reason=BOOTSTRAP_EXCEPTION_INVALID if errors else None,
        validation_errors=errors,
        missing_stage=exception.get("missing_stage"),
        implementation_identity={
            "head_sha": exception.get("implementation_head_sha")
            if isinstance(exception.get("implementation_head_sha"), str)
            else None,
            "head_tree_sha": exception.get("implementation_tree_sha")
            if isinstance(exception.get("implementation_tree_sha"), str)
            else None,
        },
        review_packet=review_packet,
        coverage_complete=coverage_complete,
        audit_message=BOOTSTRAP_EXCEPTION_AUDIT_MESSAGE if not errors else None,
    )
    return result


def write_task_capsule(task_dir: Path, task: dict[str, Any]) -> Path:
    """(Re)write the capsule so it always describes the current contract."""
    path = task_dir / "capsule.md"
    path.write_text(render_task_capsule(task), encoding="utf-8")
    return path


def render_task_capsule(task: dict[str, Any]) -> str:
    files = "\n".join(f"- `{path}`" for path in task["allowed_files"]) or "- Resolve exact ownership before implementation."
    constraints = "\n".join(f"- {value}" for value in task["constraints"]) or "- Preserve existing interfaces and unrelated work."
    verification = "\n".join(f"- `{value}`" for value in task["verification"]) or "- Define exact verification before implementation."
    return f"""# Task capsule: {task['name']}

Capsule ID: `{task['id']}`
Mode: `{task['mode']}`
Risk: `{task['risk']}`
Uncertainty: `{task['uncertainty']}`

## Objective

{task['objective']}

## Owned files

{files}

## Constraints

{constraints}

## Verification

{verification}

## Return contract

Return changed files, exact commands, concrete evidence, judgment calls, and unresolved gaps. Do not commit.
"""


def resolve_task_dir(root: Path, task_ref: str | None) -> Path:
    tasks = state_path(root) / "tasks"
    if task_ref:
        candidate = tasks / task_ref
        if candidate.is_dir():
            return candidate
        matches = sorted(tasks.glob(f"*{task_ref}*"))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise SeraError(f"Task reference is ambiguous: {task_ref}")
        raise SeraError(f"Task not found: {task_ref}")
    latest = state_path(root) / "latest-task"
    if not latest.exists():
        raise SeraError("No latest task. Create one with `sera task new`.")
    return tasks / latest.read_text(encoding="utf-8").strip()


def load_task(task_dir: Path) -> dict[str, Any]:
    return json.loads((task_dir / "task.json").read_text(encoding="utf-8"))


def save_task(task_dir: Path, task: dict[str, Any]) -> None:
    (task_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")


def ownership_summary(task: dict[str, Any], repo_map: dict[str, Any]) -> dict[str, Any]:
    """Size the files a task owns.

    Ownership is the authorization surface, not the context a stage reads. It
    is reported separately so a large owned set never masquerades as an
    oversized packet.
    """
    file_index = {item["path"]: item for item in repo_map.get("files", [])}
    owned = list(task.get("allowed_files", []))
    total_bytes = sum(file_index.get(path, {}).get("bytes", 8_000) for path in owned)
    return {
        "file_count": len(owned),
        "estimated_tokens": estimate_tokens(total_bytes) if owned else 0,
        "unmapped_files": [path for path in owned if path not in file_index],
    }


def decide_route(root: Path, task: dict[str, Any], repo_map: dict[str, Any] | None = None) -> RouteDecision:
    config = load_config(root)
    repo_map = repo_map or load_repo_map(root)
    if task["mode"] not in VALID_MODES:
        raise SeraError(f"Task mode {task['mode']!r} is invalid. Valid modes: {', '.join(VALID_MODES)}.")
    if task["risk"] not in RISK_LEVELS:
        raise SeraError(f"Task risk {task['risk']!r} is invalid. Valid levels: {', '.join(RISK_LEVELS)}.")
    ownership = ownership_summary(task, repo_map)
    context_tokens = ownership["estimated_tokens"]
    file_count = ownership["file_count"]
    risk_score = RISK_RANK[task["risk"]]
    uncertainty = int(task.get("uncertainty", 1))
    complexity = risk_score * 2 + uncertainty + min(file_count, 10) / 2 + min(context_tokens, 30_000) / 10_000
    if task["mode"] == "fast" and risk_score == 1 and complexity < 5.5:
        builder = "fast_builder"
        reviewer = None if file_count <= 2 else "independent_reviewer"
        gate = None
        reason = "Low-risk, bounded work fits the fast lane."
    else:
        builder = "deep_builder"
        reviewer = "independent_reviewer"
        gate = "release_gate" if task["mode"] == "assured" or task["risk"] == "high" else None
        reason = "Risk, uncertainty, context size, or assurance level requires a deeper implementation lane."
    for required_lane in (builder, reviewer, gate):
        if required_lane and not config["lanes"].get(required_lane, {}).get("enabled", False):
            raise SeraError(
                f"Required lane {required_lane!r} is disabled or unconfigured. "
                "Choose a different mode or configure the lane explicitly; no silent fallback is allowed."
            )
    fable = config["lanes"].get("optional_fable", {})
    fable_eligible = bool(fable.get("enabled")) and task.get("use_case") in set(fable.get("allowed_uses", []))
    budget = int(config["token_budgets"][task["mode"]])
    return RouteDecision(
        builder,
        reviewer,
        gate,
        reason,
        context_tokens,
        budget,
        fable_eligible,
        ownership_file_count=file_count,
        ownership_tokens=context_tokens,
    )


def lane_label(config: dict[str, Any], lane: str | None) -> str | None:
    if lane is None:
        return None
    value = config["lanes"].get(lane, {})
    provider = value.get("provider", "unconfigured")
    model = value.get("model", "unconfigured")
    enabled = value.get("enabled", True)
    return f"{lane}: {provider}/{model}" + ("" if enabled else " (disabled)")



def _parse_status_z(output: str) -> list[dict[str, Any]]:
    """Parse `git status --porcelain=v1 -z` records, keeping both rename sides.

    A rename or copy occupies two NUL-separated fields — destination first, then
    source. Skipping the source loses half of the change, which matters as soon
    as the two sides fall on opposite sides of the SERA runtime boundary.
    """
    items = [item for item in output.split("\0") if item]
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        entry = items[index]
        index += 1
        status = entry[:2]
        raw = entry[3:] if len(entry) >= 4 else entry
        destination = raw.replace("\\", "/")
        source: str | None = None
        if "R" in status or "C" in status:
            if index < len(items):
                source = items[index].replace("\\", "/")
                index += 1
        records.append({"status": status, "path": destination, "old_path": source})
    return records


def _status_records(root: Path) -> list[dict[str, Any]]:
    """Project-visible working-tree change records."""
    return project_visible_records(
        _parse_status_z(run_git(root, "status", "--porcelain=v1", "-z")),
        deleted_status="D ",
        added_status="A ",
    )


def working_tree_snapshot(root: Path) -> dict[str, str]:
    """Return a compact fingerprint per dirty path, including Git status.

    This lets tasks preserve an already-dirty worktree: unchanged pre-task edits
    do not become task scope, while later mutations to those paths do. Renames
    across the runtime boundary keep their project-visible side, so moving a
    project file into `.sera/tasks/**` still registers as that file changing.
    """
    snapshot: dict[str, str] = {}
    for record in _status_records(root):
        normalized = record["path"]
        path = root / normalized
        if path.is_file():
            try:
                content_hash = sha256_bytes(path.read_bytes())
            except OSError:
                content_hash = "unreadable"
        elif path.exists():
            content_hash = "non-file"
        else:
            content_hash = "deleted"
        identity = f"{record['status']}\0{record.get('old_path') or ''}\0{content_hash}"
        snapshot[normalized] = sha256_text(identity)
    return snapshot


def task_working_changes(root: Path, task: dict[str, Any]) -> list[str]:
    """Paths whose working-tree state differs from the task's dirty baseline.

    A path that was already dirty when the task began and has not moved since
    is not a task change; a path the task touched afterwards is.
    """
    baseline = task.get("baseline_changes", {})
    current = working_tree_snapshot(root)
    if not isinstance(baseline, dict):
        return sorted(current)
    return sorted(path for path in set(baseline) | set(current) if baseline.get(path) != current.get(path))


def changed_files(root: Path) -> list[str]:
    """Every project-visible path with working-tree changes."""
    paths: list[str] = []
    for record in _status_records(root):
        paths.append(record["path"])
        if record.get("old_path"):
            paths.append(record["old_path"])
    return sorted(set(paths))


_STATUS_LABELS = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "copied", "T": "type-changed"}


def _parse_raw_z(output: str) -> list[dict[str, Any]]:
    """Parse `git diff --raw -z` records into path/status/blob identity."""
    items = output.split("\0")
    records: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        meta = items[index]
        index += 1
        if not meta.startswith(":"):
            continue
        fields = meta[1:].split()
        if len(fields) < 5:
            continue
        status = fields[4]
        source = items[index] if index < len(items) else ""
        index += 1
        destination = ""
        if status[:1] in {"R", "C"}:
            destination = items[index] if index < len(items) else ""
            index += 1
        records.append(
            {
                "status": status,
                "old_sha": fields[2],
                "new_sha": fields[3],
                "path": normalize_repo_path(destination or source),
                "old_path": normalize_repo_path(source) if destination else None,
            }
        )
    return records


def task_baseline_identity(task: dict[str, Any]) -> dict[str, str] | None:
    """The repository identity recorded when the task was created, if any.

    Tasks created before 0.4.2 have none. They are readable and auditable, but
    their committed change range cannot be derived, so they fail closed rather
    than claiming complete review coverage.
    """
    identity = task.get("baseline_repository_identity")
    if not isinstance(identity, dict):
        return None
    head = identity.get("head_sha")
    tree = identity.get("head_tree_sha")
    if not isinstance(head, str) or not head or not isinstance(tree, str) or not tree:
        return None
    return {"head_sha": head, "head_tree_sha": tree}


def _commit_exists(root: Path, sha: str) -> bool:
    """True when `sha` still resolves to a commit object in this repository."""
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, capture_output=True
    )
    return result.returncode == 0


def task_committed_range(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    """Resolve the commit range a task's committed changes live in.

    The range runs from the task's baseline commit to the current HEAD. An
    unborn baseline compares against Git's empty tree rather than pretending a
    commit existed. When the range cannot be resolved at all — no baseline was
    recorded, or the baseline commit is no longer present — this reports the
    exact reason instead of silently degrading to working-tree-only coverage.
    """
    baseline = task_baseline_identity(task)
    if baseline is None:
        return {"ok": False, "reason": REVIEW_BASELINE_UNBOUND, "range": None, "baseline": None, "head": None}
    head = git_head_identity(root)
    start = EMPTY_TREE_SHA if baseline["head_sha"] == UNBORN_HEAD else baseline["head_sha"]
    if head["head_sha"] == UNBORN_HEAD:
        # Nothing is committed yet, so the committed range is empty by
        # construction rather than unresolvable.
        return {"ok": True, "reason": None, "range": None, "baseline": baseline, "head": head}
    unreachable = {
        "ok": False,
        "reason": REVIEW_BASELINE_UNREACHABLE,
        "range": None,
        "baseline": baseline,
        "head": head,
    }
    if start == EMPTY_TREE_SHA:
        # An unborn baseline diffs against Git's empty tree, which every Git
        # implementation provides implicitly. Verify rather than assume: a
        # silent failure here would render an empty diff and call it complete.
        if _rev_parse_verified(root, f"{EMPTY_TREE_SHA}^{{tree}}") is None:
            return unreachable
    elif not _commit_exists(root, start):
        return unreachable
    return {
        "ok": True,
        "reason": None,
        "range": (start, head["head_sha"]),
        "baseline": baseline,
        "head": head,
    }


def committed_change_records(
    root: Path, committed_range: tuple[str, str] | None, paths: list[str] | None = None
) -> list[dict[str, Any]]:
    """Raw change records between two commits, excluding SERA runtime state."""
    if committed_range is None or committed_range[0] == committed_range[1]:
        return []
    args = ["diff", "--no-ext-diff", "--raw", "-M", "-z", committed_range[0], committed_range[1]]
    if paths:
        args = [*args, "--", *paths]
    records = _parse_raw_z(run_git(root, *args, check=False))
    # Normalize each identity independently: a rename out of project content
    # into runtime state is still a project deletion, and one out of runtime
    # state into project content is still a project addition.
    return project_visible_records(records, deleted_status="D", added_status="A")


def task_changed_files(root: Path, task: dict[str, Any]) -> list[str]:
    """Every path this task changed, committed or not.

    Repository state outranks builder narrative: a file committed after the task
    began is a task change even when the worktree is clean, so committing an
    out-of-scope edit cannot hide it — including by renaming it across the SERA
    runtime boundary, where the project-visible side of the move survives.
    Pre-existing dirty paths the task never touched stay outside task scope, and
    runtime paths are never project content.
    """
    paths = set(task_working_changes(root, task))
    resolved = task_committed_range(root, task)
    for record in committed_change_records(root, resolved["range"]):
        paths.add(record["path"])
        if record["old_path"]:
            paths.add(record["old_path"])
    return sorted(paths)


def _raw_diff_args(source: str, committed_range: tuple[str, str] | None) -> list[str]:
    base = ["diff", "--no-ext-diff", "--raw", "-M", "-z"]
    if source == "committed":
        return [*base, committed_range[0], committed_range[1]]  # type: ignore[index]
    if source == "staged":
        return ["diff", "--cached", "--no-ext-diff", "--raw", "-M", "-z"]
    return base


def _patch_diff_args(
    source: str, committed_range: tuple[str, str] | None, no_renames: bool = False
) -> list[str]:
    # A change normalized across the runtime boundary is rendered without rename
    # detection: pairing it again would print the runtime counterpart into review
    # evidence, which is exactly what runtime exclusion exists to prevent.
    detection = "--no-renames" if no_renames else "-M"
    base = ["diff", "--no-ext-diff", "--unified=3", detection]
    if source == "committed":
        return [*base, committed_range[0], committed_range[1]]  # type: ignore[index]
    if source == "staged":
        return ["diff", "--cached", "--no-ext-diff", "--unified=3", detection]
    return base


def _collect_changed_files(
    root: Path,
    allowed_files: list[str],
    committed_range: tuple[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build one change record per changed owned file across every source.

    A file changes in up to three places — committed since the task baseline,
    staged, and unstaged — and a reviewer needs all of them. Records are keyed
    and returned by path so one file is always exactly one canonical review
    block carrying its cumulative task-relative change, never several competing
    blocks and never a file lost between the beginning and end of a combined
    patch.
    """
    if not allowed_files:
        return []
    sources = [
        source
        for source in CHANGE_SOURCES
        if source != "committed" or (committed_range is not None and committed_range[0] != committed_range[1])
    ]
    changes: dict[str, dict[str, Any]] = {}
    for source in sources:
        raw = run_git(root, *_raw_diff_args(source, committed_range), "--", *allowed_files, check=False)
        visible = project_visible_records(_parse_raw_z(raw), deleted_status="D", added_status="A")
        for record in visible:
            entry = changes.setdefault(
                record["path"],
                {
                    "path": record["path"],
                    "status": record["status"],
                    "old_path": record["old_path"],
                    "old_sha": record["old_sha"],
                    "new_sha": record["new_sha"],
                    "sources": {},
                    "patch": "",
                    "binary": False,
                },
            )
            entry["sources"][source] = record

    for path, entry in changes.items():
        present = [source for source in CHANGE_SOURCES if source in entry["sources"]]
        # Blob identity spans the whole task range: the oldest source supplies
        # where the file started, the newest where it stands now.
        entry["old_sha"] = entry["sources"][present[0]]["old_sha"]
        entry["new_sha"] = entry["sources"][present[-1]]["new_sha"]
        entry["status"] = entry["sources"][present[-1]]["status"]
        for source in present:
            if entry["sources"][source]["old_path"]:
                entry["old_path"] = entry["sources"][source]["old_path"]
                entry["status"] = entry["sources"][source]["status"]
        sections: list[str] = []
        for source in present:
            boundary = bool(entry["sources"][source].get("boundary"))
            patch = run_git(
                root, *_patch_diff_args(source, committed_range, boundary), "--", path, check=False
            )
            if patch.strip():
                sections.append(f"# {source}\n{patch}")
        entry["patch"] = "\n".join(sections)
        entry["binary"] = "Binary files" in entry["patch"] or "GIT binary patch" in entry["patch"]
        entry["staged"] = "staged" in entry["sources"]
        entry["unstaged"] = "unstaged" in entry["sources"]
        entry["committed"] = "committed" in entry["sources"]
        entry["sources"] = present

    tracked = set(run_git(root, "ls-files").splitlines())
    for relative in allowed_files:
        path = root / relative
        if relative in changes or relative in tracked or not path.is_file():
            continue
        if is_sera_runtime_path(relative):
            # Owning a runtime path cannot turn generated state into review
            # content; the untracked fallback obeys the same rule as Git's.
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        binary = b"\x00" in data[:2048]
        if binary:
            body = f"Binary untracked file, {len(data)} bytes, sha256 {sha256_bytes(data)[:16]}"
        else:
            body = data.decode("utf-8", errors="replace")
        changes[relative] = {
            "path": relative,
            "status": "A",
            "old_path": None,
            "old_sha": "0" * 7,
            "new_sha": sha256_bytes(data)[:7],
            "sources": ["unstaged"],
            "committed": False,
            "staged": False,
            "unstaged": True,
            "patch": f"# untracked\n{body}",
            "binary": binary,
        }
    return [changes[key] for key in sorted(changes)]


def _bound_patch(patch: str, allowance: int) -> str:
    """Trim a patch to at most `allowance` characters, marker included.

    The returned string never exceeds the allowance, so block length is
    monotonic in the allowance and the budget algorithm can reason about it.
    """
    if len(patch) <= allowance:
        return patch
    if allowance <= 0:
        return ""
    # The marker states how much was dropped, but its own width depends on that
    # number; two or three passes reach a fixed point.
    room = allowance
    marker = ""
    for _ in range(4):
        candidate = f"\n... {len(patch) - room:,} characters omitted from this file by review budget ...\n"
        new_room = allowance - len(candidate)
        marker = candidate
        if new_room == room or new_room <= 0:
            room = new_room
            break
        room = new_room
    if room <= 0:
        return patch[:allowance]
    head = patch[: (room * 3) // 5]
    tail_length = room - len(head)
    tail = patch[len(patch) - tail_length :] if tail_length > 0 else ""
    return (head + marker + tail)[:allowance]


def _render_change_block(entry: dict[str, Any], body_allowance: int) -> str:
    """The single canonical renderer for one changed file.

    Budgeting measures the output of this function directly rather than
    estimating it, so header width, counter digits, and omission markers can
    never drift away from what a reviewer actually receives.
    """
    body = _bound_patch(entry["patch"], body_allowance)
    status = _STATUS_LABELS.get(entry["status"][:1], entry["status"])
    where = "+".join(entry["sources"])
    lines = [
        f"### `{entry['path']}`",
        f"- status: {status}" + (f" (from `{entry['old_path']}`)" if entry.get("old_path") else ""),
        f"- location: {where or 'none'}",
        f"- blobs: `{entry['old_sha']}`..`{entry['new_sha']}`",
        f"- content: {'binary/non-text' if entry['binary'] else 'text'}",
        f"- patch sha256: `{sha256_text(entry['patch'])[:16]}`",
        f"- shown: {len(body):,} of {len(entry['patch']):,} patch characters",
    ]
    return "\n".join(lines) + "\n\n```diff\n" + body + "\n```"


def _render_change_blocks(entries: list[dict[str, Any]], allocations: list[int]) -> str:
    return BLOCK_SEPARATOR.join(
        _render_change_block(entry, allowance) for entry, allowance in zip(entries, allocations)
    )


def review_diff_coverage(
    root: Path,
    allowed_files: list[str],
    max_chars: int,
    *,
    committed_range: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Render a per-file review diff in which no changed file can be omitted.

    `committed_range` extends the change set beyond the working tree to the
    commits a task produced, so a fully committed, clean branch still reaches
    the reviewer with real patch material.

    `max_chars` counts Python string characters (Unicode code points), matching
    `len(text)`; it is not a UTF-8 byte count.

    Budgeting runs in two phases against *measured* output. Every changed file
    is first guaranteed a minimum of real patch body, and the exact rendered
    length of that minimum is what decides whether the budget can be honored at
    all. Only the remainder is distributed by relevance. When success is
    reported, `len(text) <= max_chars` holds exactly — there is no estimate and
    no tolerance.
    """
    entries = _collect_changed_files(root, allowed_files, committed_range)
    if not entries:
        return {
            "ok": True,
            "text": "No task-relative changes to review.",
            "files": [],
            "covered": [],
            "entries": [],
            "required_chars": 0,
            "rendered_chars": 0,
            "reason": None,
        }

    needs = [len(entry["patch"]) for entry in entries]
    minimums = [min(need, MIN_FILE_DIFF_CHARS) for need in needs]
    minimum_text = _render_change_blocks(entries, minimums)
    required = len(minimum_text)
    if required > max_chars:
        return {
            "ok": False,
            "text": "",
            "files": [entry["path"] for entry in entries],
            "covered": [],
            "entries": entries,
            "required_chars": required,
            "rendered_chars": 0,
            "reason": REVIEW_DIFF_BUDGET_INSUFFICIENT,
        }

    allocations = list(minimums)
    spare = max_chars - required
    order = sorted(range(len(entries)), key=lambda index: (needs[index], index))
    slots = len(order)
    for index in order:
        want = needs[index] - allocations[index]
        if spare > 0 and want > 0:
            take = min(want, max(1, spare // slots))
            allocations[index] += take
            spare -= take
        slots -= 1

    text = _render_change_blocks(entries, allocations)
    # Rendering can add characters the allocation did not model — a wider
    # `shown` counter, an omission marker appearing at a new truncation point.
    # Measured output is the authority, so shrink until it actually fits.
    for _ in range(512):
        if len(text) <= max_chars:
            break
        overflow = len(text) - max_chars
        index = max(
            range(len(entries)),
            key=lambda position: (allocations[position] - minimums[position], -position),
        )
        headroom = allocations[index] - minimums[index]
        if headroom <= 0:
            break
        allocations[index] -= min(headroom, max(1, overflow))
        text = _render_change_blocks(entries, allocations)
    if len(text) > max_chars:
        allocations, text = list(minimums), minimum_text
    if len(text) > max_chars:  # unreachable: the minimum was measured to fit
        return {
            "ok": False,
            "text": "",
            "files": [entry["path"] for entry in entries],
            "covered": [],
            "entries": entries,
            "required_chars": required,
            "rendered_chars": len(text),
            "reason": REVIEW_DIFF_BUDGET_INSUFFICIENT,
        }

    covered = [
        {
            "path": entry["path"],
            "shown_chars": len(_bound_patch(entry["patch"], allowance)),
            "total_chars": need,
        }
        for entry, allowance, need in zip(entries, allocations, needs)
    ]
    return {
        "ok": True,
        "text": text,
        "files": [entry["path"] for entry in entries],
        "covered": covered,
        "entries": entries,
        "required_chars": required,
        "rendered_chars": len(text),
        "reason": None,
    }


def review_change_fingerprint(
    root: Path,
    task: dict[str, Any],
    resolved_range: dict[str, Any],
    entries: list[dict[str, Any]],
    changed_paths: list[str],
) -> str:
    """Deterministic identity of the exact change set a review packet represents.

    Binds the range endpoints, the complete changed-path set, and each covered
    file's status, blob identity, sources, and patch bytes. Anything that moves
    the review target — a new commit in the range, a staged/unstaged edit, a
    file appearing or disappearing, a patch changing under an unchanged name —
    changes this value. A displayed filename count would not.
    """
    payload = {
        "baseline": resolved_range.get("baseline"),
        "head": resolved_range.get("head"),
        "range": list(resolved_range["range"]) if resolved_range.get("range") else None,
        "changed_paths": sorted(changed_paths),
        "files": [
            {
                "path": entry["path"],
                "status": entry["status"],
                "old_path": entry.get("old_path"),
                "old_sha": entry["old_sha"],
                "new_sha": entry["new_sha"],
                "sources": list(entry["sources"]),
                "binary": bool(entry["binary"]),
                "patch_sha256": sha256_text(entry["patch"]),
            }
            for entry in entries
        ],
    }
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def evidence_represented_paths(entries: list[dict[str, Any]]) -> set[str]:
    """Every repository path the rendered review evidence actually speaks for.

    A rename or copy is one canonical destination block that truthfully carries
    its source in `old_path`, so that block represents both identities. Nothing
    else counts: listing a filename elsewhere in the packet is not evidence.
    """
    represented: set[str] = set()
    for entry in entries:
        represented.add(entry["path"])
        if entry.get("old_path"):
            represented.add(entry["old_path"])
    return represented


def task_review_coverage(root: Path, task: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """The authoritative review change model for a task.

    Freshness and completeness are separate facts and are reported separately.
    A packet may be perfectly fresh and still not represent the whole change
    set. `coverage_complete` is the only field that claims the reviewer is
    seeing everything, it is never inferred from `current`, and it is never
    inferred from the owned-file diff having rendered successfully.

    Completeness requires all four of:

    1. the committed range resolved;
    2. review diff budgeting succeeded;
    3. no authoritative task change lies outside declared ownership;
    4. every authoritative changed path is represented by real evidence.

    Conditions 3 and 4 are the ones that matter when a task commits work it does
    not own: evidence is generated from `allowed_files`, while the authoritative
    change set is the whole repository delta, so without this check a packet
    could name an out-of-scope file in its file list and still claim complete
    coverage while carrying no patch for it.
    """
    resolved = task_committed_range(root, task)
    coverage = review_diff_coverage(
        root,
        list(task.get("allowed_files", [])),
        max_chars,
        committed_range=resolved["range"] if resolved["ok"] else None,
    )
    changed_paths = task_changed_files(root, task)
    allowed = set(task.get("allowed_files", []))
    out_of_scope = sorted(path for path in changed_paths if path not in allowed)
    missing_evidence = sorted(set(changed_paths) - evidence_represented_paths(coverage["entries"]))

    coverage["committed_range"] = list(resolved["range"]) if resolved["range"] else None
    coverage["baseline_identity"] = resolved["baseline"]
    coverage["head_identity"] = resolved["head"] or git_head_identity(root)
    coverage["changed_paths"] = changed_paths
    coverage["out_of_scope_paths"] = out_of_scope
    coverage["missing_evidence_paths"] = missing_evidence
    coverage["coverage_complete"] = bool(
        resolved["ok"] and coverage["ok"] and not out_of_scope and not missing_evidence
    )
    # Report the root cause, not a downstream symptom: unresolved scope is what
    # an operator must act on, and it is also why evidence is missing.
    coverage["coverage_reason"] = (
        resolved["reason"]
        or (REVIEW_SCOPE_UNRESOLVED if out_of_scope else None)
        or coverage["reason"]
        or (REVIEW_EVIDENCE_INCOMPLETE if missing_evidence else None)
    )
    coverage["change_fingerprint"] = review_change_fingerprint(
        root, task, resolved, coverage["entries"], changed_paths
    )
    return coverage


def compact_diff(root: Path, allowed_files: list[str], max_chars: int) -> str:
    """Per-file review diff, or a controlled failure when coverage cannot fit."""
    coverage = review_diff_coverage(root, allowed_files, max_chars)
    if not coverage["ok"]:
        raise SeraError(
            f"{coverage['reason']}: reviewing {len(coverage['files'])} changed files needs at least "
            f"{coverage['required_chars']:,} characters but max_packet_chars is {max_chars:,}. "
            "Raise max_packet_chars or split the task; SERA will not emit a review packet that hides changes."
        )
    return coverage["text"]


def read_ledger(task_dir: Path) -> list[dict[str, Any]]:
    path = task_dir / "ledger.jsonl"
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def record_evidence(task_dir: Path, command: str, exit_code: int, summary: str, output: str = "") -> dict[str, Any]:
    """Append verification evidence bound to the contract that required it.

    Records stay in the ledger for audit, but evidence collected under an older
    contract no longer satisfies the current one.
    """
    try:
        contract = task_contract_fingerprint(load_task(task_dir))
    except (OSError, json.JSONDecodeError, KeyError):
        contract = None
    record = {
        "timestamp": utc_now(),
        "command": command,
        "exit_code": int(exit_code),
        "summary": summary.strip(),
        "output_sha256": sha256_text(output),
        "output_chars": len(output),
        "task_contract_fingerprint": contract,
    }
    with (task_dir / "ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def generate_packet(
    root: Path,
    task_dir: Path,
    stage: str,
    selected_context: dict[str, Any] | None = None,
) -> tuple[Path, str]:
    """Render a stage handoff packet.

    `selected_context` is supplied by the controller layer so the packet carries
    stage-selected reading material rather than every owned file. When it is
    omitted the packet still lists full ownership, which stays correct but is
    not budget-aware.
    """
    task = load_task(task_dir)
    config = load_config(root)
    repo_map = build_repo_map(root)
    decision = decide_route(root, task, repo_map)
    head_identity = git_head_identity(root)
    coverage: dict[str, Any] | None = None
    if stage == "review":
        coverage = task_review_coverage(root, task, int(config["max_packet_chars"]))
        if not coverage["ok"]:
            raise SeraError(
                f"{coverage['reason']}: reviewing {len(coverage['files'])} changed files needs at least "
                f"{coverage['required_chars']:,} characters but max_packet_chars is "
                f"{int(config['max_packet_chars']):,}. Raise max_packet_chars or split the task; "
                "SERA will not emit a review packet that hides changes."
            )
        if not coverage["coverage_complete"]:
            reason = coverage["coverage_reason"]
            if reason == REVIEW_SCOPE_UNRESOLVED:
                detail = (
                    f"this task changed {', '.join(coverage['out_of_scope_paths'])} outside its declared "
                    "ownership, so review evidence cannot represent every change. Split or revert the "
                    "out-of-scope work, or declare ownership of it deliberately."
                )
            elif reason == REVIEW_EVIDENCE_INCOMPLETE:
                detail = (
                    f"no review evidence was produced for {', '.join(coverage['missing_evidence_paths'])}, "
                    "so the packet would understate what changed."
                )
            else:
                detail = "the complete task change set cannot be derived, so the packet would understate what changed."
            raise SeraError(
                f"{reason}: {detail} SERA will not emit a review packet that claims coverage it does not have."
            )
    file_index = {item["path"]: item for item in repo_map["files"]}
    owned_meta = [file_index[path] for path in task["allowed_files"] if path in file_index]
    owned_lines = []
    for item in owned_meta:
        symbols = ", ".join(item["symbols"]) if item["symbols"] else "—"
        owned_lines.append(f"- `{item['path']}` · {item['lines']} lines · `{item['sha256'][:12]}` · symbols: {symbols}")
    for path in task["allowed_files"]:
        if path not in file_index:
            owned_lines.append(f"- `{path}` · not yet indexed in the repository map")
    route_lines = [
        f"- Builder: {lane_label(config, decision.builder)}",
        f"- Reviewer: {lane_label(config, decision.reviewer) or 'not required by current mode'}",
        f"- Release gate: {lane_label(config, decision.gate) or 'not required by current mode'}",
        f"- Reason: {decision.reason}",
        f"- Owned files: {decision.ownership_file_count} · ~{decision.ownership_tokens:,} tokens",
        f"- Stage token budget: {decision.budget_tokens:,}",
        f"- Optional Fable eligible: {'yes' if decision.fable_eligible else 'no'}",
    ]
    context_lines: list[str] = []
    if selected_context:
        chosen = selected_context.get("selected_files", [])
        route_lines.insert(
            5,
            f"- Selected {stage} context: {len(chosen)} files · "
            f"~{selected_context.get('selected_source_tokens', 0):,} tokens",
        )
        for item in chosen:
            context_lines.append(f"- `{item['path']}` · {item['reason']} · ~{item['tokens']:,} tokens")
        withheld = selected_context.get("excluded_files", [])
        if withheld:
            context_lines.append(
                f"- {len(withheld)} owned file(s) retain ownership but are outside this stage's context; "
                "read them only if the objective requires it."
            )
    ledger = read_ledger(task_dir)
    evidence_lines = [
        f"- `{item['command']}` → exit {item['exit_code']} · {item['summary']} · output `{item['output_sha256'][:12]}`"
        for item in ledger
    ] or ["- No evidence recorded yet."]
    packet = [
        f"# {stage.title()} packet: {task['name']}",
        "",
        f"Packet generated: `{utc_now()}`",
        f"Task ID: `{task['id']}`",
        f"Task contract: `{task_contract_fingerprint(task)}`",
        f"Repository fingerprint: `{repo_map['fingerprint']}`",
        "",
        "## Objective",
        "",
        task["objective"],
        "",
        "## Route",
        "",
        *route_lines,
        "",
        "## Exact ownership",
        "",
        *(owned_lines or ["- Ownership is unresolved. Stop before implementation."]),
        "",
        "## Selected context",
        "",
        *(context_lines or ["- Context selection was not supplied; ownership above is the reading list."]),
        "",
        "## Constraints",
        "",
        *([f"- {item}" for item in task["constraints"]] or ["- Preserve interfaces and unrelated work."]),
        "",
        "## Required verification",
        "",
        *([f"- `{item}`" for item in task["verification"]] or ["- Verification is unresolved. Stop before implementation."]),
    ]
    if stage == "review":
        assert coverage is not None  # set above for every review packet
        baseline = coverage["baseline_identity"] or {}
        committed = coverage["committed_range"]
        packet.extend([
            "",
            "## Repository review identity",
            "",
            "This review is bound to the exact repository state below. If HEAD moves before the",
            "verdict is recorded, this packet is stale and SERA will refuse the review.",
            "",
            f"- HEAD: `{head_identity['head_sha']}`",
            f"- HEAD tree: `{head_identity['head_tree_sha']}`",
            f"- Task baseline HEAD: `{baseline.get('head_sha', 'unrecorded')}`",
            "- Committed range: " + (f"`{committed[0]}..{committed[1]}`" if committed else "none"),
            f"- Change set fingerprint: `{coverage['change_fingerprint']}`",
            "- Change coverage: complete",
            "",
            "## Evidence ledger",
            "",
            *evidence_lines,
            "",
            "## Changed files",
            "",
            "Every changed file is represented below and in the bounded diff, whether or not",
            "its full contents were selected into review context. Changes committed since the",
            "task baseline are included alongside staged and unstaged work.",
            "",
            *([f"- `{item}`" for item in coverage["changed_paths"]] or ["- No task-relative changes detected."]),
            "",
            "## Per-file change evidence",
            "",
            "Each changed file below carries bounded patch material of its own. If the budget",
            "could not cover every changed file, this packet would have failed instead.",
            "",
            coverage["text"],
            "",
            "## Required verdict",
            "",
            "Return exactly one: `ship`, `fix-first`, or `rethink`. Do not edit files.",
        ])
    else:
        packet.extend([
            "",
            "## Builder return",
            "",
            "Return status, changed files, exact commands, concrete output evidence, judgment calls, and gaps. Do not commit.",
        ])
    text = "\n".join(packet) + "\n"
    checksum = sha256_text(text)
    text = text.replace("Packet generated:", f"Packet checksum: `{checksum}`\nPacket generated:", 1)
    path = task_dir / f"packet-{stage}.md"
    path.write_text(text, encoding="utf-8")
    # Hash what actually landed on disk, not the in-memory string, so the
    # provenance envelope binds the exact bytes a runtime will read.
    content_sha256 = sha256_bytes(path.read_bytes())
    write_packet_provenance(
        task_dir,
        stage,
        task,
        route=resolved_route_identity(config, decision),
        # A review packet embeds the diff and evidence, so it is additionally
        # bound to that state; a build packet is not.
        state_fingerprint=task_fingerprint(root, task_dir) if stage == "review" else None,
        route_identity_fingerprint=route_fingerprint(config, decision),
        content_sha256=content_sha256,
        repository_identity=head_identity,
        review_change_fingerprint=coverage["change_fingerprint"] if coverage else None,
        coverage_complete=True if coverage else None,
    )
    return path, text


def task_fingerprint(root: Path, task_dir: Path) -> str:
    task_bytes = (task_dir / "task.json").read_bytes()
    ledger_bytes = (task_dir / "ledger.jsonl").read_bytes()
    diff = run_git(root, "diff", "--binary", "--no-ext-diff", check=False).encode("utf-8", errors="replace")
    staged = run_git(root, "diff", "--cached", "--binary", "--no-ext-diff", check=False).encode("utf-8", errors="replace")
    untracked_parts: list[bytes] = []
    for relative in sorted(run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()):
        normalized = relative.replace("\\", "/")
        if is_sera_runtime_path(normalized):
            continue
        path = root / relative
        if path.is_file():
            untracked_parts.extend([normalized.encode("utf-8"), b"\0", path.read_bytes(), b"\0"])
    return sha256_bytes(
        task_bytes + b"\0" + ledger_bytes + b"\0" + diff + b"\0" + staged + b"\0" + b"".join(untracked_parts)
    )



def run_verification(root: Path, task_dir: Path) -> list[dict[str, Any]]:
    task = load_task(task_dir)
    logs = task_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for index, command in enumerate(task["verification"], start=1):
        completed = subprocess.run(
            command,
            cwd=root,
            shell=True,
            text=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        output = completed.stdout + ("\n" if completed.stdout and completed.stderr else "") + completed.stderr
        log_path = logs / f"{index:02d}-{slugify(command)[:48]}.log"
        log_path.write_text(output, encoding="utf-8")
        summary = f"completed with exit {completed.returncode}; log {log_path.name}"
        record = record_evidence(task_dir, command, completed.returncode, summary, output)
        record["log"] = log_path.name
        results.append(record)
        if completed.returncode != 0:
            break
    return results


def budget_report(root: Path, task_dir: Path) -> dict[str, Any]:
    task = load_task(task_dir)
    repo_map = load_repo_map(root)
    decision = decide_route(root, task, repo_map)
    map_path = state_path(root) / "cache" / "repo-map.md"
    map_tokens = estimate_tokens(map_path.read_text(encoding="utf-8")) if map_path.exists() else 0
    capsule_tokens = estimate_tokens((task_dir / "capsule.md").read_text(encoding="utf-8"))
    full_tokens = int(repo_map["estimated_full_source_tokens"])
    orientation_tokens = map_tokens + capsule_tokens
    avoided = max(0, full_tokens - orientation_tokens)
    ownership = ownership_summary(task, repo_map)
    return {
        "full_source_tokens": full_tokens,
        "repo_map_tokens": map_tokens,
        "capsule_tokens": capsule_tokens,
        "orientation_tokens": orientation_tokens,
        "estimated_orientation_tokens_avoided": avoided,
        "estimated_avoidance_percent": round((avoided / full_tokens * 100), 1) if full_tokens else 0.0,
        "ownership": {
            "file_count": ownership["file_count"],
            "estimated_tokens": ownership["estimated_tokens"],
        },
        "owned_context_tokens": decision.estimated_context_tokens,
        "stage_budget_tokens": decision.budget_tokens,
        "ownership_note": (
            "Ownership size is an authorization measure, not a packet size. "
            "Stage budget compliance is reported by `sera context`."
        ),
    }


def read_reviews(task_dir: Path) -> list[dict[str, Any]]:
    path = task_dir / "reviews.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

def review_ledger_fingerprint(task_dir: Path) -> str:
    """Canonical fingerprint of the full review ledger for a task.

    Binds every persisted field of every review record — stage, verdict,
    reviewer, rationale, reviewed fingerprint, timestamp — in ledger order,
    not merely the stage names. Serialization is canonical JSON so the value is
    stable across runs.

    This deliberately lives outside `task_fingerprint`: reviews record the task
    fingerprint they judged, so folding reviews back into that fingerprint would
    be circular. Acceptance composes the two instead.
    """
    records = read_reviews(task_dir)
    return sha256_text(json.dumps(records, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def generate_summary(root: Path, task_dir: Path) -> tuple[Path, str]:
    task = load_task(task_dir)
    result = check_task(root, task_dir)
    ledger = read_ledger(task_dir)
    verification = [
        f"- `{item['command']}` → exit {item['exit_code']} · {item['summary']}"
        for item in ledger
    ] or ["- No evidence recorded."]
    reviews = [
        f"- {stage}: `{review['verdict']}` by {review['reviewer']}"
        for stage, review in sorted(result["reviews"].items())
    ] or ["- No reviews recorded."]
    lines = [
        f"# {task['name']}",
        "",
        "## Objective",
        "",
        task["objective"],
        "",
        "## Delivery profile",
        "",
        f"- Mode: `{task['mode']}`",
        f"- Risk: `{task['risk']}`",
        f"- Fingerprint: `{result['fingerprint']}`",
        "",
        "## Changed files",
        "",
        *([f"- `{item}`" for item in result["changed_files"]] or ["- No changes detected."]),
        "",
        "## Verification",
        "",
        *verification,
        "",
        "## Reviews",
        "",
        *reviews,
        "",
        "## SERA status",
        "",
        f"- Contract check: `{'pass' if result['ok'] else 'blocked'}`",
        f"- Seal: `{result['seal_status']}`",
        f"- Accepted HEAD: `{result['head_identity']['head_sha']}`",
        f"- Next action: {result['next_action']}",
    ]
    output = "\n".join(lines) + "\n"
    path = task_dir / "summary.md"
    path.write_text(output, encoding="utf-8")
    return path, output


def evaluate_seal(
    seal: dict[str, Any] | None,
    fingerprint: str,
    head_identity: dict[str, str],
    review_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Decide whether a seal still describes the exact reviewed repository state.

    The declared schema version is interpreted *before* anything else, so a
    tampered or unknown version can never be validated as if it were current.

    - `schema_version: 2` must carry every required v2 field, and then binds the
      task/evidence/delta fingerprint, the review ledger, and the exact HEAD
      commit and tree.
    - `schema_version: 1` is a genuine 0.4.0 seal only when it carries no
      v2-only fields; it reports `legacy_unbound` and fails closed.
    - A v1 record carrying v2-only fields, a v2 record missing required fields,
      a missing version, and any unknown version all fail closed.
    """
    if not seal:
        return {"status": "none", "stale": False, "reasons": []}

    version = seal.get("schema_version")
    present_v2_only = [field for field in SEAL_V2_ONLY_FIELDS if field in seal]

    if version == SEAL_LEGACY_SCHEMA_VERSION:
        if present_v2_only:
            return {
                "status": "schema_inconsistent",
                "stale": True,
                "reasons": [SEAL_SCHEMA_INCONSISTENT],
            }
        return {
            "status": "legacy_unbound",
            "stale": True,
            "reasons": [SEAL_MISSING_HEAD_IDENTITY],
        }

    if version != SEAL_SCHEMA_VERSION:
        return {"status": "schema_unsupported", "stale": True, "reasons": [SEAL_SCHEMA_UNSUPPORTED]}

    missing = [field for field in SEAL_V2_REQUIRED_FIELDS if seal.get(field) in (None, "")]
    identity = seal.get("repository_identity")
    if not isinstance(identity, dict) or not identity.get("head_sha") or not identity.get("head_tree_sha"):
        if "repository_identity" not in missing:
            missing.append("repository_identity")
    if missing:
        return {"status": "schema_inconsistent", "stale": True, "reasons": [SEAL_SCHEMA_INCONSISTENT]}

    reasons: list[str] = []
    if seal.get("fingerprint") != fingerprint:
        reasons.append(SEAL_FINGERPRINT_MISMATCH)
    if review_fingerprint is not None and seal.get("review_ledger_fingerprint") != review_fingerprint:
        reasons.append(SEAL_REVIEW_MISMATCH)
    if identity.get("head_sha") != head_identity.get("head_sha"):
        reasons.append(SEAL_HEAD_MISMATCH)
    elif identity.get("head_tree_sha") != head_identity.get("head_tree_sha"):
        reasons.append(SEAL_HEAD_TREE_MISMATCH)

    if not reasons:
        status = "current"
    elif SEAL_HEAD_MISMATCH in reasons or SEAL_HEAD_TREE_MISMATCH in reasons:
        status = "head_mismatch"
    elif SEAL_REVIEW_MISMATCH in reasons:
        status = "review_mismatch"
    else:
        status = "stale_fingerprint"
    return {"status": status, "stale": bool(reasons), "reasons": reasons}


def create_seal(root: Path, task_dir: Path) -> dict[str, Any]:
    result = check_task(root, task_dir)
    if not result["ok"]:
        raise SeraError(f"Task cannot be sealed: {result['next_action']}")
    task = load_task(task_dir)
    seal = {
        "schema_version": SEAL_SCHEMA_VERSION,
        "sera_version": __version__,
        "task_id": task["id"],
        "sealed_at": utc_now(),
        "fingerprint": result["fingerprint"],
        "repository_identity": result["head_identity"],
        "review_ledger_fingerprint": result["review_ledger_fingerprint"],
        "evidence_records": len(read_ledger(task_dir)),
        "review_stages": sorted(result["reviews"].keys()),
        "changed_files": result["changed_files"],
    }
    (task_dir / "seal.json").write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    return seal


def check_task(root: Path, task_dir: Path) -> dict[str, Any]:
    task = load_task(task_dir)
    changed = task_changed_files(root, task)
    allowed = set(task["allowed_files"])
    out_of_scope = [path for path in changed if path not in allowed]
    ledger = read_ledger(task_dir)
    contract = task_contract_fingerprint(task)
    successful = {
        item["command"]
        for item in ledger
        if item["exit_code"] == 0 and item.get("task_contract_fingerprint") == contract
    }
    missing_verification = [command for command in task["verification"] if command not in successful]
    fingerprint = task_fingerprint(root, task_dir)
    decision = decide_route(root, task)
    required_review_stages: list[str] = []
    if decision.reviewer:
        required_review_stages.append("independent")
    if decision.gate:
        required_review_stages.append("gate")
    head_identity = git_head_identity(root)
    reviews = read_reviews(task_dir)
    latest_by_stage: dict[str, dict[str, Any]] = {}
    for review in reviews:
        latest_by_stage[review["stage"]] = review
    review_states = {
        stage: evaluate_review_record(latest_by_stage[stage], fingerprint, head_identity)
        for stage in required_review_stages
        if stage in latest_by_stage
    }
    missing_reviews = [stage for stage in required_review_stages if stage not in latest_by_stage]
    stale_reviews = [stage for stage in review_states if not review_states[stage]["current"]]
    failed_reviews = [
        stage
        for stage in review_states
        if review_states[stage]["current"] and review_states[stage]["verdict"] != "ship"
    ]
    stale_review_reasons = {stage: review_states[stage]["reasons"] for stage in stale_reviews}
    ok = not out_of_scope and not missing_verification and not missing_reviews and not stale_reviews and not failed_reviews
    if out_of_scope:
        next_action = "Split or revert out-of-scope files before continuing."
    elif missing_verification:
        next_action = "Run `sera verify` or record the missing verification evidence."
    elif stale_reviews:
        if any(REVIEW_REPOSITORY_UNBOUND in reasons for reasons in stale_review_reasons.values()):
            next_action = (
                "A required review predates exact-HEAD binding and cannot satisfy 0.4.2 acceptance. "
                "Regenerate the review packet and repeat the review."
            )
        elif any(
            REVIEW_HEAD_MISMATCH in reasons or REVIEW_HEAD_TREE_MISMATCH in reasons
            for reasons in stale_review_reasons.values()
        ):
            next_action = (
                "HEAD moved after review; the accepted reviews describe a different commit. "
                "Regenerate the review packet and repeat the review at the current HEAD."
            )
        else:
            next_action = "Regenerate the review packet and repeat stale review stages."
    # A rejected implementation stops the workflow here. Asking for a later gate
    # before the earlier stage's findings are addressed would dispatch a release
    # review for work the independent stage already refused.
    elif failed_reviews:
        next_action = "Address reviewer findings, rerun verification, and review the new fingerprint."
    elif missing_reviews:
        next_action = f"Obtain required review stage: {missing_reviews[0]}."
    else:
        next_action = "The current tree satisfies the task contract; proceed to the separate commit decision."
    seal_path = task_dir / "seal.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8")) if seal_path.exists() else None
    review_fingerprint = review_ledger_fingerprint(task_dir)
    seal_state = evaluate_seal(seal, fingerprint, head_identity, review_fingerprint)
    seal_stale = seal_state["stale"]
    if ok and seal_stale:
        if seal_state["status"] == "review_mismatch":
            next_action = (
                "The review ledger changed after acceptance; this seal no longer binds the reviews "
                "that justified it. Re-review and seal the current records."
            )
        elif seal_state["status"] in {"schema_unsupported", "schema_inconsistent"}:
            next_action = (
                "This seal's schema version is unsupported or inconsistent with its contents. "
                "Create a new SERA Seal."
            )
        elif seal_state["status"] == "head_mismatch":
            next_action = (
                "HEAD moved after acceptance; this seal no longer describes the current commit. "
                "Re-review and seal the exact current repository identity."
            )
        elif seal_state["status"] == "legacy_unbound":
            next_action = (
                "This seal predates exact-HEAD binding (0.4.0 format) and cannot satisfy "
                "exact-head acceptance. Create a 0.4.1 seal."
            )
        else:
            next_action = "Create a new SERA Seal for the current fingerprint."
    elif ok and seal is None:
        next_action = "Create the SERA Seal, then proceed to the separate commit decision."
    return {
        "ok": ok,
        "changed_files": changed,
        "out_of_scope": out_of_scope,
        "missing_verification": missing_verification,
        "fingerprint": fingerprint,
        "head_identity": head_identity,
        "review_ledger_fingerprint": review_fingerprint,
        "required_review_stages": required_review_stages,
        "reviews": latest_by_stage,
        "review_states": review_states,
        "missing_reviews": missing_reviews,
        "stale_reviews": stale_reviews,
        "stale_review_reasons": stale_review_reasons,
        "failed_reviews": failed_reviews,
        "baseline_repository_identity": task_baseline_identity(task),
        "seal": seal,
        "seal_stale": seal_stale,
        "seal_status": seal_state["status"],
        "seal_stale_reasons": seal_state["reasons"],
        "next_action": next_action,
    }


def record_review(
    task_dir: Path,
    fingerprint: str,
    verdict: str,
    reviewer: str,
    reason: str,
    stage: str = "independent",
    repository_identity: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Append a review verdict to the ledger.

    `repository_identity` is the exact HEAD and tree the reviewer inspected. It
    is never a caller's free-form claim in normal use: `accept_review` derives
    it from the repository after confirming the reviewer's packet is still
    current. A record written without it stays readable for audit but cannot
    satisfy 0.4.2 exact-head acceptance.
    """
    if verdict not in {"ship", "fix-first", "rethink"}:
        raise SeraError("Verdict must be ship, fix-first, or rethink.")
    if stage not in {"independent", "gate", "supplementary"}:
        raise SeraError("Review stage must be independent, gate, or supplementary.")
    review: dict[str, Any] = {
        "timestamp": utc_now(),
        "fingerprint": fingerprint,
        "stage": stage,
        "verdict": verdict,
        "reviewer": reviewer,
        "reason": reason.strip(),
    }
    if repository_identity is not None:
        review["repository_identity"] = {
            "head_sha": repository_identity["head_sha"],
            "head_tree_sha": repository_identity["head_tree_sha"],
        }
    with (task_dir / "reviews.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(review, sort_keys=True) + "\n")
    return review


def accept_review(
    root: Path,
    task_dir: Path,
    verdict: str,
    reviewer: str,
    reason: str,
    stage: str = "independent",
) -> dict[str, Any]:
    """Record a review verdict bound to the repository the reviewer inspected.

    SERA never trusts a supplied commit SHA. The reviewer works from a review
    packet, that packet already binds an exact HEAD, tree, and change set, and
    acceptance re-validates it against the repository as it stands right now.
    Only then is the identity read from Git and written into the ledger. If
    HEAD moved, or the change set moved, or coverage is no longer complete, the
    verdict is refused until a fresh packet is generated and reviewed.
    """
    task = load_task(task_dir)
    fingerprint = task_fingerprint(root, task_dir)
    state = packet_state(root, task_dir, "review", task, fingerprint)
    if not state["current"]:
        raise SeraError(
            f"{state['reason']}: the review packet is not current for this repository state, so a verdict "
            "recorded now would not describe what was reviewed. Run `sera packet review`, review the fresh "
            "packet, and record the verdict against it."
        )
    return record_review(
        task_dir,
        fingerprint,
        verdict,
        reviewer,
        reason,
        stage,
        repository_identity=git_head_identity(root),
    )


def evaluate_review_record(
    review: dict[str, Any], fingerprint: str, head_identity: dict[str, str]
) -> dict[str, Any]:
    """Decide whether an accepted review still describes the current state.

    Freshness requires every applicable binding to match at once: the task,
    evidence, and delta fingerprint the reviewer judged, plus the exact HEAD
    commit and tree they judged it at. An empty commit leaves the fingerprint
    and the tree untouched, so only the HEAD binding catches it — which is
    precisely the case a release gate must not be allowed to inherit.
    """
    reasons: list[str] = []
    if review.get("fingerprint") != fingerprint:
        reasons.append(REVIEW_FINGERPRINT_MISMATCH)
    identity = review.get("repository_identity")
    if not _valid_identity(identity):
        reasons.append(REVIEW_REPOSITORY_UNBOUND)
    elif identity.get("head_sha") != head_identity.get("head_sha"):
        reasons.append(REVIEW_HEAD_MISMATCH)
    elif identity.get("head_tree_sha") != head_identity.get("head_tree_sha"):
        reasons.append(REVIEW_HEAD_TREE_MISMATCH)
    return {
        "status": "stale" if reasons else "current",
        "current": not reasons,
        "reasons": reasons,
        "verdict": review.get("verdict"),
        "reviewer": review.get("reviewer"),
        "stage": review.get("stage"),
    }
