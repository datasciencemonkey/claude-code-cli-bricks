import os
import json
import subprocess
from pathlib import Path

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

# Create ~/.claude directory
claude_dir = home / ".claude"
claude_dir.mkdir(exist_ok=True)

# 1. Write settings.json for Databricks model serving
settings = {
    "env": {
        "ANTHROPIC_MODEL": "databricks-claude-sonnet-4-5",
        "ANTHROPIC_BASE_URL": f"{os.environ['DATABRICKS_HOST']}/serving-endpoints/anthropic",
        "ANTHROPIC_AUTH_TOKEN": os.environ["DATABRICKS_TOKEN"],
        "ANTHROPIC_CUSTOM_HEADERS": "x-databricks-use-coding-agent-mode: true"
    }
}

settings_path = claude_dir / "settings.json"
settings_path.write_text(json.dumps(settings, indent=2))

# 2. Write ~/.claude.json to skip onboarding (v2.0.65+ fix)
claude_json = {
    "hasCompletedOnboarding": True
}

claude_json_path = home / ".claude.json"
claude_json_path.write_text(json.dumps(claude_json, indent=2))

print(f"Claude configured: {settings_path}")
print(f"Onboarding skipped: {claude_json_path}")

# 3. Install Claude Code CLI if not present
local_bin = home / ".local" / "bin"
claude_bin = local_bin / "claude"

if not claude_bin.exists():
    print("Installing Claude Code CLI...")
    result = subprocess.run(
        ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True
    )
    if result.returncode == 0:
        print("Claude Code CLI installed successfully")
    else:
        print(f"CLI install warning: {result.stderr}")
else:
    print(f"Claude Code CLI already installed at {claude_bin}")

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
source /app/python/source_code/.venv/bin/activate
python /app/python/source_code/sync_to_workspace.py "$(pwd)" &
''')
post_commit_hook.chmod(0o755)

# Configure git to use template for new repos
subprocess.run(
    ["git", "config", "--global", "init.templateDir", str(home / ".git-templates")],
    capture_output=True
)
print("Git post-commit hook template configured")

# 6. Register bundled superpowers plugin
plugins_dir = claude_dir / "plugins"
plugins_dir.mkdir(exist_ok=True)

installed_plugins = {
    "version": 2,
    "plugins": {
        "superpowers@bundled": [
            {
                "scope": "user",
                "installPath": str(home / ".claude" / "plugins" / "superpowers"),
                "version": "4.0.3",
                "installedAt": "2025-01-01T00:00:00.000Z",
                "lastUpdated": "2025-01-01T00:00:00.000Z"
            }
        ]
    }
}

plugins_json_path = plugins_dir / "installed_plugins.json"
plugins_json_path.write_text(json.dumps(installed_plugins, indent=2))
print("Superpowers plugin registered")
