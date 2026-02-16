from __future__ import annotations

import argparse
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from redrift import cli
from redrift.cli import ExitCode


class _FakeWorkgraph:
    def __init__(self) -> None:
        self.logs: list[tuple[str, str]] = []

    def show_task(self, task_id: str) -> dict:
        return {"id": task_id, "title": "Analyze baseline"}

    def wg_log(self, task_id: str, message: str) -> None:
        self.logs.append((task_id, message))


class TestCommitCommand(unittest.TestCase):
    def _init_repo(self, root: Path) -> None:
        subprocess.check_call(["git", "init"], cwd=str(root))
        subprocess.check_call(["git", "config", "user.email", "redrift-test@example.com"], cwd=str(root))
        subprocess.check_call(["git", "config", "user.name", "Redrift Test"], cwd=str(root))
        (root / "README.md").write_text("# test\n", encoding="utf-8")
        subprocess.check_call(["git", "add", "README.md"], cwd=str(root))
        subprocess.check_call(["git", "commit", "-m", "init"], cwd=str(root))

    def test_commit_creates_structured_message_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            self._init_repo(project_dir)

            (project_dir / "docs.md").write_text("artifact\n", encoding="utf-8")

            fake_wg = _FakeWorkgraph()
            args = argparse.Namespace(
                task="redrift-exec-analyze-root-task",
                dir=str(project_dir),
                phase=None,
                message=None,
                no_verify=False,
                write_log=True,
                dry_run=False,
                json=False,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=wg_dir):
                with patch("redrift.cli.Workgraph", return_value=fake_wg):
                    rc = cli.cmd_wg_commit(args)

            self.assertEqual(ExitCode.ok, rc)
            subject = subprocess.check_output(["git", "log", "-1", "--pretty=%s"], cwd=str(project_dir), text=True).strip()
            self.assertEqual(
                "redrift(analyze): Analyze baseline [redrift-exec-analyze-root-task]",
                subject,
            )
            self.assertEqual(1, len(fake_wg.logs))
            self.assertIn("Redrift commit:", fake_wg.logs[0][1])

    def test_commit_excludes_drift_state_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            self._init_repo(project_dir)

            (project_dir / "docs.md").write_text("artifact\n", encoding="utf-8")
            state_file = project_dir / ".workgraph" / ".coredrift" / "state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text("{\"x\":1}\n", encoding="utf-8")

            fake_wg = _FakeWorkgraph()
            args = argparse.Namespace(
                task="redrift-exec-analyze-root-task",
                dir=str(project_dir),
                phase=None,
                message=None,
                no_verify=False,
                write_log=False,
                dry_run=False,
                json=False,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=wg_dir):
                with patch("redrift.cli.Workgraph", return_value=fake_wg):
                    rc = cli.cmd_wg_commit(args)

            self.assertEqual(ExitCode.ok, rc)
            changed = subprocess.check_output(
                ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
                cwd=str(project_dir),
                text=True,
            )
            self.assertIn("docs.md", changed)
            self.assertNotIn(".workgraph/.coredrift/state.json", changed)
            self.assertEqual(0, len(fake_wg.logs))

    def test_commit_fails_when_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            self._init_repo(project_dir)

            fake_wg = _FakeWorkgraph()
            args = argparse.Namespace(
                task="redrift-exec-design-root-task",
                dir=str(project_dir),
                phase=None,
                message=None,
                no_verify=False,
                write_log=False,
                dry_run=False,
                json=False,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=wg_dir):
                with patch("redrift.cli.Workgraph", return_value=fake_wg):
                    rc = cli.cmd_wg_commit(args)

            self.assertEqual(ExitCode.usage, rc)
            self.assertEqual(0, len(fake_wg.logs))

    def test_commit_fails_when_only_excluded_changes_exist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)
            self._init_repo(project_dir)

            state_file = project_dir / ".workgraph" / ".coredrift" / "state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text("{\"x\":1}\n", encoding="utf-8")

            fake_wg = _FakeWorkgraph()
            args = argparse.Namespace(
                task="redrift-exec-analyze-root-task",
                dir=str(project_dir),
                phase=None,
                message=None,
                no_verify=False,
                write_log=False,
                dry_run=False,
                json=False,
            )

            with patch("redrift.cli.find_workgraph_dir", return_value=wg_dir):
                with patch("redrift.cli.Workgraph", return_value=fake_wg):
                    rc = cli.cmd_wg_commit(args)

            self.assertEqual(ExitCode.usage, rc)
            self.assertEqual(0, len(fake_wg.logs))


if __name__ == "__main__":
    unittest.main()
