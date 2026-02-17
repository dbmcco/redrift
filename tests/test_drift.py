from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from redrift.drift import compute_redrift
from redrift.specs import RedriftSpec


class TestRedrift(unittest.TestCase):
    def test_green_when_all_artifacts_exist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            task_id = "t1"
            spec = RedriftSpec.from_raw({"schema": 1, "verify_required": False})

            for rel in spec.required_artifacts:
                fp = project_dir / spec.artifact_root / task_id / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text("ok\n", encoding="utf-8")

            report = compute_redrift(
                task_id=task_id,
                task_title="Task",
                description="",
                spec=spec,
                project_dir=project_dir,
                git_root=None,
            )
            self.assertEqual("green", report["score"])
            self.assertEqual([], report["findings"])

    def test_missing_artifacts_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            task_id = "t2"
            spec = RedriftSpec.from_raw({"schema": 1, "verify_required": False})

            report = compute_redrift(
                task_id=task_id,
                task_title="Task",
                description="",
                spec=spec,
                project_dir=project_dir,
                git_root=None,
            )
            kinds = {f["kind"] for f in report["findings"]}
            self.assertIn("missing_redrift_artifacts", kinds)
            self.assertIn("phase_incomplete_analyze", kinds)
            self.assertIn("phase_incomplete_respec", kinds)
            self.assertIn("phase_incomplete_design", kinds)
            self.assertIn("phase_incomplete_build", kinds)
            self.assertEqual("red", report["score"])

    def test_requires_verify_report_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            task_id = "t3"
            spec = RedriftSpec.from_raw({"schema": 1})
            for rel in spec.required_artifacts:
                fp = project_dir / spec.artifact_root / task_id / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text("ok\n", encoding="utf-8")

            report = compute_redrift(
                task_id=task_id,
                task_title="Task",
                description="",
                spec=spec,
                project_dir=project_dir,
                git_root=None,
            )
            kinds = {f["kind"] for f in report["findings"]}
            self.assertIn("verification_missing", kinds)
            self.assertEqual("red", report["score"])


if __name__ == "__main__":
    unittest.main()
