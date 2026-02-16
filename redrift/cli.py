from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
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


OPTIONAL_SUITE_FENCES = (
    "specdrift",
    "datadrift",
    "depsdrift",
    "uxdrift",
    "therapydrift",
    "yagnidrift",
)
EXECUTE_PLUGIN_ORDER = ("speedrift", "specdrift", "datadrift", "depsdrift", "uxdrift", "therapydrift", "yagnidrift", "redrift")
COMMIT_PHASES = ("root", "analyze", "respec", "design", "build")
V2_WORKGRAPH_IGNORES = (
    ".speedrift/",
    ".specdrift/",
    ".datadrift/",
    ".depsdrift/",
    ".uxdrift/",
    ".therapydrift/",
    ".yagnidrift/",
    ".redrift/last.json",
)
COMMIT_EXCLUDE_PATHS = (
    ".workgraph/.speedrift/**",
    ".workgraph/.specdrift/**",
    ".workgraph/.datadrift/**",
    ".workgraph/.depsdrift/**",
    ".workgraph/.uxdrift/**",
    ".workgraph/.therapydrift/**",
    ".workgraph/.yagnidrift/**",
    ".workgraph/.redrift/last.json",
)

_GENERIC_FENCE_RE = re.compile(
    r"```(?P<info>[a-zA-Z0-9_-]+)\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


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


def _emit_execute_text(report: dict) -> None:
    task_id = str(report.get("task_id") or "")
    task_title = str(report.get("task_title") or task_id)
    phase_tasks = [str(x) for x in (report.get("phase_tasks") or [])]
    suite_results = report.get("suite_results") or []
    inherited = [str(x) for x in (report.get("inherited_fences") or [])]

    print(f"redrift execute: {task_id}: {task_title}")
    if not phase_tasks:
        print("phase tasks: none")
    else:
        print("phase tasks:")
        for t in phase_tasks:
            print(f"- {t}")

    if inherited:
        print(f"inherited fences: {', '.join(inherited)}")
    else:
        print("inherited fences: none")

    if suite_results:
        print("suite checks:")
        for row in suite_results:
            print(f"- {row.get('task_id')}: exit={row.get('exit_code')}")

    if report.get("service_started"):
        print("wg service: started")
    elif report.get("service_error"):
        print(f"wg service: failed ({report.get('service_error')})")


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


def _phase_from_task_id(task_id: str) -> str:
    m = re.match(r"^redrift-exec-(analyze|respec|design|build)-", str(task_id))
    if m:
        return str(m.group(1))
    return "root"


def _toml_string(s: str) -> str:
    s2 = str(s).replace('"', "").replace("\n", " ").strip()
    return f'"{s2}"'


def _merge_v2_workgraph_gitignore(*, source: Path, target: Path) -> list[str]:
    source_lines: list[str] = []
    if source.exists():
        source_lines = source.read_text(encoding="utf-8").splitlines()

    target_lines: list[str] = []
    if target.exists():
        target_lines = target.read_text(encoding="utf-8").splitlines()

    existing = set(target_lines)
    additions: list[str] = []

    for line in source_lines:
        ln = str(line).rstrip("\n")
        if not ln:
            continue
        # Keep v2 artifacts under .redrift tracked; only ignore state file.
        if ln.strip() == ".redrift/":
            continue
        if ln not in existing:
            additions.append(ln)
            existing.add(ln)

    for line in V2_WORKGRAPH_IGNORES:
        if line not in existing:
            additions.append(line)
            existing.add(line)

    if additions:
        text = "\n".join([*target_lines, *additions]).rstrip() + "\n"
        target.write_text(text, encoding="utf-8")

    return additions


def _stage_redrift_commit(project_dir: Path) -> None:
    cmd = ["git", "add", "-A", "--", "."]
    for pat in COMMIT_EXCLUDE_PATHS:
        cmd.append(f":(exclude){pat}")
    proc = subprocess.run(cmd, cwd=str(project_dir), text=True, capture_output=True)
    if proc.returncode == 0:
        return
    if proc.returncode == 1 and "ignored by one of your .gitignore files" in (proc.stderr or ""):
        return
    raise subprocess.CalledProcessError(proc.returncode, cmd, output=proc.stdout, stderr=proc.stderr)


def _has_staged_changes(project_dir: Path) -> bool:
    diff = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=str(project_dir), text=True)
    return bool(diff.strip())


def _extract_suite_fence_blocks(description: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    for m in _GENERIC_FENCE_RE.finditer(description or ""):
        info = str(m.group("info") or "").strip().lower()
        if info not in OPTIONAL_SUITE_FENCES:
            continue
        if info in blocks:
            continue
        blocks[info] = str(m.group("body") or "").strip()
    return blocks


def _phase_artifacts(spec: RedriftSpec) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {phase: [] for phase in PHASE_ORDER}
    for rel in spec.required_artifacts:
        rel_clean = str(rel).strip().lstrip("/")
        if not rel_clean:
            continue
        phase = str(rel_clean).split("/", 1)[0].strip().lower()
        if phase not in out:
            phase = "build"
        out[phase].append(rel_clean)
    return out


def _format_redrift_block(*, spec: RedriftSpec, required_artifacts: list[str], create_phase_followups: bool) -> str:
    lines: list[str] = []
    lines.append("```redrift")
    lines.append(f"schema = {int(spec.schema)}")
    lines.append(f"artifact_root = {_toml_string(spec.artifact_root)}")
    lines.append("required_artifacts = [")
    for rel in required_artifacts:
        lines.append(f"  {_toml_string(str(rel))},")
    lines.append("]")
    lines.append(f"create_phase_followups = {'true' if create_phase_followups else 'false'}")
    lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


def _phase_touch_paths(*, spec: RedriftSpec, root_task_id: str, phase: str) -> list[str]:
    base = [f"{spec.artifact_root}/{root_task_id}/{phase}/**", "docs/**", ".workgraph/**"]
    if phase == "build":
        base.extend(["src/**", "api/**", "db/**"])
    return base


def _phase_task_id(*, phase: str, root_task_id: str) -> str:
    return f"redrift-exec-{phase}-{root_task_id}"


def _default_v2_repo_dir(source_project_dir: Path) -> Path:
    return source_project_dir.parent / f"{source_project_dir.name}-v2"


def _bootstrap_v2_repo(
    *,
    source_project_dir: Path,
    source_wg_dir: Path,
    requested: str | None,
) -> tuple[Path, Path, list[str]]:
    notes: list[str] = []
    if requested and str(requested).strip().lower() != "auto":
        target_project_dir = Path(str(requested)).expanduser().resolve()
    else:
        target_project_dir = _default_v2_repo_dir(source_project_dir).resolve()

    target_project_dir.mkdir(parents=True, exist_ok=True)

    if not (target_project_dir / ".git").exists():
        subprocess.check_call(["git", "init"], cwd=str(target_project_dir))
        notes.append("initialized_git_repo")

    target_wg_dir = target_project_dir / ".workgraph"
    if not (target_wg_dir / "graph.jsonl").exists():
        subprocess.check_call(["wg", "init", "--dir", str(target_wg_dir)])
        notes.append("initialized_workgraph")

    if not (target_project_dir / "README.md").exists():
        (target_project_dir / "README.md").write_text(
            (
                f"# {target_project_dir.name}\n\n"
                "v2 rebuild workspace bootstrapped by redrift.\n\n"
                f"Source repo: `{source_project_dir}`\n"
            ),
            encoding="utf-8",
        )
        notes.append("created_readme")

    copy_names = [
        "drifts",
        "driftdriver",
        "speedrift",
        "specdrift",
        "datadrift",
        "depsdrift",
        "uxdrift",
        "therapydrift",
        "yagnidrift",
        "redrift",
        "drift-policy.toml",
    ]

    gitignore_additions = _merge_v2_workgraph_gitignore(
        source=source_wg_dir / ".gitignore",
        target=target_wg_dir / ".gitignore",
    )
    if gitignore_additions:
        notes.append(f"merged:.gitignore:{len(gitignore_additions)}")

    for name in copy_names:
        src = source_wg_dir / name
        dst = target_wg_dir / name
        if not src.exists() or dst.exists():
            continue
        shutil.copy2(src, dst)
        if src.stat().st_mode & 0o111:
            dst.chmod(dst.stat().st_mode | 0o755)
        notes.append(f"copied:{name}")

    src_exec = source_wg_dir / "executors"
    dst_exec = target_wg_dir / "executors"
    if src_exec.exists() and src_exec.is_dir() and not dst_exec.exists():
        shutil.copytree(src_exec, dst_exec)
        notes.append("copied:executors")

    # Create a first checkpoint commit in brand-new repos when possible.
    try:
        has_head = (
            subprocess.call(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=str(target_project_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            == 0
        )
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(target_project_dir), text=True)
        if (not has_head) and status.strip():
            subprocess.check_call(["git", "add", "-A"], cwd=str(target_project_dir))
            subprocess.check_call(["git", "commit", "-m", "redrift: bootstrap v2 workspace"], cwd=str(target_project_dir))
            sha = subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=str(target_project_dir),
                text=True,
            ).strip()
            notes.append(f"bootstrap_commit:{sha}")
    except Exception as e:
        notes.append(f"bootstrap_commit_failed:{e.__class__.__name__}")

    return target_project_dir, target_wg_dir, notes


def _build_phase_task_description(
    *,
    phase: str,
    root_task_id: str,
    root_title: str,
    phase_task_id: str,
    spec: RedriftSpec,
    required_artifacts: list[str],
    inherited_fences: dict[str, str],
) -> str:
    title = f"redrift execute {phase}: {root_title}"
    missing_lines = "\n".join([f"- {m}" for m in required_artifacts]) if required_artifacts else "- (none)"
    parts: list[str] = []
    parts.append(f"Execute redrift {phase} phase for `{root_task_id}`.")
    parts.append("")
    parts.append("Context:")
    parts.append(f"- Origin task: {root_task_id}")
    parts.append(f"- Phase: {phase}")
    parts.append("- Required artifacts:")
    parts.append(missing_lines)
    parts.append("")
    parts.append("Execution protocol:")
    parts.append(f"- Before edits: `./.workgraph/drifts check --task {phase_task_id} --write-log`")
    parts.append(f"- Before done: `./.workgraph/drifts check --task {phase_task_id} --write-log`")
    parts.append(f"- Checkpoint commit: `./.workgraph/redrift wg commit --task {phase_task_id} --phase {phase}`")
    parts.append("")
    parts.append(
        format_default_contract_block(
            mode=_phase_mode(phase),
            objective=title,
            touch=_phase_touch_paths(spec=spec, root_task_id=root_task_id, phase=phase),
        ).strip()
    )
    parts.append("")
    parts.append(
        _format_redrift_block(
            spec=spec,
            required_artifacts=required_artifacts,
            create_phase_followups=False,
        ).strip()
    )
    for fence in OPTIONAL_SUITE_FENCES:
        body = inherited_fences.get(fence)
        if not body:
            continue
        parts.append("")
        parts.append(f"```{fence}")
        parts.append(body)
        parts.append("```")
    return "\n".join(parts).rstrip() + "\n"


def _run_suite_check(
    *,
    wg_dir: Path,
    project_dir: Path,
    task_id: str,
    description: str,
    write_log: bool,
    create_followups: bool,
) -> tuple[int, list[dict[str, int | str]]]:
    plugins_run: list[dict[str, int | str]] = []

    enabled = _extract_suite_fence_blocks(description)
    if extract_redrift_spec(description) is not None:
        enabled["redrift"] = "<embedded>"

    speedrift = wg_dir / "speedrift"
    if not speedrift.exists():
        raise FileNotFoundError(f"{speedrift} not found")

    overall = ExitCode.ok

    cmd = [str(speedrift), "--dir", str(project_dir), "check", "--task", str(task_id)]
    if write_log:
        cmd.append("--write-log")
    if create_followups:
        cmd.append("--create-followups")
    speed_rc = int(subprocess.call(cmd))
    plugins_run.append({"plugin": "speedrift", "exit_code": speed_rc})
    if speed_rc not in (ExitCode.ok, ExitCode.findings):
        return speed_rc, plugins_run
    if speed_rc == ExitCode.findings:
        overall = ExitCode.findings

    for plugin in EXECUTE_PLUGIN_ORDER:
        if plugin == "speedrift":
            continue
        if plugin not in enabled:
            continue

        plugin_bin = wg_dir / plugin
        if not plugin_bin.exists():
            plugins_run.append({"plugin": plugin, "exit_code": ExitCode.ok, "note": "wrapper_missing"})
            continue

        if plugin == "uxdrift":
            cmd = [str(plugin_bin), "wg", "--dir", str(project_dir), "check", "--task", str(task_id)]
        else:
            cmd = [str(plugin_bin), "--dir", str(project_dir), "wg", "check", "--task", str(task_id)]

        if write_log:
            cmd.append("--write-log")
        if create_followups:
            cmd.append("--create-followups")

        rc = int(subprocess.call(cmd))
        plugins_run.append({"plugin": plugin, "exit_code": rc})
        if rc not in (ExitCode.ok, ExitCode.findings):
            return rc, plugins_run
        if rc == ExitCode.findings and overall == ExitCode.ok:
            overall = ExitCode.findings

    return overall, plugins_run


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


def cmd_wg_execute(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    if not (wg_dir / "speedrift").exists():
        print("error: .workgraph/speedrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    source_wg = Workgraph(wg_dir=wg_dir, project_dir=project_dir)
    task_id = str(args.task)
    task = _load_task(wg=source_wg, task_id=task_id)
    title = str(task.get("title") or task_id)
    description = str(task.get("description") or "")

    raw_block = extract_redrift_spec(description)
    if raw_block is None:
        print("error: task has no redrift block; add one first", file=sys.stderr)
        return ExitCode.usage

    try:
        spec = RedriftSpec.from_raw(parse_redrift_spec(raw_block))
    except Exception as e:
        print(f"error: invalid redrift block: {e}", file=sys.stderr)
        return ExitCode.findings

    v2_repo = str(getattr(args, "v2_repo", "") or "").strip()
    target_project_dir = project_dir
    target_wg_dir = wg_dir
    target_wg = source_wg
    bootstrap_notes: list[str] = []

    if v2_repo:
        target_project_dir, target_wg_dir, bootstrap_notes = _bootstrap_v2_repo(
            source_project_dir=project_dir,
            source_wg_dir=wg_dir,
            requested=v2_repo,
        )
        target_wg = Workgraph(wg_dir=target_wg_dir, project_dir=target_project_dir)
        root_desc = (
            "Redrift v2 root lane generated from source repository.\n\n"
            f"Source repo: `{project_dir}`\n"
            f"Source task: `{task_id}`\n\n"
            f"{description}"
        )
        target_wg.ensure_task(
            task_id=task_id,
            title=title,
            description=root_desc,
            blocked_by=None,
            tags=["redrift", "execute", "v2-root"],
        )
        description = root_desc

    if not (target_wg_dir / "speedrift").exists():
        print(
            "error: target repo missing .workgraph/speedrift wrapper; install driftdriver in target repo first",
            file=sys.stderr,
        )
        return ExitCode.usage

    inherited_fences = _extract_suite_fence_blocks(description)
    phase_map = _phase_artifacts(spec)
    phase_task_ids: list[str] = []
    phase_task_descriptions: dict[str, str] = {}
    previous_task_id: str | None = None

    for phase in PHASE_ORDER:
        required = [str(x) for x in (phase_map.get(phase) or []) if str(x).strip()]
        if not required:
            continue
        phase_task_id = _phase_task_id(phase=phase, root_task_id=task_id)
        phase_title = f"redrift execute {phase}: {title}"
        phase_desc = _build_phase_task_description(
            phase=phase,
            root_task_id=task_id,
            root_title=title,
            phase_task_id=phase_task_id,
            spec=spec,
            required_artifacts=required,
            inherited_fences=inherited_fences,
        )
        blocked_by = [previous_task_id] if previous_task_id else None
        target_wg.ensure_task(
            task_id=phase_task_id,
            title=phase_title,
            description=phase_desc,
            blocked_by=blocked_by,
            tags=["drift", "redrift", "execute", phase],
        )
        phase_task_ids.append(phase_task_id)
        phase_task_descriptions[phase_task_id] = phase_desc
        previous_task_id = phase_task_id

    suite_results: list[dict[str, object]] = []
    out_rc = ExitCode.ok

    root_rc, root_plugins = _run_suite_check(
        wg_dir=target_wg_dir,
        project_dir=target_project_dir,
        task_id=task_id,
        description=description,
        write_log=bool(args.write_log),
        create_followups=bool(args.create_followups),
    )
    suite_results.append({"task_id": task_id, "exit_code": int(root_rc), "plugins": root_plugins})
    if root_rc not in (ExitCode.ok, ExitCode.findings):
        out_rc = int(root_rc)
    elif root_rc == ExitCode.findings:
        out_rc = ExitCode.findings

    if int(out_rc) in (ExitCode.ok, ExitCode.findings) and bool(args.phase_checks):
        for phase_task_id in phase_task_ids:
            phase_rc, phase_plugins = _run_suite_check(
                wg_dir=target_wg_dir,
                project_dir=target_project_dir,
                task_id=phase_task_id,
                description=phase_task_descriptions.get(phase_task_id, ""),
                write_log=bool(args.write_log),
                create_followups=bool(args.phase_followups and args.create_followups),
            )
            suite_results.append({"task_id": phase_task_id, "exit_code": int(phase_rc), "plugins": phase_plugins})
            if phase_rc not in (ExitCode.ok, ExitCode.findings):
                out_rc = int(phase_rc)
                break
            if phase_rc == ExitCode.findings and out_rc == ExitCode.ok:
                out_rc = ExitCode.findings

    service_started = False
    service_error = ""
    if bool(args.start_service):
        try:
            subprocess.check_call(["wg", "--dir", str(target_wg_dir), "service", "start"])
            service_started = True
        except Exception as e:
            service_error = str(e)

    report = {
        "task_id": task_id,
        "task_title": title,
        "target_repo": str(target_project_dir),
        "target_workgraph": str(target_wg_dir),
        "bootstrap_notes": bootstrap_notes,
        "phase_tasks": phase_task_ids,
        "inherited_fences": sorted(inherited_fences.keys()),
        "suite_results": suite_results,
        "service_started": service_started,
        "service_error": service_error,
        "exit_code": int(out_rc),
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        _emit_execute_text(report)

    return int(out_rc)


def cmd_wg_commit(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    if get_git_root(project_dir) is None:
        print("error: project is not inside a git repository", file=sys.stderr)
        return ExitCode.usage

    wg = Workgraph(wg_dir=wg_dir, project_dir=project_dir)
    task_id = str(args.task)
    task = _load_task(wg=wg, task_id=task_id)
    title = str(task.get("title") or task_id)

    phase = str(args.phase).strip().lower() if args.phase else _phase_from_task_id(task_id)
    if phase not in COMMIT_PHASES:
        print(f"error: unsupported phase '{phase}'", file=sys.stderr)
        return ExitCode.usage

    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(project_dir), text=True)
    if not status.strip():
        print("error: no git changes to commit", file=sys.stderr)
        return ExitCode.usage

    subject = str(args.message or title).strip() or title
    commit_message = f"redrift({phase}): {subject} [{task_id}]"

    if not bool(args.dry_run):
        _stage_redrift_commit(project_dir)
        if not _has_staged_changes(project_dir):
            print("error: no commit-eligible git changes after redrift excludes", file=sys.stderr)
            return ExitCode.usage
        cmd = ["git", "commit", "-m", commit_message]
        if bool(args.no_verify):
            cmd.append("--no-verify")
        subprocess.check_call(cmd, cwd=str(project_dir))
        sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=str(project_dir), text=True).strip()
        if bool(args.write_log):
            wg.wg_log(task_id, f"Redrift commit: {sha} {commit_message}")
    else:
        sha = "dry-run"

    report = {
        "task_id": task_id,
        "phase": phase,
        "commit_message": commit_message,
        "commit_sha": sha,
        "project_dir": str(project_dir),
        "dry_run": bool(args.dry_run),
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(f"{task_id}: committed ({phase})")
        print(f"sha: {sha}")
        print(f"message: {commit_message}")

    return ExitCode.ok


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

    execute = wg_sub.add_parser(
        "execute",
        help="Build v2 execution lane: create phase tasks and run speedrift suite checks",
    )
    execute.add_argument("--task", help="Root task id containing a redrift block")
    execute.add_argument(
        "--v2-repo",
        nargs="?",
        const="auto",
        help="Create/use a net-new v2 repo (default path: sibling '<current>-v2') and run lane there",
    )
    execute.add_argument("--write-log", action="store_true", help="Write suite check summaries into wg log")
    execute.add_argument("--create-followups", action="store_true", help="Allow root suite check to create follow-up tasks")
    execute.add_argument(
        "--phase-checks",
        dest="phase_checks",
        action="store_true",
        default=False,
        help="Run suite checks for generated phase tasks (default: disabled)",
    )
    execute.add_argument(
        "--phase-followups",
        action="store_true",
        help="Allow phase task suite checks to create follow-up tasks (default: off)",
    )
    execute.add_argument(
        "--start-service",
        action="store_true",
        help="Start `wg service` after execution lane setup",
    )
    execute.set_defaults(func=cmd_wg_execute)

    commit = wg_sub.add_parser("commit", help="Create a structured git commit checkpoint for a redrift task")
    commit.add_argument("--task", help="Task id to commit against")
    commit.add_argument(
        "--phase",
        choices=list(COMMIT_PHASES),
        help="Commit phase label (defaults from task id, e.g., redrift-exec-analyze-* -> analyze)",
    )
    commit.add_argument("--message", help="Optional commit subject override")
    commit.add_argument("--no-verify", action="store_true", help="Pass --no-verify to git commit")
    commit.add_argument("--write-log", action="store_true", help="Write commit summary into wg log after commit")
    commit.add_argument("--dry-run", action="store_true", help="Print planned commit without creating it")
    commit.set_defaults(func=cmd_wg_commit)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
