from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from redrift.specs import RedriftSpec

PHASE_ORDER = ["analyze", "respec", "design", "build"]


@dataclass(frozen=True)
class Finding:
    kind: str
    severity: str
    summary: str
    details: dict[str, Any] | None = None


def _phase_for_artifact(rel_path: str) -> str:
    part = str(rel_path).split("/", 1)[0].strip().lower()
    if part in PHASE_ORDER:
        return part
    return "build"


def compute_redrift(
    *,
    task_id: str,
    task_title: str,
    description: str,
    spec: RedriftSpec,
    project_dir: Path,
    git_root: str | None,
) -> dict[str, Any]:
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
                severity="warn",
                summary=f"Unsupported redrift schema: {spec.schema} (expected 1)",
            )
        )

    if missing:
        findings.append(
            Finding(
                kind="missing_redrift_artifacts",
                severity="warn",
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
                severity="warn",
                summary=f"{phase} phase is incomplete ({len(m)} artifact(s) missing)",
                details={"missing": m[:60]},
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
