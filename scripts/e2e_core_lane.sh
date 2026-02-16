#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/e2e_core_lane.sh --run-id <runN> [--mode worktree|repo] [--source-repo <path>] [--target-root <path>] [--worktree-branch <name>] [--phase-include-therapydrift]

Examples:
  scripts/e2e_core_lane.sh --run-id run3
  scripts/e2e_core_lane.sh --run-id run4 --mode worktree --worktree-branch redrift-v2-run4
  scripts/e2e_core_lane.sh --run-id run5 --mode repo
EOF
}

SOURCE_REPO="/Users/braydon/projects/experiments/driftdriver"
TARGET_ROOT="/Users/braydon/projects/experiments"
RUN_ID=""
PHASE_INCLUDE_THERAPY=false
MODE="worktree"
WORKTREE_BRANCH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-id)
      RUN_ID="${2:-}"
      shift 2
      ;;
    --mode)
      MODE="${2:-}"
      shift 2
      ;;
    --source-repo)
      SOURCE_REPO="${2:-}"
      shift 2
      ;;
    --target-root)
      TARGET_ROOT="${2:-}"
      shift 2
      ;;
    --worktree-branch)
      WORKTREE_BRANCH="${2:-}"
      shift 2
      ;;
    --phase-include-therapydrift)
      PHASE_INCLUDE_THERAPY=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

if [[ -z "$RUN_ID" ]]; then
  echo "error: --run-id is required (example: run3)" >&2
  exit 2
fi

if [[ "$MODE" != "worktree" && "$MODE" != "repo" ]]; then
  echo "error: --mode must be one of: worktree, repo" >&2
  exit 2
fi

WG_DIR="$SOURCE_REPO/.workgraph"
WRAPPER="$WG_DIR/redrift"
TASK_ID="redrift-speedrift-core-ecosystem-v2-${RUN_ID}"
TARGET_REPO="$TARGET_ROOT/speedrift-ecosystem-v2-${RUN_ID}"
if [[ -z "$WORKTREE_BRANCH" ]]; then
  WORKTREE_BRANCH="redrift-v2-${RUN_ID}"
fi

if [[ ! -x "$WRAPPER" ]]; then
  echo "error: redrift wrapper not found at $WRAPPER" >&2
  echo "hint: run '$SOURCE_REPO/bin/driftdriver install --with-redrift --with-uxdrift --with-therapydrift --with-yagnidrift'" >&2
  exit 2
fi

if [[ -e "$TARGET_REPO" ]]; then
  echo "error: target repo already exists: $TARGET_REPO" >&2
  exit 2
fi

if [[ "$MODE" == "worktree" ]]; then
  if ! git -C "$SOURCE_REPO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "error: source repo is not a git repository: $SOURCE_REPO" >&2
    exit 2
  fi
  if git -C "$SOURCE_REPO" show-ref --verify --quiet "refs/heads/$WORKTREE_BRANCH"; then
    echo "error: worktree branch already exists: $WORKTREE_BRANCH" >&2
    exit 2
  fi
  git -C "$SOURCE_REPO" worktree add -b "$WORKTREE_BRANCH" "$TARGET_REPO"
fi

tmp_desc="$(mktemp)"
cat > "$tmp_desc" <<EOF
Redrift the core Speedrift ecosystem with a controlled v2 lane.

Scope:
- driftdriver (orchestration spine)
- speedrift (baseline lane)
- redrift (brownfield v2 lane)

Success metrics:
- Fresh bootstrap path succeeds in one pass (install -> execute -> phase commit).
- Core docs and commands are internally consistent and cross-referenced.
- Redrift phase checkpoints produce clean, structured commits.
- No unmanaged recursion loops (therapy/yagni checks stay advisory and bounded).

Execution notes:
- Use net-new v2 workspace for execution lane.
- Keep changes scoped to core orchestration concerns; defer new drift modules unless evidence demands them.

\`\`\`wg-contract
schema = 1
mode = "core"
objective = "Redrift core ecosystem with stable bootstrap, commit checkpoints, and e2e lane evidence"
non_goals = [
  "Do not add net-new drift modules in this lane unless explicit evidence appears",
  "Do not rewrite non-core module internals in this lane",
]
touch = [
  "README.md",
  "driftdriver/**",
  "speedrift/**",
  "redrift/**",
  ".workgraph/**",
  "docs/**",
]
acceptance = [
  "./.workgraph/redrift wg execute --task ${TASK_ID} --v2-repo ${TARGET_REPO} --write-log --phase-checks",
]
max_files = 120
max_loc = 4000
pit_stop_after = 3
auto_followups = true
\`\`\`

\`\`\`redrift
schema = 1
artifact_root = ".workgraph/.redrift"
required_artifacts = [
  "analyze/current-state.md",
  "analyze/gap-matrix.md",
  "respec/target-operating-model.md",
  "respec/acceptance-gates.md",
  "design/control-plane-architecture.md",
  "design/phase-interfaces.md",
  "build/e2e-dogfood-runbook.md",
  "build/release-cut-plan.md",
]
create_phase_followups = true
\`\`\`

\`\`\`specdrift
schema = 1
spec = ["README.md", "docs/**", ".workgraph/.redrift/**"]
require_spec_update_when_code_changes = true
\`\`\`

\`\`\`therapydrift
schema = 1
min_signal_count = 2
followup_prefixes = ["drift-", "speedrift-pit-", "redrift-"]
require_recovery_plan = true
ignore_signal_prefixes = ["Therapydrift:"]
cooldown_seconds = 1800
max_auto_actions_per_hour = 2
min_new_signals = 1
circuit_breaker_after = 6
\`\`\`

\`\`\`yagnidrift
schema = 1
max_new_files = 25
max_new_dirs = 6
enforce_no_speculative_abstractions = true
abstraction_keywords = ["framework", "engine", "orchestrator", "provider", "base"]
allow_paths = ["driftdriver/**", "speedrift/**", "redrift/**", "docs/**", ".workgraph/**"]
\`\`\`
EOF

if wg --dir "$WG_DIR" show "$TASK_ID" >/dev/null 2>&1; then
  echo "task already exists: $TASK_ID"
else
  wg --dir "$WG_DIR" add "Redrift core ecosystem (driftdriver + speedrift + redrift) ${RUN_ID}" \
    --id "$TASK_ID" \
    -d "$(cat "$tmp_desc")" \
    -t drift -t redrift -t ecosystem -t core
fi

cmd=( "$WRAPPER" --dir "$SOURCE_REPO" wg execute --task "$TASK_ID" --v2-repo "$TARGET_REPO" --write-log --create-followups --phase-checks )
if [[ "$PHASE_INCLUDE_THERAPY" == true ]]; then
  cmd+=( --phase-include-therapydrift )
fi

set +e
"${cmd[@]}"
rc=$?
set -e

rm -f "$tmp_desc"

echo "task_id=$TASK_ID"
echo "target_repo=$TARGET_REPO"
echo "mode=$MODE"
if [[ "$MODE" == "worktree" ]]; then
  echo "worktree_branch=$WORKTREE_BRANCH"
fi
echo "execute_exit=$rc"
if [[ $rc -ne 0 && $rc -ne 3 ]]; then
  exit "$rc"
fi

exit 0
