from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Workgraph:
    wg_dir: Path
    project_dir: Path

    def wg_log(self, task_id: str, message: str) -> None:
        subprocess.check_call(["wg", "--dir", str(self.wg_dir), "log", task_id, message])

    def ensure_task(
        self,
        *,
        task_id: str,
        title: str,
        description: str,
        blocked_by: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        try:
            subprocess.check_output(
                ["wg", "--dir", str(self.wg_dir), "show", task_id, "--json"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            return
        except Exception:
            pass

        cmd = ["wg", "--dir", str(self.wg_dir), "add", title, "--id", task_id]
        if description:
            cmd += ["-d", description]
        if blocked_by:
            cmd += ["--blocked-by", *blocked_by]
        if tags:
            for t in tags:
                cmd += ["-t", t]
        subprocess.check_call(cmd)

    def show_task(self, task_id: str) -> dict[str, Any]:
        out = subprocess.check_output(["wg", "--dir", str(self.wg_dir), "show", task_id, "--json"], text=True)
        return json.loads(out)


def find_workgraph_dir(explicit: Path | None) -> Path:
    if explicit:
        p = explicit
        if p.name != ".workgraph":
            p = p / ".workgraph"
        if not (p / "graph.jsonl").exists():
            raise FileNotFoundError(f"Workgraph not found at: {p}")
        return p

    cur = Path.cwd()
    for p in [cur, *cur.parents]:
        candidate = p / ".workgraph" / "graph.jsonl"
        if candidate.exists():
            return candidate.parent
    raise FileNotFoundError("Could not find .workgraph/graph.jsonl; pass --dir.")
