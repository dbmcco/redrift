from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from redrift.specs import RedriftSpec

_SKIP_DIRS = {
    ".git",
    ".workgraph/.coredrift",
    ".workgraph/.speedrift",
    ".workgraph/.specdrift",
    ".workgraph/.datadrift",
    ".workgraph/.archdrift",
    ".workgraph/.depsdrift",
    ".workgraph/.uxdrift",
    ".workgraph/.therapydrift",
    ".workgraph/.yagnidrift",
    ".workgraph/.redrift",
    "node_modules",
    ".next",
    ".venv",
    "venv",
    "__pycache__",
}


def _safe_task_key(task_id: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_.-]+", "__", str(task_id or "").strip())
    key = key.strip("._")
    return key or "task"


def verify_state_path(*, project_dir: Path, task_id: str) -> Path:
    return project_dir / ".workgraph" / ".redrift" / "verify" / f"{_safe_task_key(task_id)}.json"


def load_verify_state(*, project_dir: Path, task_id: str) -> dict[str, Any] | None:
    path = verify_state_path(project_dir=project_dir, task_id=task_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_verify_state(*, project_dir: Path, task_id: str, report: dict[str, Any]) -> None:
    path = verify_state_path(project_dir=project_dir, task_id=task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _truncate(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def _walk_files(project_dir: Path, include_patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for fp in project_dir.rglob("*"):
        if not fp.is_file():
            continue
        rel = fp.relative_to(project_dir).as_posix()
        if any(rel == skip or rel.startswith(f"{skip}/") for skip in _SKIP_DIRS):
            continue
        if not any(fnmatch.fnmatch(rel, pat) for pat in include_patterns):
            continue
        out.append(fp)
    return out


def _assert_max_lines(*, project_dir: Path, assertion: dict[str, Any]) -> dict[str, Any]:
    path = str(assertion.get("path") or "").strip()
    try:
        max_lines = int(assertion.get("max"))
    except Exception:
        max_lines = 0
    if not path or max_lines <= 0:
        return {
            "kind": "max_lines",
            "ok": False,
            "summary": "max_lines assertion requires path + positive max",
            "details": {"path": path, "max": assertion.get("max")},
        }

    fp = project_dir / path
    if not fp.exists():
        return {
            "kind": "max_lines",
            "ok": False,
            "summary": "file not found for max_lines assertion",
            "details": {"path": path, "max": max_lines},
        }

    lines = 0
    with fp.open("r", encoding="utf-8", errors="replace") as handle:
        for _ in handle:
            lines += 1

    ok = lines <= max_lines
    return {
        "kind": "max_lines",
        "ok": ok,
        "summary": f"{path}: {lines} lines (max {max_lines})",
        "details": {"path": path, "lines": lines, "max": max_lines},
    }


def _assert_file_exists(*, project_dir: Path, assertion: dict[str, Any]) -> dict[str, Any]:
    path = str(assertion.get("path") or "").strip()
    if not path:
        return {
            "kind": "file_exists",
            "ok": False,
            "summary": "file_exists assertion requires path",
            "details": {},
        }
    fp = project_dir / path
    ok = fp.exists()
    return {
        "kind": "file_exists",
        "ok": ok,
        "summary": f"{path}: {'exists' if ok else 'missing'}",
        "details": {"path": path},
    }


def _pattern_assertion(*, project_dir: Path, assertion: dict[str, Any], require_hit: bool) -> dict[str, Any]:
    pattern = str(assertion.get("pattern") or "").strip()
    include = assertion.get("include")
    if isinstance(include, list):
        include_patterns = [str(x).strip() for x in include if str(x).strip()]
    else:
        include_patterns = []
    if not include_patterns:
        include_patterns = ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.jsx", "**/*.md", "**/*.toml"]
    if not pattern:
        kind = "require_pattern" if require_hit else "forbid_pattern"
        return {
            "kind": kind,
            "ok": False,
            "summary": f"{kind} assertion requires pattern",
            "details": {"include": include_patterns},
        }

    try:
        rx = re.compile(pattern, re.MULTILINE)
    except Exception as e:
        kind = "require_pattern" if require_hit else "forbid_pattern"
        return {
            "kind": kind,
            "ok": False,
            "summary": f"invalid regex pattern: {e}",
            "details": {"pattern": pattern},
        }

    hits: list[dict[str, Any]] = []
    for fp in _walk_files(project_dir, include_patterns):
        try:
            content = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        m = rx.search(content)
        if not m:
            continue
        rel = fp.relative_to(project_dir).as_posix()
        hits.append(
            {
                "path": rel,
                "line": int(content.count("\n", 0, m.start()) + 1),
            }
        )
        if not require_hit:
            break

    if require_hit:
        ok = bool(hits)
        summary = f"require_pattern {'matched' if ok else 'did not match'}"
        kind = "require_pattern"
    else:
        ok = not hits
        summary = "forbid_pattern clean" if ok else "forbid_pattern matched"
        kind = "forbid_pattern"

    return {
        "kind": kind,
        "ok": ok,
        "summary": summary,
        "details": {"pattern": pattern, "include": include_patterns, "hits": hits[:20]},
    }


def _run_assertion(*, project_dir: Path, assertion: dict[str, Any]) -> dict[str, Any]:
    kind = str(assertion.get("kind") or assertion.get("type") or "").strip().lower()
    if kind == "max_lines":
        return _assert_max_lines(project_dir=project_dir, assertion=assertion)
    if kind == "file_exists":
        return _assert_file_exists(project_dir=project_dir, assertion=assertion)
    if kind == "forbid_pattern":
        return _pattern_assertion(project_dir=project_dir, assertion=assertion, require_hit=False)
    if kind == "require_pattern":
        return _pattern_assertion(project_dir=project_dir, assertion=assertion, require_hit=True)
    return {
        "kind": kind or "unknown",
        "ok": False,
        "summary": f"unsupported assertion kind: {kind or '<missing>'}",
        "details": assertion,
    }


def _run_command(*, project_dir: Path, command: str) -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(project_dir),
        text=True,
        capture_output=True,
    )
    duration_ms = int((time.time() - started) * 1000)
    return {
        "command": command,
        "exit_code": int(proc.returncode),
        "ok": int(proc.returncode) == 0,
        "duration_ms": duration_ms,
        "stdout": _truncate(proc.stdout or ""),
        "stderr": _truncate(proc.stderr or ""),
    }


def run_verify(
    *,
    task_id: str,
    task_title: str,
    spec: RedriftSpec,
    project_dir: Path,
    git_root: str | None,
) -> dict[str, Any]:
    command_results: list[dict[str, Any]] = []
    assertion_results: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    commands = [str(x).strip() for x in spec.verify_commands if str(x).strip()]
    assertions = [x for x in spec.verify_assertions if isinstance(x, dict)]

    if spec.verify_required and not commands and not assertions:
        findings.append(
            {
                "kind": "verify_unconfigured",
                "severity": "error",
                "summary": "verify_required=true but no verify commands/assertions are configured",
            }
        )

    for command in commands:
        row = _run_command(project_dir=project_dir, command=command)
        command_results.append(row)
        if not row.get("ok"):
            findings.append(
                {
                    "kind": "verify_command_failed",
                    "severity": "error",
                    "summary": f"Command failed: {command}",
                    "details": {"exit_code": row.get("exit_code")},
                }
            )

    for assertion in assertions:
        row = _run_assertion(project_dir=project_dir, assertion=assertion)
        assertion_results.append(row)
        if not row.get("ok"):
            findings.append(
                {
                    "kind": "verify_assertion_failed",
                    "severity": "error",
                    "summary": str(row.get("summary") or "assertion failed"),
                    "details": {"assertion": assertion, "result": row},
                }
            )

    score = "green" if not findings else "red"
    return {
        "task_id": task_id,
        "task_title": task_title,
        "git_root": git_root,
        "score": score,
        "required": bool(spec.verify_required),
        "commands": command_results,
        "assertions": assertion_results,
        "findings": findings,
        "summary": {
            "commands_total": len(command_results),
            "commands_failed": len([r for r in command_results if not r.get("ok")]),
            "assertions_total": len(assertion_results),
            "assertions_failed": len([r for r in assertion_results if not r.get("ok")]),
        },
        "generated_at_epoch_ms": int(time.time() * 1000),
    }
