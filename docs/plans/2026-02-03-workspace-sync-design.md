# Git-Based Workspace Sync Design

**Goal:** Auto-sync user projects from the container to Databricks Workspace on git commit.

**Architecture:** Git post-commit hook triggers `databricks sync` to upload project files to `/Workspace/Users/<email>/projects/<project-name>`.

---

## Overview

When users create projects in the `~/projects` folder and commit with git, their code automatically syncs to their Databricks Workspace. This ensures work persists even when the container restarts.

```
Container                              Databricks Workspace
┌─────────────────────┐                ┌──────────────────────────────────┐
│ ~/projects/         │   git commit   │ /Workspace/Users/<email>/        │
│   my-app/           │ ────────────►  │   projects/                      │
│     .git/hooks/     │   post-commit  │     my-app/                      │
│       post-commit   │   triggers     │       (synced files)             │
└─────────────────────┘                └──────────────────────────────────┘
```

---

## Implementation Plan

### Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `sync_to_workspace.py` | Create | Sync script called by git hook |
| `setup_claude.py` | Modify | Add projects folder + git template setup |
| `requirements.txt` | Modify | Add databricks-sdk |
| `static/index.html` | Modify | Update welcome message |

---

### Task 1: Create sync_to_workspace.py

```python
#!/usr/bin/env python3
"""Sync a project directory to Databricks Workspace."""
import os
import sys
import subprocess
from pathlib import Path

def get_user_email():
    """Get current user's email from Databricks token."""
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    return w.current_user.me().user_name

def sync_project(project_path: Path):
    """Sync project to user's Workspace."""
    try:
        user_email = get_user_email()
        workspace_dest = f"/Workspace/Users/{user_email}/projects/{project_path.name}"

        result = subprocess.run(
            ["databricks", "sync", str(project_path), workspace_dest, "--watch=false"],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            print(f"✓ Synced to {workspace_dest}")
        else:
            print(f"⚠ Sync warning: {result.stderr}", file=sys.stderr)

    except Exception as e:
        # Log error but don't block the commit
        error_log = Path.home() / ".sync-errors.log"
        with open(error_log, "a") as f:
            f.write(f"{project_path}: {e}\n")
        print(f"⚠ Sync failed (logged to ~/.sync-errors.log)", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        sync_project(Path(sys.argv[1]))
    else:
        sync_project(Path.cwd())
```

---

### Task 2: Update setup_claude.py

Add after existing code:

```python
# 4. Create projects directory
projects_dir = home / "projects"
projects_dir.mkdir(exist_ok=True)
print(f"Projects directory: {projects_dir}")

# 5. Set up git template with post-commit hook
git_template_hooks = home / ".git-templates" / "hooks"
git_template_hooks.mkdir(parents=True, exist_ok=True)

post_commit_hook = git_template_hooks / "post-commit"
post_commit_hook.write_text('''#!/bin/bash
# Auto-sync to Databricks Workspace on commit
python3 /app/python/source_code/sync_to_workspace.py "$(pwd)" &
''')
post_commit_hook.chmod(0o755)

# Configure git to use template for new repos
subprocess.run(
    ["git", "config", "--global", "init.templateDir", str(home / ".git-templates")],
    capture_output=True
)
print("Git post-commit hook template configured")
```

---

### Task 3: Update requirements.txt

Add:
```
databricks-sdk>=0.20.0
```

---

### Task 4: Update welcome message in static/index.html

Change the welcome message to:
```javascript
term.write('\x1b[32mConnected. Type "claude" to start coding.\x1b[0m\r\n');
term.write('\x1b[90mProjects in ~/projects auto-sync to Workspace on git commit.\x1b[0m\r\n\r\n');
```

---

## User Workflow

```bash
# 1. User connects to terminal
# 2. Navigate to projects folder
cd ~/projects

# 3. Create a new project
mkdir my-app && cd my-app

# 4. Initialize git (post-commit hook auto-installed)
git init

# 5. Write code with Claude...

# 6. Commit triggers sync
git add . && git commit -m "initial"
# Output: ✓ Synced to /Workspace/Users/user@company.com/projects/my-app
```

---

## Configuration

- **Sync destination:** `/Workspace/Users/<email>/projects/<project-name>`
- **User email:** Derived from Databricks token via SDK at runtime
- **Trigger:** Git post-commit hook (only on commits)

---

## Error Handling

- Sync failures are logged to `~/.sync-errors.log`
- Errors don't block git commits
- Failed syncs retry on next commit

---

## Verification

1. Deploy the updated app
2. Connect to terminal
3. Run:
   ```bash
   cd ~/projects
   mkdir test-sync && cd test-sync
   git init
   echo "# Test" > README.md
   git add . && git commit -m "test"
   ```
4. Check Databricks Workspace: `/Workspace/Users/<your-email>/projects/test-sync/`
5. Verify README.md appears

---

## Dependencies

- `databricks-sdk>=0.20.0` (add to requirements.txt)
