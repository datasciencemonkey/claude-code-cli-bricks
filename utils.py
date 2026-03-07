"""Shared utilities for Databricks App setup scripts."""

import enum
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def adapt_instructions_file(
    source_path: Path,
    target_path: Path,
    new_header: str,
    cli_name: str,
) -> bool:
    """Read a CLAUDE.md file and adapt it for another CLI's instructions format.

    Reads the source instructions file (typically CLAUDE.md), replaces the first
    header line with a CLI-specific header, and writes to the target location.

    Args:
        source_path: Path to the source instructions file (e.g., CLAUDE.md)
        target_path: Path to write the adapted instructions file
        new_header: The new header line (e.g., "# Codex Agent Instructions")
        cli_name: Name of the CLI for logging (e.g., "Codex", "Gemini")

    Returns:
        True if successful, False if source file not found
    """
    if not source_path.exists():
        logger.warning(f"{source_path} not found, skipping {cli_name} instructions")
        return False

    content = source_path.read_text()

    # Replace the first markdown header (# ...) with the new header
    # This handles "# Claude Code on Databricks" -> "# Codex Agent Instructions"
    adapted_content = re.sub(
        r"^#\s+.*$", new_header, content, count=1, flags=re.MULTILINE
    )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(adapted_content)
    logger.info(f"{cli_name} instructions configured: {target_path}")
    return True


def ensure_https(url: str) -> str:
    """Ensure a URL has the https:// prefix.

    Databricks Apps may inject DATABRICKS_HOST without the protocol prefix,
    which causes URL parsing errors downstream.

    Args:
        url: A URL that may or may not have a protocol prefix

    Returns:
        The URL with https:// prefix (or unchanged if already has http(s)://)
    """
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        return f"https://{url}"
    return url


class AuthMode(enum.Enum):
    """How the app authenticates with Databricks."""

    PAT = "pat"
    OAUTH_M2M = "oauth_m2m"


@dataclass
class AuthState:
    """Resolved authentication state."""

    mode: AuthMode
    host: str
    token: str
    # Only populated for OAUTH_M2M
    client_id: Optional[str] = None
    client_secret: Optional[str] = None


def resolve_auth() -> AuthState:
    """Resolve Databricks authentication - PAT first, OAuth M2M fallback.

    Priority:
    1) DATABRICKS_TOKEN set -> PAT mode (existing behavior)
    2) DATABRICKS_CLIENT_ID + DATABRICKS_CLIENT_SECRET set -> OAuth M2M
    3) SDK auto-detect (WorkspaceClient.config.authenticate())

    Returns:
        AuthState with mode, host, and token.
    """
    host = ensure_https(os.environ.get("DATABRICKS_HOST", "").strip())
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()

    # 1. PAT mode - explicit token
    if host and token:
        logger.info("Auth mode: PAT (explicit DATABRICKS_TOKEN)")
        return AuthState(mode=AuthMode.PAT, host=host, token=token)

    # 2. OAuth M2M - auto-provisioned SP credentials
    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "").strip()
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "").strip()

    if host and client_id and client_secret:
        logger.info("Auth mode: OAuth M2M (service principal credentials)")
        oauth_token = _generate_oauth_token(host, client_id, client_secret)
        return AuthState(
            mode=AuthMode.OAUTH_M2M,
            host=host,
            token=oauth_token,
            client_id=client_id,
            client_secret=client_secret,
        )

    # 3. SDK auto-detect fallback
    try:
        from databricks.sdk import WorkspaceClient

        client = WorkspaceClient()
        if not host:
            host = ensure_https((client.config.host or "").strip())

        auth_headers = client.config.authenticate() or {}
        authorization = auth_headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization.replace("Bearer ", "", 1).strip()
        elif not token:
            token = (getattr(client.config, "token", "") or "").strip()

        if host and token:
            logger.info("Auth mode: SDK auto-detect")
            return AuthState(mode=AuthMode.PAT, host=host, token=token)
    except Exception as e:
        logger.warning(f"SDK auto-detect failed: {e}")

    # Return whatever we have (may be incomplete)
    logger.warning("Auth: could not fully resolve credentials")
    return AuthState(mode=AuthMode.PAT, host=host, token=token)


def _generate_oauth_token(host: str, client_id: str, client_secret: str) -> str:
    """Generate an OAuth Bearer token using SP credentials.

    Uses WorkspaceClient.config.authenticate() which handles the OAuth token
    exchange with Databricks' OIDC endpoint.
    """
    from databricks.sdk import WorkspaceClient

    client = WorkspaceClient(
        host=host,
        client_id=client_id,
        client_secret=client_secret,
    )
    auth_headers = client.config.authenticate()
    authorization = auth_headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization.replace("Bearer ", "", 1).strip()
    raise RuntimeError("OAuth M2M token exchange did not return a Bearer token")


class TokenRefresher:
    """Background thread that refreshes OAuth tokens and updates config files.

    Only active in OAUTH_M2M mode. Refreshes every `interval` seconds and
    updates all agent config files with the new token.
    """

    def __init__(self, auth: AuthState, interval: int = 1800):
        self._auth = auth
        self._interval = interval
        self._lock = threading.Lock()
        self._current_token = auth.token
        self._thread: Optional[threading.Thread] = None

    @property
    def current_token(self) -> str:
        with self._lock:
            return self._current_token

    def start(self):
        if self._auth.mode != AuthMode.OAUTH_M2M:
            logger.info("TokenRefresher: PAT mode, no refresh needed")
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="token-refresher"
        )
        self._thread.start()
        logger.info(f"TokenRefresher: started (interval={self._interval}s)")

    def _run(self):
        while True:
            time.sleep(self._interval)
            try:
                old_token = self.current_token
                new_token = _generate_oauth_token(
                    self._auth.host,
                    self._auth.client_id,
                    self._auth.client_secret,
                )
                with self._lock:
                    self._current_token = new_token

                # Update DATABRICKS_TOKEN env var so new subprocesses pick it up
                os.environ["DATABRICKS_TOKEN"] = new_token

                # Update all config files that contain the old token
                _update_all_token_files(old_token, new_token)
                logger.info("TokenRefresher: token refreshed and config files updated")
            except Exception as e:
                logger.error(f"TokenRefresher: refresh failed: {e}")


def _update_all_token_files(old_token: str, new_token: str):
    """Replace old_token with new_token in all agent config files."""
    if old_token == new_token or not old_token or not new_token:
        return

    home = Path(os.environ.get("HOME", "/app/python/source_code"))

    config_files = [
        home / ".claude" / "settings.json",  # ANTHROPIC_AUTH_TOKEN
        home / ".gemini" / ".env",  # GEMINI_API_KEY
        home / ".codex" / ".env",  # OPENAI_API_KEY
        home / ".local" / "share" / "opencode" / "auth.json",  # api_key
        home / ".databrickscfg",  # token
    ]

    for path in config_files:
        if not path.exists():
            continue
        try:
            content = path.read_text()
            if old_token in content:
                path.write_text(content.replace(old_token, new_token))
                path.chmod(0o600)
                logger.debug(f"TokenRefresher: updated {path}")
        except Exception as e:
            logger.warning(f"TokenRefresher: failed to update {path}: {e}")


def resolve_databricks_host_and_token() -> tuple[str, str]:
    """Resolve Databricks host + auth token for setup scripts.

    Backward-compatible wrapper around resolve_auth().

    Returns:
        (host, token) where each value may be an empty string if unresolved.
    """
    auth = resolve_auth()
    return auth.host, auth.token
