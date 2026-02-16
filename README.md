# redrift

`redrift` is a Speedrift-suite sidecar for **brownfield rebuild drift**.

Use it when you are taking an existing codebase toward a cleaner v2 path and want agents to stay synchronized through four phases:
- analyze
- respec
- design
- build

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

Standalone:

```bash
/path/to/redrift/bin/redrift --dir . wg check --task <id> --write-log --create-followups
```

Exit codes:
- `0`: clean
- `3`: findings exist (advisory)
