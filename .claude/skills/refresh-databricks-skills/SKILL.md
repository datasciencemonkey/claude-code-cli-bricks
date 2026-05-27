---
name: refresh-databricks-skills
description: Use when Databricks skills need updating, user asks to refresh or sync skills from upstream, or skills seem outdated compared to the databricks-agent-skills repo
---

# Refresh Databricks Skills

## Overview

Pulls the latest Databricks skills from the upstream source repo and replaces all existing Databricks skills in the project while preserving non-Databricks skills (e.g., superpowers workflow skills).

**Source repo:** `https://github.com/databricks/databricks-agent-skills` (paths: `skills/` for stable + `experimental/` for experimental).

The upstream content was previously [`databricks-solutions/ai-dev-kit`](https://github.com/databricks-solutions/ai-dev-kit) (now deprecated). DAS is the canonical source going forward — stable skills live under `skills/` and experimental skills (the verbatim a-d-k snapshot) live under `experimental/`.

## When to Use

- User asks to update, refresh, or sync Databricks skills
- Skills seem outdated or missing newer Databricks features
- A new Databricks skill was added upstream that the project needs

## Process

1. **Clone the upstream repo** (shallow clone for speed):
   ```bash
   git clone --depth 1 https://github.com/databricks/databricks-agent-skills.git $TMPDIR/das
   ```

2. **Identify non-Databricks skills to preserve.** These are the superpowers workflow skills that live alongside Databricks skills. List them by checking which directories in `.claude/skills/` do NOT have a matching folder under `skills/` or `experimental/` in the upstream repo. Common superpowers skills include: `brainstorming`, `dispatching-parallel-agents`, `executing-plans`, `finishing-a-development-branch`, `receiving-code-review`, `requesting-code-review`, `subagent-driven-development`, `systematic-debugging`, `test-driven-development`, `using-git-worktrees`, `using-superpowers`, `verification-before-completion`, `writing-plans`, `writing-skills`. Also preserve any other project-specific skills (like this one: `refresh-databricks-skills`).

3. **Remove old Databricks skills** from `.claude/skills/`, keeping all non-Databricks skills identified above.

4. **Copy new Databricks skills** from the cloned repo. Copy every directory under both `skills/` (stable) and `experimental/`:
   ```bash
   SKILLS_DIR=".claude/skills"
   UPSTREAM_STABLE="$TMPDIR/das/skills"
   UPSTREAM_EXPERIMENTAL="$TMPDIR/das/experimental"
   for src in "$UPSTREAM_STABLE" "$UPSTREAM_EXPERIMENTAL"; do
     for dir in "$src"/databricks-* "$src"/spark-*; do
       [ -d "$dir" ] && cp -r "$dir" "$SKILLS_DIR/$(basename "$dir")"
     done
   done
   ```

   Note: name collisions across `skills/` and `experimental/` are impossible — DAS's `scripts/skills.py` rejects them at generate-time.

5. **Clean up** the cloned repo:
   ```bash
   rm -rf $TMPDIR/das
   ```

6. **Report** the count of skills added, removed, and updated, and call out whether each added skill came from `skills/` (stable) or `experimental/`.

## After Refreshing

If the project is deployed as a Databricks App, remind the user to sync the updated skills to the workspace and redeploy:
```bash
databricks workspace import-dir <local-path> <workspace-path> --overwrite --profile <profile>
databricks apps deploy <app-name> --source-code-path <workspace-path> --profile <profile>
```

## Common Mistakes

- **Pointing at the deprecated upstream:** Old versions of this skill cloned `databricks-solutions/ai-dev-kit`. That repo is deprecated and no longer receives skill updates — always use `databricks/databricks-agent-skills`.
- **Forgetting `experimental/`:** DAS splits skills into `skills/` (stable, reviewed) and `experimental/` (best-effort a-d-k import). Both directories must be copied or you'll regress functionality.
- **Deleting non-Databricks skills:** Always identify and preserve superpowers and project-specific skills before removing anything.
- **Forgetting this skill itself:** `refresh-databricks-skills` must be preserved during the refresh.
- **Not using `--depth 1`:** Full clone is slow and unnecessary. Always shallow clone.
