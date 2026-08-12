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
VALID_MODES = ("fast", "standard", "assured")
MODE_RANK = {"fast": 1, "standard": 2, "assured": 3}
BUILTIN_DEFAULT_MODE = "standard"
RISK_LEVELS = ("low", "medium", "high")
RISK_RANK = {"low": 1, "medium": 2, "high": 3}

SEAL_SCHEMA_VERSION = 2
SEAL_FINGERPRINT_MISMATCH = "seal_fingerprint_mismatch"
SEAL_HEAD_MISMATCH = "seal_head_mismatch"
SEAL_HEAD_TREE_MISMATCH = "seal_head_tree_mismatch"
SEAL_MISSING_HEAD_IDENTITY = "seal_missing_head_identity"
UNBORN_HEAD = "unborn"

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


def git_head_identity(root: Path) -> dict[str, str]:
    """Return the exact commit and tree identity acceptance is bound to.

    A repository with no commits yet reports the sentinel `unborn` for both
    fields so seal comparison stays total and never compares `None` to `None`
    as if it were a real match.
    """
    head = run_git(root, "rev-parse", "HEAD", check=False).strip()
    tree = run_git(root, "rev-parse", "HEAD^{tree}", check=False).strip()
    return {"head_sha": head or UNBORN_HEAD, "head_tree_sha": tree or UNBORN_HEAD}


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


def validate_config(config: dict[str, Any]) -> None:
    """Fail closed on configuration SERA cannot honor deterministically."""
    resolve_mode(config)
    policy = config.get("risk_policy", {})
    if not isinstance(policy, dict):
        raise SeraError("risk_policy must be an object with high_risk_terms and high_risk_paths.")
    for key in ("high_risk_terms", "high_risk_paths"):
        value = policy.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise SeraError(f"risk_policy.{key} must be a list of strings.")
    budgets = config.get("token_budgets", {})
    for mode in VALID_MODES:
        if mode not in budgets:
            raise SeraError(f"token_budgets is missing the {mode!r} mode budget.")


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
    marker = "# SERA runtime\n.sera/cache/\n.sera/tasks/*/packet-*.md\n"
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
        "head_sha": run_git(root, "rev-parse", "HEAD", check=False).strip() or None,
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
    current_head = run_git(root, "rev-parse", "HEAD", check=False).strip() or None
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
    resolved_mode, mode_source = resolve_mode(config, mode)
    assessment = assess_risk(config, objective, owned, explicit_risk=risk)
    final_risk = assessment["risk"]
    final_mode = escalate_mode_for_risk(resolved_mode, final_risk)
    risk_reasons = list(assessment["reasons"])
    if final_mode != resolved_mode:
        risk_reasons.append({"type": "mode_escalation", "value": final_mode})
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
        "mode": final_mode,
        "mode_source": mode_source,
        "risk": final_risk,
        "risk_reasons": risk_reasons,
        "uncertainty": max(0, min(3, int(uncertainty))),
        "use_case": use_case,
        "objective": objective.strip(),
        "allowed_files": owned,
        "constraints": constraints or [],
        "verification": verification or list(config.get("verification", [])),
        "builder_attempts": 0,
        "status": "specified",
        "baseline_changes": working_tree_snapshot(root),
    }
    (task_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    (task_dir / "ledger.jsonl").write_text("", encoding="utf-8")
    (task_dir / "capsule.md").write_text(render_task_capsule(task), encoding="utf-8")
    (state_path(root) / "latest-task").write_text(task_id + "\n", encoding="utf-8")
    return task_dir


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



def working_tree_snapshot(root: Path) -> dict[str, str]:
    """Return a compact fingerprint per dirty path, including Git status.

    This lets tasks preserve an already-dirty worktree: unchanged pre-task edits
    do not become task scope, while later mutations to those paths do.
    """
    output = run_git(root, "status", "--porcelain=v1", "-z")
    items = output.split("\0")
    snapshot: dict[str, str] = {}
    skip_next_rename_source = False
    for item in items:
        if not item:
            continue
        if skip_next_rename_source:
            skip_next_rename_source = False
            continue
        status = item[:2]
        raw = item[3:] if len(item) >= 4 else item
        if status.startswith("R") or status.endswith("R") or status.startswith("C") or status.endswith("C"):
            skip_next_rename_source = True
        normalized = raw.replace("\\", "/")
        if normalized == STATE_DIR or normalized.startswith(f"{STATE_DIR}/"):
            continue
        path = root / raw
        if path.is_file():
            try:
                content_hash = sha256_bytes(path.read_bytes())
            except OSError:
                content_hash = "unreadable"
        elif path.exists():
            content_hash = "non-file"
        else:
            content_hash = "deleted"
        snapshot[normalized] = sha256_text(f"{status}\0{content_hash}")
    return snapshot


def task_changed_files(root: Path, task: dict[str, Any]) -> list[str]:
    baseline = task.get("baseline_changes", {})
    current = working_tree_snapshot(root)
    if not isinstance(baseline, dict):
        return sorted(current)
    return sorted(path for path in set(baseline) | set(current) if baseline.get(path) != current.get(path))

def changed_files(root: Path) -> list[str]:
    output = run_git(root, "status", "--porcelain=v1", "-z")
    items = output.split("\0")
    paths: list[str] = []
    skip_next_rename_source = False
    for item in items:
        if not item:
            continue
        if skip_next_rename_source:
            skip_next_rename_source = False
            continue
        status = item[:2]
        raw = item[3:] if len(item) >= 4 else item
        if status.startswith("R") or status.endswith("R") or status.startswith("C") or status.endswith("C"):
            skip_next_rename_source = True
        normalized = raw.replace("\\", "/")
        if normalized == STATE_DIR or normalized.startswith(f"{STATE_DIR}/"):
            continue
        paths.append(normalized)
    return sorted(set(paths))


def compact_diff(root: Path, allowed_files: list[str], max_chars: int) -> str:
    args = ["diff", "--no-ext-diff", "--unified=3", "--"]
    args.extend(allowed_files)
    diff = run_git(root, *args, check=False)
    staged_args = ["diff", "--cached", "--no-ext-diff", "--unified=3", "--"]
    staged_args.extend(allowed_files)
    staged = run_git(root, *staged_args, check=False)
    if staged:
        diff += "\n# STAGED CHANGES\n" + staged
    tracked = set(run_git(root, "ls-files").splitlines())
    for relative in allowed_files:
        path = root / relative
        if relative not in tracked and path.is_file():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            diff += f"\n# UNTRACKED FILE: {relative}\n" + content + "\n"
    if len(diff) <= max_chars:
        return diff
    head = diff[: max_chars // 2]
    tail = diff[-max_chars // 2 :]
    omitted = len(diff) - len(head) - len(tail)
    return head + f"\n\n... {omitted} characters omitted by packet budget ...\n\n" + tail


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
    record = {
        "timestamp": utc_now(),
        "command": command,
        "exit_code": int(exit_code),
        "summary": summary.strip(),
        "output_sha256": sha256_text(output),
        "output_chars": len(output),
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
        packet.extend([
            "",
            "## Evidence ledger",
            "",
            *evidence_lines,
            "",
            "## Changed files",
            "",
            "Every changed file is represented below and in the bounded diff, whether or not",
            "its full contents were selected into review context.",
            "",
            *([f"- `{item}`" for item in task_changed_files(root, task)] or ["- No task-relative working-tree changes detected."]),
            "",
            "## Compact diff",
            "",
            "```diff",
            compact_diff(root, task["allowed_files"], int(config["max_packet_chars"])),
            "```",
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
    return path, text


def task_fingerprint(root: Path, task_dir: Path) -> str:
    task_bytes = (task_dir / "task.json").read_bytes()
    ledger_bytes = (task_dir / "ledger.jsonl").read_bytes()
    diff = run_git(root, "diff", "--binary", "--no-ext-diff", check=False).encode("utf-8", errors="replace")
    staged = run_git(root, "diff", "--cached", "--binary", "--no-ext-diff", check=False).encode("utf-8", errors="replace")
    untracked_parts: list[bytes] = []
    for relative in sorted(run_git(root, "ls-files", "--others", "--exclude-standard").splitlines()):
        normalized = relative.replace("\\", "/")
        if normalized == STATE_DIR or normalized.startswith(f"{STATE_DIR}/"):
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
) -> dict[str, Any]:
    """Decide whether a seal still describes the exact reviewed repository state.

    A 0.4.1 seal binds the task/evidence/delta fingerprint *and* the exact HEAD
    commit and tree. A 0.4.0 seal carries no repository identity, so it can
    never be treated as an exact-head acceptance; it fails closed with
    `seal_missing_head_identity` and must be re-sealed under 0.4.1.
    """
    if not seal:
        return {"status": "none", "stale": False, "reasons": []}
    reasons: list[str] = []
    if seal.get("fingerprint") != fingerprint:
        reasons.append(SEAL_FINGERPRINT_MISMATCH)
    identity = seal.get("repository_identity")
    if not isinstance(identity, dict) or not identity.get("head_sha"):
        reasons.append(SEAL_MISSING_HEAD_IDENTITY)
    elif identity.get("head_sha") != head_identity.get("head_sha"):
        reasons.append(SEAL_HEAD_MISMATCH)
    elif identity.get("head_tree_sha") != head_identity.get("head_tree_sha"):
        reasons.append(SEAL_HEAD_TREE_MISMATCH)

    if not reasons:
        status = "current"
    elif reasons == [SEAL_MISSING_HEAD_IDENTITY]:
        status = "legacy_unbound"
    elif SEAL_HEAD_MISMATCH in reasons or SEAL_HEAD_TREE_MISMATCH in reasons:
        status = "head_mismatch"
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
    successful = {item["command"] for item in ledger if item["exit_code"] == 0}
    missing_verification = [command for command in task["verification"] if command not in successful]
    fingerprint = task_fingerprint(root, task_dir)
    decision = decide_route(root, task)
    required_review_stages: list[str] = []
    if decision.reviewer:
        required_review_stages.append("independent")
    if decision.gate:
        required_review_stages.append("gate")
    reviews = read_reviews(task_dir)
    latest_by_stage: dict[str, dict[str, Any]] = {}
    for review in reviews:
        latest_by_stage[review["stage"]] = review
    missing_reviews = [stage for stage in required_review_stages if stage not in latest_by_stage]
    stale_reviews = [
        stage
        for stage in required_review_stages
        if stage in latest_by_stage and latest_by_stage[stage].get("fingerprint") != fingerprint
    ]
    failed_reviews = [
        stage
        for stage in required_review_stages
        if stage in latest_by_stage
        and latest_by_stage[stage].get("fingerprint") == fingerprint
        and latest_by_stage[stage].get("verdict") != "ship"
    ]
    ok = not out_of_scope and not missing_verification and not missing_reviews and not stale_reviews and not failed_reviews
    if out_of_scope:
        next_action = "Split or revert out-of-scope files before continuing."
    elif missing_verification:
        next_action = "Run `sera verify` or record the missing verification evidence."
    elif stale_reviews:
        next_action = "Regenerate the review packet and repeat stale review stages."
    elif missing_reviews:
        next_action = f"Obtain required review stage: {missing_reviews[0]}."
    elif failed_reviews:
        next_action = "Address reviewer findings, rerun verification, and review the new fingerprint."
    else:
        next_action = "The current tree satisfies the task contract; proceed to the separate commit decision."
    seal_path = task_dir / "seal.json"
    seal = json.loads(seal_path.read_text(encoding="utf-8")) if seal_path.exists() else None
    head_identity = git_head_identity(root)
    seal_state = evaluate_seal(seal, fingerprint, head_identity)
    seal_stale = seal_state["stale"]
    if ok and seal_stale:
        if seal_state["status"] == "head_mismatch":
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
        "required_review_stages": required_review_stages,
        "reviews": latest_by_stage,
        "missing_reviews": missing_reviews,
        "stale_reviews": stale_reviews,
        "failed_reviews": failed_reviews,
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
) -> dict[str, Any]:
    if verdict not in {"ship", "fix-first", "rethink"}:
        raise SeraError("Verdict must be ship, fix-first, or rethink.")
    if stage not in {"independent", "gate", "supplementary"}:
        raise SeraError("Review stage must be independent, gate, or supplementary.")
    review = {
        "timestamp": utc_now(),
        "fingerprint": fingerprint,
        "stage": stage,
        "verdict": verdict,
        "reviewer": reviewer,
        "reason": reason.strip(),
    }
    with (task_dir / "reviews.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(review, sort_keys=True) + "\n")
    return review
