from __future__ import annotations

import subprocess
from pathlib import Path


def get_git_root(project_dir: Path) -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_dir), "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:
        return None
