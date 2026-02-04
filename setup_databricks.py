#!/usr/bin/env python
"""Configure Databricks CLI with the user's PAT from environment."""
import os
import subprocess
from pathlib import Path

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

# Get credentials from environment
host = os.environ.get("DATABRICKS_HOST")
token = os.environ.get("DATABRICKS_TOKEN")

if not host or not token:
    print("Warning: DATABRICKS_HOST or DATABRICKS_TOKEN not set, skipping CLI config")
    exit(0)

# Create ~/.databrickscfg with DEFAULT profile using PAT auth
databrickscfg = home / ".databrickscfg"
config_content = f"""[DEFAULT]
host = {host}
token = {token}
"""

databrickscfg.write_text(config_content)
databrickscfg.chmod(0o600)  # Restrict permissions
print(f"Databricks CLI configured: {databrickscfg}")

# Verify it works
result = subprocess.run(
    ["databricks", "current-user", "me", "--output", "json"],
    capture_output=True,
    text=True,
    env={
        **os.environ,
        # Remove OAuth vars to force PAT auth
        "DATABRICKS_CLIENT_ID": "",
        "DATABRICKS_CLIENT_SECRET": ""
    }
)

if result.returncode == 0:
    import json
    try:
        user = json.loads(result.stdout)
        print(f"Databricks CLI authenticated as: {user.get('userName', 'unknown')}")
    except json.JSONDecodeError:
        print("Databricks CLI configured (couldn't parse user)")
else:
    print(f"Warning: CLI config may have issues: {result.stderr}")
