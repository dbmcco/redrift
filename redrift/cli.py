from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from redrift.contracts import format_default_contract_block
from redrift.drift import PHASE_ORDER, compute_redrift
from redrift.git_tools import get_git_root
from redrift.specs import RedriftSpec, extract_redrift_spec, parse_redrift_spec
from redrift.workgraph import Workgraph, find_workgraph_dir


class ExitCode:
    ok = 0
    findings = 3
    usage = 2


def _emit_text(report: dict) -> None:
    task_id = report.get("task_id")
    title = report.get("task_title")
    score = report.get("score")
    findings = report.get("findings") or []

    print(f"{task_id}: {title}")
    print(f"score: {score}")
    if not findings:
        print("findings: none")
        return

    print("findings:")
    for f in findings:
        print(f"- [{f.get('severity')}] {f.get('kind')}: {f.get('summary')}")


def _write_state(*, wg_dir: Path, report: dict) -> None:
    try:
        out_dir = wg_dir / ".redrift"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "last.json").write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def _maybe_write_log(wg: Workgraph, task_id: str, report: dict) -> None:
    findings = report.get("findings") or []
    score = report.get("score", "unknown")
    recs = report.get("recommendations") or []

    if not findings:
        msg = "Redrift: OK (no findings)"
    else:
        kinds = ", ".join(sorted({str(f.get("kind")) for f in findings}))
        msg = f"Redrift: {score} ({kinds})"
        if recs:
            next_action = str(recs[0].get("action") or "").strip()
            if next_action:
                msg += f" | next: {next_action}"

    wg.wg_log(task_id, msg)


def _phase_mode(phase: str) -> str:
    if phase in ("analyze", "respec", "design"):
        return "explore"
    return "core"


def _maybe_create_followups(wg: Workgraph, report: dict) -> None:
    task_id = str(report["task_id"])
    task_title = str(report.get("task_title") or task_id)
    findings = report.get("findings") or []
    if not findings:
        return

    spec = report.get("spec") or {}
    phase_missing = ((report.get("telemetry") or {}).get("phase_missing") or {})

    create_phase_followups = bool(spec.get("create_phase_followups", True))
    if not create_phase_followups:
        follow_id = f"redrift-v2-{task_id}"
        title = f"redrift: {task_title}"
        desc = (
            "Run redrift v2 cycle for this task.\n\n"
            "Context:\n"
            f"- Origin task: {task_id}\n"
            f"- Findings: {', '.join(sorted({str(f.get('kind')) for f in findings}))}\n\n"
            + format_default_contract_block(mode="explore", objective=title, touch=[])
            + "\n"
            + (report.get("_redrift_block") or "").strip()
            + "\n"
        )
        wg.ensure_task(
            task_id=follow_id,
            title=title,
            description=desc,
            blocked_by=[task_id],
            tags=["drift", "redrift"],
        )
        return

    for phase in PHASE_ORDER:
        missing = [str(x) for x in (phase_missing.get(phase) or []) if str(x).strip()]
        if not missing:
            continue
        follow_id = f"redrift-{phase}-{task_id}"
        title = f"redrift {phase}: {task_title}"
        missing_lines = "\n".join([f"- {m}" for m in missing])
        desc = (
            f"Complete redrift {phase} artifacts before proceeding.\n\n"
            "Context:\n"
            f"- Origin task: {task_id}\n"
            f"- Phase: {phase}\n"
            "- Missing artifacts:\n"
            f"{missing_lines}\n\n"
            + format_default_contract_block(mode=_phase_mode(phase), objective=title, touch=[])
            + "\n"
            + (report.get("_redrift_block") or "").strip()
            + "\n"
        )
        wg.ensure_task(
            task_id=follow_id,
            title=title,
            description=desc,
            blocked_by=[task_id],
            tags=["drift", "redrift", phase],
        )


def _load_task(*, wg: Workgraph, task_id: str) -> dict:
    task = wg.show_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")
    return task


def cmd_wg_check(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    wg = Workgraph(wg_dir=wg_dir, project_dir=project_dir)

    task_id = str(args.task)
    task = _load_task(wg=wg, task_id=task_id)
    title = str(task.get("title") or task_id)
    description = str(task.get("description") or "")

    raw_block = extract_redrift_spec(description)
    if raw_block is None:
        report = {
            "task_id": task_id,
            "task_title": title,
            "git_root": None,
            "score": "green",
            "spec": None,
            "telemetry": {"note": "no redrift block"},
            "findings": [],
            "recommendations": [],
        }
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=False))
        else:
            _emit_text(report)
        return ExitCode.ok

    try:
        spec_raw = parse_redrift_spec(raw_block)
        spec = RedriftSpec.from_raw(spec_raw)
    except Exception as e:
        report = {
            "task_id": task_id,
            "task_title": title,
            "git_root": None,
            "score": "yellow",
            "spec": None,
            "telemetry": {"parse_error": str(e)},
            "findings": [
                {
                    "kind": "invalid_redrift_spec",
                    "severity": "warn",
                    "summary": "redrift block present but could not be parsed",
                }
            ],
            "recommendations": [
                {
                    "priority": "high",
                    "action": "Fix the redrift TOML block so it parses",
                    "rationale": "Redrift can only run when it can read migration requirements.",
                }
            ],
        }
        report["_redrift_block"] = f"```redrift\n{raw_block}\n```"
        _write_state(wg_dir=wg_dir, report=report)
        if args.write_log:
            _maybe_write_log(wg, task_id, report)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=False))
        else:
            _emit_text(report)
        return ExitCode.findings

    git_root = get_git_root(project_dir)
    report = compute_redrift(
        task_id=task_id,
        task_title=title,
        description=description,
        spec=spec,
        project_dir=project_dir,
        git_root=git_root,
    )
    report["_redrift_block"] = f"```redrift\n{raw_block}\n```"

    _write_state(wg_dir=wg_dir, report=report)

    if args.write_log:
        _maybe_write_log(wg, task_id, report)
    if args.create_followups:
        _maybe_create_followups(wg, report)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        _emit_text(report)

    return ExitCode.findings if report.get("findings") else ExitCode.ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="redrift")
    p.add_argument("--dir", help="Project directory (or .workgraph dir). Defaults to cwd search.")
    p.add_argument("--json", action="store_true", help="JSON output (where supported)")

    sub = p.add_subparsers(dest="cmd", required=True)

    wg = sub.add_parser("wg", help="Workgraph-integrated commands")
    wg_sub = wg.add_subparsers(dest="wg_cmd", required=True)

    check = wg_sub.add_parser("check", help="Check for redrift migration drift (requires a redrift block in the task)")
    check.add_argument("--task", help="Task id to check")
    check.add_argument("--write-log", action="store_true", help="Write summary into wg log")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for findings")
    check.set_defaults(func=cmd_wg_check)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
