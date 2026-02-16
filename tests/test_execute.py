from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from redrift import cli
from redrift.cli import ExitCode
from redrift.specs import RedriftSpec


class _FakeWorkgraph:
    def __init__(self, task: dict) -> None:
        self.task = task
        self.ensured: list[dict] = []

    def show_task(self, task_id: str) -> dict:
        _ = task_id
        return self.task

    def ensure_task(
        self,
        *,
        task_id: str,
        title: str,
        description: str,
        blocked_by: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        self.ensured.append(
            {
                "task_id": task_id,
                "title": title,
                "description": description,
                "blocked_by": blocked_by,
                "tags": tags,
            }
        )


class TestExecuteHelpers(unittest.TestCase):
    def test_phase_artifacts_groups_unknown_paths_into_build(self) -> None:
        spec = RedriftSpec.from_raw(
            {
                "schema": 1,
                "required_artifacts": [
                    "analyze/inventory.md",
                    "design/adr.md",
                    "foo/custom.md",
                ],
            }
        )
        grouped = cli._phase_artifacts(spec)
        self.assertEqual(["analyze/inventory.md"], grouped["analyze"])
        self.assertEqual(["design/adr.md"], grouped["design"])
        self.assertIn("foo/custom.md", grouped["build"])

    def test_merge_v2_workgraph_gitignore_adds_defaults_and_keeps_redrift_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src.gitignore"
            dst = root / "dst.gitignore"

            src.write_text(
                "\n".join(
                    [
                        "# Workgraph gitignore",
                        ".speedrift/",
                        ".redrift/",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            dst.write_text("# target\n", encoding="utf-8")

            additions = cli._merge_v2_workgraph_gitignore(source=src, target=dst)
            merged = dst.read_text(encoding="utf-8")

            self.assertIn(".speedrift/", merged)
            self.assertIn(".redrift/last.json", merged)
            self.assertNotIn(".redrift/\n", merged)
            self.assertGreaterEqual(len(additions), 1)


class TestExecuteCommand(unittest.TestCase):
    def _root_description(self) -> str:
        return """
```redrift
schema = 1
artifact_root = ".workgraph/.redrift"
required_artifacts = [
  "analyze/inventory.md",
  "analyze/constraints.md",
  "respec/v2-spec.md",
  "design/v2-architecture.md",
  "design/adr.md",
  "build/migration-plan.md",
]
create_phase_followups = true
```

```specdrift
schema = 1
spec = ["README.md", "docs/**"]
require_spec_update_when_code_changes = true
```
""".strip()

    def test_execute_creates_phase_lane_and_runs_suite_checks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "speedrift").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            (wg_dir / "speedrift").chmod(0o755)

            fake_wg = _FakeWorkgraph(task={"title": "Root", "description": self._root_description()})
            args = argparse.Namespace(
                task="root-task",
                dir=str(project_dir),
                write_log=True,
                create_followups=True,
                phase_checks=False,
                phase_followups=False,
                start_service=False,
                json=False,
                v2_repo=None,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=wg_dir):
                with patch("redrift.cli.Workgraph", return_value=fake_wg):
                    with patch("redrift.cli._run_suite_check", side_effect=[(0, [{"plugin": "speedrift", "exit_code": 0}])]) as mock_suite:
                        rc = cli.cmd_wg_execute(args)

            self.assertEqual(ExitCode.ok, rc)
            self.assertEqual(4, len(fake_wg.ensured))

            phase_ids = [row["task_id"] for row in fake_wg.ensured]
            self.assertEqual(
                [
                    "redrift-exec-analyze-root-task",
                    "redrift-exec-respec-root-task",
                    "redrift-exec-design-root-task",
                    "redrift-exec-build-root-task",
                ],
                phase_ids,
            )
            self.assertIsNone(fake_wg.ensured[0]["blocked_by"])
            self.assertEqual([phase_ids[0]], fake_wg.ensured[1]["blocked_by"])
            self.assertEqual([phase_ids[1]], fake_wg.ensured[2]["blocked_by"])
            self.assertEqual([phase_ids[2]], fake_wg.ensured[3]["blocked_by"])

            analyze_desc = fake_wg.ensured[0]["description"]
            self.assertIn("```specdrift", analyze_desc)
            self.assertIn("create_phase_followups = false", analyze_desc)
            self.assertIn("./.workgraph/drifts check --task redrift-exec-analyze-root-task --write-log", analyze_desc)

            self.assertEqual(1, mock_suite.call_count)
            root_call = mock_suite.call_args_list[0].kwargs
            self.assertEqual("root-task", root_call["task_id"])
            self.assertTrue(root_call["create_followups"])

    def test_execute_requires_speedrift_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            args = argparse.Namespace(
                task="root-task",
                dir=str(project_dir),
                write_log=False,
                create_followups=False,
                phase_checks=True,
                phase_followups=False,
                start_service=False,
                json=False,
                v2_repo=None,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=wg_dir):
                rc = cli.cmd_wg_execute(args)

            self.assertEqual(ExitCode.usage, rc)

    def test_execute_phase_checks_respect_phase_followups_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            (wg_dir / "speedrift").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            (wg_dir / "speedrift").chmod(0o755)

            fake_wg = _FakeWorkgraph(task={"title": "Root", "description": self._root_description()})
            args = argparse.Namespace(
                task="root-task",
                dir=str(project_dir),
                write_log=True,
                create_followups=True,
                phase_checks=True,
                phase_followups=False,
                start_service=False,
                json=False,
                v2_repo=None,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=wg_dir):
                with patch("redrift.cli.Workgraph", return_value=fake_wg):
                    side_effect = [
                        (ExitCode.findings, [{"plugin": "speedrift", "exit_code": ExitCode.findings}]),
                        (ExitCode.ok, [{"plugin": "speedrift", "exit_code": ExitCode.ok}]),
                        (ExitCode.ok, [{"plugin": "speedrift", "exit_code": ExitCode.ok}]),
                        (ExitCode.ok, [{"plugin": "speedrift", "exit_code": ExitCode.ok}]),
                        (ExitCode.ok, [{"plugin": "speedrift", "exit_code": ExitCode.ok}]),
                    ]
                    with patch("redrift.cli._run_suite_check", side_effect=side_effect) as mock_suite:
                        rc = cli.cmd_wg_execute(args)

            self.assertEqual(ExitCode.findings, rc)
            self.assertEqual(5, mock_suite.call_count)
            root_call = mock_suite.call_args_list[0].kwargs
            self.assertTrue(root_call["create_followups"])
            phase_call = mock_suite.call_args_list[1].kwargs
            self.assertFalse(phase_call["create_followups"])

    def test_execute_with_v2_repo_bootstraps_and_runs_in_target_repo(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            source_project_dir = Path(td) / "source"
            source_wg_dir = source_project_dir / ".workgraph"
            source_wg_dir.mkdir(parents=True, exist_ok=True)
            (source_wg_dir / "speedrift").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            (source_wg_dir / "speedrift").chmod(0o755)

            target_project_dir = Path(td) / "source-v2"
            target_wg_dir = target_project_dir / ".workgraph"
            target_wg_dir.mkdir(parents=True, exist_ok=True)
            (target_wg_dir / "speedrift").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            (target_wg_dir / "speedrift").chmod(0o755)

            source_wg = _FakeWorkgraph(task={"title": "Root", "description": self._root_description()})
            target_wg = _FakeWorkgraph(task={"title": "Root", "description": self._root_description()})

            args = argparse.Namespace(
                task="root-task",
                dir=str(source_project_dir),
                v2_repo="auto",
                write_log=True,
                create_followups=True,
                phase_checks=False,
                phase_followups=False,
                start_service=False,
                json=False,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=source_wg_dir):
                with patch("redrift.cli.Workgraph", side_effect=[source_wg, target_wg]):
                    with patch(
                        "redrift.cli._bootstrap_v2_repo",
                        return_value=(target_project_dir, target_wg_dir, ["initialized_workgraph"]),
                    ) as mock_bootstrap:
                        with patch(
                            "redrift.cli._run_suite_check",
                            return_value=(ExitCode.ok, [{"plugin": "speedrift", "exit_code": ExitCode.ok}]),
                        ) as mock_suite:
                            rc = cli.cmd_wg_execute(args)

            self.assertEqual(ExitCode.ok, rc)
            self.assertEqual(1, mock_bootstrap.call_count)
            self.assertEqual(1, mock_suite.call_count)

            # Root + 4 phase tasks are created on target workgraph.
            self.assertEqual(5, len(target_wg.ensured))
            self.assertEqual("root-task", target_wg.ensured[0]["task_id"])
            self.assertIn("v2-root", target_wg.ensured[0]["tags"])
            self.assertEqual("redrift-exec-analyze-root-task", target_wg.ensured[1]["task_id"])


if __name__ == "__main__":
    unittest.main()
