import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

from utils import ensure_https, get_gateway_host

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])

# Create ~/.claude directory
claude_dir = home / ".claude"
claude_dir.mkdir(exist_ok=True)

# The coda-marketplace bundled with the CODA source is registered via
# extraKnownMarketplaces in settings.json below. Claude Code auto-discovers
# agents/ and commands/ inside enabled plugins, so we only need to:
#   1. ensure hook scripts are executable (git doesn't preserve +x reliably)
#   2. know the hooks/ path so we can wire hooks into settings.json
marketplace_dir = Path(__file__).parent / "coda-marketplace"
plugin_dir = marketplace_dir / "plugins" / "coda-essentials"
hooks_dir = plugin_dir / "hooks"
if hooks_dir.exists():
    for hook in hooks_dir.iterdir():
        if hook.is_file():
            os.chmod(hook, 0o755)
    print(f"coda-essentials hooks ready: {hooks_dir}")

# Register the bundled marketplace with Claude Code's plugin system. Just
# listing the marketplace in settings.json's extraKnownMarketplaces and the
# plugin in enabledPlugins is NOT enough — Claude Code also requires state
# files under ~/.claude/plugins/ (known_marketplaces.json + installed_plugins.json)
# before plugin content (skills, commands, agents, hooks) is actually loaded.
# For a directory-source marketplace the "installLocation" is the source path,
# no copy needed. We write these here so fresh CODA instances get /cache-stats,
# /til, and the marketplace skills available on first Claude Code session.
import datetime as _dt
plugins_state_dir = home / ".claude" / "plugins"
plugins_state_dir.mkdir(exist_ok=True)
cache_root = plugins_state_dir / "cache" / "coda"
cache_root.mkdir(parents=True, exist_ok=True)

# Stage each plugin into ~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/.
# Claude Code requires this layout — even directory-source marketplaces get
# their plugins copied into a versioned cache path, and `installPath` in
# installed_plugins.json must point at the cache, not at the source.
# Verified by inspecting a working fe-vibe install where the marketplace
# source lives at ~/Repos/vibe-ebc-fix but plugin installPath is
# ~/.claude/plugins/cache/fe-vibe/fe-html-slides/1.1.4.
PLUGIN_VERSION = "0.1.0"
plugin_cache_paths = {}
for pname in ("coda-essentials", "coda-databricks-skills"):
    src_p = marketplace_dir / "plugins" / pname
    dst_p = cache_root / pname / PLUGIN_VERSION
    if dst_p.exists():
        shutil.rmtree(dst_p)
    shutil.copytree(src_p, dst_p)
    plugin_cache_paths[pname] = dst_p
    print(f"Staged plugin {pname} -> {dst_p}")

# Re-point hooks_dir at the cached coda-essentials so settings.json hooks
# reference the copy Claude Code actually loads, not the source tree.
# (Source and cache have identical contents; this keeps the hook path
# consistent with the plugin loader's view of the filesystem.)
hooks_dir = plugin_cache_paths["coda-essentials"] / "hooks"
if hooks_dir.exists():
    for hook in hooks_dir.iterdir():
        if hook.is_file():
            os.chmod(hook, 0o755)

_now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

(plugins_state_dir / "known_marketplaces.json").write_text(json.dumps({
    "coda": {
        "source": {"source": "directory", "path": str(marketplace_dir)},
        "installLocation": str(marketplace_dir),
        "lastUpdated": _now,
    }
}, indent=2))

(plugins_state_dir / "installed_plugins.json").write_text(json.dumps({
    "version": 2,
    "plugins": {
        "coda-essentials@coda": [{
            "scope": "user",
            "installPath": str(plugin_cache_paths["coda-essentials"]),
            "version": PLUGIN_VERSION,
            "installedAt": _now,
            "lastUpdated": _now,
        }],
        "coda-databricks-skills@coda": [{
            "scope": "user",
            "installPath": str(plugin_cache_paths["coda-databricks-skills"]),
            "version": PLUGIN_VERSION,
            "installedAt": _now,
            "lastUpdated": _now,
        }],
    },
}, indent=2))
print(f"Registered coda marketplace + plugins in {plugins_state_dir}")

# Defence-in-depth: also copy commands/agents into ~/.claude/commands/
# and ~/.claude/agents/ at the user level. Claude Code's plugin loader
# on the Databricks Apps runtime didn't surface plugin-bundled commands
# on first attempt; user-level paths are the canonical fallback and
# are always scanned regardless of plugin state. Running both keeps the
# marketplace as the source of truth for content while guaranteeing the
# slash commands + subagents actually work.
user_commands_dir = claude_dir / "commands"
user_commands_dir.mkdir(exist_ok=True)
user_agents_dir = claude_dir / "agents"
user_agents_dir.mkdir(exist_ok=True)

for src_commands in [plugin_cache_paths["coda-essentials"] / "commands"]:
    if src_commands.exists():
        for f in src_commands.glob("*.md"):
            shutil.copy2(str(f), str(user_commands_dir / f.name))
print(f"User-level commands synced: {sorted(p.name for p in user_commands_dir.glob('*.md'))}")

for src_agents in [plugin_cache_paths["coda-essentials"] / "agents"]:
    if src_agents.exists():
        for f in src_agents.glob("*.md"):
            shutil.copy2(str(f), str(user_agents_dir / f.name))
print(f"User-level agents synced: {sorted(p.name for p in user_agents_dir.glob('*.md'))}")

# 1. Write settings.json for Databricks model serving (requires DATABRICKS_TOKEN)
token = os.environ.get("DATABRICKS_TOKEN", "").strip()
if token:
    gateway_host = get_gateway_host()
    databricks_host = ensure_https(os.environ.get("DATABRICKS_HOST", "").rstrip("/"))

    if gateway_host:
        anthropic_base_url = f"{gateway_host}/anthropic"
        print(f"Using Databricks AI Gateway: {gateway_host}")
    else:
        anthropic_base_url = f"{databricks_host}/serving-endpoints/anthropic"
        print(f"Using Databricks Host: {databricks_host}")

    settings = {
        "theme": "dark",
        "outputStyle": "Explanatory",
        "extraKnownMarketplaces": {
            "coda": {
                "source": {
                    "source": "directory",
                    "path": str(marketplace_dir),
                },
            },
        },
        "enabledPlugins": {
            "coda-essentials@coda": True,
            "coda-databricks-skills@coda": True,
        },
        "permissions": {
            "defaultMode": "auto",
            "allow": [
                "Bash(databricks *)",
                "Bash(uv *)",
                "Bash(git *)",
                "Bash(make *)",
                "Bash(python *)",
                "Bash(pytest *)",
                "Bash(ruff *)",
                "Bash(wsync)",
                "Bash(databricks sync * /Workspace/Shared/apps/coding-agents*)",
                "Bash(databricks workspace import /Workspace/Shared/apps/coding-agents/*)",
                "Bash(databricks workspace import-dir * /Workspace/Shared/apps/coding-agents*)",
            ],
            "deny": [
                # Process kills that would take down the gunicorn worker (single-worker app)
                "Bash(pkill *)",
                "Bash(pkill)",
                "Bash(killall *)",
                "Bash(fuser -k *)",
                "Bash(kill 1)",
                "Bash(kill -9 1)",
                "Bash(kill -- -1)",
                # Catastrophic filesystem deletion (would wipe app source / home)
                "Bash(rm -rf /)",
                "Bash(rm -rf /*)",
                "Bash(rm -rf /app*)",
                "Bash(rm -rf ~)",
                "Bash(rm -rf ~/*)",
                "Bash(rm -rf $HOME)",
                "Bash(rm -rf $HOME/*)",
                # Credential/config destruction (breaks auth + PAT rotator)
                "Bash(rm ~/.databrickscfg*)",
                "Bash(rm -rf ~/.claude*)",
                # Shared Workspace paths that other apps depend on
                "Bash(rm -rf /Workspace*)",
                "Bash(databricks workspace delete /Workspace/Shared*)",
                "Bash(databricks workspace delete-dir /Workspace/Shared*)",
                # Don't delete other users' coda apps
                "Bash(databricks apps delete *)",
                # System-level destructive
                "Bash(shutdown *)",
                "Bash(reboot *)",
                "Bash(halt *)",
                "Bash(mkfs *)",
                "Bash(dd if=* of=/dev/*)",
                "Bash(chmod -R * /app*)",
                "Bash(chown -R * /app*)",
            ],
        },
        "env": {
            "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "databricks-claude-opus-4-7"),
            "ANTHROPIC_BASE_URL": anthropic_base_url,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "databricks-claude-opus-4-7",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "databricks-claude-sonnet-4-6",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": "databricks-claude-haiku-4-5",
            "ANTHROPIC_CUSTOM_HEADERS": "x-databricks-use-coding-agent-mode: true",
            "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        },
        "hooks": {
            "SessionStart": [{
                "matcher": "",
                "hooks": [
                    {"type": "command",
                     "command": f"python3 {hooks_dir}/check-memory-staleness.py --cwd \"$PWD\"",
                     "timeout": 10},
                    {"type": "command",
                     "command": f"bash {hooks_dir}/session-context-loader.sh",
                     "timeout": 15},
                ],
            }],
            "PostToolUse": [{
                "matcher": "Edit|Write",
                "hooks": [{
                    "type": "command",
                    "command": f"bash {hooks_dir}/memory-stamp-verified.sh",
                    "timeout": 5,
                }],
            }],
            "Stop": [{
                "matcher": "",
                "hooks": [
                    {"type": "command",
                     "command": f"bash {hooks_dir}/session-crystallize-nudge.sh",
                     "timeout": 10},
                    {"type": "command",
                     "command": f"bash {hooks_dir}/push-brain-to-workspace.sh",
                     "timeout": 5},
                ],
            }],
        },
    }

    settings_path = claude_dir / "settings.json"
    settings_path.write_text(json.dumps(settings, indent=2))
    print(f"Claude configured: {settings_path}")

    # 1b. Secure-egress network detection. If docs.databricks.com is blocked
    # (common in enterprise Azure workspaces with a restrictive outbound
    # allowlist), append a note to ~/.claude/CLAUDE.md telling agents to
    # substitute learn.microsoft.com/en-us/azure/databricks/ — Microsoft
    # Learn mirrors the Azure Databricks docs one-to-one and is usually
    # allowlisted by default. Idempotent via a marker comment.
    import urllib.request  # stdlib, no extra deps
    _egress_marker = "<!-- coda-egress-fallback -->"
    _egress_note = (
        f"\n{_egress_marker}\n"
        "## Documentation fallback — secure-egress workspace\n"
        "`docs.databricks.com` is blocked from this environment. "
        "When looking up Databricks docs, rewrite URLs:\n"
        "- `docs.databricks.com/azure/en/X` → `learn.microsoft.com/en-us/azure/databricks/X`\n"
        "- `docs.databricks.com/aws/en/X`   → `learn.microsoft.com/en-us/azure/databricks/X`\n"
        "Microsoft Learn mirrors the Azure Databricks docs one-to-one and is usually reachable.\n"
    )
    try:
        urllib.request.urlopen("https://docs.databricks.com/", timeout=3)
        print("docs.databricks.com reachable — no egress fallback needed")
    except Exception as _e:
        print(f"docs.databricks.com unreachable ({type(_e).__name__}) — installing learn.microsoft.com fallback note")
        _claude_md = claude_dir / "CLAUDE.md"
        _existing = _claude_md.read_text() if _claude_md.exists() else ""
        if _egress_marker not in _existing:
            with open(_claude_md, "a") as _f:
                _f.write(_egress_note)
            print(f"Appended egress fallback note to {_claude_md}")
else:
    print("No DATABRICKS_TOKEN — skipping settings.json (will be configured after PAT setup)")

# 2. Write ~/.claude.json with onboarding skip AND MCP servers
mcp_servers = {}

# Auto-configure team-memory MCP if URL is provided
team_memory_url = os.environ.get("TEAM_MEMORY_MCP_URL", "").strip().rstrip("/")
if team_memory_url:
    mcp_servers["team-memory"] = {
        "type": "http",
        "url": f"{team_memory_url}/mcp"
    }
    print(f"Team memory MCP configured: {team_memory_url}/mcp")

# Public-internet MCPs (deepwiki, exa) are opt-in: they live on the open
# internet and won't work in air-gapped or secure-egress deployments. Set
# ENABLE_PUBLIC_MCPS=true only when you know the runtime can reach them.
if os.environ.get("ENABLE_PUBLIC_MCPS", "").strip().lower() in ("1", "true", "yes"):
    mcp_servers["deepwiki"] = {"type": "http", "url": "https://mcp.deepwiki.com/mcp"}
    mcp_servers["exa"] = {"type": "http", "url": "https://mcp.exa.ai/mcp"}
    print("Public MCPs enabled (ENABLE_PUBLIC_MCPS=true): deepwiki, exa")

claude_json = {
    "hasCompletedOnboarding": True,
    "mcpServers": mcp_servers
}

claude_json_path = home / ".claude.json"
claude_json_path.write_text(json.dumps(claude_json, indent=2))

print(f"Onboarding skipped + MCPs configured: {claude_json_path}")

# 3. Install Claude Code CLI if not present
local_bin = home / ".local" / "bin"
claude_bin = local_bin / "claude"

print("Installing/upgrading Claude Code CLI...")
result = subprocess.run(
    ["bash", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
    env={**os.environ, "HOME": str(home)},
    capture_output=True,
    text=True
)
if result.returncode == 0:
    print("Claude Code CLI installed successfully")
else:
    print(f"CLI install warning: {result.stderr}")

# 4. Subagents are discovered automatically from coda-essentials plugin
# (no manual copy step needed — the plugin's agents/ dir is scanned by Claude Code).

# 5. Create projects directory
projects_dir = home / "projects"
projects_dir.mkdir(exist_ok=True)
print(f"Projects directory: {projects_dir}")

# 5. Git identity and hooks are now configured by app.py's _setup_git_config()
# (runs directly in Python before setup_claude.py, writes ~/.gitconfig and ~/.githooks/)
print("Git identity and hooks: configured by app.py (skipping here)")

# 6. Restore Claude Code auto-memory ("brain") from workspace if present.
# This makes accumulated memories survive app redeployment. Best-effort —
# failures are logged but don't break startup.
if token:
    brain_sync = Path(__file__).parent / "claude_brain_sync.py"
    if brain_sync.exists():
        try:
            result = subprocess.run(
                [sys.executable, str(brain_sync), "pull"],
                capture_output=True, text=True, timeout=60,
                env={**os.environ, "HOME": str(home)},
            )
            if result.stdout:
                print(result.stdout.strip())
            if result.returncode != 0 and result.stderr:
                print(f"brain-sync pull warning: {result.stderr.strip()}")
        except Exception as e:
            print(f"brain-sync pull skipped: {e}")
