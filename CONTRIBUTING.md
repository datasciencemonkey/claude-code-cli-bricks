# Contributing to CoDA

Thank you for your interest in contributing to Coding Agents on Databricks Apps (CoDA).

## Getting Started

1. **Fork the repository** and clone your fork locally
2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install pytest  # for running tests
   ```
3. **Run tests** to verify your environment:
   ```bash
   python -m pytest tests/ -v
   ```

## Development Workflow

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feat/your-feature main
   ```
2. Make your changes
3. Add or update tests for any new functionality
4. Run the test suite to confirm nothing is broken:
   ```bash
   python -m pytest tests/ -v
   ```
5. Commit with a descriptive message using [conventional commits](https://www.conventionalcommits.org/):
   ```bash
   git commit -m "feat: add support for new agent"
   ```
6. Push and open a pull request against `main`

## Project Structure

```
app.py                  # Flask app — PTY management, API routes
gunicorn.conf.py        # Gunicorn config (single worker, 8 threads)
setup_*.py              # Per-agent CLI setup scripts
sync_to_workspace.py    # Post-commit hook for Workspace sync
utils.py                # Shared utilities
static/                 # Frontend (HTML, JS, CSS)
tests/                  # pytest test suite
.claude/skills/         # Databricks + workflow skills
docs/                   # Design docs and deployment guide
```

## Adding a New Agent

To add support for a new coding agent CLI:

1. Create `setup_<agent>.py` following the pattern in existing setup scripts
2. Register the setup step in `app.py`'s `SETUP_STEPS` list
3. Add any agent-specific instructions adaptation in `utils.py`
4. Add tests in `tests/`

## Code Style

- Follow existing patterns in the codebase
- Keep functions focused and small
- Add docstrings for public functions
- Use type hints where practical

## Reporting Issues

Open an issue on GitHub with:
- A clear description of the problem or feature request
- Steps to reproduce (for bugs)
- Expected vs actual behavior
- Your Databricks Apps environment details (if relevant)
