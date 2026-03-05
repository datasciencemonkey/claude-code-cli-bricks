# PRD: Multi-Terminal Support & Git Authentication

**Status:** COMPLETE
**Author:** Claude Code
**Date:** 2025-03-05

---

## Problem Statement

The browser-based terminal app currently supports only a single full-screen terminal. Users running AI coding agents (Claude Code, Gemini CLI, etc.) frequently need multiple terminals simultaneously -- one for the agent, one for testing, one for git operations. Switching between tasks requires closing and reopening sessions. Additionally, git credential helpers are not configured, so HTTPS git operations against GitHub/GitLab fail when users try to clone private repos or push changes.

## Goals

1. Enable multiple terminal panes visible simultaneously with predefined layouts
2. Provide a toolbar for layout switching, pane management, and focus control
3. Optimize polling performance with a batch output endpoint
4. Configure git credential helpers so Databricks token-based git operations work seamlessly

## Non-Goals

- WebSocket support (Databricks Apps proxy limitation)
- Drag-and-drop pane resizing (keep it simple with predefined layouts)
- Saving/restoring terminal sessions across page reloads
- External JS framework dependencies
- Modifying the loading screen (static/loading.html)

---

## Acceptance Criteria

### Multi-Terminal UI

**AC-1: Layout System**
The frontend must support four predefined layouts: "single" (1 terminal, full screen), "hsplit" (2 terminals side-by-side), "vsplit" (2 terminals stacked), and "quad" (4 terminals in a 2x2 grid). Each layout allocates equal space to its panes.

**AC-2: Toolbar**
A toolbar at the top of the page displays: layout toggle buttons (icons or labels for single/hsplit/vsplit/quad), indicators showing which panes are active, and a visual indicator of which pane has focus. The toolbar must use the existing dark theme (#1e1e1e background).

**AC-3: Pane Lifecycle**
Each pane gets its own independent PTY session via POST /api/session. Sessions are created when a pane is added and closed (via POST /api/session/close) when a pane is removed. Users can close individual panes via a close button on each pane header. Closing a pane in a layout that requires fewer panes does not force a layout change -- the slot becomes empty and shows a "+" button to reopen.

**AC-4: Independent Resize**
Each pane's xterm.js instance must report its own correct dimensions. When the window resizes or the layout changes, each pane calls fitAddon.fit() and sends its dimensions via POST /api/resize. Resize events must be debounced (at least 150ms).

**AC-5: Focus Management**
Clicking a pane gives it focus (visually indicated by a highlighted border). The keyboard shortcut Ctrl+Shift+N cycles focus to the next active pane. The focused pane receives all keyboard input.

**AC-6: Close Pane**
Each pane has a close button (X) in its header bar. Closing a pane sends POST /api/session/close and removes the terminal from the UI. The pane slot shows a "+" button to create a new session in that slot.

### Performance

**AC-7: Batch Output Endpoint**
A new endpoint POST /api/output-batch accepts `{"session_ids": ["id1", "id2", ...]}` and returns `{"outputs": {"id1": {"output": "...", "exited": false}, "id2": {...}}}`. The frontend uses this single endpoint instead of individual /api/output calls to reduce HTTP overhead. The existing /api/output endpoint remains for backward compatibility.

**AC-8: Polling Efficiency**
The frontend uses a single setInterval (100ms) that calls /api/output-batch with all active session IDs. This replaces per-terminal polling intervals. If no sessions are active, polling pauses.

### Git Authentication

**AC-9: Git Credential Helper**
During setup (in setup_databricks.py or app.py's _setup_git_config), a git credential helper script is written to ~/.local/bin/git-credential-databricks. It reads DATABRICKS_TOKEN from the environment and returns it as the password for HTTPS git operations. The ~/.gitconfig is updated to include `[credential] helper = /path/to/git-credential-databricks`. This enables `git clone https://...`, `git push`, etc. to authenticate using the Databricks token for Databricks-hosted repos (Repos API).

**AC-10: Credential Helper Protocol**
The git credential helper must implement the git credential helper protocol: when invoked with "get" as an argument, it reads key=value pairs from stdin (including "host" and "protocol") and writes `username=token\npassword=<DATABRICKS_TOKEN>\n` to stdout. For any other action (store, erase), it exits silently.

---

## Technical Design

### Frontend (static/index.html)

- Replace the single `#terminal` div with a `#toolbar` and `#pane-container`
- TerminalPane class: manages one xterm.js Terminal + FitAddon + session lifecycle
- LayoutManager class: manages pane creation/destruction, CSS grid layout switching
- Single poll loop calls /api/output-batch with all active session IDs
- Debounced resize handler updates all panes

### Backend (app.py)

- New route: POST /api/output-batch
- Acquires sessions_lock once, reads all requested buffers, returns combined response

### Git Auth (setup_databricks.py or _setup_git_config in app.py)

- Write git-credential-databricks shell script to ~/.local/bin/
- Append credential helper config to ~/.gitconfig
- The credential helper reads DATABRICKS_TOKEN from env at runtime (so token refresh works)

### Files Changed

| File | Change |
|------|--------|
| static/index.html | Complete rewrite: toolbar, layout manager, multi-pane support, batch polling |
| app.py | Add /api/output-batch endpoint |
| app.py (_setup_git_config) | Add git credential helper setup |

---

## Resolved Questions

1. **Last pane behavior:** Closing the last pane auto-creates a new terminal (always at least one terminal open).
2. **Credential helper scope:** The credential helper works for ALL HTTPS git URLs (general helper, not scoped to Databricks only).

---
