# Web Terminal with Databricks Integration

A browser-based terminal emulator built with Flask and xterm.js, designed for cloud development environments with Databricks workspace integration and Claude Code CLI support.

## Features

- **Browser-based Terminal** - Full PTY support with xterm.js frontend
- **Real-time I/O** - Responsive terminal with polling-based communication
- **Terminal Resizing** - Dynamic resize support for responsive layouts
- **Databricks Integration** - Auto-sync projects to Databricks Workspace on git commits
- **Claude Code CLI** - Pre-configured AI-assisted coding with Anthropic's Claude
- **Micro Editor** - Ships with [micro](https://micro-editor.github.io/), a modern terminal-based text editor

## Quick Start

### Prerequisites

- Python 3.8+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/xterm-experiment.git
cd xterm-experiment

# Install dependencies
uv pip install -r requirements.txt
```

### Running Locally

```bash
uv run python app.py
```

Open http://localhost:8000 in your browser.

## Architecture

```
┌─────────────────────┐     HTTP      ┌─────────────────────┐
│   Browser Client    │◄────────────►│   Flask Backend     │
│   (xterm.js)        │   Polling     │   (PTY Manager)     │
└─────────────────────┘               └─────────────────────┘
                                              │
                                              ▼
                                      ┌─────────────────────┐
                                      │   Shell Process     │
                                      │   (/bin/bash)       │
                                      └─────────────────────┘
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serves the terminal UI |
| `/health` | GET | Health check with session count |
| `/api/session` | POST | Create new terminal session |
| `/api/input` | POST | Send input to terminal |
| `/api/output` | POST | Poll for terminal output |
| `/api/resize` | POST | Resize terminal dimensions |
| `/api/session` | DELETE | Close terminal session |

## Project Structure

```
xterm-experiment/
├── app.py                 # Flask backend with PTY management
├── app.yaml               # Databricks Apps deployment config
├── requirements.txt       # Python dependencies
├── setup_claude.py        # Claude Code CLI configuration
├── sync_to_workspace.py   # Git hook for Databricks sync
├── static/
│   ├── index.html         # Terminal UI
│   └── lib/               # xterm.js library files
└── docs/
    └── plans/             # Design documentation
```

## Databricks Deployment

This project is configured for deployment as a Databricks App.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABRICKS_HOST` | Databricks workspace URL |
| `DATABRICKS_TOKEN` | Personal Access Token (PAT) |

### Deploy

```bash
databricks apps deploy xterm-experiment
```

## Workspace Sync

When deployed, git commits automatically sync your projects to Databricks Workspace:

```
/Workspace/Users/{email}/projects/{project-name}/
```

This is enabled via a git post-commit hook configured by `setup_claude.py`.

## Technologies

- **Backend**: Flask, Python PTY/termios
- **Frontend**: xterm.js, FitAddon
- **Integration**: Databricks SDK, Claude Agent SDK

## License

MIT
