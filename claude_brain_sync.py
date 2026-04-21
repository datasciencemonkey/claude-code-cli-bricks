#!/usr/bin/env python
"""Sync Claude Code's auto-memory ("brain") to/from Databricks Workspace.

The "brain" is the set of memory files Claude Code maintains at
`~/.claude/projects/{slug}/memory/`, one slug per working directory.
They accumulate user/project/feedback/reference memories that make
future sessions smarter.

Ephemeral Databricks App compute means these files vanish when the
app restarts unless we persist them. This script syncs them to the
user's workspace so they survive redeploys and restarts.

Usage:
    python claude_brain_sync.py push                      # local -> workspace ([DEFAULT])
    python claude_brain_sync.py pull                      # workspace -> local ([DEFAULT])
    python claude_brain_sync.py push --profile daveok     # use a named profile
    python claude_brain_sync.py                           # push (default)
"""
from __future__ import annotations

import argparse
import configparser
import os
import subprocess
import sys
from pathlib import Path

try:
    from databricks.sdk import WorkspaceClient
except ImportError:
    print("databricks-sdk not available, skipping brain sync", file=sys.stderr)
    sys.exit(0)


CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
WORKSPACE_SUBPATH = ".coda/claude-brain/projects"


def _read_databrickscfg(profile: str = "DEFAULT") -> tuple[str | None, str | None]:
    cfg = Path.home() / ".databrickscfg"
    if not cfg.exists():
        return None, None
    p = configparser.ConfigParser()
    p.read(cfg)
    if profile not in p and profile != "DEFAULT":
        return None, None
    return (
        p.get(profile, "host", fallback=None),
        p.get(profile, "token", fallback=None),
    )


def _workspace_client(profile: str | None) -> WorkspaceClient:
    """Build a WorkspaceClient. Named profiles delegate auth to the SDK
    so OAuth (`auth_type = databricks-cli`) works for local testing;
    the default path reads [DEFAULT] explicitly for the production PAT flow."""
    if profile:
        return WorkspaceClient(profile=profile)
    host, token = _read_databrickscfg("DEFAULT")
    if not host or not token:
        raise RuntimeError("~/.databrickscfg [DEFAULT] missing host or token")
    return WorkspaceClient(host=host, token=token, auth_type="pat")


def _user_email(profile: str | None) -> str:
    return _workspace_client(profile).current_user.me().user_name


def _sync_env() -> dict[str, str]:
    """Env for databricks CLI. Strip OAuth M2M vars so CLI falls through to
    the profile config. Profile selection is passed via --profile CLI flag."""
    env = os.environ.copy()
    for var in ("DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET",
                "DATABRICKS_HOST", "DATABRICKS_TOKEN"):
        env.pop(var, None)
    return env


def _profile_args(profile: str | None) -> list[str]:
    return ["--profile", profile] if profile else []


def _memory_dirs() -> list[Path]:
    """Return memory dirs that actually contain files worth syncing."""
    if not CLAUDE_PROJECTS.exists():
        return []
    dirs = []
    for project_dir in CLAUDE_PROJECTS.iterdir():
        if not project_dir.is_dir():
            continue
        memory = project_dir / "memory"
        if memory.exists() and any(memory.iterdir()):
            dirs.append(memory)
    return dirs


def push(profile: str | None = None) -> int:
    """Push each project's memory dir to workspace."""
    dirs = _memory_dirs()
    if not dirs:
        print("brain-sync: no memory dirs to push")
        return 0

    try:
        email = _user_email(profile)
    except Exception as e:
        print(f"brain-sync: could not resolve user email: {e}", file=sys.stderr)
        return 1

    env = _sync_env()
    profile_flags = _profile_args(profile)
    failures = 0
    for memory_dir in dirs:
        project_slug = memory_dir.parent.name
        remote = f"/Workspace/Users/{email}/{WORKSPACE_SUBPATH}/{project_slug}/memory"
        result = subprocess.run(
            ["databricks", "sync", str(memory_dir), remote, "--watch=false"] + profile_flags,
            capture_output=True, text=True, env=env,
        )
        if result.returncode == 0:
            print(f"brain-sync push: {project_slug}")
        else:
            print(f"brain-sync push FAILED for {project_slug}: {result.stderr.strip()}",
                  file=sys.stderr)
            failures += 1
    return 0 if failures == 0 else 1


def pull(profile: str | None = None) -> int:
    """Pull brain from workspace into ~/.claude/projects/.

    Uses databricks workspace export-dir because `databricks sync` is
    local->remote only.
    """
    try:
        email = _user_email(profile)
    except Exception as e:
        print(f"brain-sync: could not resolve user email: {e}", file=sys.stderr)
        return 1

    env = _sync_env()
    profile_flags = _profile_args(profile)
    remote_root = f"/Workspace/Users/{email}/{WORKSPACE_SUBPATH}"

    check = subprocess.run(
        ["databricks", "workspace", "list", remote_root] + profile_flags,
        capture_output=True, text=True, env=env,
    )
    if check.returncode != 0:
        print(f"brain-sync pull: no remote brain yet at {remote_root}")
        return 0

    CLAUDE_PROJECTS.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["databricks", "workspace", "export-dir",
         remote_root, str(CLAUDE_PROJECTS), "--overwrite"] + profile_flags,
        capture_output=True, text=True, env=env,
    )
    if result.returncode == 0:
        print(f"brain-sync pull: restored from {remote_root}")
        return 0
    print(f"brain-sync pull FAILED: {result.stderr.strip()}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("direction", nargs="?", default="push", choices=["push", "pull"])
    parser.add_argument("--profile", help="databricks CLI profile name (default: [DEFAULT])")
    args = parser.parse_args()
    if args.direction == "push":
        return push(args.profile)
    return pull(args.profile)


if __name__ == "__main__":
    sys.exit(main())
