"""Deterministic, stage-aware context selection.

Ownership answers "what may this task change?". Context answers "what does this
stage need to read?". They are sized and reported independently: a task can own
thirty-six files while a builder packet selects twelve and a reviewer packet
selects eight plus the bounded diff, without any file losing ownership.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .core import (
    decide_route,
    estimate_tokens,
    load_config,
    load_repo_map,
    load_task,
    normalize_repo_path,
    ownership_summary,
    read_ledger,
    task_review_coverage,
    text_tokens,
    utc_now,
)

STOP_WORDS = {
    "about", "after", "again", "against", "also", "and", "are", "because", "before", "build",
    "change", "code", "could", "does", "from", "have", "into", "just", "make", "more", "project",
    "should", "that", "the", "their", "then", "this", "through", "use", "using", "want", "what",
    "when", "where", "which", "with", "without", "work",
}

# Inclusion reasons.
REASON_EXPLICIT_CONTEXT = "explicit_context"
REASON_CHANGED = "selected_changed_file"
REASON_OWNED = "selected_owned"
REASON_RELEVANCE = "selected_by_relevance"
REASON_DEPENDENCY = "selected_dependency"
# Exclusion reasons.
REASON_OWNED_NOT_SELECTED = "owned_not_selected"
REASON_BUDGET = "excluded_by_budget"
REASON_NOT_IN_MAP = "not_in_repository_map"

SELECTION_REASONS = (
    REASON_EXPLICIT_CONTEXT,
    REASON_CHANGED,
    REASON_OWNED,
    REASON_RELEVANCE,
    REASON_DEPENDENCY,
    REASON_OWNED_NOT_SELECTED,
    REASON_BUDGET,
    REASON_NOT_IN_MAP,
)

SCORE_EXPLICIT_CONTEXT = 10_000
SCORE_CHANGED = 4_000
SCORE_OWNED = 1_000
SCORE_DEPENDENCY = 200

_IMPORT_LINE = re.compile(
    r"^\s*(?:from|import|include|require|use|using|#\s*include)\b|\b(?:require|import)\s*\(",
    re.IGNORECASE,
)


def _tokens(value: str) -> set[str]:
    return {token for token in text_tokens(value) if len(token) >= 3 and token not in STOP_WORDS}


def _symbol_tokens(symbol: str) -> set[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", symbol).replace("_", "-")
    return _tokens(expanded)


def _dependency_tokens(root: Path, files: list[str], max_bytes: int) -> set[str]:
    """Collect identifiers referenced by import-like lines in the given files.

    This is a bounded, language-agnostic signal: only the named files are read,
    and only their import/include/require lines contribute tokens.
    """
    tokens: set[str] = set()
    for relative in files:
        path = root / relative
        try:
            if not path.is_file() or path.stat().st_size > max_bytes:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if _IMPORT_LINE.search(line):
                tokens |= {token for token in text_tokens(line) if len(token) >= 3}
    return tokens


def select_context(
    root: Path,
    objective: str,
    owned_files: list[str] | None = None,
    max_files: int | None = None,
    *,
    stage: str = "build",
    budget_tokens: int | None = None,
    changed_files: list[str] | None = None,
    explicit_context: list[str] | None = None,
    config: dict[str, Any] | None = None,
    repo_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select the files a stage should read, and explain every decision.

    Selection is deterministic: candidates are ranked by score, then by size,
    then by path. The highest-priority candidate is always selected so a stage
    is never handed empty context; further candidates are added while both the
    file cap and the token budget allow. Owned files that are not selected keep
    their ownership and are reported with an accurate exclusion reason.
    """
    repo_map = repo_map if repo_map is not None else load_repo_map(root)
    config = config if config is not None else load_config(root)
    controller = config.get("controller", {})
    max_files = int(max_files or controller.get("context_max_files", 12))
    min_score = int(controller.get("context_min_score", 2))
    max_bytes = int(config.get("max_file_bytes", 300_000))

    query = _tokens(objective)
    owned = {normalize_repo_path(path) for path in (owned_files or [])}
    pinned = {normalize_repo_path(path) for path in (explicit_context or [])}
    changed = {normalize_repo_path(path) for path in (changed_files or [])}
    mapped = {item["path"] for item in repo_map["files"]}

    dependency_tokens: set[str] = set()
    if stage == "review" and changed:
        dependency_tokens = _dependency_tokens(root, sorted(changed), max_bytes)

    ranked: list[dict[str, Any]] = []
    for item in repo_map["files"]:
        path = item["path"]
        path_words = _tokens(path.replace("/", " ").replace(".", " "))
        symbol_words: set[str] = set()
        for symbol in item.get("symbols", []):
            symbol_words |= _symbol_tokens(symbol)
        overlap_path = sorted(query & path_words)
        overlap_symbol = sorted(query & symbol_words)
        score = len(overlap_path) * 4 + len(overlap_symbol) * 3
        detail: list[str] = []
        stem = Path(path).stem.lower()
        is_dependency = (
            bool(dependency_tokens)
            and path not in changed
            and len(stem) >= 3
            and stem not in STOP_WORDS
            and stem in dependency_tokens
        )

        if path in pinned:
            reason = REASON_EXPLICIT_CONTEXT
            score += SCORE_EXPLICIT_CONTEXT
            detail.append("explicitly requested context")
        elif stage == "review" and path in changed:
            reason = REASON_CHANGED
            score += SCORE_CHANGED
            detail.append("changed by this task")
        elif path in owned:
            reason = REASON_OWNED
            score += SCORE_OWNED
            detail.append("owned by this task")
        elif is_dependency:
            reason = REASON_DEPENDENCY
            score += SCORE_DEPENDENCY
            detail.append("imported by a changed file")
        else:
            reason = REASON_RELEVANCE

        if overlap_path:
            detail.append("path matches: " + ", ".join(overlap_path))
        if overlap_symbol:
            detail.append("symbol matches: " + ", ".join(overlap_symbol))
        if score < min_score and reason == REASON_RELEVANCE:
            continue
        ranked.append(
            {
                "path": path,
                "score": score,
                "reason": reason,
                "reasons": detail or ["objective relevance"],
                "bytes": int(item.get("bytes", 0)),
                "tokens": estimate_tokens(int(item.get("bytes", 0))),
                "symbols": item.get("symbols", []),
            }
        )

    ranked.sort(key=lambda value: (-value["score"], value["tokens"], value["path"]))

    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    used = 0
    for candidate in ranked:
        if candidate["reason"] == REASON_EXPLICIT_CONTEXT or not selected:
            # Pinned context is never dropped, and a stage always receives its
            # single highest-priority file so context is never silently empty.
            take, drop_reason = True, None
        elif len(selected) >= max_files:
            take, drop_reason = False, REASON_OWNED_NOT_SELECTED
        elif budget_tokens is not None and used + candidate["tokens"] > budget_tokens:
            take, drop_reason = False, REASON_BUDGET
        else:
            take, drop_reason = True, None
        if take:
            selected.append(candidate)
            used += candidate["tokens"]
        elif candidate["path"] in owned or candidate["path"] in pinned:
            excluded.append(
                {
                    "path": candidate["path"],
                    "reason": drop_reason,
                    "tokens": candidate["tokens"],
                    "score": candidate["score"],
                }
            )

    ranked_paths = {item["path"] for item in ranked}
    for path in sorted(owned - ranked_paths):
        # An owned path is only absent from the repository map when it genuinely
        # is not indexed — a new file, or one excluded by size/type filters.
        excluded.append(
            {
                "path": path,
                "reason": REASON_NOT_IN_MAP if path not in mapped else REASON_OWNED_NOT_SELECTED,
                "tokens": 0,
                "score": 0,
            }
        )
    excluded.sort(key=lambda value: value["path"])

    selected_tokens = sum(item["tokens"] for item in selected)
    full_tokens = int(repo_map.get("estimated_full_source_tokens", 0))
    reduction = max(0, full_tokens - selected_tokens)
    ownership_tokens = sum(
        item["tokens"]
        for item in ranked
        if item["path"] in owned
    )
    return {
        "generated_at": utc_now(),
        "objective": objective,
        "stage": stage,
        "query_terms": sorted(query),
        "repository_fingerprint": repo_map["fingerprint"],
        "repository_available_tokens": full_tokens,
        "ownership": {
            "file_count": len(owned),
            "estimated_tokens": ownership_tokens,
            "unmapped_file_count": len(owned - mapped),
        },
        "selected_context": {
            "file_count": len(selected),
            "estimated_tokens": selected_tokens,
        },
        "selected_files": selected,
        "excluded_files": excluded,
        "selected_source_tokens": selected_tokens,
        "context_reduction_tokens": reduction,
        "context_reduction_percent": round(reduction / full_tokens * 100, 2) if full_tokens else 0.0,
        "max_files": max_files,
        "context_budget_tokens": budget_tokens,
    }


def required_stage(result: dict[str, Any]) -> str:
    """The stage whose context budget governs the next controller step."""
    if result["changed_files"] and (result["missing_reviews"] or result["stale_reviews"]):
        return "review"
    return "build"


def select_task_context(
    root: Path,
    task_dir: Path,
    stage: str = "build",
    *,
    repo_map: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select stage context for a task and budget it against the stage allowance.

    Fixed stage overhead — the capsule, plus the bounded diff and evidence
    summary for review — is subtracted from the mode budget first, so the token
    allowance passed to selection is the amount genuinely available for source.
    """
    task = load_task(task_dir)
    config = load_config(root)
    repo_map = repo_map if repo_map is not None else load_repo_map(root)
    route = decide_route(root, task, repo_map)

    capsule_path = task_dir / "capsule.md"
    capsule_tokens = estimate_tokens(capsule_path.read_text(encoding="utf-8")) if capsule_path.exists() else 0
    changed: list[str] = []
    diff_tokens = 0
    evidence_tokens = 0
    diff_ok = True
    diff_reason: str | None = None
    diff_required_chars = 0
    # `None`, not `True`: outside a review stage no coverage assessment was made,
    # and reporting completeness that was never computed is the same overclaim
    # this release exists to remove.
    coverage_complete: bool | None = None
    coverage_reason: str | None = None
    change_fingerprint: str | None = None
    committed_range: list[str] | None = None
    if stage == "review":
        # Non-raising here: `sera next` must report an insufficient review-diff
        # budget or incomplete change coverage as controller states rather than
        # crashing.
        coverage = task_review_coverage(root, task, int(config["max_packet_chars"]))
        changed = coverage["changed_paths"]
        diff_ok = coverage["ok"]
        diff_reason = coverage["reason"]
        diff_required_chars = coverage["required_chars"]
        coverage_complete = coverage["coverage_complete"]
        coverage_reason = coverage["coverage_reason"]
        change_fingerprint = coverage["change_fingerprint"]
        committed_range = coverage["committed_range"]
        diff_tokens = estimate_tokens(coverage["text"]) if coverage["text"] else 0
        ledger = read_ledger(task_dir)
        evidence_tokens = estimate_tokens(json.dumps(ledger, sort_keys=True)) if ledger else 0

    overhead = capsule_tokens + diff_tokens + evidence_tokens
    allowance = max(0, route.budget_tokens - overhead)
    report = select_context(
        root,
        task["objective"],
        task.get("allowed_files", []),
        stage=stage,
        budget_tokens=allowance,
        changed_files=changed,
        config=config,
        repo_map=repo_map,
    )

    total = report["selected_source_tokens"] + overhead
    report["task_id"] = task["id"]
    report["capsule_tokens"] = capsule_tokens
    report["diff_tokens"] = diff_tokens
    report["evidence_tokens"] = evidence_tokens
    report["stage_overhead_tokens"] = overhead
    report["stage_budget_tokens"] = route.budget_tokens
    report["context_allowance_tokens"] = allowance
    report["estimated_context_tokens"] = total
    report["within_budget"] = total <= route.budget_tokens
    report["budget_overage_tokens"] = max(0, total - route.budget_tokens)
    report["changed_files"] = changed
    report["ownership"] = ownership_summary(task, repo_map)
    report["changed_files_covered_by_diff"] = diff_ok
    report["review_diff_ok"] = diff_ok
    report["review_diff_reason"] = diff_reason
    report["review_diff_required_chars"] = diff_required_chars
    # Freshness and completeness are distinct facts. A packet can be perfectly
    # current and still not represent the whole change set.
    report["review_coverage_complete"] = coverage_complete
    report["review_coverage_reason"] = coverage_reason
    report["review_change_fingerprint"] = change_fingerprint
    report["review_committed_range"] = committed_range
    return report
