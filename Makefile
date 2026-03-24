# Makefile for deploying Coding Agents to Databricks Apps
#
# Usage:
#   make deploy PROFILE=daveok              # full deploy (prompts for PAT interactively)
#   make redeploy PROFILE=daveok            # skip secret setup, just sync + deploy
#   make create-pat PROFILE=daveok          # auto-generate a 90-day PAT and store it
#   make status PROFILE=daveok              # check app status
#   make logs PROFILE=daveok                # tail app logs
#   make clean PROFILE=daveok               # remove app and secret scope

# Configuration
PROFILE       ?= DEFAULT
APP_NAME      ?= coding-agents
SECRET_SCOPE  ?= $(APP_NAME)-secrets
SECRET_KEY    ?= databricks-token

# Resolve user email and workspace path from the profile
USER_EMAIL    = $(shell databricks current-user me --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")
WORKSPACE_PATH = /Workspace/Users/$(USER_EMAIL)/apps/$(APP_NAME)

.PHONY: help deploy redeploy create-app setup-secret sync deploy-app status logs clean-secret clean create-pat

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

deploy: create-app setup-secret sync deploy-app ## Full deploy: create app, set secret, sync, deploy
	@echo ""
	@echo "Deployment complete! App URL:"
	@databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('url','(pending)'))"

redeploy: sync deploy-app ## Redeploy: sync + deploy (skip secret setup)
	@echo ""
	@echo "Redeployment complete!"

create-app: ## Create the Databricks App (idempotent)
	@echo "==> Checking if app '$(APP_NAME)' exists..."
	@if databricks apps get $(APP_NAME) --profile $(PROFILE) >/dev/null 2>&1; then \
		echo "    App '$(APP_NAME)' already exists, skipping create."; \
	else \
		echo "    Creating app '$(APP_NAME)'..."; \
		databricks apps create $(APP_NAME) --profile $(PROFILE); \
	fi

setup-secret: ## Create secret scope and store PAT
	@echo "==> Setting up DATABRICKS_TOKEN secret..."
	@# Create scope if it doesn't exist
	@if databricks secrets list-scopes --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; scopes=[s['name'] for s in json.load(sys.stdin).get('scopes',[])]; exit(0 if '$(SECRET_SCOPE)' in scopes else 1)" 2>/dev/null; then \
		echo "    Secret scope '$(SECRET_SCOPE)' already exists."; \
	else \
		echo "    Creating secret scope '$(SECRET_SCOPE)'..."; \
		databricks secrets create-scope $(SECRET_SCOPE) --profile $(PROFILE); \
	fi
	@# Store the PAT - prompt if not provided
	@if [ -z "$(PAT)" ]; then \
		echo "    Enter your Databricks PAT (will not echo):"; \
		read -s pat_value && \
		echo "$$pat_value" | databricks secrets put-secret $(SECRET_SCOPE) $(SECRET_KEY) --profile $(PROFILE); \
	else \
		echo "$(PAT)" | databricks secrets put-secret $(SECRET_SCOPE) $(SECRET_KEY) --profile $(PROFILE); \
	fi
	@echo "    Secret stored in $(SECRET_SCOPE)/$(SECRET_KEY)"
	@# Link secret to app resource
	@echo "    Linking secret to app resource 'DATABRICKS_TOKEN'..."
	@curl -s -X PATCH \
		"$$(databricks auth env --profile $(PROFILE) 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['env']['DATABRICKS_HOST'])")/api/2.0/apps/$(APP_NAME)" \
		-H "Authorization: Bearer $$(databricks auth token --profile $(PROFILE) 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")" \
		-H "Content-Type: application/json" \
		-d '{"resources":[{"name":"DATABRICKS_TOKEN","description":"PAT for model serving access","secret":{"scope":"$(SECRET_SCOPE)","key":"$(SECRET_KEY)","permission":"READ"}}]}' \
		>/dev/null
	@echo "    App resource linked."

sync: ## Sync local files to Databricks workspace
	@echo "==> Syncing to $(WORKSPACE_PATH)..."
	databricks sync . $(WORKSPACE_PATH) --watch=false --profile $(PROFILE)

deploy-app: ## Deploy the app from workspace
	@echo "==> Deploying app '$(APP_NAME)'..."
	databricks apps deploy $(APP_NAME) --source-code-path $(WORKSPACE_PATH) --profile $(PROFILE) --no-wait

status: ## Check app status
	@databricks apps get $(APP_NAME) --profile $(PROFILE)

logs: ## Tail app logs
	databricks apps logs $(APP_NAME) --profile $(PROFILE)

clean-secret: ## Remove secret scope (destructive)
	@echo "==> Removing secret scope '$(SECRET_SCOPE)'..."
	databricks secrets delete-scope $(SECRET_SCOPE) --profile $(PROFILE)

clean: ## Remove app and secret scope (destructive)
	@echo "==> Removing app '$(APP_NAME)'..."
	@databricks apps delete $(APP_NAME) --profile $(PROFILE) 2>/dev/null && \
		echo "    App '$(APP_NAME)' deleted." || \
		echo "    App '$(APP_NAME)' not found or already deleted."
	@echo "==> Removing secret scope '$(SECRET_SCOPE)'..."
	@databricks secrets delete-scope $(SECRET_SCOPE) --profile $(PROFILE) 2>/dev/null && \
		echo "    Secret scope '$(SECRET_SCOPE)' deleted." || \
		echo "    Secret scope '$(SECRET_SCOPE)' not found or already deleted."

create-pat: ## Generate a 90-day PAT and store it as the app secret
	@echo "==> Generating a 90-day PAT..."
	@databricks tokens create --lifetime-seconds $$((90 * 24 * 60 * 60)) --comment "coding-agents (auto-generated)" --profile $(PROFILE) --output json \
		| python3 -c "import sys,json; print(json.load(sys.stdin)['token_value'])" \
		| databricks secrets put-secret $(SECRET_SCOPE) $(SECRET_KEY) --profile $(PROFILE)
	@echo "    PAT created and stored in $(SECRET_SCOPE)/$(SECRET_KEY)"
