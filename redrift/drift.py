from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from redrift.specs import RedriftSpec
from redrift.verify import load_verify_state, verify_state_path

PHASE_ORDER = ["analyze", "respec", "design", "build"]
_FOLLOWUP_PREFIX_RE = re.compile(r"^(?:redrift-exec-(?:analyze|respec|design|build)-|redrift-(?:analyze|respec|design|build|v2)-|drift-therapy-redrift-)")


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    summary: str
    details: dict[str, Any] | None = None


def redrift_lineage(task_id: str) -> tuple[str, int]:
    current = str(task_id or "")
    depth = 0
    for _ in range(20):
        m = _FOLLOWUP_PREFIX_RE.match(current)
        if not m:
            break
        current = current[m.end() :]
        depth += 1
    return current or str(task_id or ""), depth


def _phase_for_artifact(rel_path: str) -> str:
    part = str(rel_path).split("/", 1)[0].strip().lower()
    if part in PHASE_ORDER:
        return part
    return "build"


def _normalize_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"done", "completed", "complete"}:
        return "done"
    if value in {"abandoned"}:
        return "abandoned"
    if value in {"failed", "error"}:
        return "failed"
    if value in {"blocked"}:
        return "blocked"
    if value in {"in-progress", "in_progress"}:
        return "in_progress"
    if value in {"pending-review", "pending_review"}:
        return "pending_review"
    if value in {"open", "todo", "pending", "not_started", "not-started"}:
        return "open"
    return value or "open"


def _load_unresolved_followups(*, project_dir: Path, root_task_id: str, task_id: str) -> list[dict[str, str]]:
    graph_path = project_dir / ".workgraph" / "graph.jsonl"
    if not graph_path.exists():
        return []

    unresolved: list[dict[str, str]] = []
    try:
        lines = graph_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return unresolved

    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("kind") or "") != "task":
            continue

        rid = str(row.get("id") or "")
        if not rid or rid == task_id:
            continue
        if root_task_id not in rid:
            continue
        if not (rid.startswith("redrift-") or rid.startswith("drift-therapy-redrift-")):
            continue

        status = _normalize_status(row.get("status"))
        if status in {"done", "abandoned"}:
            continue
        unresolved.append({"id": rid, "status": status})

    return unresolved


def compute_redrift(
    *,
    task_id: str,
    task_title: str,
    description: str,
    spec: RedriftSpec,
    project_dir: Path,
    git_root: str | None,
) -> dict[str, Any]:
    _ = description
    root_task_id, lineage_depth = redrift_lineage(task_id)
    findings: list[Finding] = []

    artifacts_root = project_dir / spec.artifact_root / task_id
    phase_missing: dict[str, list[str]] = {}
    existing_count = 0
    missing: list[str] = []

    for rel in spec.required_artifacts:
        rel_clean = str(rel).strip().lstrip("/")
        if not rel_clean:
            continue
        fp = artifacts_root / rel_clean
        if fp.exists():
            existing_count += 1
            continue
        missing.append(rel_clean)
        ph = _phase_for_artifact(rel_clean)
        phase_missing.setdefault(ph, []).append(rel_clean)

    if spec.schema != 1:
        findings.append(
            Finding(
                kind="unsupported_schema",
                severity="error",
                summary=f"Unsupported redrift schema: {spec.schema} (expected 1)",
            )
        )

    if missing:
        findings.append(
            Finding(
                kind="missing_redrift_artifacts",
                severity="error",
                summary=f"Missing {len(missing)} required redrift artifact(s)",
                details={"missing": missing[:120]},
            )
        )

    for phase in PHASE_ORDER:
        m = phase_missing.get(phase) or []
        if not m:
            continue
        findings.append(
            Finding(
                kind=f"phase_incomplete_{phase}",
                severity="error",
                summary=f"{phase} phase is incomplete ({len(m)} artifact(s) missing)",
                details={"missing": m[:60]},
            )
        )

    unresolved_followups = _load_unresolved_followups(
        project_dir=project_dir,
        root_task_id=root_task_id,
        task_id=task_id,
    )
    if unresolved_followups:
        findings.append(
            Finding(
                kind="unresolved_redrift_followups",
                severity="error",
                summary=f"{len(unresolved_followups)} unresolved redrift follow-up task(s) still open",
                details={"tasks": unresolved_followups[:30], "root_task_id": root_task_id},
            )
        )

    verify_required = bool(spec.verify_required)
    verify_report = load_verify_state(project_dir=project_dir, task_id=task_id)
    verify_path = verify_state_path(project_dir=project_dir, task_id=task_id)
    if verify_required and not verify_report:
        findings.append(
            Finding(
                kind="verification_missing",
                severity="error",
                summary="Verification is required but no redrift verify report exists",
                details={"path": str(verify_path)},
            )
        )
    elif verify_report and str(verify_report.get("score") or "").lower() != "green":
        findings.append(
            Finding(
                kind="verification_failed",
                severity="error",
                summary="Latest redrift verification is not green",
                details={
                    "path": str(verify_path),
                    "score": verify_report.get("score"),
                    "summary": verify_report.get("summary"),
                },
            )
        )

    score = "green"
    if any(f.severity == "warn" for f in findings):
        score = "yellow"
    if any(f.severity == "error" for f in findings):
        score = "red"

    recommendations: list[dict[str, Any]] = []
    if missing:
        recommendations.append(
            {
                "priority": "high",
                "action": "Fill missing redrift artifacts before adding new implementation scope",
                "rationale": "Missing migration artifacts cause intent and implementation to drift apart.",
            }
        )

    if phase_missing.get("analyze"):
        recommendations.append(
            {
                "priority": "high",
                "action": "Complete analyze artifacts (inventory + constraints)",
                "rationale": "You need a baseline map of the legacy system before re-spec decisions.",
            }
        )
    if phase_missing.get("respec"):
        recommendations.append(
            {
                "priority": "high",
                "action": "Complete v2 spec artifacts",
                "rationale": "Rebuild quality depends on explicit target behavior and interfaces.",
            }
        )
    if phase_missing.get("design"):
        recommendations.append(
            {
                "priority": "high",
                "action": "Complete architecture/ADR artifacts",
                "rationale": "Design decisions should be explicit before broad implementation changes.",
            }
        )
    if phase_missing.get("build"):
        recommendations.append(
            {
                "priority": "high",
                "action": "Complete migration/build plan artifacts",
                "rationale": "Execution sequencing prevents partial rewrites and hidden regressions.",
            }
        )

    if any(f.kind == "verification_missing" for f in findings):
        recommendations.append(
            {
                "priority": "high",
                "action": f"Run `./.workgraph/redrift wg verify --task {task_id} --write-log`",
                "rationale": "Redrift done-state now requires green verification gates.",
            }
        )

    if any(f.kind == "verification_failed" for f in findings):
        recommendations.append(
            {
                "priority": "high",
                "action": "Fix failing verify commands/assertions and re-run redrift verify",
                "rationale": "Artifact presence alone is insufficient for reliable cutovers.",
            }
        )

    if any(f.kind == "unresolved_redrift_followups" for f in findings):
        recommendations.append(
            {
                "priority": "high",
                "action": "Resolve or close open redrift follow-up tasks before marking done",
                "rationale": "Unresolved follow-ups indicate active drift and incomplete synchronization.",
            }
        )

    if any(f.kind == "unsupported_schema" for f in findings):
        recommendations.append(
            {
                "priority": "high",
                "action": "Set redrift schema = 1",
                "rationale": "Only schema v1 is currently supported.",
            }
        )

    seen_actions: set[str] = set()
    recommendations = [r for r in recommendations if not (r["action"] in seen_actions or seen_actions.add(r["action"]))]  # type: ignore[arg-type]

    telemetry = {
        "artifact_dir": str(artifacts_root),
        "required_count": len(spec.required_artifacts),
        "existing_count": existing_count,
        "missing_count": len(missing),
        "phase_missing": phase_missing,
        "root_task_id": root_task_id,
        "lineage_depth": lineage_depth,
        "verify_required": verify_required,
        "verify_report_path": str(verify_path),
        "verify_report_found": bool(verify_report),
        "verify_score": verify_report.get("score") if verify_report else None,
        "open_redrift_followups": unresolved_followups[:30],
    }

    return {
        "task_id": task_id,
        "task_title": task_title,
        "git_root": git_root,
        "score": score,
        "spec": asdict(spec),
        "telemetry": telemetry,
        "findings": [asdict(f) for f in findings],
        "recommendations": recommendations,
    }
