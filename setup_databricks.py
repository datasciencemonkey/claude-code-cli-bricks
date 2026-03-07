#!/usr/bin/env python
"""Configure Databricks CLI with the user's PAT from environment."""

import logging
import os
import subprocess
from pathlib import Path

from utils import resolve_databricks_host_and_token

logger = logging.getLogger(__name__)

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

# Get credentials from environment or SDK auto-auth fallback
host, token = resolve_databricks_host_and_token()

if not host or not token:
    logger.error(
        "DATABRICKS_HOST or auth token not available, cannot configure Databricks CLI"
    )
    raise SystemExit(1)

# Create ~/.databrickscfg with DEFAULT profile using PAT auth
databrickscfg = home / ".databrickscfg"
config_content = f"""[DEFAULT]
host = {host}
token = {token}
"""

databrickscfg.write_text(config_content)
databrickscfg.chmod(0o600)  # Restrict permissions
logger.info(f"Databricks CLI configured: {databrickscfg}")

# Verify it works
result = subprocess.run(
    ["databricks", "current-user", "me", "--output", "json"],
    capture_output=True,
    text=True,
)

if result.returncode == 0:
    import json

    try:
        user = json.loads(result.stdout)
        email = user.get("userName", "")
        display_name = user.get("displayName", "")
        logger.info(f"Databricks CLI authenticated as: {email}")

        # Configure git with user's email and name
        if email:
            subprocess.run(
                ["git", "config", "--global", "user.email", email], check=False
            )
            logger.info(f"Git configured with email: {email}")
        if display_name:
            subprocess.run(
                ["git", "config", "--global", "user.name", display_name], check=False
            )
            logger.info(f"Git configured with name: {display_name}")
        elif email:
            # Fall back to email prefix as name if no display name
            name_from_email = email.split("@")[0].replace(".", " ").title()
            subprocess.run(
                ["git", "config", "--global", "user.name", name_from_email], check=False
            )
            logger.info(f"Git configured with name: {name_from_email}")
    except json.JSONDecodeError:
        logger.info("Databricks CLI configured (couldn't parse user)")
else:
    logger.warning(f"CLI config may have issues: {result.stderr}")
