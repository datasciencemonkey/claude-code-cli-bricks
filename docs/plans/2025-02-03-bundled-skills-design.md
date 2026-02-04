# Pre-bundled Databricks Skills & Superpowers Plugin

**Date:** 2025-02-03
**Status:** Approved

## Overview

Bundle Databricks skills and the superpowers plugin into the Claude Code on Databricks app so users have immediate access to Databricks-specific knowledge and development workflows.

## Goals

- Users get 16 Databricks skills out of the box (no manual installation)
- Users get the full superpowers plugin (TDD, debugging, brainstorming, etc.)
- Skills are version-controlled with the app
- Welcome message shows available capabilities

## Directory Structure

```
xterm-experiment/
├── .claude/
│   ├── skills/                    # Databricks skills (16)
│   │   ├── agent-bricks/
│   │   ├── aibi-dashboards/
│   │   ├── asset-bundles/
│   │   ├── databricks-app-apx/
│   │   ├── databricks-app-python/
│   │   ├── databricks-config/
│   │   ├── databricks-docs/
│   │   ├── databricks-genie/
│   │   ├── databricks-jobs/
│   │   ├── databricks-python-sdk/
│   │   ├── databricks-unity-catalog/
│   │   ├── mlflow-evaluation/
│   │   ├── model-serving/
│   │   ├── spark-declarative-pipelines/
│   │   ├── synthetic-data-generation/
│   │   └── unstructured-pdf-generation/
│   │
│   └── plugins/
│       └── superpowers/           # Full superpowers plugin
│           ├── .claude-plugin/
│           │   └── plugin.json
│           ├── skills/            # 14 skills
│           ├── commands/
│           ├── hooks/
│           ├── agents/
│           └── ...
├── setup_claude.py                # Modified to register plugin
├── app.py                         # Modified to start PTY in ~/projects/
├── CLAUDE.md                      # Welcome message
└── README.md                      # Updated documentation
```

## Implementation Details

### 1. Bundle Databricks Skills

Copy all 16 skills from [ai-dev-kit](https://github.com/databricks-solutions/ai-dev-kit) `databricks-skills/` to `.claude/skills/`:

| Category | Skills |
|----------|--------|
| AI & Agents | agent-bricks, databricks-genie, mlflow-evaluation, model-serving |
| Analytics | aibi-dashboards, databricks-unity-catalog |
| Data Engineering | spark-declarative-pipelines, databricks-jobs, synthetic-data-generation |
| Development | asset-bundles, databricks-app-apx, databricks-app-python, databricks-python-sdk, databricks-config |
| Reference | databricks-docs, unstructured-pdf-generation |

### 2. Bundle Superpowers Plugin

Copy full plugin from [superpowers](https://github.com/obra/superpowers) to `.claude/plugins/superpowers/`:

- 14 skills (brainstorming, TDD, systematic-debugging, etc.)
- Commands (/commit, etc.)
- Hooks
- Agents

### 3. Register Plugin in setup_claude.py

```python
# 6. Register bundled superpowers plugin
plugins_dir = claude_dir / "plugins"
plugins_dir.mkdir(exist_ok=True)

installed_plugins = {
    "version": 2,
    "plugins": {
        "superpowers@bundled": [
            {
                "scope": "user",
                "installPath": str(home / ".claude" / "plugins" / "superpowers"),
                "version": "4.0.3",
                "installedAt": "2025-01-01T00:00:00.000Z",
                "lastUpdated": "2025-01-01T00:00:00.000Z"
            }
        ]
    }
}

plugins_json_path = plugins_dir / "installed_plugins.json"
plugins_json_path.write_text(json.dumps(installed_plugins, indent=2))
print("Superpowers plugin registered")
```

### 4. Start PTY in ~/projects/

Modify `app.py` to start shell sessions in the projects directory:

```python
# In create_session(), when spawning the PTY:
projects_dir = os.path.expanduser("~/projects")
os.makedirs(projects_dir, exist_ok=True)

pid, fd = pty.fork()
if pid == 0:
    os.chdir(projects_dir)  # Start in projects/
    os.execvpe('/bin/bash', ['/bin/bash', '-l'], env)
```

### 5. Welcome Message (CLAUDE.md)

Create `CLAUDE.md` at repo root:

```markdown
# Claude Code on Databricks

Welcome! This environment comes pre-configured with:

## Databricks Skills (16)
- **AI & Agents**: agent-bricks, databricks-genie, mlflow-evaluation, model-serving
- **Analytics**: aibi-dashboards, databricks-unity-catalog
- **Data Engineering**: spark-declarative-pipelines, databricks-jobs, synthetic-data-generation
- **Development**: asset-bundles, databricks-app-apx, databricks-app-python, databricks-python-sdk, databricks-config
- **Reference**: databricks-docs, unstructured-pdf-generation

## Superpowers Plugin
- brainstorming, test-driven-development, systematic-debugging, writing-plans, and more

## Quick Start
- Projects sync to Databricks Workspace on git commit
- Use `/commit` for guided commits
- Ask "help me create a dashboard" to see skills in action
```

### 6. README Update

Document bundled skills with credits to source repositories:

- [databricks-solutions/ai-dev-kit](https://github.com/databricks-solutions/ai-dev-kit) - Databricks skills
- [obra/superpowers](https://github.com/obra/superpowers) - Development workflow plugin

Include update instructions for keeping skills current.

## Updating Skills

Since skills are bundled (not downloaded at startup), updates require:

1. Pull latest from ai-dev-kit repo
2. Copy updated skills to `.claude/skills/`
3. Redeploy the app

## Trade-offs

| Approach | Chosen | Reason |
|----------|--------|--------|
| Bundled vs Download at startup | Bundled | Faster startup, no network dependency, predictable |
| All skills vs Subset | All 16 | Comprehensive coverage |
| Skills location | `.claude/skills/` | Standard location, auto-loaded |
| Superpowers full vs skills-only | Full plugin | Get commands, hooks, agents too |
| HOME vs working dir change | Working dir | Keep .claude/ separate, only projects sync |
