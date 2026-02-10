#!/usr/bin/env python
"""Configure Gemini CLI with Databricks Model Serving.

Gemini CLI uses the Google Generative Language API protocol, not OpenAI-compatible.
Databricks provides a Google-native endpoint at /serving-endpoints/google
(similar to /serving-endpoints/anthropic for Claude).

PR #11893 (by Databricks engineer AarushiShah) added auto-detection of *.databricks.com
URLs, switching to Bearer token auth automatically.

Auth: GEMINI_API_KEY_AUTH_MECHANISM=bearer sends Databricks PAT as Bearer token.
"""
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
    print("Warning: DATABRICKS_HOST or DATABRICKS_TOKEN not set, skipping Gemini CLI config")
    exit(0)

# Strip trailing slash from host
host = host.rstrip("/")

# 1. Install Gemini CLI if not present
gemini_installed = subprocess.run(
    ["which", "gemini"], capture_output=True, text=True
).returncode == 0

if not gemini_installed:
    print("Installing Gemini CLI...")
    result = subprocess.run(
        ["npm", "install", "-g", "@google/gemini-cli"],
        capture_output=True, text=True,
        env={**os.environ, "HOME": str(home)}
    )
    if result.returncode == 0:
        print("Gemini CLI installed successfully")
    else:
        print(f"Gemini CLI install warning: {result.stderr}")
else:
    print("Gemini CLI already installed")

# 2. Create ~/.gemini directory and configure environment
gemini_dir = home / ".gemini"
gemini_dir.mkdir(exist_ok=True)

# Write .env file with Databricks endpoint configuration
# Gemini CLI auto-loads env from ~/.gemini/.env
# The Google-native endpoint on Databricks mirrors /serving-endpoints/anthropic
env_content = f"""# Databricks Model Serving - Google Gemini native endpoint
GOOGLE_GEMINI_BASE_URL={host}/serving-endpoints/google
GEMINI_API_KEY={token}
GEMINI_API_KEY_AUTH_MECHANISM=bearer
"""

env_path = gemini_dir / ".env"
env_path.write_text(env_content)
env_path.chmod(0o600)
print(f"Gemini CLI env configured: {env_path}")

# 3. Write settings.json with model preferences
settings = {
    "theme": "Default",
    "selectedAuthType": "api-key"
}

settings_path = gemini_dir / "settings.json"
settings_path.write_text(json.dumps(settings, indent=2))
print(f"Gemini CLI settings configured: {settings_path}")

print("\nGemini CLI ready! Usage:")
print("  gemini                                    # Start Gemini CLI")
print(f"  gemini -m gemini-2.5-flash                # Use Gemini 2.5 Flash")
print(f"  gemini -m gemini-2.5-pro                  # Use Gemini 2.5 Pro")
print(f"\nEndpoint: {host}/serving-endpoints/google")
print("Auth: Bearer token (Databricks PAT)")
