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

STATE_DIR = ".sera"
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
            "provider": "custom",
            "model": "claude-fable-5",
            "enabled": False,
            "allowed_uses": ["prototype", "creative-ui", "second-attempt", "supplementary-review"],
            "may_be_sole_release_gate": False,
        },
    },
    "verification": [],
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
    return merged


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
    mode: str,
    risk: str,
    allowed_files: list[str],
    constraints: list[str],
    verification: list[str],
    uncertainty: int = 1,
    use_case: str = "implementation",
) -> Path:
    if mode not in {"fast", "standard", "assured"}:
        raise SeraError("Mode must be fast, standard, or assured.")
    if risk not in {"low", "medium", "high"}:
        raise SeraError("Risk must be low, medium, or high.")
    if not objective.strip():
        raise SeraError("Objective cannot be empty.")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    task_id = f"{timestamp}-{slugify(name)}"
    task_dir = state_path(root) / "tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=False)
    config = load_config(root)
    task = {
        "schema_version": 1,
        "id": task_id,
        "name": name,
        "created_at": utc_now(),
        "mode": mode,
        "risk": risk,
        "uncertainty": max(0, min(3, int(uncertainty))),
        "use_case": use_case,
        "objective": objective.strip(),
        "allowed_files": sorted(set(allowed_files)),
        "constraints": constraints,
        "verification": verification or list(config.get("verification", [])),
        "builder_attempts": 0,
        "status": "specified",
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


def decide_route(root: Path, task: dict[str, Any], repo_map: dict[str, Any] | None = None) -> RouteDecision:
    config = load_config(root)
    repo_map = repo_map or load_repo_map(root)
    file_index = {item["path"]: item for item in repo_map["files"]}
    context_bytes = sum(file_index.get(path, {}).get("bytes", 8_000) for path in task["allowed_files"])
    context_tokens = estimate_tokens(context_bytes)
    file_count = len(task["allowed_files"])
    risk_score = {"low": 1, "medium": 2, "high": 3}[task["risk"]]
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
    return RouteDecision(builder, reviewer, gate, reason, context_tokens, budget, fable_eligible)


def lane_label(config: dict[str, Any], lane: str | None) -> str | None:
    if lane is None:
        return None
    value = config["lanes"].get(lane, {})
    provider = value.get("provider", "unconfigured")
    model = value.get("model", "unconfigured")
    enabled = value.get("enabled", True)
    return f"{lane}: {provider}/{model}" + ("" if enabled else " (disabled)")


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


def generate_packet(root: Path, task_dir: Path, stage: str) -> tuple[Path, str]:
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
    route_lines = [
        f"- Builder: {lane_label(config, decision.builder)}",
        f"- Reviewer: {lane_label(config, decision.reviewer) or 'not required by current mode'}",
        f"- Release gate: {lane_label(config, decision.gate) or 'not required by current mode'}",
        f"- Reason: {decision.reason}",
        f"- Estimated owned-context tokens: {decision.estimated_context_tokens:,}",
        f"- Stage token budget: {decision.budget_tokens:,}",
        f"- Optional Fable eligible: {'yes' if decision.fable_eligible else 'no'}",
    ]
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
            *([f"- `{item}`" for item in changed_files(root)] or ["- No working-tree changes detected."]),
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
    return {
        "full_source_tokens": full_tokens,
        "repo_map_tokens": map_tokens,
        "capsule_tokens": capsule_tokens,
        "orientation_tokens": orientation_tokens,
        "estimated_orientation_tokens_avoided": avoided,
        "estimated_avoidance_percent": round((avoided / full_tokens * 100), 1) if full_tokens else 0.0,
        "owned_context_tokens": decision.estimated_context_tokens,
        "stage_budget_tokens": decision.budget_tokens,
        "within_budget": decision.estimated_context_tokens + orientation_tokens <= decision.budget_tokens,
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
        f"- Seal: `{'stale' if result['seal_stale'] else 'current' if result['seal'] else 'none'}`",
        f"- Next action: {result['next_action']}",
    ]
    output = "\n".join(lines) + "\n"
    path = task_dir / "summary.md"
    path.write_text(output, encoding="utf-8")
    return path, output


def create_seal(root: Path, task_dir: Path) -> dict[str, Any]:
    result = check_task(root, task_dir)
    if not result["ok"]:
        raise SeraError(f"Task cannot be sealed: {result['next_action']}")
    task = load_task(task_dir)
    seal = {
        "schema_version": 1,
        "task_id": task["id"],
        "sealed_at": utc_now(),
        "fingerprint": result["fingerprint"],
        "evidence_records": len(read_ledger(task_dir)),
        "review_stages": sorted(result["reviews"].keys()),
        "changed_files": result["changed_files"],
    }
    (task_dir / "seal.json").write_text(json.dumps(seal, indent=2) + "\n", encoding="utf-8")
    return seal


def check_task(root: Path, task_dir: Path) -> dict[str, Any]:
    task = load_task(task_dir)
    changed = changed_files(root)
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
    seal_stale = bool(seal and seal.get("fingerprint") != fingerprint)
    if ok and seal_stale:
        next_action = "Create a new SERA Seal for the current fingerprint."
    elif ok and seal is None:
        next_action = "Create the SERA Seal, then proceed to the separate commit decision."
    return {
        "ok": ok,
        "changed_files": changed,
        "out_of_scope": out_of_scope,
        "missing_verification": missing_verification,
        "fingerprint": fingerprint,
        "required_review_stages": required_review_stages,
        "reviews": latest_by_stage,
        "missing_reviews": missing_reviews,
        "stale_reviews": stale_reviews,
        "failed_reviews": failed_reviews,
        "seal": seal,
        "seal_stale": seal_stale,
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
