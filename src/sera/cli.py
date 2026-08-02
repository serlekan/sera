from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import (
    SeraError,
    budget_report,
    build_repo_map,
    check_task,
    create_seal,
    decide_route,
    estimate_tokens,
    find_repo_root,
    generate_packet,
    generate_summary,
    initialize,
    lane_label,
    load_config,
    load_repo_map,
    load_task,
    new_task,
    record_evidence,
    record_review,
    run_verification,
    resolve_task_dir,
    task_fingerprint,
)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="sera", description="Token-efficient multi-model software delivery relay.")
    root.add_argument("--version", action="version", version=f"SERA {__version__}")
    commands = root.add_subparsers(dest="command", required=True)

    init_cmd = commands.add_parser("init", help="Initialize .sera policy and runtime directories.")
    init_cmd.add_argument("--force", action="store_true")

    commands.add_parser("map", help="Build a compact, content-hashed repository map.")

    task = commands.add_parser("task", help="Create and inspect task capsules.")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    new = task_commands.add_parser("new", help="Create a task capsule.")
    new.add_argument("name")
    new.add_argument("--objective", required=True)
    new.add_argument("--mode", choices=["fast", "standard", "assured"], default="standard")
    new.add_argument("--risk", choices=["low", "medium", "high"], default="medium")
    new.add_argument("--uncertainty", type=int, choices=[0, 1, 2, 3], default=1)
    new.add_argument("--use-case", default="implementation")
    new.add_argument("--file", action="append", default=[], dest="files")
    new.add_argument("--constraint", action="append", default=[], dest="constraints")
    new.add_argument("--verify", action="append", default=[], dest="verification")

    route = commands.add_parser("route", help="Choose the cheapest adequate implementation and review lanes.")
    route.add_argument("task", nargs="?")

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

    review = commands.add_parser("review", help="Record a review verdict bound to the exact task fingerprint.")
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
    check.add_argument("--json", action="store_true")
    check.add_argument("--require-seal", action="store_true")

    status = commands.add_parser("status", help="Show current task route, budget, scope, and evidence state.")
    status.add_argument("task", nargs="?")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = find_repo_root()
        if args.command == "init":
            created = initialize(root, force=args.force)
            print("Initialized SERA:")
            for path in created:
                print(f"  {path.relative_to(root)}")
            return 0
        if args.command == "map":
            result = build_repo_map(root)
            print(f"Mapped {result['file_count']} files; fingerprint {result['fingerprint'][:16]}")
            print(f"Estimated full-source context: ~{result['estimated_full_source_tokens']:,} tokens")
            print("Reusable map: .sera/cache/repo-map.md")
            return 0
        if args.command == "task" and args.task_command == "new":
            task_dir = new_task(
                root,
                args.name,
                args.objective,
                args.mode,
                args.risk,
                args.files,
                args.constraints,
                args.verification,
                args.uncertainty,
                args.use_case,
            )
            print(task_dir.relative_to(root))
            return 0
        task_dir = resolve_task_dir(root, getattr(args, "task", None))
        task = load_task(task_dir)
        if args.command == "route":
            decision = decide_route(root, task, load_repo_map(root))
            config = load_config(root)
            print(lane_label(config, decision.builder))
            print(lane_label(config, decision.reviewer) or "reviewer: not required")
            print(lane_label(config, decision.gate) or "release gate: not required")
            print(f"reason: {decision.reason}")
            print(f"owned-context estimate: {decision.estimated_context_tokens:,} tokens")
            print(f"stage budget: {decision.budget_tokens:,} tokens")
            print(f"optional Fable eligible: {'yes' if decision.fable_eligible else 'no'}")
            return 0
        if args.command == "packet":
            path, text = generate_packet(root, task_dir, args.stage)
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
            report = budget_report(root, task_dir)
            print(f"Full-source orientation: ~{report['full_source_tokens']:,} tokens")
            print(f"Map + capsule orientation: ~{report['orientation_tokens']:,} tokens")
            print(f"Estimated orientation avoided: ~{report['estimated_orientation_tokens_avoided']:,} tokens ({report['estimated_avoidance_percent']}%)")
            print(f"Owned context: ~{report['owned_context_tokens']:,} tokens")
            print(f"Stage budget: {report['stage_budget_tokens']:,} tokens")
            print(f"Within budget: {'yes' if report['within_budget'] else 'no'}")
            return 0
        if args.command == "review":
            fingerprint = task_fingerprint(root, task_dir)
            record_review(task_dir, fingerprint, args.verdict, args.reviewer, args.reason, args.stage)
            print(f"{args.stage} review recorded for fingerprint {fingerprint[:16]}")
            return 0
        if args.command == "summary":
            path, output = generate_summary(root, task_dir)
            print(path.relative_to(root))
            print(f"summary size: ~{estimate_tokens(output):,} tokens")
            return 0
        if args.command == "seal":
            seal = create_seal(root, task_dir)
            print(f"Sealed {seal['task_id']} at fingerprint {seal['fingerprint'][:16]}")
            return 0
        if args.command in {"check", "status"}:
            result = check_task(root, task_dir)
            seal_required_failure = bool(
                args.command == "check"
                and getattr(args, "require_seal", False)
                and (not result["seal"] or result["seal_stale"])
            )
            if args.command == "check" and args.json:
                result["seal_required_failure"] = seal_required_failure
                print(json.dumps(result, indent=2))
            else:
                print(f"Task: {task['id']}")
                print(f"Status: {'ready' if result['ok'] else 'blocked'}")
                print(f"Fingerprint: {result['fingerprint']}")
                print(f"Changed files: {len(result['changed_files'])}")
                print(f"Out of scope: {', '.join(result['out_of_scope']) or 'none'}")
                print(f"Missing verification: {', '.join(result['missing_verification']) or 'none'}")
                if result["reviews"]:
                    for stage, review in result["reviews"].items():
                        freshness = "stale" if stage in result["stale_reviews"] else "current"
                        print(f"Review {stage}: {review['verdict']} by {review['reviewer']} ({freshness})")
                else:
                    print("Reviews: none")
                print(f"Missing reviews: {', '.join(result['missing_reviews']) or 'none'}")
                if result["seal"]:
                    print(f"Seal: {'stale' if result['seal_stale'] else 'current'} ({result['seal']['fingerprint'][:16]})")
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
