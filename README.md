# redrift

`redrift` is a Speedrift-suite sidecar for **brownfield rebuild drift**.

Use it when you are taking an existing codebase toward a cleaner v2 path and want agents to stay synchronized through four phases:
- analyze
- respec
- design
- build

## Ecosystem Map

This project is part of the Speedrift suite for Workgraph-first drift control.

- Spine: [Workgraph](https://graphwork.github.io/)
- Orchestrator: [driftdriver](https://github.com/dbmcco/driftdriver)
- Baseline lane: [coredrift](https://github.com/dbmcco/coredrift)
- Optional lanes: [specdrift](https://github.com/dbmcco/specdrift), [datadrift](https://github.com/dbmcco/datadrift), [archdrift](https://github.com/dbmcco/archdrift), [depsdrift](https://github.com/dbmcco/depsdrift), [uxdrift](https://github.com/dbmcco/uxdrift), [therapydrift](https://github.com/dbmcco/therapydrift), [yagnidrift](https://github.com/dbmcco/yagnidrift), [redrift](https://github.com/dbmcco/redrift)

## Task Spec Format

Add a per-task fenced TOML block:

````md
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
````

Artifacts are expected at:
- `<artifact_root>/<task_id>/<artifact path>`

## Workgraph Integration

From a Workgraph repo (where `driftdriver install` has written wrappers):

```bash
./.workgraph/drifts check --task <id> --write-log --create-followups
```

## Execute v2 Lane (Build Workflow)

`redrift` can create and kick an execution lane that uses the full Speedrift suite wrappers in `.workgraph/`:

```bash
./.workgraph/redrift wg execute --task <root-id> --write-log
```

What `wg execute` does:
- reads the root task's `redrift` block
- creates phase tasks with deterministic IDs:
  - `redrift-exec-analyze-<root-id>`
  - `redrift-exec-respec-<root-id>`
  - `redrift-exec-design-<root-id>`
  - `redrift-exec-build-<root-id>`
- chains dependencies analyze -> respec -> design -> build
- copies optional suite fence blocks from the root task into phase tasks (`specdrift`, `datadrift`, `archdrift`, `depsdrift`, `uxdrift`, `yagnidrift`)
- runs suite checks for the root task (`coredrift` + fenced modules, including `redrift`)
- can also run suite checks for each generated phase task (`--phase-checks`)
- writes phase task protocol lines that include a structured redrift commit checkpoint command

Optional flags:
- `--v2-repo [path]` (create/use a net-new v2 repo and run lane there; default sibling path `<current>-v2`)
- `--create-followups` (root suite check follow-ups)
- `--phase-checks` (run phase task checks; default off)
- `--phase-followups` (phase suite check follow-ups; default off)
- `--phase-include-therapydrift` (opt-in to copy `therapydrift` fence into phase tasks)
- `--start-service` (start `wg service` after lane setup)

Notes:
- `--v2-repo` bootstraps Git + Workgraph and copies `.workgraph` suite wrappers/policy from the source repo when available.
- It does **not** copy application source files by default; it creates a clean v2 workspace lane.
- On brand-new v2 repos, redrift attempts an initial bootstrap commit (`redrift: bootstrap v2 workspace`).
- By default, `therapydrift` is excluded from generated phase-task fences to reduce recursive loop-noise during heavy phase checks.

## Run Naming

Use run labels as `v2-runN` to avoid confusion with product/version naming:

- `speedrift-ecosystem-v2-run1`
- `speedrift-ecosystem-v2-run2`
- `speedrift-ecosystem-v2-run3`

## E2E Helper Script

For repeatable core-lane dogfood runs:

```bash
scripts/e2e_core_lane.sh --run-id run3
```

Optional:

- `--mode worktree|repo` (default: `worktree`; recommended)
- `--source-repo /path/to/driftdriver`
- `--target-root /path/to/experiments`
- `--worktree-branch redrift-v2-run3` (only in worktree mode)
- `--phase-include-therapydrift` (opt-in)

The script:
- creates a standard root task (`redrift-coredrift-core-ecosystem-v2-<run-id>`)
- defaults to creating a git worktree + branch at `speedrift-ecosystem-v2-<run-id>`
- executes the lane in that worktree path
- treats execute exit codes `0` and `3` as expected outcomes

## Structured Commit Workflow

Use redrift to create checkpoint commits tied to Workgraph tasks:

```bash
./.workgraph/redrift wg commit --task redrift-exec-analyze-<root-id> --phase analyze
```

Behavior:
- stages all changes (`git add -A`)
- excludes known drift state files (`.workgraph/.coredrift/**`, `.workgraph/.speedrift/**`, etc.) from commit staging
- commits with a structured message:
  - `redrift(<phase>): <task title> [<task_id>]`
- optionally writes a `wg log` entry with commit SHA and message (`--write-log`)

Useful flags:
- `--message "<subject>"` override subject text
- `--write-log` append commit summary to task log
- `--dry-run` preview message without creating commit
- `--no-verify` pass-through to `git commit`

Standalone:

```bash
/path/to/redrift/bin/redrift --dir . wg check --task <id> --write-log --create-followups
```

Exit codes:
- `0`: clean
- `3`: findings exist (advisory)
