from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import (
    SELECTION_REASONS,
    STOP_WORDS,
    required_stage,
    select_context,
    select_task_context,
)
from .core import (
    HIGH_RISK_TERMS,
    MEDIUM_RISK_TERMS,
    SeraError,
    apply_task_policy,
    assess_risk,
    budget_report,
    check_task,
    decide_route,
    estimate_tokens,
    format_risk_reason,
    generate_packet,
    lane_label,
    load_config,
    load_task,
    new_task,
    normalize_repo_path,
    packet_state,
    resolve_task_dir,
    state_path,
    utc_now,
    update_repo_map,
    write_task_capsule,
)

__all__ = [
    "HIGH_RISK_TERMS",
    "MEDIUM_RISK_TERMS",
    "STOP_WORDS",
    "SELECTION_REASONS",
    "assess_risk",
    "auto_task",
    "budget_view",
    "build_packet",
    "confirm_task_ownership",
    "context_report",
    "efficiency_report",
    "ensure_controller_config",
    "inbox_report",
    "infer_risk",
    "next_action",
    "prepare_run",
    "record_context",
    "required_stage",
    "resume_report",
    "select_context",
    "select_task_context",
]


def ensure_controller_config(root: Path) -> dict[str, Any]:
    path = state_path(root) / "config.json"
    config = load_config(root)
    controller = config.setdefault("controller", {})
    controller.setdefault("context_max_files", 12)
    controller.setdefault("context_min_score", 2)
    controller.setdefault("enforce_context_budget", True)
    controller.setdefault("auto_risk", True)
    policy = config.setdefault("risk_policy", {})
    policy.setdefault("high_risk_terms", [])
    policy.setdefault("high_risk_paths", [])
    fable = config.setdefault("lanes", {}).setdefault("optional_fable", {})
    if fable.get("provider") in {None, "custom"}:
        fable["provider"] = "anthropic"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def infer_risk(
    objective: str,
    files: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Human-readable view of `assess_risk`.

    Machine consumers should use `assess_risk`, which returns structured
    reasons; this wrapper renders them as text for terminal output.
    """
    assessment = assess_risk(config, objective, files)
    return assessment["risk"], [format_risk_reason(reason) for reason in assessment["reasons"]]


def record_context(task_dir: Path, report: dict[str, Any], stage: str) -> dict[str, Any]:
    record = {
        "timestamp": utc_now(),
        "stage": stage,
        "repository_fingerprint": report["repository_fingerprint"],
        "repository_available_tokens": report["repository_available_tokens"],
        "ownership": report.get("ownership", {}),
        "selected_context": report.get("selected_context", {}),
        "selected_source_tokens": report["selected_source_tokens"],
        "context_reduction_tokens": report["context_reduction_tokens"],
        "context_reduction_percent": report["context_reduction_percent"],
        "files": [
            {
                "path": item["path"],
                "tokens": item["tokens"],
                "reason": item["reason"],
                "reasons": item["reasons"],
            }
            for item in report["selected_files"]
        ],
        "excluded": [
            {"path": item["path"], "reason": item["reason"], "tokens": item["tokens"]}
            for item in report.get("excluded_files", [])
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
    candidate_files = list(files) if files else context_candidates
    # `new_task` owns mode precedence and risk composition so every drafting
    # path resolves them identically.
    task_dir = new_task(
        root,
        name or request[:72],
        request,
        mode,
        risk,
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
        "mode_source": task.get("mode_source"),
        "risk_reasons": task.get("risk_reasons", []),
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
    """Confirm exact ownership and re-derive policy from the new contract.

    Confirming ownership changes what the task is authorized to touch, so it
    must re-run the same risk and mode resolution used at creation. Skipping it
    would let a task confirmed onto a high-risk path keep a fast, review-free
    route.
    """
    task = load_task(task_dir)
    if files:
        task["allowed_files"] = sorted({normalize_repo_path(path) for path in files})
    if not task.get("allowed_files"):
        raise SeraError("Cannot confirm empty ownership; provide --file at least once.")
    config = load_config(root)
    apply_task_policy(config, task)
    controller = task.setdefault("controller", {})
    controller["ownership_confirmed"] = True
    controller["ownership_confirmed_at"] = utc_now()
    controller["mode_source"] = task.get("mode_source")
    controller["risk_reasons"] = task.get("risk_reasons", [])
    (task_dir / "task.json").write_text(json.dumps(task, indent=2) + "\n", encoding="utf-8")
    # Derived artifacts must not survive a contract mutation as authoritative:
    # the capsule is rewritten now, and packets become stale by fingerprint.
    write_task_capsule(task_dir, task)
    return task

def next_action(root: Path, task_dir: Path) -> dict[str, Any]:
    task = load_task(task_dir)
    config = load_config(root)
    decision = decide_route(root, task)
    result = check_task(root, task_dir)
    build_packet_state = packet_state(root, task_dir, "build", task)
    review_packet_state = packet_state(root, task_dir, "review", task, result["fingerprint"])
    stage = required_stage(result)
    stage_context = context_report(root, task_dir, stage=stage, record=False)

    controller = task.get("controller", {})
    if not task.get("allowed_files"):
        action, command, reason = "resolve_ownership", None, "No exact owned files are defined."
    elif controller.get("auto_drafted") and not controller.get("ownership_confirmed", False):
        action, command, reason = "confirm_ownership", "sera task confirm", "Auto-selected files are candidates until the controller confirms exact ownership."
    elif stage == "review" and not stage_context.get("review_diff_ok", True):
        # Fail closed rather than hand a reviewer a packet that hides changes.
        action, command, reason = (
            "review_diff_budget_insufficient",
            None,
            f"Covering every changed file needs at least "
            f"{stage_context['review_diff_required_chars']:,} characters of review diff, which exceeds "
            f"max_packet_chars. Raise max_packet_chars or split the task.",
        )
    elif stage == "review" and not stage_context.get("review_coverage_complete", True):
        # Currency is not completeness: refuse to dispatch a review that cannot
        # be shown to represent the whole change set.
        action, command, reason = (
            "review_coverage_incomplete",
            None,
            f"{stage_context.get('review_coverage_reason')}: the complete task change set cannot be "
            "derived, so a review packet would understate what changed. Create a task under this "
            "version of SERA, or restore the baseline commit, before requesting review.",
        )
    elif config.get("controller", {}).get("enforce_context_budget", True) and not stage_context["within_budget"]:
        # Budget is measured against the context selected for the required next
        # stage, never against the full ownership set.
        action, command, reason = (
            "reduce_context",
            "sera context --why",
            f"Selected {stage} context is {stage_context['estimated_context_tokens']:,} tokens against a "
            f"{stage_context['stage_budget_tokens']:,} budget; split the task or narrow the stage context.",
        )
    elif not build_packet_state["current"]:
        action, command = "build_packet", "sera packet build"
        reason = {
            "packet_missing": "The task is specified but no builder handoff exists.",
            "packet_unbound": "The existing builder packet has no valid task binding and cannot be dispatched.",
            "packet_stale_contract": "The task contract changed after this builder packet was generated; regenerate before dispatch.",
        }.get(build_packet_state["reason"], "The builder packet is not current for this task contract.")
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
        action, command = "review", "sera packet review"
        reasons = sorted({item for stage_reasons in result["stale_review_reasons"].values() for item in stage_reasons})
        reason = (
            "One or more required reviews no longer describe the current repository state "
            f"({', '.join(reasons)}). Regenerate the review packet and repeat those stages."
        )
    # A current failed review outranks a missing later stage: never dispatch a
    # release gate for an implementation the independent stage already refused.
    elif result["failed_reviews"]:
        action, command, reason = (
            "fix_first",
            None,
            f"A current required review returned "
            f"`{result['reviews'][result['failed_reviews'][0]]['verdict']}` at the "
            f"{result['failed_reviews'][0]} stage. Address the findings before any later stage.",
        )
    elif result["missing_reviews"]:
        if not review_packet_state["current"]:
            action, command = "review_packet", "sera packet review"
        else:
            action, command = "dispatch_review", None
        reason = f"Required review stage: {result['missing_reviews'][0]}."
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
        "mode": task["mode"],
        "risk": task["risk"],
        "risk_reasons": task.get("risk_reasons", []),
        "route": {
            "builder": lane_label(config, decision.builder),
            "reviewer": lane_label(config, decision.reviewer),
            "gate": lane_label(config, decision.gate),
        },
        "required_stage": stage,
        "build_packet": build_packet_state,
        "review_packet": review_packet_state,
        "ownership": stage_context["ownership"],
        "selected_context": stage_context["selected_context"],
        "stage_budget_tokens": stage_context["stage_budget_tokens"],
        "within_budget": stage_context["within_budget"],
        "fingerprint": result["fingerprint"],
        "head_identity": result["head_identity"],
        "baseline_repository_identity": result["baseline_repository_identity"],
        "review_states": result["review_states"],
        "stale_review_reasons": result["stale_review_reasons"],
        "failed_reviews": result["failed_reviews"],
        "review_coverage": {
            "complete": stage_context.get("review_coverage_complete", True),
            "reason": stage_context.get("review_coverage_reason"),
            "change_fingerprint": stage_context.get("review_change_fingerprint"),
            "committed_range": stage_context.get("review_committed_range"),
        },
        "seal_status": result["seal_status"],
        "seal_stale_reasons": result["seal_stale_reasons"],
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
            "risk_reasons": task.get("risk_reasons", []),
            "owned_files": task["allowed_files"],
        },
        "budget": budget_view(root, task_dir),
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


def context_report(
    root: Path,
    task_dir: Path,
    *,
    stage: str | None = None,
    record: bool = True,
) -> dict[str, Any]:
    """Select and budget context for one stage of a task."""
    if stage is None:
        stage = required_stage(check_task(root, task_dir))
    report = select_task_context(root, task_dir, stage)
    if record:
        record_context(task_dir, report, stage)
    return report


def build_packet(root: Path, task_dir: Path, stage: str) -> tuple[Path, str]:
    """Generate a stage packet carrying stage-selected context."""
    selected = select_task_context(root, task_dir, stage)
    record_context(task_dir, selected, stage)
    return generate_packet(root, task_dir, stage, selected_context=selected)


def budget_view(root: Path, task_dir: Path) -> dict[str, Any]:
    """Merge orientation/ownership accounting with stage context budgeting."""
    report = budget_report(root, task_dir)
    stage = required_stage(check_task(root, task_dir))
    context = context_report(root, task_dir, stage=stage, record=False)
    report["required_stage"] = stage
    report["selected_context"] = context["selected_context"]
    report["stage_overhead_tokens"] = context["stage_overhead_tokens"]
    report["estimated_context_tokens"] = context["estimated_context_tokens"]
    report["within_budget"] = context["within_budget"]
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
    context_budget = context_report(root, task_dir, stage="build", record=False)
    ownership_confirmed = task.get("controller", {}).get("ownership_confirmed", False)
    budget_ok = context_budget["within_budget"] or not config.get("controller", {}).get("enforce_context_budget", True)
    if ownership_confirmed and budget_ok:
        packet_path, packet = build_packet(root, task_dir, "build")
    route = decide_route(root, task)
    return {
        "task_id": task["id"],
        "task_dir": task_dir.relative_to(root).as_posix(),
        "mode": task["mode"],
        "mode_source": task.get("mode_source"),
        "risk": task["risk"],
        "risk_reasons": task.get("risk_reasons", []),
        "owned_files": task["allowed_files"],
        "ownership": context_budget["ownership"],
        "selected_context": context_budget["selected_context"],
        "context": {
            "repository_available_tokens": context["repository_available_tokens"],
            "selected_source_tokens": context_budget["selected_source_tokens"],
            "context_reduction_percent": context_budget["context_reduction_percent"],
            "stage_budget_tokens": context_budget["stage_budget_tokens"],
            "within_budget": context_budget["within_budget"],
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
