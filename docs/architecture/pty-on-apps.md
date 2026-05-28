# PTY-on-Apps: hosted-agent reference architecture

CoDA ("Coding agents on Databricks Apps") is the reference implementation of an "interactive agent in your Databricks workspace" pattern. This doc describes the stack so other projects considering similar plumbing don't have to reverse-engineer it from `app.py`.

If you are evaluating whether to build a similar product, **read this first** — most of the hard problems are already solved in here, and the answers are not obvious.

## What problem this solves

A coding agent (Claude Code, Codex, Hermes, Gemini, OpenCode) is a CLI that reads stdin and writes to a TTY. Customers want that experience in a browser, hosted on Databricks Apps, without local installs. That means:

1. The agent runs server-side inside a Databricks App container.
2. The browser is the terminal: stdin keystrokes go up, stdout (with ANSI escape codes intact) comes back.
3. Auth, file sync, and session lifecycle all have to work on top of Databricks Apps' platform constraints (single container, no persistent disk between deploys, request-scoped tokens).

## Stack at a glance

```
Browser                        Databricks App container
─────────                      ──────────────────────────────────────
xterm.js  ←─ WebSocket ──→     Flask-SocketIO  ←─ pipe ──→  PTY (pty.openpty + fork)
                                          │                    │
                                          │                    └─→ Claude/Codex/Hermes/Gemini/OpenCode
                                          ├─→  PAT rotator (background thread)
                                          ├─→  workspace sync hook (post-commit)
                                          └─→  session lock / per-session state
```

## Per-layer notes

### 1. PTY allocation: `pty.openpty()` + `os.fork()`

Use `pty.openpty()` to create the master/slave pair, then `os.fork()` so the child can `dup2` slave_fd to stdin/stdout/stderr and `execvp` the agent. The parent retains the master_fd and uses a background thread (`read_pty_output`) to pump bytes to Socket.IO.

**Why this rather than `subprocess.Popen(...stdin=PIPE)`:** agents are interactive terminal applications. They issue cursor-positioning, alternate-screen, and color escape codes that only behave correctly against a real TTY. A subprocess pipe gets you cooked-mode line buffering and broken redraws. `pty.openpty()` is the only path that makes Claude Code's TUI work.

**Why single-worker gunicorn:** PTY file descriptors are process-local. If gunicorn spawns N workers, a session created on worker A becomes unreachable from worker B. CoDA pins to one worker (`workers = 1`) with `gthread` for concurrency. The PAT rotator and session reaper share the worker's memory; you can't scale by adding workers.

### 2. Transport: Flask-SocketIO over WebSocket, HTTP polling fallback

WebSocket is the happy path. Behind some enterprise proxies or fronting gateways, long-lived WebSockets get cut after a request-timeout window (often around 60 seconds); CoDA mitigates with a Web Worker doing HTTP long-polling as a fallback. This is invisible to xterm.js — same event names, same payloads.

**Why xterm.js:** it parses ANSI escape codes and renders a real terminal grid. Anything dumber (`<pre>` with a JS handler) will mangle anything more complex than `echo`.

### 3. Auth: short-lived PATs + auto-rotation (`pat_rotator.py`)

Databricks Apps inject a request-scoped OAuth token. That works for one-shot HTTP calls. It does **not** work for a long-running CLI that needs the Databricks SDK, `databricks` CLI, etc. for many minutes.

`pat_rotator.py` mints a fresh 15-minute PAT, writes it to `~/.databrickscfg`, and revokes the old one — every 10 minutes, only while there are active sessions. When the last session reaps (24h idle timeout), rotation pauses. New session → rotation resumes.

**Why not just store one long-lived PAT:** PATs are user credentials. A long-lived one in container memory survives across the user's auth events. Rotating to 15-minute lifetimes bounds the blast radius.

**Single-user gate:** only the token owner can open a terminal. The app deliberately rejects requests from anyone but the deployer-identity. This is what makes the PAT-rotation model safe — the "user" is one well-known principal, not arbitrary visitors.

### 4. Workspace sync: post-commit hook → `databricks workspace import-dir`

A git post-commit hook runs `sync_to_workspace.py`, which mirrors the project into `/Workspace/Users/{user}/projects/{repo}/` so the work survives container restarts (Databricks Apps containers are ephemeral; only the workspace is durable).

Caveats — don't paper over these:

- **Never `import-dir` a `.git/` folder**: the workspace import codec mangles it and the next `git status` is unrecoverable. The hook excludes `.git/` explicitly. Add that exclusion in any re-implementation.
- **Token-aware:** the sync uses the rotated PAT (#3), not the original Apps-injected token, since the Apps token may have expired between the commit and the sync.

### 5. Per-session state: terminal id, lock, idle timer

Each Socket.IO connection gets a `session_id`, a per-session `Lock` (so concurrent socket events don't race the same PTY), and an idle-timer that reaps stale sessions. State lives in process memory — there is no Redis / external session store. That's deliberate: single-worker (see #1), small fleet, simple.

## When to extract vs document

If "hosted agent in the browser" becomes a recurring pattern people need to build on Databricks Apps, this stack is the reference implementation. Two ways to share it:

- **(a) Extract** the PTY + auth + sync layer into a reusable Databricks Apps template. Drop the agent-specific bits (model config, AI Gateway routing) and expose a `BackgroundCommand` primitive that any hosted-CLI product can build on.
- **(b) Document** (this file) and point at CoDA as the canonical example. Lower-effort, but doesn't save other implementers from re-deriving the same trade-offs.

This file is the option-(b) version. Option (a) is the right long-term move once a second hosted-CLI product wants the same plumbing.

## Related design docs

- [docs/plans/2026-02-02-web-terminal-design.md](../plans/2026-02-02-web-terminal-design.md) — original PTY + Socket.IO design.
- [docs/plans/2026-02-02-web-terminal-implementation.md](../plans/2026-02-02-web-terminal-implementation.md) — implementation walkthrough.
- [docs/plans/2026-02-03-session-timeout-design.md](../plans/2026-02-03-session-timeout-design.md) — idle reaping.
- [docs/plans/2026-02-03-workspace-sync-design.md](../plans/2026-02-03-workspace-sync-design.md) — git post-commit → workspace sync.
- [docs/plans/2026-03-27-pat-auto-rotation-implementation.md](../plans/2026-03-27-pat-auto-rotation-implementation.md) — PAT rotation.
- [docs/plans/2026-03-28-session-detach-reconnect.md](../plans/2026-03-28-session-detach-reconnect.md) — reconnect semantics.
- [docs/plans/2026-03-08-multi-tab-terminals-design.md](../plans/2026-03-08-multi-tab-terminals-design.md) — multi-tab handling.
- [docs/plans/2026-03-08-multi-tab-terminals-implementation.md](../plans/2026-03-08-multi-tab-terminals-implementation.md) — multi-tab implementation.
- [docs/2026-03-08-tmux-evaluation.md](../2026-03-08-tmux-evaluation.md) — why we chose `pty.openpty()` over `tmux`.
