#!/usr/bin/env python
"""Configure OpenCode CLI with Databricks Model Serving as an OpenAI-compatible provider."""
import os
import json
import subprocess
from pathlib import Path

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

host = os.environ.get("DATABRICKS_HOST", "")
token = os.environ.get("DATABRICKS_TOKEN", "")

if not host or not token:
    print("Warning: DATABRICKS_HOST or DATABRICKS_TOKEN not set, skipping OpenCode config")
    exit(0)

# Strip trailing slash from host
host = host.rstrip("/")

# 1. Install OpenCode CLI if not present
opencode_bin = home / ".local" / "bin" / "opencode"
npm_global_bin = subprocess.run(
    ["npm", "config", "get", "prefix"],
    capture_output=True, text=True
).stdout.strip()

# Check if opencode is already installed anywhere on PATH
opencode_installed = subprocess.run(
    ["which", "opencode"], capture_output=True, text=True
).returncode == 0

if not opencode_installed:
    print("Installing OpenCode CLI...")
    result = subprocess.run(
        ["npm", "install", "-g", "opencode-ai@latest"],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(home)}
    )
    if result.returncode == 0:
        print("OpenCode CLI installed successfully")
    else:
        print(f"OpenCode install warning: {result.stderr}")
else:
    print("OpenCode CLI already installed")

# 2. Write global opencode.json config
# OpenCode looks for config at ~/.config/opencode/opencode.json (global)
# and ./opencode.json (project-level)
opencode_config_dir = home / ".config" / "opencode"
opencode_config_dir.mkdir(parents=True, exist_ok=True)

# Databricks OpenAI-compatible endpoint: {host}/serving-endpoints
# Model names follow: databricks-<provider>-<model>
opencode_config = {
    "$schema": "https://opencode.ai/config.json",
    "provider": {
        "databricks": {
            "npm": "@ai-sdk/openai-compatible",
            "name": "Databricks Model Serving",
            "options": {
                "baseURL": f"{host}/serving-endpoints",
                "apiKey": "{env:DATABRICKS_TOKEN}"
            },
            "models": {
                "databricks-claude-sonnet-4-5": {
                    "name": "Claude Sonnet 4.5 (Databricks)",
                    "limit": {
                        "context": 200000,
                        "output": 8192
                    }
                },
                "databricks-gemini-2-5-flash": {
                    "name": "Gemini 2.5 Flash (Databricks)",
                    "limit": {
                        "context": 1000000,
                        "output": 8192
                    }
                },
                "databricks-gemini-2-5-pro": {
                    "name": "Gemini 2.5 Pro (Databricks)",
                    "limit": {
                        "context": 1000000,
                        "output": 8192
                    }
                },
                "databricks-meta-llama-3-3-70b-instruct": {
                    "name": "Llama 3.3 70B (Databricks)",
                    "limit": {
                        "context": 128000,
                        "output": 4096
                    }
                }
            }
        }
    },
    "model": "databricks/databricks-claude-sonnet-4-5"
}

config_path = opencode_config_dir / "opencode.json"
config_path.write_text(json.dumps(opencode_config, indent=2))
print(f"OpenCode configured: {config_path}")

# 3. Also create auth credentials for the databricks provider
# OpenCode stores credentials at ~/.local/share/opencode/auth.json
opencode_data_dir = home / ".local" / "share" / "opencode"
opencode_data_dir.mkdir(parents=True, exist_ok=True)

auth_data = {
    "databricks": {
        "api_key": token
    }
}

auth_path = opencode_data_dir / "auth.json"
auth_path.write_text(json.dumps(auth_data, indent=2))
auth_path.chmod(0o600)
print(f"OpenCode auth configured: {auth_path}")

print("\nOpenCode ready! Usage:")
print("  opencode                          # Start OpenCode TUI")
print("  opencode -m databricks/databricks-gemini-2-5-flash  # Use Gemini")
print("  opencode -m databricks/databricks-claude-sonnet-4-5 # Use Claude")
