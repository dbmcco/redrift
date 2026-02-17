from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from redrift.specs import RedriftSpec
from redrift.verify import load_verify_state, run_verify, write_verify_state


class TestVerify(unittest.TestCase):
    def test_run_verify_green(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            (project_dir / "src").mkdir(parents=True, exist_ok=True)
            (project_dir / "src" / "app.ts").write_text("export const x = 1;\n", encoding="utf-8")

            spec = RedriftSpec.from_raw(
                {
                    "schema": 1,
                    "verify_required": True,
                    "verify_commands": ["true"],
                    "verify_assertions": [
                        {"kind": "max_lines", "path": "src/app.ts", "max": 20},
                        {"kind": "forbid_pattern", "pattern": "python3", "include": ["src/**/*.ts"]},
                    ],
                }
            )
            report = run_verify(
                task_id="task-1",
                task_title="Task 1",
                spec=spec,
                project_dir=project_dir,
                git_root=None,
            )
            self.assertEqual("green", report["score"])
            self.assertEqual([], report["findings"])

    def test_run_verify_red_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            (project_dir / "src").mkdir(parents=True, exist_ok=True)
            (project_dir / "src" / "app.ts").write_text("python3\n", encoding="utf-8")

            spec = RedriftSpec.from_raw(
                {
                    "schema": 1,
                    "verify_required": True,
                    "verify_commands": ["false"],
                    "verify_assertions": [
                        {"kind": "forbid_pattern", "pattern": "python3", "include": ["src/**/*.ts"]},
                    ],
                }
            )
            report = run_verify(
                task_id="task-2",
                task_title="Task 2",
                spec=spec,
                project_dir=project_dir,
                git_root=None,
            )
            self.assertEqual("red", report["score"])
            write_verify_state(project_dir=project_dir, task_id="task-2", report=report)
            persisted = load_verify_state(project_dir=project_dir, task_id="task-2")
            self.assertIsNotNone(persisted)
            self.assertEqual("red", persisted["score"])


if __name__ == "__main__":
    unittest.main()
