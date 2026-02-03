# claude-code-cli-bricks
#### What is it?

TL;DR: Claude Code on Databricks Apps for All Databricks Users 🚀

A browser-based terminal emulator built with Flask and xterm.js, designed for cloud development environments with Databricks workspace integration and Claude Code CLI support.

### Why now?
On Jan 26. 2026, Andrej Karpathy made [this viral tweet](https://x.com/karpathy/status/2015883857489522876?s=46&t=tEsLJXJnGFIkaWs-Bhs1yA). Boris Cherny, the creator of claude code responded and said the following.
![alt text](image.png)

This app template opens this up for all Databricks Users! ❤️

No more pesky IDE setups, no bespoke tweaks. 

Just use it all on Databricks, from the browser. Wired up to model serving endpoints on your workspace.

## Features

✅ **Browser-based Terminal** - Full PTY support with xterm.js frontend

✅  **Real-time I/O** - Responsive terminal with polling-based communication

✅ **Terminal Resizing** - Dynamic resize support for responsive layouts

✅ **Databricks Workspace Integration** - Auto-sync projects to Databricks Workspace on git commits

✅ **Claude Code CLI** - Pre-configured to use the Databricks hosted model via `app.yaml` as the API endpoint

✅ **Micro Editor** - Ships with [micro](https://micro-editor.github.io/), a modern terminal-based text editor

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

## Deploying to Databricks

1. Clone this repo to your Databricks Workspace
2. Navigate to **Compute** → **Apps**
3. Click **Create App** and select **Custom App**
4. Point to the cloned repo and deploy

### Installation
```bash
# Clone the repository
git clone https://github.com/your-username/claude-code-cli-bricks.git
cd claude-code-cli-bricks

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
claude-code-cli-bricks/
├── app.py                 # Flask backend with PTY management
├── app.yaml               # Databricks Apps deployment config
├── app.yaml.template      # Template for app.yaml configuration
├── requirements.txt       # Python dependencies
├── setup_claude.py        # Claude Code CLI configuration
├── sync_to_workspace.py   # Git hook for Databricks sync
├── static/
│   ├── index.html         # Terminal UI
│   └── lib/               # xterm.js library files
└── docs/
    └── plans/             # Design documentation
```

## Configuration

### Setting up app.yaml

Copy the template and configure your Databricks workspace:

```bash
cp app.yaml.template app.yaml
```

Edit `app.yaml` and replace `<your-workspace>` with your Databricks workspace URL:

```yaml
env:
  - name: DATABRICKS_HOST
    value: https://<your-workspace>.cloud.databricks.com
```

The `DATABRICKS_HOST` is used by both:
- **Workspace sync** - To upload projects on git commits
- **Claude Code CLI** - As the Anthropic API endpoint (via Databricks serving endpoints)

## Databricks Deployment

This project is configured for deployment as a Databricks App.

### Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABRICKS_HOST` | Databricks workspace URL |
| `DATABRICKS_TOKEN` | Personal Access Token (PAT) |

### Create App

First, create the app in your Databricks workspace:

```bash
databricks apps create claude-code-cli-bricks
```

### Deploy

Then deploy the code:

```bash
databricks apps deploy claude-code-cli-bricks
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
