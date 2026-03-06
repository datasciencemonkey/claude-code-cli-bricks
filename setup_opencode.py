#!/usr/bin/env python
"""Configure OpenCode CLI with native Databricks provider from fork.

Installs from https://github.com/dgokeeffe/opencode (feat/databricks-ai-sdk-provider branch)
which has built-in Databricks model serving support via @databricks/ai-sdk-provider.
The native provider auto-discovers models from serving endpoints and handles auth
through the full Databricks SDK credential chain (PAT, OAuth M2M, CLI, Azure, GCP).
"""
import os
import json
import subprocess
import platform
from pathlib import Path

from utils import ensure_https, resolve_databricks_host_and_token

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

host, token = resolve_databricks_host_and_token()
anthropic_model = os.environ.get("ANTHROPIC_MODEL", "databricks-claude-sonnet-4-6")

if not host or not token:
    print("Error: DATABRICKS_HOST or auth token not available, cannot configure OpenCode")
    raise SystemExit(1)

# Strip trailing slash and ensure https:// prefix
host = ensure_https(host.rstrip("/"))

FORK_REPO = "https://github.com/dgokeeffe/opencode.git"
FORK_BRANCH = "feat/databricks-ai-sdk-provider"

# 1. Install OpenCode CLI from fork
local_bin = home / ".local" / "bin"
local_bin.mkdir(parents=True, exist_ok=True)
opencode_bin = local_bin / "opencode"

if not opencode_bin.exists():
    print("Installing OpenCode CLI from Databricks fork...")
    npm_prefix = str(home / ".local")
    build_dir = home / ".cache" / "opencode-build"
    env = {**os.environ, "HOME": str(home)}

    # Step 1: Install bun via npm
    print("  Installing bun...")
    result = subprocess.run(
        ["npm", "install", "-g", f"--prefix={npm_prefix}", "bun"],
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        print(f"  bun install failed: {result.stderr}")
        raise SystemExit(1)

    bun_bin = local_bin / "bun"
    if not bun_bin.exists():
        # bun might be in a different location
        bun_candidates = list((home / ".local" / "lib").rglob("bun"))
        if bun_candidates:
            bun_bin = bun_candidates[0]
        else:
            print("  Error: bun binary not found after install")
            raise SystemExit(1)
    print(f"  bun installed: {bun_bin}")

    # Step 2: Clone the fork
    print(f"  Cloning {FORK_REPO} ({FORK_BRANCH})...")
    if build_dir.exists():
        subprocess.run(["rm", "-rf", str(build_dir)], check=True)
    result = subprocess.run(
        ["git", "clone", "--depth=1", f"--branch={FORK_BRANCH}", FORK_REPO, str(build_dir)],
        capture_output=True, text=True, env=env
    )
    if result.returncode != 0:
        print(f"  git clone failed: {result.stderr}")
        raise SystemExit(1)

    # Step 3: Install dependencies
    print("  Installing dependencies (bun install)...")
    # Ensure bun's directory is on PATH for child processes
    bun_dir = str(bun_bin.parent)
    install_env = {**env, "PATH": f"{bun_dir}:{env.get('PATH', '')}"}
    result = subprocess.run(
        [str(bun_bin), "install"],
        capture_output=True, text=True,
        cwd=str(build_dir), env=install_env
    )
    if result.returncode != 0:
        print(f"  bun install failed: {result.stderr}")
        raise SystemExit(1)

    # Step 4: Build for current platform only
    print("  Building OpenCode (single platform)...")
    pkg_dir = build_dir / "packages" / "opencode"
    # Ensure bun's directory is on PATH so child processes can find it
    bun_dir = str(bun_bin.parent)
    build_env = {**env, "PATH": f"{bun_dir}:{env.get('PATH', '')}"}
    result = subprocess.run(
        [str(bun_bin), "run", "build", "--", "--single"],
        capture_output=True, text=True,
        cwd=str(pkg_dir), env=build_env,
        timeout=180
    )
    if result.returncode != 0:
        print(f"  Build failed: {result.stderr}")
        print(f"  Build stdout: {result.stdout}")
        raise SystemExit(1)

    # Step 5: Find and copy the built binary
    # Build output: dist/@opencode-ai/script-{os}-{arch}/bin/opencode
    os_name = "linux" if platform.system() == "Linux" else "darwin"
    arch_name = "arm64" if platform.machine() in ("aarch64", "arm64") else "x64"
    dist_dir = pkg_dir / "dist"

    # Find the binary - try exact match first, then glob
    expected_bin = dist_dir / f"@opencode-ai/script-{os_name}-{arch_name}" / "bin" / "opencode"
    if not expected_bin.exists():
        # Try to find any built binary
        candidates = list(dist_dir.rglob("bin/opencode"))
        if candidates:
            expected_bin = candidates[0]
        else:
            print(f"  Error: built binary not found in {dist_dir}")
            print(f"  Contents: {list(dist_dir.iterdir()) if dist_dir.exists() else 'dist dir missing'}")
            raise SystemExit(1)

    # Copy binary to ~/.local/bin
    import shutil
    shutil.copy2(str(expected_bin), str(opencode_bin))
    opencode_bin.chmod(0o755)
    print(f"  OpenCode CLI installed to {opencode_bin}")

    # Clean up build directory to save space
    print("  Cleaning up build directory...")
    subprocess.run(["rm", "-rf", str(build_dir)], check=True)
else:
    print(f"OpenCode CLI already installed at {opencode_bin}")

# 2. Write minimal opencode.json config
# The fork's native Databricks provider auto-discovers models from serving endpoints
# and handles auth via DATABRICKS_TOKEN env var / ~/.databrickscfg / SDK credential chain.
# We just need to enable the provider and set a default model.
opencode_config_dir = home / ".config" / "opencode"
opencode_config_dir.mkdir(parents=True, exist_ok=True)

opencode_config = {
    "$schema": "https://opencode.ai/config.json",
    "enabled_providers": ["databricks"],
    "model": f"databricks/{anthropic_model}"
}

config_path = opencode_config_dir / "opencode.json"
config_path.write_text(json.dumps(opencode_config, indent=2))
print(f"OpenCode configured: {config_path}")
print(f"  Provider: databricks (native, auto-discovers models)")
print(f"  Default model: databricks/{anthropic_model}")

print(f"\nOpenCode ready! Default model: {anthropic_model}")
print("  opencode                          # Start OpenCode TUI")
print("  opencode -m databricks/<model>    # Use a specific model")
print("  (Models auto-discovered from serving endpoints)")
