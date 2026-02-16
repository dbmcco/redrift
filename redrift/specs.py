from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import Any


FENCE_INFO = "redrift"

_FENCE_RE = re.compile(
    r"```(?P<info>redrift)\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)


def extract_redrift_spec(description: str) -> str | None:
    m = _FENCE_RE.search(description or "")
    if not m:
        return None
    return m.group("body").strip()


def parse_redrift_spec(text: str) -> dict[str, Any]:
    data = tomllib.loads(text)
    if not isinstance(data, dict):
        raise ValueError("redrift block must parse to a TOML table/object.")
    return data


@dataclass(frozen=True)
class RedriftSpec:
    schema: int
    artifact_root: str
    required_artifacts: list[str]
    create_phase_followups: bool

    @staticmethod
    def from_raw(raw: dict[str, Any]) -> "RedriftSpec":
        schema = int(raw.get("schema", 1))
        artifact_root = str(raw.get("artifact_root") or ".workgraph/.redrift").strip() or ".workgraph/.redrift"
        required_artifacts = [
            str(x).strip().lstrip("/")
            for x in (
                raw.get("required_artifacts")
                or [
                    "analyze/inventory.md",
                    "analyze/constraints.md",
                    "respec/v2-spec.md",
                    "design/v2-architecture.md",
                    "design/adr.md",
                    "build/migration-plan.md",
                ]
            )
            if str(x).strip()
        ]
        create_phase_followups = bool(raw.get("create_phase_followups", True))
        return RedriftSpec(
            schema=schema,
            artifact_root=artifact_root,
            required_artifacts=required_artifacts,
            create_phase_followups=create_phase_followups,
        )
