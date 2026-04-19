# Makefile for deploying Coding Agents Spawner to Databricks Apps
#
# Usage:
#   make deploy PROFILE=daveok        # full deploy (first time)
#   make redeploy PROFILE=daveok      # sync + deploy only
#   make status PROFILE=daveok
#   make logs PROFILE=daveok

PROFILE       ?= DEFAULT
APP_NAME      := coding-agents-spawner
TEMPLATE_SRC  := /Workspace/Shared/apps/coding-agents

USER_EMAIL    = $(shell databricks current-user me --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")
WORKSPACE_PATH = /Workspace/Users/$(USER_EMAIL)/apps/$(APP_NAME)
HOST          = $(shell databricks auth env --profile $(PROFILE) 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['env']['DATABRICKS_HOST'])")

.PHONY: help run deploy redeploy create-app sync-template sync deploy-app status logs test

run: ## Wait for app to be running and print URL
	@echo "==> Waiting for '$(APP_NAME)' to be running..."
	@for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do \
		STATE=$$(databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('app_status',{}).get('state',''))"); \
		if [ "$$STATE" = "RUNNING" ]; then \
			echo ""; \
			echo "App is RUNNING!"; \
			databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print('URL:', json.load(sys.stdin).get('url','(unknown)'))"; \
			exit 0; \
		fi; \
		echo "    State: $$STATE (waiting...)"; \
		sleep 10; \
	done; \
	echo "    Timed out waiting for app to reach RUNNING state."

test: ## Run spawner gate tests (AC-1 through AC-12)
	@echo "==> Running spawner gate tests..."
	cd .. && uv run pytest tests/gates/test_spawner_*.py -v --tb=short

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

deploy: create-app sync-template sync deploy-app run ## Full deploy: create app, sync template + spawner, deploy + run

redeploy: sync deploy-app run ## Redeploy: sync spawner + deploy

create-app: ## Create the spawner app (idempotent)
	@echo "==> Checking if app '$(APP_NAME)' exists..."
	@if databricks apps get $(APP_NAME) --profile $(PROFILE) >/dev/null 2>&1; then \
		echo "    App '$(APP_NAME)' already exists, skipping create."; \
	else \
		echo "    Creating app '$(APP_NAME)'..."; \
		databricks apps create $(APP_NAME) --profile $(PROFILE); \
	fi

sync-template: ## Sync coding-agents source to shared template path
	@echo "==> Syncing coding-agents template to $(TEMPLATE_SRC)..."
	@databricks workspace mkdirs /Workspace/Shared/apps --profile $(PROFILE) 2>/dev/null || true
	@cd .. && databricks sync . $(TEMPLATE_SRC) --watch=false --exclude-from .syncignore --profile $(PROFILE)
	@# Override app.yaml with template defaults (no pre-loaded token — users paste PAT in CODA)
	@echo "    Uploading template app.yaml..."
	@# Resolve team-memory-mcp app URL (if deployed)
	$(eval TEAM_MEMORY_URL := $(shell databricks apps get team-memory-mcp --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null))
	@printf 'command:\n  - gunicorn\n  - app:app\nenv:\n  - name: HOME\n    value: /app/python/source_code\n  - name: ANTHROPIC_MODEL\n    value: databricks-claude-opus-4-7\n  - name: GEMINI_MODEL\n    value: databricks-gemini-3-1-pro\n  - name: CODEX_MODEL\n    value: databricks-gpt-5-2\n  - name: CLAUDE_CODE_DISABLE_AUTO_MEMORY\n    value: 0\n' > /tmp/_coda_template_app.yaml
	@if [ -n "$(TEAM_MEMORY_URL)" ]; then \
		printf '  - name: TEAM_MEMORY_MCP_URL\n    value: %s\n' "$(TEAM_MEMORY_URL)" >> /tmp/_coda_template_app.yaml; \
		echo "    Team memory MCP URL: $(TEAM_MEMORY_URL)"; \
	else \
		echo "    Team memory MCP: not deployed (skipping)"; \
	fi
	@databricks workspace import $(TEMPLATE_SRC)/app.yaml --file /tmp/_coda_template_app.yaml --format AUTO --overwrite --profile $(PROFILE)
	@rm -f /tmp/_coda_template_app.yaml
	@echo "    Template synced."

sync: ## Sync spawner source to workspace
	@echo "==> Syncing spawner to $(WORKSPACE_PATH)..."
	@databricks sync . $(WORKSPACE_PATH) --watch=false --exclude-from ../.syncignore --profile $(PROFILE)

deploy-app: ## Deploy the spawner app
	@echo "==> Deploying '$(APP_NAME)'..."
	@databricks apps deploy $(APP_NAME) --source-code-path $(WORKSPACE_PATH) --profile $(PROFILE) --no-wait
	@echo ""
	@echo "App URL:"
	@databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('url','(pending)'))"

status: ## Check spawner app status
	@databricks apps get $(APP_NAME) --profile $(PROFILE)

logs: ## Tail spawner app logs
	@databricks apps logs $(APP_NAME) --profile $(PROFILE)
