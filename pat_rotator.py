"""Auto-rotate short-lived PATs in the background.

Mints a new 2-hour PAT every 90 minutes, persists to app secret
(survives restart), writes to ~/.databrickscfg (immediate CLI/SDK use),
and revokes the old PAT. Fixes #81.
"""

import os
import threading
import logging

import requests
from databricks.sdk import WorkspaceClient

from utils import ensure_https

logger = logging.getLogger(__name__)

DEFAULT_TOKEN_LIFETIME = 7200       # 2 hours
DEFAULT_ROTATION_INTERVAL = 120     # 2 minutes


class PATRotator:
    """Background PAT rotation with secret persistence."""

    def __init__(self, host=None, rotation_interval=DEFAULT_ROTATION_INTERVAL,
                 token_lifetime=DEFAULT_TOKEN_LIFETIME,
                 secret_scope=None, secret_key=None):
        self._host = ensure_https(host or os.environ.get("DATABRICKS_HOST", ""))
        self._rotation_interval = rotation_interval
        self._token_lifetime = token_lifetime
        self._secret_scope = secret_scope
        self._secret_key = secret_key
        self._current_token = os.environ.get("DATABRICKS_TOKEN", "").strip() or None
        self._current_token_id = None
        self._lock = threading.Lock()
        self._thread = None
        self._stop_event = threading.Event()
        self._databrickscfg_path = os.path.join(
            os.environ.get("HOME", "/app/python/source_code"),
            ".databrickscfg"
        )

    @property
    def token(self):
        with self._lock:
            return self._current_token

    def start(self):
        """Start the background rotation thread."""
        if not self._current_token:
            logger.warning("PAT rotation: no token configured — rotation disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._rotation_loop, daemon=True,
                                        name="pat-rotation")
        self._thread.start()
        logger.info(f"PAT rotation started (interval={self._rotation_interval}s, "
                    f"lifetime={self._token_lifetime}s)")

    def stop(self):
        """Signal the rotation thread to stop."""
        self._stop_event.set()

    def _rotation_loop(self):
        """Background loop: sleep, rotate, repeat."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._rotation_interval)
            if self._stop_event.is_set():
                break
            try:
                self._rotate_once()
            except Exception as e:
                logger.error(f"PAT rotation failed unexpectedly: {e}")

    def _rotate_once(self):
        """Mint new PAT, persist, revoke old. Returns True on success."""
        if not self._current_token:
            return False

        logger.info("INFO: PAT rotation starting — minting new short-lived token...")

        # 1. Mint new token
        try:
            resp = requests.post(
                f"{self._host}/api/2.0/token/create",
                headers={"Authorization": f"Bearer {self._current_token}"},
                json={
                    "lifetime_seconds": self._token_lifetime,
                    "comment": "coda-auto-rotated"
                },
                timeout=30
            )
        except requests.RequestException as e:
            logger.error(f"PAT rotation: create request failed: {e}")
            return False

        if resp.status_code != 200:
            logger.error(f"PAT rotation: create failed ({resp.status_code}): {resp.text}")
            return False

        data = resp.json()
        new_token = data["token_value"]
        new_token_id = data["token_info"]["token_id"]

        old_token_id = self._current_token_id

        # 2. Persist new token (env + file + secret)
        with self._lock:
            self._current_token = new_token
            self._current_token_id = new_token_id
        self._persist_token(new_token)

        # 3. Revoke old token (best-effort — expires in 2h anyway)
        if old_token_id:
            try:
                resp = requests.post(
                    f"{self._host}/api/2.0/token/delete",
                    headers={"Authorization": f"Bearer {new_token}"},
                    json={"token_id": old_token_id},
                    timeout=30
                )
                if resp.status_code == 200:
                    logger.info(f"INFO: PAT rotation complete — new token (id={new_token_id}, "
                                f"expires in {self._token_lifetime}s). "
                                f"Old token ELIMINATED (id={old_token_id}).")
                else:
                    logger.warning(f"INFO: PAT rotation complete — new token active (id={new_token_id}), "
                                   f"but old token revocation failed ({resp.status_code}). "
                                   f"Old token (id={old_token_id}) will expire naturally in {self._token_lifetime}s.")
            except requests.RequestException as e:
                logger.warning(f"INFO: PAT rotation complete — new token active (id={new_token_id}), "
                               f"old token revocation request failed: {e}. "
                               f"Old token (id={old_token_id}) will expire naturally in {self._token_lifetime}s.")
        else:
            logger.info(f"INFO: PAT rotation complete — new token (id={new_token_id}, "
                        f"expires in {self._token_lifetime}s). First rotation — no old token to revoke.")

        return True

    def _persist_token(self, token):
        """Write rotated token to all persistence layers."""
        os.environ["DATABRICKS_TOKEN"] = token
        self._write_databrickscfg(token)
        self._persist_to_secret(token)
        logger.info("PAT persisted: env var + ~/.databrickscfg updated")

    def _write_databrickscfg(self, token):
        """Write token to ~/.databrickscfg for CLI/SDK tools."""
        content = (
            "[DEFAULT]\n"
            f"host = {self._host}\n"
            f"token = {token}\n"
        )
        try:
            with open(self._databrickscfg_path, "w") as f:
                f.write(content)
            os.chmod(self._databrickscfg_path, 0o600)
        except OSError as e:
            logger.warning(f"Could not write .databrickscfg: {e}")

    def _persist_to_secret(self, token):
        """Persist token to Databricks app secret (survives restart)."""
        if not self._secret_scope or not self._secret_key:
            return
        try:
            w = WorkspaceClient()
            w.secrets.put_secret(scope=self._secret_scope, key=self._secret_key,
                                string_value=token)
            logger.info(f"PAT persisted to app secret ({self._secret_scope}/{self._secret_key})")
        except Exception as e:
            logger.warning(f"Could not persist PAT to secret: {e}")
