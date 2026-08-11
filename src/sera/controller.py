from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .core import (
    SeraError,
    budget_report,
    check_task,
    decide_route,
    estimate_tokens,
    generate_packet,
    lane_label,
    load_config,
    load_repo_map,
    load_task,
    new_task,
    resolve_task_dir,
    state_path,
    utc_now,
    update_repo_map,
)

STOP_WORDS = {
    "about", "after", "again", "against", "also", "and", "are", "because", "before", "build",
    "change", "code", "could", "does", "from", "have", "into", "just", "make", "more", "project",
    "should", "that", "the", "their", "then", "this", "through", "use", "using", "want", "what",
    "when", "where", "which", "with", "without", "work",
}

HIGH_RISK_TERMS = {
    "auth", "authentication", "authorization", "balance", "cryptography", "deploy", "deployment",
    "ledger", "migration", "money", "password", "payment", "payout", "permission", "production",
    "secret", "session", "settlement", "transaction", "treasury", "wallet",
}

MEDIUM_RISK_TERMS = {
    "api", "concurrency", "database", "idempotency", "idempotent", "integration", "schema", "state",
    "webhook",
}


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if len(token) >= 3 and token not in STOP_WORDS
    }


def _symbol_tokens(symbol: str) -> set[str]:
    expanded = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", symbol).replace("_", "-")
    return _tokens(expanded)


def ensure_controller_config(root: Path) -> dict[str, Any]:
    path = state_path(root) / "config.json"
    config = load_config(root)
    controller = config.setdefault("controller", {})
    controller.setdefault("context_max_files", 12)
    controller.setdefault("context_min_score", 2)
    controller.setdefault("enforce_context_budget", True)
    controller.setdefault("auto_risk", True)
    fable = config.setdefault("lanes", {}).setdefault("optional_fable", {})
    if fable.get("provider") in {None, "custom"}:
        fable["provider"] = "anthropic"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def infer_risk(objective: str, files: list[str] | None = None) -> tuple[str, list[str]]:
    haystack = " ".join([objective, *(files or [])]).lower()
    words = _tokens(haystack)
    high = sorted(term for term in HIGH_RISK_TERMS if term in words)
    if high:
        return "high", [f"high-risk term: {term}" for term in high]
    medium = sorted(term for term in MEDIUM_RISK_TERMS if term in words)
    if medium:
        return "medium", [f"medium-risk term: {term}" for term in medium]
    return "low", ["no configured high- or medium-risk terms detected"]


def select_context(
    root: Path,
    objective: str,
    explicit_files: list[str] | None = None,
    max_files: int | None = None,
) -> dict[str, Any]:
    repo_map = load_repo_map(root)
    config = load_config(root)
    controller = config.get("controller", {})
    max_files = int(max_files or controller.get("context_max_files", 12))
    min_score = int(controller.get("context_min_score", 2))
    query = _tokens(objective)
    explicit = {path.replace("\\", "/") for path in (explicit_files or [])}
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
        reasons: list[str] = []
        if path in explicit:
            score += 1000
            reasons.append("explicit ownership")
        if overlap_path:
            reasons.append("path matches: " + ", ".join(overlap_path))
        if overlap_symbol:
            reasons.append("symbol matches: " + ", ".join(overlap_symbol))
        if score >= min_score or path in explicit:
            ranked.append(
                {
                    "path": path,
                    "score": score,
                    "reasons": reasons or ["objective relevance"],
                    "bytes": int(item.get("bytes", 0)),
                    "tokens": estimate_tokens(int(item.get("bytes", 0))),
                    "symbols": item.get("symbols", []),
                }
            )

    ranked.sort(key=lambda value: (-value["score"], value["tokens"], value["path"]))
    selected = ranked[:max_files]
    selected_paths = {item["path"] for item in selected}
    for path in sorted(explicit - selected_paths):
        selected.append(
            {
                "path": path,
                "score": 1000,
                "reasons": ["explicit ownership; file is not present in the current repository map"],
                "bytes": 0,
                "tokens": 0,
                "symbols": [],
            }
        )

    selected_tokens = sum(item["tokens"] for item in selected)
    full_tokens = int(repo_map.get("estimated_full_source_tokens", 0))
    reduction = max(0, full_tokens - selected_tokens)
    return {
        "generated_at": utc_now(),
        "objective": objective,
        "query_terms": sorted(query),
        "repository_fingerprint": repo_map["fingerprint"],
        "repository_available_tokens": full_tokens,
        "selected_files": selected,
        "selected_source_tokens": selected_tokens,
        "context_reduction_tokens": reduction,
        "context_reduction_percent": round(reduction / full_tokens * 100, 2) if full_tokens else 0.0,
        "max_files": max_files,
    }


def record_context(task_dir: Path, report: dict[str, Any], stage: str) -> dict[str, Any]:
    record = {
        "timestamp": utc_now(),
        "stage": stage,
        "repository_fingerprint": report["repository_fingerprint"],
        "repository_available_tokens": report["repository_available_tokens"],
        "selected_source_tokens": report["selected_source_tokens"],
        "context_reduction_tokens": report["context_reduction_tokens"],
        "context_reduction_percent": report["context_reduction_percent"],
        "files": [
            {"path": item["path"], "tokens": item["tokens"], "reasons": item["reasons"]}
            for item in report["selected_files"]
        ],
    }
    with (task_dir / "context-ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return record


def auto_task(
    root: Path,
    request: str,
    *,
    name: str | None = None,
    mode: str = "auto",
    risk: str = "auto",
    uncertainty: int = 1,
    files: list[str] | None = None,
    constraints: list[str] | None = None,
    verification: list[str] | None = None,
    use_case: str = "implementation",
) -> tuple[Path, dict[str, Any]]:
    ensure_controller_config(root)
    update_repo_map(root)
    context = select_context(root, request, files)
    context_candidates = [item["path"] for item in context["selected_files"]]
    candidate_files = [path.replace("\\", "/") for path in files] if files else context_candidates
    inferred_risk, risk_reasons = infer_risk(request, candidate_files)
    controller_cfg = load_config(root).get("controller", {})
    final_risk = (inferred_risk if controller_cfg.get("auto_risk", True) else "medium") if risk == "auto" else risk
    if mode == "auto":
        if final_risk == "high":
            final_mode = "assured"
        elif final_risk == "low" and uncertainty == 0 and len(candidate_files) <= 2:
            final_mode = "fast"
        else:
            final_mode = "standard"
    else:
        final_mode = mode

    if final_risk == "high" and final_mode != "assured":
        final_mode = "assured"
        risk_reasons.append("high-risk work automatically escalates to assured mode")

    task_dir = new_task(
        root,
        name or request[:72],
        request,
        final_mode,
        final_risk,
        candidate_files,
        constraints or [],
        verification or [],
        uncertainty,
        use_case,
    )
    task = load_task(task_dir)
    task["controller"] = {
        "auto_drafted": True,
        "ownership_confirmed": bool(files),
        "risk_reasons": risk_reasons,
        "context_selection": {
            "repository_fingerprint": context["repository_fingerprint"],
            "selected_source_tokens": context["selected_source_tokens"],
            "repository_available_tokens": context["repository_available_tokens"],
            "context_reduction_percent": context["context_reduction_percent"],
        },
    }
    (task_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    record_context(task_dir, context, "specify")
    return task_dir, context



def confirm_task_ownership(root: Path, task_dir: Path, files: list[str] | None = None) -> dict[str, Any]:
    task = load_task(task_dir)
    if files:
        task["allowed_files"] = sorted({path.replace("\\", "/") for path in files})
    if not task.get("allowed_files"):
        raise SeraError("Cannot confirm empty ownership; provide --file at least once.")
    controller = task.setdefault("controller", {})
    controller["ownership_confirmed"] = True
    controller["ownership_confirmed_at"] = utc_now()
    (task_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    return task

def next_action(root: Path, task_dir: Path) -> dict[str, Any]:
    task = load_task(task_dir)
    config = load_config(root)
    decision = decide_route(root, task)
    packet_build = task_dir / "packet-build.md"
    packet_review = task_dir / "packet-review.md"
    result = check_task(root, task_dir)

    controller = task.get("controller", {})
    if not task.get("allowed_files"):
        action, command, reason = "resolve_ownership", None, "No exact owned files are defined."
    elif controller.get("auto_drafted") and not controller.get("ownership_confirmed", False):
        action, command, reason = "confirm_ownership", "sera task confirm", "Auto-selected files are candidates until the controller confirms exact ownership."
    elif load_config(root).get("controller", {}).get("enforce_context_budget", True) and not context_report(root, task_dir, record=False)["within_budget"]:
        action, command, reason = "reduce_context", "sera context --why", "Selected task context exceeds the configured mode budget; reduce ownership/context or split the task."
    elif not packet_build.exists():
        action, command, reason = "build_packet", "sera packet build", "The task is specified but no builder handoff exists."
    elif not result["changed_files"]:
        action, command, reason = (
            "dispatch_builder",
            None,
            f"Dispatch {lane_label(config, decision.builder)} with packet-build.md; SERA core does not invoke providers directly.",
        )
    elif result["out_of_scope"]:
        action, command, reason = "resolve_scope", None, "Working-tree changes exceed declared ownership."
    elif result["missing_verification"]:
        action, command, reason = "verify", "sera verify", "Required verification evidence is missing."
    elif result["stale_reviews"]:
        action, command, reason = "review", "sera packet review", "One or more required reviews are stale for the current fingerprint."
    elif result["missing_reviews"]:
        if not packet_review.exists():
            action, command = "review_packet", "sera packet review"
        else:
            action, command = "dispatch_review", None
        reason = f"Required review stage: {result['missing_reviews'][0]}."
    elif result["failed_reviews"]:
        action, command, reason = "fix_first", None, "A current required review did not return ship."
    elif result["ok"] and (not result["seal"] or result["seal_stale"]):
        action, command, reason = "seal", "sera seal", "Verification and required reviews pass; bind acceptance to this fingerprint."
    elif result["ok"] and result["seal"] and not result["seal_stale"]:
        action, command, reason = "accepted", None, "The exact current tree is verified, reviewed, and sealed."
    else:
        action, command, reason = "inspect", "sera status", result["next_action"]

    return {
        "task_id": task["id"],
        "state": action,
        "next_action": action,
        "command": command,
        "reason": reason,
        "route": {
            "builder": lane_label(config, decision.builder),
            "reviewer": lane_label(config, decision.reviewer),
            "gate": lane_label(config, decision.gate),
        },
        "fingerprint": result["fingerprint"],
        "seal_current": bool(result["seal"] and not result["seal_stale"]),
    }


def resume_report(root: Path, task_ref: str | None = None) -> dict[str, Any]:
    task_dir = resolve_task_dir(root, task_ref)
    task = load_task(task_dir)
    return {
        "task": {
            "id": task["id"],
            "name": task["name"],
            "objective": task["objective"],
            "mode": task["mode"],
            "risk": task["risk"],
            "owned_files": task["allowed_files"],
        },
        "budget": budget_report(root, task_dir),
        "next": next_action(root, task_dir),
    }


def inbox_report(root: Path) -> dict[str, Any]:
    tasks_root = state_path(root) / "tasks"
    tasks: list[dict[str, Any]] = []
    if tasks_root.exists():
        for task_dir in sorted((path for path in tasks_root.iterdir() if path.is_dir()), reverse=True):
            try:
                task = load_task(task_dir)
                nxt = next_action(root, task_dir)
            except (OSError, json.JSONDecodeError):
                continue
            tasks.append(
                {
                    "id": task["id"],
                    "name": task["name"],
                    "mode": task["mode"],
                    "risk": task["risk"],
                    "state": nxt["state"],
                    "next_action": nxt["next_action"],
                }
            )
    return {"generated_at": utc_now(), "count": len(tasks), "tasks": tasks}


def context_report(root: Path, task_dir: Path, *, record: bool = True) -> dict[str, Any]:
    task = load_task(task_dir)
    report = select_context(root, task["objective"], task.get("allowed_files", []))
    if record:
        record_context(task_dir, report, "inspect")
    route = decide_route(root, task)
    capsule_tokens = estimate_tokens((task_dir / "capsule.md").read_text(encoding="utf-8"))
    total = report["selected_source_tokens"] + capsule_tokens
    report["task_id"] = task["id"]
    report["capsule_tokens"] = capsule_tokens
    report["stage_budget_tokens"] = route.budget_tokens
    report["estimated_context_tokens"] = total
    report["within_budget"] = total <= route.budget_tokens
    report["budget_overage_tokens"] = max(0, total - route.budget_tokens)
    return report


def efficiency_report(root: Path, task_dir: Path) -> dict[str, Any]:
    task = load_task(task_dir)
    context = context_report(root, task_dir, record=False)
    packet_tokens: dict[str, int] = {}
    for stage in ("build", "review"):
        path = task_dir / f"packet-{stage}.md"
        if path.exists():
            packet_tokens[stage] = estimate_tokens(path.read_text(encoding="utf-8"))
    ledger_path = task_dir / "ledger.jsonl"
    raw_evidence_tokens = 0
    compact_evidence_tokens = 0
    if ledger_path.exists():
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            evidence = json.loads(line)
            raw_evidence_tokens += estimate_tokens(int(evidence.get("output_chars", 0)))
            compact_evidence_tokens += estimate_tokens(json.dumps(evidence, sort_keys=True))

    selected = context["estimated_context_tokens"]
    full = context["repository_available_tokens"]
    score = 100
    if not context["within_budget"]:
        score -= min(35, 10 + context["budget_overage_tokens"] // 1000)
    if context["context_reduction_percent"] < 80:
        score -= 20
    if len(context["selected_files"]) >= int(load_config(root).get("controller", {}).get("context_max_files", 12)):
        score -= 5
    score = max(0, min(100, int(score)))
    return {
        "task_id": task["id"],
        "mode": task["mode"],
        "repository_available_tokens": full,
        "selected_orientation_tokens": selected,
        "context_reduction_tokens": max(0, full - selected),
        "context_reduction_percent": round(max(0, full - selected) / full * 100, 2) if full else 0.0,
        "packet_tokens": packet_tokens,
        "raw_evidence_output_tokens": raw_evidence_tokens,
        "compact_evidence_record_tokens": compact_evidence_tokens,
        "estimated_evidence_tokens_avoided": max(0, raw_evidence_tokens - compact_evidence_tokens),
        "within_budget": context["within_budget"],
        "efficiency_score": score,
        "measurement_note": "Provider-neutral estimates based on local characters/bytes; not API billing telemetry.",
    }


def prepare_run(
    root: Path,
    request: str,
    *,
    name: str | None = None,
    mode: str = "auto",
    risk: str = "auto",
    uncertainty: int = 1,
    files: list[str] | None = None,
    constraints: list[str] | None = None,
    verification: list[str] | None = None,
    use_case: str = "implementation",
) -> dict[str, Any]:
    task_dir, context = auto_task(
        root,
        request,
        name=name,
        mode=mode,
        risk=risk,
        uncertainty=uncertainty,
        files=files,
        constraints=constraints,
        verification=verification,
        use_case=use_case,
    )
    task = load_task(task_dir)
    packet_path = None
    packet = ""
    config = load_config(root)
    context_budget = context_report(root, task_dir, record=False)
    ownership_confirmed = task.get("controller", {}).get("ownership_confirmed", False)
    budget_ok = context_budget["within_budget"] or not config.get("controller", {}).get("enforce_context_budget", True)
    if ownership_confirmed and budget_ok:
        packet_path, packet = generate_packet(root, task_dir, "build")
    route = decide_route(root, task)
    return {
        "task_id": task["id"],
        "task_dir": task_dir.relative_to(root).as_posix(),
        "mode": task["mode"],
        "risk": task["risk"],
        "owned_files": task["allowed_files"],
        "context": {
            "repository_available_tokens": context["repository_available_tokens"],
            "selected_source_tokens": context["selected_source_tokens"],
            "context_reduction_percent": context["context_reduction_percent"],
        },
        "route": {
            "builder": lane_label(config, route.builder),
            "reviewer": lane_label(config, route.reviewer),
            "gate": lane_label(config, route.gate),
        },
        "builder_packet": packet_path.relative_to(root).as_posix() if packet_path else None,
        "builder_packet_tokens": estimate_tokens(packet) if packet else 0,
        "next": next_action(root, task_dir),
        "provider_dispatch": "controller-managed; SERA core does not invoke model providers",
    }
