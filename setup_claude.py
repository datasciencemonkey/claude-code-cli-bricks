import logging
import os
import json
import subprocess
from pathlib import Path

from utils import ensure_https, resolve_databricks_host_and_token

logger = logging.getLogger(__name__)

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

# Create ~/.claude directory
claude_dir = home / ".claude"
claude_dir.mkdir(exist_ok=True)

# 1. Write settings.json for Databricks model serving
# Use DATABRICKS_GATEWAY_HOST if available (new AI Gateway), otherwise fall back to DATABRICKS_HOST
gateway_host = ensure_https(os.environ.get("DATABRICKS_GATEWAY_HOST", "").rstrip("/"))
databricks_host, auth_token = resolve_databricks_host_and_token()

if gateway_host and not auth_token:
    logger.warning(
        "DATABRICKS_GATEWAY_HOST set but token unavailable, falling back to DATABRICKS_HOST"
    )
    gateway_host = ""

if gateway_host:
    anthropic_base_url = f"{gateway_host}/anthropic"
    logger.info(f"Using Databricks AI Gateway: {gateway_host}")
else:
    if not databricks_host or not auth_token:
        logger.error("could not resolve Databricks host/token for Claude setup")
        raise SystemExit(1)
    anthropic_base_url = f"{databricks_host}/serving-endpoints/anthropic"
    logger.info(f"Using Databricks Host: {databricks_host}")

settings = {
    "env": {
        "ANTHROPIC_MODEL": os.environ.get(
            "ANTHROPIC_MODEL", "databricks-claude-sonnet-4-6"
        ),
        "ANTHROPIC_BASE_URL": anthropic_base_url,
        "ANTHROPIC_AUTH_TOKEN": auth_token,
        "ANTHROPIC_CUSTOM_HEADERS": "x-databricks-use-coding-agent-mode: true",
    }
}

settings_path = claude_dir / "settings.json"
settings_path.write_text(json.dumps(settings, indent=2))

# 2. Write ~/.claude.json with onboarding skip AND MCP servers
claude_json = {
    "hasCompletedOnboarding": True,
    "mcpServers": {
        "deepwiki": {"type": "http", "url": "https://mcp.deepwiki.com/mcp"},
        "exa": {"type": "http", "url": "https://mcp.exa.ai/mcp"},
    },
}

claude_json_path = home / ".claude.json"
claude_json_path.write_text(json.dumps(claude_json, indent=2))

logger.info(f"Claude configured: {settings_path}")
logger.info(f"Onboarding skipped + MCPs configured: {claude_json_path}")

# 3. Install Claude Code CLI if not present
local_bin = home / ".local" / "bin"
claude_bin = local_bin / "claude"

if not claude_bin.exists():
    logger.info("Installing Claude Code CLI...")
    install_script = "/tmp/claude_install.sh"
    # Download install script first (don't pipe directly to bash)
    dl_result = subprocess.run(
        ["curl", "-fsSL", "-o", install_script, "https://claude.ai/install.sh"],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )
    if dl_result.returncode != 0:
        logger.error(f"Failed to download install script: {dl_result.stderr}")
        raise SystemExit(1)
    # Verify the download is a shell script (basic sanity check)
    with open(install_script, "r") as f:
        first_line = f.readline()
    if not first_line.startswith("#!"):
        logger.error(
            f"Downloaded file doesn't look like a shell script: {first_line[:50]}"
        )
        os.remove(install_script)
        raise SystemExit(1)
    # Execute the verified script
    result = subprocess.run(
        ["bash", install_script],
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )
    os.remove(install_script)
    if result.returncode == 0:
        logger.info("Claude Code CLI installed successfully")
    else:
        logger.error(f"CLI install failed: {result.stderr}")
        raise SystemExit(1)
else:
    logger.info(f"Claude Code CLI already installed at {claude_bin}")

# 4. Create projects directory
projects_dir = home / "projects"
projects_dir.mkdir(exist_ok=True)
logger.info(f"Projects directory: {projects_dir}")

# 5. Git identity and hooks are now configured by app.py's _setup_git_config()
# (runs directly in Python before setup_claude.py, writes ~/.gitconfig and ~/.githooks/)
logger.info("Git identity and hooks: configured by app.py (skipping here)")
