import os
import json
from pathlib import Path

# Create ~/.claude directory
claude_dir = Path.home() / ".claude"
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

claude_json_path = Path.home() / ".claude.json"
claude_json_path.write_text(json.dumps(claude_json, indent=2))

print(f"Claude configured: {settings_path}")
print(f"Onboarding skipped: {claude_json_path}")
