"""Configure MLflow tracing for Claude Code sessions.

Merges MLflow env vars into ~/.claude/settings.json. Tracing is disabled
by default — the Stop hook stalls on transcript processing. Set
MLFLOW_CLAUDE_TRACING_ENABLED=true to opt in once the hook is reliable.
"""

import os
import json
from pathlib import Path

# Set HOME if not properly set
if not os.environ.get("HOME") or os.environ["HOME"] == "/":
    os.environ["HOME"] = "/app/python/source_code"

home = Path(os.environ["HOME"])
settings_path = home / ".claude" / "settings.json"

# Read existing settings (written by setup_claude.py)
if settings_path.exists():
    settings = json.loads(settings_path.read_text())
else:
    settings = {}

app_owner = os.environ.get("APP_OWNER", "")
app_name = os.environ.get("DATABRICKS_APP_NAME", "coding-agents")

if not app_owner:
    print("MLflow tracing skipped: APP_OWNER not set")
    raise SystemExit(0)

experiment_name = f"/Users/{app_owner}/{app_name}"

# Tracing disabled by default — stop hook stalls on transcript processing
tracing_enabled = os.environ.get("MLFLOW_CLAUDE_TRACING_ENABLED", "false").lower() == "true"

# Merge MLflow env vars (always set so tracing can be toggled at runtime)
settings.setdefault("env", {})
settings["env"]["MLFLOW_CLAUDE_TRACING_ENABLED"] = str(tracing_enabled).lower()
settings["env"]["MLFLOW_TRACKING_URI"] = "databricks"
settings["env"]["MLFLOW_EXPERIMENT_NAME"] = experiment_name
# Override container-level OTEL endpoint so MLflow uses its native MlflowV3SpanExporter
# instead of sending traces to a non-existent localhost:4314 OTLP collector
settings["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""

# Only register the Stop hook when explicitly enabled
if tracing_enabled:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    # Delegate to a proper hook script that backgrounds the handler via
    # `nohup timeout 30 ... & disown`. This:
    #   1. unblocks the Stop chain immediately (brain-push, /til, etc.)
    #   2. caps the backgrounded flush at 30s so a stuck handler can't
    #      eat memory/CPU forever — one dropped trace beats a leaked
    #      transcript processor
    hook_script = os.path.join(
        app_dir,
        "coda-marketplace", "plugins", "coda-essentials", "hooks",
        "mlflow-trace-stop.sh",
    )
    os.chmod(hook_script, 0o755)
    mlflow_hook = {
        "hooks": [
            {
                "type": "command",
                "command": f"bash {hook_script}",
                # The wrapper script backgrounds the work and returns in <1s,
                # so this outer timeout is belt-and-braces only.
                "timeout": 5,
            }
        ]
    }

    existing_hooks = settings.get("hooks", {})
    stop_hooks = existing_hooks.get("Stop", [])
    stop_hooks.append(mlflow_hook)
    existing_hooks["Stop"] = stop_hooks
    settings["hooks"] = existing_hooks
    print(f"MLflow tracing enabled: experiment={experiment_name}")
else:
    print("MLflow tracing disabled (set MLFLOW_CLAUDE_TRACING_ENABLED=true to enable)")

settings_path.write_text(json.dumps(settings, indent=2))
print(f"  Tracking URI: databricks")
print(f"  Settings updated: {settings_path}")
