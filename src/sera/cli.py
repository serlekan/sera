from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .controller import (
    auto_task,
    budget_view,
    build_packet,
    confirm_task_ownership,
    context_report,
    efficiency_report,
    ensure_controller_config,
    inbox_report,
    next_action,
    prepare_run,
    resume_report,
)
from .core import (
    SeraError,
    accept_review,
    build_repo_map,
    check_task,
    create_seal,
    decide_route,
    estimate_tokens,
    find_repo_root,
    format_risk_reason,
    generate_summary,
    initialize,
    lane_label,
    load_config,
    load_repo_map,
    load_task,
    new_task,
    record_evidence,
    resolve_task_dir,
    run_verification,
    update_repo_map,
)


def _json_flag(command: argparse.ArgumentParser) -> None:
    command.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")


def _auto_task_flags(command: argparse.ArgumentParser) -> None:
    command.add_argument("--name")
    command.add_argument("--mode", choices=["auto", "fast", "standard", "assured"], default="auto")
    command.add_argument("--risk", choices=["auto", "low", "medium", "high"], default="auto")
    command.add_argument("--uncertainty", type=int, choices=[0, 1, 2, 3], default=1)
    command.add_argument("--use-case", default="implementation")
    command.add_argument("--file", action="append", default=[], dest="files")
    command.add_argument("--constraint", action="append", default=[], dest="constraints")
    command.add_argument("--verify", action="append", default=[], dest="verification")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sera", description="Token-efficient multi-model software delivery controller.")
    root.add_argument("--version", action="version", version=f"SERA {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    init_cmd = commands.add_parser("init", help="Initialize .sera policy and runtime directories.")
    init_cmd.add_argument("--force", action="store_true")

    map_cmd = commands.add_parser("map", help="Build a compact, content-hashed repository map.")
    map_cmd.add_argument("--update", action="store_true", help="Reuse unchanged map entries and rescan only changed files.")
    _json_flag(map_cmd)

    task = commands.add_parser("task", help="Create and inspect task capsules.")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    new = task_commands.add_parser("new", help="Create a task capsule explicitly.")
    new.add_argument("name")
    new.add_argument("--objective", required=True)
    new.add_argument(
        "--mode",
        choices=["fast", "standard", "assured"],
        default=None,
        help="Override the configured default_mode for this task.",
    )
    new.add_argument(
        "--risk",
        choices=["low", "medium", "high"],
        default=None,
        help="Explicit risk floor; automatic assessment still applies and may raise it.",
    )
    new.add_argument("--uncertainty", type=int, choices=[0, 1, 2, 3], default=1)
    new.add_argument("--use-case", default="implementation")
    new.add_argument("--file", action="append", default=[], dest="files")
    new.add_argument("--constraint", action="append", default=[], dest="constraints")
    new.add_argument("--verify", action="append", default=[], dest="verification")

    confirm = task_commands.add_parser("confirm", help="Confirm or replace auto-selected exact file ownership.")
    confirm.add_argument("task", nargs="?")
    confirm.add_argument("--file", action="append", default=[], dest="files")

    auto = task_commands.add_parser("auto", help="Draft a task capsule from a natural-language request.")
    auto.add_argument("request")
    _auto_task_flags(auto)
    _json_flag(auto)

    run = commands.add_parser("run", help="Prepare a routed task, context selection, and builder packet in one step.")
    run.add_argument("request")
    _auto_task_flags(run)
    _json_flag(run)

    route = commands.add_parser("route", help="Choose the cheapest adequate implementation and review lanes.")
    route.add_argument("task", nargs="?")
    _json_flag(route)

    context = commands.add_parser("context", help="Explain and budget the context selected for a task.")
    context.add_argument("task", nargs="?")
    context.add_argument("--why", action="store_true", help="Show the reason each file earned or lost context inclusion.")
    context.add_argument(
        "--stage",
        choices=["build", "review"],
        default=None,
        help="Budget a specific stage instead of the required next stage.",
    )
    _json_flag(context)

    packet = commands.add_parser("packet", help="Generate compact build or review handoff packets.")
    packet.add_argument("stage", choices=["build", "review"])
    packet.add_argument("task", nargs="?")

    record = commands.add_parser("record", help="Append verification evidence to the task ledger.")
    record.add_argument("task", nargs="?")
    record.add_argument("--command", required=True, dest="evidence_command")
    record.add_argument("--exit-code", type=int, required=True)
    record.add_argument("--summary", required=True)
    record.add_argument("--output-file", type=Path)

    verify = commands.add_parser("verify", help="Run task verification commands and record evidence automatically.")
    verify.add_argument("task", nargs="?")

    budget = commands.add_parser("budget", help="Estimate context reuse and token savings for a task.")
    budget.add_argument("task", nargs="?")
    _json_flag(budget)

    cost = commands.add_parser("cost", help="Report task context efficiency and evidence compression estimates.")
    cost.add_argument("task", nargs="?")
    _json_flag(cost)

    next_cmd = commands.add_parser("next", help="Return the next required SERA action for the current task.")
    next_cmd.add_argument("task", nargs="?")
    _json_flag(next_cmd)

    resume = commands.add_parser("resume", help="Reconstruct the active task from repository state, not chat history.")
    resume.add_argument("task", nargs="?")
    _json_flag(resume)

    inbox = commands.add_parser("inbox", help="Show all local SERA tasks and their next actions.")
    _json_flag(inbox)

    review = commands.add_parser(
        "review",
        help="Record a review verdict bound to the exact reviewed HEAD, tree, and task fingerprint.",
    )
    review.add_argument("task", nargs="?")
    review.add_argument("--verdict", choices=["ship", "fix-first", "rethink"], required=True)
    review.add_argument("--reviewer", required=True)
    review.add_argument("--stage", choices=["independent", "gate", "supplementary"], default="independent")
    review.add_argument("--reason", required=True)

    summary = commands.add_parser("summary", help="Generate a compact pull-request or handoff summary.")
    summary.add_argument("task", nargs="?")

    seal = commands.add_parser("seal", help="Create a final fingerprint-bound completion seal.")
    seal.add_argument("task", nargs="?")

    check = commands.add_parser("check", help="Check scope, evidence, review freshness, and optional seal state.")
    check.add_argument("task", nargs="?")
    _json_flag(check)
    check.add_argument("--require-seal", action="store_true")

    status = commands.add_parser("status", help="Show current task route, budget, scope, and evidence state.")
    status.add_argument("task", nargs="?")
    _json_flag(status)
    return root


def _print_json(value: object) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = find_repo_root()
        if args.command == "init":
            created = initialize(root, force=args.force)
            ensure_controller_config(root)
            print("Initialized SERA:")
            for path in created:
                print(f"  {path.relative_to(root)}")
            print("Controller defaults: enabled")
            return 0
        if args.command == "map":
            result = update_repo_map(root) if args.update else build_repo_map(root)
            if args.json:
                _print_json({
                    "file_count": result["file_count"],
                    "fingerprint": result["fingerprint"],
                    "estimated_full_source_tokens": result["estimated_full_source_tokens"],
                    "map": ".sera/cache/repo-map.md",
                    "reused_files": result.get("reused_files", 0),
                    "rescanned_files": result.get("rescanned_files", result["file_count"]),
                })
            else:
                print(f"Mapped {result['file_count']} files; fingerprint {result['fingerprint'][:16]}")
                print(f"Estimated full-source context: ~{result['estimated_full_source_tokens']:,} tokens")
                if args.update:
                    print(f"Reused: {result.get('reused_files', 0)} | rescanned: {result.get('rescanned_files', 0)}")
                print("Reusable map: .sera/cache/repo-map.md")
            return 0
        if args.command == "task" and args.task_command == "new":
            task_dir = new_task(
                root, args.name, args.objective, args.mode, args.risk, args.files,
                args.constraints, args.verification, args.uncertainty, args.use_case,
            )
            print(task_dir.relative_to(root).as_posix())
            return 0
        if args.command == "task" and args.task_command == "confirm":
            task_dir = resolve_task_dir(root, args.task)
            task = confirm_task_ownership(root, task_dir, args.files or None)
            print(f"Confirmed ownership for {task['id']}: {len(task['allowed_files'])} files")
            return 0
        if args.command == "task" and args.task_command == "auto":
            task_dir, report = auto_task(
                root, args.request, name=args.name, mode=args.mode, risk=args.risk,
                uncertainty=args.uncertainty, files=args.files, constraints=args.constraints,
                verification=args.verification, use_case=args.use_case,
            )
            task = load_task(task_dir)
            output = {
                "task_id": task["id"],
                "task_dir": task_dir.relative_to(root).as_posix(),
                "mode": task["mode"],
                "mode_source": task.get("mode_source"),
                "risk": task["risk"],
                "risk_reasons": task.get("risk_reasons", []),
                "owned_files": task["allowed_files"],
                "context_reduction_percent": report["context_reduction_percent"],
            }
            if args.json:
                _print_json(output)
            else:
                print(output["task_dir"])
                print(f"mode: {task['mode']} ({task.get('mode_source', 'builtin')}) | risk: {task['risk']}")
                for reason in task.get("risk_reasons", []):
                    print(f"  risk: {format_risk_reason(reason)}")
                print(f"candidate ownership: {len(task['allowed_files'])} files")
                print(f"context reduction vs full-source availability: {report['context_reduction_percent']}%")
            return 0
        if args.command == "run":
            report = prepare_run(
                root, args.request, name=args.name, mode=args.mode, risk=args.risk,
                uncertainty=args.uncertainty, files=args.files, constraints=args.constraints,
                verification=args.verification, use_case=args.use_case,
            )
            if args.json:
                _print_json(report)
            else:
                print(f"Task: {report['task_id']}")
                print(f"Mode/risk: {report['mode']} ({report.get('mode_source', 'builtin')})/{report['risk']}")
                for reason in report.get("risk_reasons", []):
                    print(f"  risk: {format_risk_reason(reason)}")
                print(
                    f"Ownership: {report['ownership']['file_count']} files "
                    f"(~{report['ownership']['estimated_tokens']:,} tokens)"
                )
                print(
                    f"Selected context: {report['selected_context']['file_count']} files "
                    f"(~{report['selected_context']['estimated_tokens']:,} tokens) "
                    f"of a {report['context']['stage_budget_tokens']:,} budget"
                )
                print(f"Context reduction vs full-source availability: {report['context']['context_reduction_percent']}%")
                print(f"Builder: {report['route']['builder']}")
                print(f"Reviewer: {report['route']['reviewer'] or 'not required'}")
                print(f"Gate: {report['route']['gate'] or 'not required'}")
                if report["builder_packet"]:
                    print(f"Packet: {report['builder_packet']} (~{report['builder_packet_tokens']:,} tokens)")
                else:
                    print("Packet: not generated until exact ownership is confirmed")
                print(f"Next: {report['next']['reason']}")
            return 0

        if args.command == "inbox":
            report = inbox_report(root)
            if args.json:
                _print_json(report)
            else:
                print(f"SERA inbox: {report['count']} tasks")
                for item in report["tasks"]:
                    print(f"{item['id']} | {item['mode']}/{item['risk']} | {item['state']} | {item['name']}")
            return 0

        task_dir = resolve_task_dir(root, getattr(args, "task", None))
        task = load_task(task_dir)
        if args.command == "route":
            decision = decide_route(root, task, load_repo_map(root))
            config = load_config(root)
            output = {
                "builder": lane_label(config, decision.builder),
                "reviewer": lane_label(config, decision.reviewer),
                "gate": lane_label(config, decision.gate),
                "reason": decision.reason,
                "risk": task["risk"],
                "risk_reasons": task.get("risk_reasons", []),
                "ownership_file_count": decision.ownership_file_count,
                "ownership_tokens": decision.ownership_tokens,
                "owned_context_tokens": decision.estimated_context_tokens,
                "stage_budget_tokens": decision.budget_tokens,
                "optional_fable_eligible": decision.fable_eligible,
            }
            if args.json:
                _print_json(output)
            else:
                print(output["builder"])
                print(output["reviewer"] or "reviewer: not required")
                print(output["gate"] or "release gate: not required")
                print(f"reason: {decision.reason}")
                print(f"risk: {task['risk']}")
                for reason in task.get("risk_reasons", []):
                    print(f"  {format_risk_reason(reason)}")
                print(
                    f"ownership: {decision.ownership_file_count} files "
                    f"(~{decision.ownership_tokens:,} tokens, authorization only)"
                )
                print(f"stage budget: {decision.budget_tokens:,} tokens")
                print(f"optional Fable eligible: {'yes' if decision.fable_eligible else 'no'}")
            return 0
        if args.command == "context":
            report = context_report(root, task_dir, stage=args.stage)
            if args.json:
                _print_json(report)
            else:
                print(f"Stage: {report['stage']}")
                print(f"Repository context available: ~{report['repository_available_tokens']:,} tokens")
                print(
                    f"Ownership: {report['ownership']['file_count']} files "
                    f"(~{report['ownership']['estimated_tokens']:,} tokens) — authorization, not context"
                )
                print(
                    f"Selected context: {report['selected_context']['file_count']} files "
                    f"(~{report['selected_context']['estimated_tokens']:,} tokens)"
                )
                print(f"Capsule: ~{report['capsule_tokens']:,} tokens")
                if report["stage"] == "review":
                    print(f"Diff: ~{report['diff_tokens']:,} tokens | evidence: ~{report['evidence_tokens']:,} tokens")
                print(f"Estimated stage context: ~{report['estimated_context_tokens']:,} tokens")
                print(f"Reduction vs full-source availability: {report['context_reduction_percent']}%")
                print(f"Budget: {report['stage_budget_tokens']:,} | within budget: {'yes' if report['within_budget'] else 'no'}")
                if args.why:
                    print("Selected:")
                    for item in report["selected_files"]:
                        print(f"  {item['path']} — {item['reason']} ({'; '.join(item['reasons'])})")
                    if report["excluded_files"]:
                        print("Not selected (ownership retained):")
                        for item in report["excluded_files"]:
                            print(f"  {item['path']} — {item['reason']}")
            return 0
        if args.command == "packet":
            path, text = build_packet(root, task_dir, args.stage)
            print(path.relative_to(root))
            print(f"estimated packet size: ~{estimate_tokens(text):,} tokens")
            return 0
        if args.command == "record":
            output = args.output_file.read_text(encoding="utf-8", errors="replace") if args.output_file else ""
            record_evidence(task_dir, args.evidence_command, args.exit_code, args.summary, output)
            print("Evidence recorded.")
            return 0
        if args.command == "verify":
            if not task["verification"]:
                print("No verification commands configured for this task.")
                return 0
            results = run_verification(root, task_dir)
            for item in results:
                print(f"{item['command']} -> exit {item['exit_code']} ({item['output_sha256'][:12]})")
            return 0 if all(item["exit_code"] == 0 for item in results) else 2
        if args.command == "budget":
            report = budget_view(root, task_dir)
            if args.json:
                _print_json(report)
            else:
                print(f"Full-source orientation: ~{report['full_source_tokens']:,} tokens")
                print(f"Map + capsule orientation: ~{report['orientation_tokens']:,} tokens")
                print(f"Estimated orientation avoided: ~{report['estimated_orientation_tokens_avoided']:,} tokens ({report['estimated_avoidance_percent']}%)")
                print(
                    f"Ownership: {report['ownership']['file_count']} files "
                    f"(~{report['ownership']['estimated_tokens']:,} tokens, authorization only)"
                )
                print(
                    f"Selected {report['required_stage']} context: {report['selected_context']['file_count']} files "
                    f"(~{report['estimated_context_tokens']:,} tokens with stage overhead)"
                )
                print(f"Stage budget: {report['stage_budget_tokens']:,} tokens")
                print(f"Within budget: {'yes' if report['within_budget'] else 'no'}")
            return 0
        if args.command == "cost":
            report = efficiency_report(root, task_dir)
            if args.json:
                _print_json(report)
            else:
                print(f"SERA efficiency score: {report['efficiency_score']}/100")
                print(f"Repository context available: ~{report['repository_available_tokens']:,} tokens")
                print(f"Selected orientation: ~{report['selected_orientation_tokens']:,} tokens")
                print(f"Context reduction: ~{report['context_reduction_tokens']:,} tokens ({report['context_reduction_percent']}%)")
                print(f"Evidence tokens avoided: ~{report['estimated_evidence_tokens_avoided']:,}")
                print(f"Within budget: {'yes' if report['within_budget'] else 'no'}")
                print(report["measurement_note"])
            return 0
        if args.command == "next":
            report = next_action(root, task_dir)
            if args.json:
                _print_json(report)
            else:
                print(f"Task: {report['task_id']}")
                print(f"State: {report['state']}")
                print(f"Next: {report['next_action']}")
                if report["command"]:
                    print(f"Command: {report['command']}")
                print(f"Why: {report['reason']}")
            return 0
        if args.command == "resume":
            report = resume_report(root, task["id"])
            if args.json:
                _print_json(report)
            else:
                print(f"Task: {report['task']['id']}")
                print(f"Objective: {report['task']['objective']}")
                print(f"Mode/risk: {report['task']['mode']}/{report['task']['risk']}")
                print(f"Owned files: {len(report['task']['owned_files'])}")
                print(f"Next: {report['next']['next_action']} — {report['next']['reason']}")
            return 0
        if args.command == "review":
            review = accept_review(root, task_dir, args.verdict, args.reviewer, args.reason, args.stage)
            print(f"{args.stage} review recorded for fingerprint {review['fingerprint'][:16]}")
            print(f"Bound to HEAD {review['repository_identity']['head_sha']}")
            print(f"Bound to HEAD tree {review['repository_identity']['head_tree_sha']}")
            return 0
        if args.command == "summary":
            path, output = generate_summary(root, task_dir)
            print(path.relative_to(root))
            print(f"summary size: ~{estimate_tokens(output):,} tokens")
            return 0
        if args.command == "seal":
            seal = create_seal(root, task_dir)
            print(f"Sealed {seal['task_id']} at fingerprint {seal['fingerprint'][:16]}")
            print(f"Bound to HEAD {seal['repository_identity']['head_sha']}")
            print(f"Bound to HEAD tree {seal['repository_identity']['head_tree_sha']}")
            print(f"Bound to review ledger {seal['review_ledger_fingerprint'][:16]}")
            return 0
        if args.command in {"check", "status"}:
            result = check_task(root, task_dir)
            seal_required_failure = bool(
                args.command == "check" and getattr(args, "require_seal", False)
                and (not result["seal"] or result["seal_stale"])
            )
            if args.json:
                if args.command == "check":
                    result["seal_required_failure"] = seal_required_failure
                    result["seal_required_failure_reasons"] = (
                        (result["seal_stale_reasons"] or ["seal_missing"])
                        if seal_required_failure
                        else []
                    )
                _print_json(result)
            else:
                print(f"Task: {task['id']}")
                print(f"Status: {'ready' if result['ok'] else 'blocked'}")
                print(f"Fingerprint: {result['fingerprint']}")
                print(f"HEAD: {result['head_identity']['head_sha']}")
                print(f"Changed files: {len(result['changed_files'])}")
                print(f"Out of scope: {', '.join(result['out_of_scope']) or 'none'}")
                print(f"Missing verification: {', '.join(result['missing_verification']) or 'none'}")
                if result["reviews"]:
                    for stage, review in result["reviews"].items():
                        freshness = "stale" if stage in result["stale_reviews"] else "current"
                        print(f"Review {stage}: {review['verdict']} by {review['reviewer']} ({freshness})")
                        for stale_reason in result["stale_review_reasons"].get(stage, []):
                            print(f"  {stale_reason}")
                else:
                    print("Reviews: none")
                print(f"Missing reviews: {', '.join(result['missing_reviews']) or 'none'}")
                if result["seal"]:
                    print(f"Seal: {result['seal_status']} ({result['seal']['fingerprint'][:16]})")
                    for reason in result["seal_stale_reasons"]:
                        print(f"  {reason}")
                else:
                    print("Seal: none")
                print(f"Next: {result['next_action']}")
            return 0 if result["ok"] and not seal_required_failure else 2
        raise SeraError("Unsupported command")
    except (SeraError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
