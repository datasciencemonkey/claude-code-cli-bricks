#!/usr/bin/env python
"""Install the Databricks MCP server from ai-dev-kit.

Clones databricks-solutions/ai-dev-kit, creates a venv, and installs
databricks-tools-core + databricks-mcp-server. The MCP server is then
available as a stdio server for Claude Code, OpenCode, Gemini CLI, etc.

Reference: https://github.com/databricks-solutions/ai-dev-kit/tree/main/databricks-mcp-server
"""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

AI_DEV_KIT_DIR = home / ".ai-dev-kit"
REPO_DIR = AI_DEV_KIT_DIR / "repo"
VENV_DIR = AI_DEV_KIT_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "bin" / "python"
RUN_SERVER = REPO_DIR / "databricks-mcp-server" / "run_server.py"

REPO_URL = "https://github.com/databricks-solutions/ai-dev-kit.git"

env = {**os.environ, "HOME": str(home)}


def is_installed():
    """Check if the MCP server is already installed and functional."""
    return VENV_PYTHON.exists() and RUN_SERVER.exists()


if is_installed():
    logger.info(f"Databricks MCP server already installed at {AI_DEV_KIT_DIR}")
else:
    logger.info("Installing Databricks MCP server from ai-dev-kit...")
    AI_DEV_KIT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Clone the repo (sparse checkout — only what we need)
    if not REPO_DIR.exists():
        logger.info(f"  Cloning {REPO_URL}...")
        result = subprocess.run(
            [
                "git", "clone", "--depth=1",
                "--filter=blob:none",
                "--sparse",
                REPO_URL,
                str(REPO_DIR),
            ],
            capture_output=True, text=True, env=env,
        )
        if result.returncode != 0:
            logger.error(f"  git clone failed: {result.stderr}")
            raise SystemExit(1)

        # Only check out the directories we need
        subprocess.run(
            ["git", "sparse-checkout", "set",
             "databricks-tools-core", "databricks-mcp-server"],
            capture_output=True, text=True, cwd=str(REPO_DIR), env=env, check=True,
        )
        logger.info("  Repo cloned (sparse: databricks-tools-core + databricks-mcp-server)")
    else:
        logger.info("  Repo already cloned, pulling latest...")
        subprocess.run(
            ["git", "pull", "--ff-only"],
            capture_output=True, text=True, cwd=str(REPO_DIR), env=env,
        )

    # 2. Create venv
    if not VENV_PYTHON.exists():
        logger.info("  Creating venv...")
        result = subprocess.run(
            ["python3", "-m", "venv", str(VENV_DIR)],
            capture_output=True, text=True, env=env,
        )
        if result.returncode != 0:
            logger.error(f"  venv creation failed: {result.stderr}")
            raise SystemExit(1)

    # 3. Install packages into venv
    logger.info("  Installing databricks-tools-core...")
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-q",
         "-e", str(REPO_DIR / "databricks-tools-core")],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        logger.error(f"  databricks-tools-core install failed: {result.stderr}")
        raise SystemExit(1)

    logger.info("  Installing databricks-mcp-server...")
    result = subprocess.run(
        [str(VENV_PYTHON), "-m", "pip", "install", "-q",
         "-e", str(REPO_DIR / "databricks-mcp-server")],
        capture_output=True, text=True, env=env,
    )
    if result.returncode != 0:
        logger.error(f"  databricks-mcp-server install failed: {result.stderr}")
        raise SystemExit(1)

    logger.info(f"Databricks MCP server installed: {RUN_SERVER}")

# Export paths for other setup scripts to reference
DATABRICKS_MCP_PYTHON = str(VENV_PYTHON)
DATABRICKS_MCP_SERVER_SCRIPT = str(RUN_SERVER)

logger.info(f"  venv python: {DATABRICKS_MCP_PYTHON}")
logger.info(f"  server script: {DATABRICKS_MCP_SERVER_SCRIPT}")
