# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A browser-based terminal app (Databricks App) that gives Databricks users access to AI coding agents (Claude Code, Gemini CLI, Codex CLI, OpenCode) via xterm.js. No local IDE needed — models route through Databricks AI Gateway or Model Serving endpoints.

## Development Commands

```bash
# Run locally (Flask dev server)
uv run python app.py
# Open http://localhost:8000

# Production (Gunicorn, used by Databricks Apps)
uv run gunicorn app:app

# Deploy to Databricks Apps
databricks sync . /Workspace/Users/<email>/apps/<app-name> --watch=false
databricks apps deploy <app-name> --source-code-path /Workspace/Users/<email>/apps/<app-name>

# No test suite exists — skip test discovery
```

## Architecture

**Single-process Flask app** with PTY-based terminal sessions, served by Gunicorn (1 worker, 8 threads via gthread).

### Startup Flow
1. `gunicorn.conf.py` → `post_worker_init` → `app.initialize_app()`
2. `initialize_app()` resolves auth (PAT or OAuth M2M via `utils.resolve_auth()`), determines app owner, starts cleanup thread, launches setup in background thread
3. Setup runs sequentially: git config (Python), micro editor (bash), GitHub CLI (`gh`), then `setup_claude.py`, `setup_codex.py`, `setup_opencode.py`, `setup_gemini.py`, `setup_databricks.py` — each installs a CLI and writes its config files. Each step has a 300s timeout. If `GIT_REPOS` is set, repos are auto-cloned into `~/projects/` after setup.
4. During setup, `/` serves `static/loading.html` (snake game); after setup, serves `static/index.html` (xterm.js terminal)
5. New terminal sessions start in `~/projects/` directory

### Key Files
- **`app.py`** — Flask server, PTY session management (create/input/output/resize/close), authorization, setup orchestration
- **`utils.py`** — Auth resolution (PAT → OAuth M2M → SDK fallback), `TokenRefresher` for OAuth, `adapt_instructions_file()` for cross-CLI instruction sharing, `ensure_https()`
- **`setup_*.py`** — Per-agent setup scripts. Each resolves gateway vs direct endpoint, installs CLI binary, writes config files. Claude uses `~/.claude/settings.json`, Gemini uses `~/.gemini/.env`, OpenCode is built from fork (`dgokeeffe/opencode#feat/databricks-ai-sdk-provider`) with native Databricks provider — auto-discovers models and handles auth via `@databricks/sdk-experimental`, config at `~/.config/opencode/opencode.json`, Codex uses `~/.codex/config.toml` + `~/.codex/.env`, Databricks CLI uses `~/.databrickscfg`
- **`sync_to_workspace.py`** — Post-commit hook target: syncs `~/projects/*` repos to `/Workspace/Users/{email}/projects/` via `databricks sync`
- **`gunicorn.conf.py`** — Must use `workers=1` (PTY fds and session state are process-local)

### Authentication Model
`utils.resolve_auth()` tries in order: explicit `DATABRICKS_TOKEN` (PAT), `DATABRICKS_CLIENT_ID`+`SECRET` (OAuth M2M with token refresh), SDK auto-detect. The `TokenRefresher` class runs a background thread (every 30min) to refresh OAuth tokens and update all agent config files in-place.

**Git credentials** are handled by a host-aware credential helper (`git-credential-databricks`). It checks `GIT_TOKEN` first (scoped to `GIT_TOKEN_HOST` if set), then falls back to `DATABRICKS_TOKEN`. Users can also authenticate interactively via `gh auth login` (GitHub CLI is pre-installed). Workspace file sync is opt-in via `WORKSPACE_SYNC=true`.

### Security
Single-user app: the PAT owner is determined at startup, and `@app.before_request` checks `X-Forwarded-Email` against the owner. In OAuth M2M mode, authorization is delegated to the Databricks Apps proxy.

### Session Management
PTY sessions use `pty.openpty()` + background reader threads. A cleanup thread kills sessions with no poll activity for 60s (SIGHUP → wait 3s → SIGKILL).

### API Endpoints
- `GET /` — Loading screen (during setup) or terminal UI
- `GET /health` — Health check (no auth required)
- `GET /api/setup-status` — Setup progress (no auth required)
- `POST /api/session` — Create new PTY session
- `POST /api/input` — Send keystrokes to terminal (`{session_id, input}`)
- `POST /api/output` — Poll for terminal output (`{session_id}`) — also updates `last_poll_time`
- `POST /api/resize` — Resize terminal (`{session_id, cols, rows}`)
- `POST /api/session/close` — Close terminal session

## Deployment Config

- `app.yaml.template` — Template to copy to `app.yaml`. Set `DATABRICKS_GATEWAY_HOST` or remove it to fall back to direct Model Serving.
- Use `databricks sync` (not `workspace import-dir`) to upload — it respects `.gitignore` and handles `.git` correctly.
- **Never move the `.git` folder** to the workspace when running workspace import.

## Skills

39 pre-installed skills live in `.claude/skills/`. Databricks skills come from [databricks-solutions/ai-dev-kit](https://github.com/databricks-solutions/ai-dev-kit), workflow skills from [obra/superpowers](https://github.com/obra/superpowers). Use `/refresh-databricks-skills` to pull latest.

## Dependencies

`requirements.txt`: flask, claude-agent-sdk, databricks-sdk. No pyproject.toml — no build system.
