# Makefile for deploying Coding Agents to Databricks Apps
#
# Usage:
#   make deploy PROFILE=dogfood              # full deploy (create app, sync, deploy)
#   make redeploy PROFILE=dogfood            # skip app creation, just sync + deploy
#   make create-pat PROFILE=dogfood          # generate a 1-day PAT and copy to clipboard
#   make status PROFILE=dogfood              # check app status
#   make open PROFILE=dogfood                # open app in browser
#   make clean PROFILE=dogfood               # remove app and secret scope

# Configuration (accepts lowercase: make deploy profile=dogfood)
ifdef profile
PROFILE := $(profile)
endif
ifdef app_name
APP_NAME := $(app_name)
endif
ifdef pat
PAT := $(pat)
endif
PROFILE          ?= DEFAULT
APP_NAME         ?= coding-agents
SECRET_SCOPE     ?= $(APP_NAME)-secrets
SECRET_KEY       ?= databricks-token
# Lakebase memory: set LAKEBASE_ENDPOINT to wire database resource (grants SP access + injects host)
# Format: projects/{project_id}/branches/{branch_id}/endpoints/{endpoint_id}
LAKEBASE_ENDPOINT ?=
LAKEBASE_DB       ?= databricks_postgres

# Resolve user email and workspace path from the profile
USER_EMAIL    = $(shell databricks current-user me --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")
WORKSPACE_PATH = /Workspace/Users/$(USER_EMAIL)/apps/$(APP_NAME)

.PHONY: help deploy-e2e deploy redeploy create-app create-pat setup-secret link-resources sync deploy-app status open clean clean-secret

# ── Help ─────────────────────────────────────────────

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── Workflows ────────────────────────────────────────

deploy: create-app sync deploy-app ## Full deploy (create app, sync, deploy)
	@echo ""
	@echo "Deployment complete! App URL:"
	@databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('url','(pending)'))"

redeploy: sync deploy-app ## Redeploy: sync + deploy (skip secret setup)
	@echo ""
	@echo "Redeployment complete!"

# ── Building Blocks ──────────────────────────────────

create-app: ## Create the Databricks App (idempotent)
	@echo "==> Checking if app '$(APP_NAME)' exists..."
	@state=$$(databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null \
		| python3 -c "import sys,json; print(json.load(sys.stdin).get('compute_status',{}).get('state',''))" 2>/dev/null); \
	if [ "$$state" = "DELETING" ]; then \
		echo "    App '$(APP_NAME)' is still deleting, waiting..."; \
		while [ "$$state" = "DELETING" ]; do \
			sleep 10; \
			state=$$(databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null \
				| python3 -c "import sys,json; print(json.load(sys.stdin).get('compute_status',{}).get('state',''))" 2>/dev/null); \
		done; \
		echo "    Deletion complete."; \
		echo "    Creating app '$(APP_NAME)'..."; \
		databricks apps create $(APP_NAME) --profile $(PROFILE); \
	elif [ -n "$$state" ]; then \
		echo "    App '$(APP_NAME)' already exists (state: $$state), skipping create."; \
	else \
		echo "    Creating app '$(APP_NAME)'..."; \
		databricks apps create $(APP_NAME) --profile $(PROFILE); \
	fi

create-pat: ## Generate a 90-day PAT and store it as the app secret
	@echo "==> Ensuring secret scope '$(SECRET_SCOPE)' exists..."
	@if databricks secrets list-scopes --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); scopes=[s['name'] for s in (d if isinstance(d,list) else d.get('scopes',[]))]; exit(0 if '$(SECRET_SCOPE)' in scopes else 1)" 2>/dev/null; then \
		echo "    Secret scope '$(SECRET_SCOPE)' already exists."; \
	else \
		echo "    Creating secret scope '$(SECRET_SCOPE)'..."; \
		databricks secrets create-scope $(SECRET_SCOPE) --profile $(PROFILE); \
	fi
	@echo "==> Generating a 90-day PAT..."
	@databricks tokens create --lifetime-seconds $$((90 * 24 * 60 * 60)) --comment "coding-agents (auto-generated)" --profile $(PROFILE) --output json \
		| python3 -c "import sys,json; print(json.load(sys.stdin)['token_value'])" \
		| databricks secrets put-secret $(SECRET_SCOPE) $(SECRET_KEY) --profile $(PROFILE)
	@echo "    PAT created and stored in $(SECRET_SCOPE)/$(SECRET_KEY)"

setup-secret: ## Create secret scope and store PAT (interactive)
	@echo "==> Setting up DATABRICKS_TOKEN secret..."
	@# Create scope if it doesn't exist
	@if databricks secrets list-scopes --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); scopes=[s['name'] for s in (d if isinstance(d,list) else d.get('scopes',[]))]; exit(0 if '$(SECRET_SCOPE)' in scopes else 1)" 2>/dev/null; then \
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

link-resources: ## Add Lakebase postgres resource to app (preserves existing resources)
	@if [ -z "$(LAKEBASE_ENDPOINT)" ]; then \
		echo "Error: LAKEBASE_ENDPOINT required."; \
		echo "Usage: make link-resources LAKEBASE_ENDPOINT=projects/.../branches/.../endpoints/... PROFILE=daveok"; \
		exit 1; \
	fi
	@echo "==> Ensuring secret scope and ENDPOINT_NAME secret..."
	@if ! databricks secrets list-scopes --profile $(PROFILE) --output json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); scopes=[s['name'] for s in (d if isinstance(d,list) else d.get('scopes',[]))]; exit(0 if '$(SECRET_SCOPE)' in scopes else 1)" 2>/dev/null; then \
		databricks secrets create-scope $(SECRET_SCOPE) --profile $(PROFILE); \
	fi
	@printf '%s' '$(LAKEBASE_ENDPOINT)' | databricks secrets put-secret $(SECRET_SCOPE) ENDPOINT_NAME --profile $(PROFILE)
	@echo "==> Merging Lakebase resource into existing app resources..."
	@DATABRICKS_CONFIG_PROFILE=$(PROFILE) uv run python3 -c "\
import json, os, sys; \
from databricks.sdk import WorkspaceClient; \
w = WorkspaceClient(profile='$(PROFILE)'); \
ep = '$(LAKEBASE_ENDPOINT)'; \
branch = ep.rsplit('/endpoints/', 1)[0]; \
dbs = list(w.postgres.list_databases(parent=branch)); \
db = next((d.name for d in dbs if d.database_name == '$(LAKEBASE_DB)'), dbs[0].name if dbs else None); \
assert db, 'no database found under ' + branch; \
app = w.apps.get(name='$(APP_NAME)'); \
existing = [r.as_dict() for r in (app.resources or [])]; \
merged = {r['name']: r for r in existing}; \
merged['postgres'] = {'name':'postgres','description':'Lakebase Autoscaling memory database','postgres':{'branch':branch,'database':db,'permission':'CAN_CONNECT_AND_CREATE'}}; \
merged['ENDPOINT_NAME'] = {'name':'ENDPOINT_NAME','description':'Lakebase endpoint resource path','secret':{'scope':'$(SECRET_SCOPE)','key':'ENDPOINT_NAME','permission':'READ'}}; \
print(json.dumps({'resources': list(merged.values())})) \
" | curl -s -X PATCH \
		"$$(databricks auth env --profile $(PROFILE) 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['env']['DATABRICKS_HOST'])")/api/2.0/apps/$(APP_NAME)" \
		-H "Authorization: Bearer $$(databricks auth token --profile $(PROFILE) 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")" \
		-H "Content-Type: application/json" \
		-d @- >/dev/null
	@echo "    Linked postgres resource ($(LAKEBASE_ENDPOINT)) + ENDPOINT_NAME secret; existing resources preserved."

sync: ## Sync local files to Databricks workspace
	@echo "==> Syncing to $(WORKSPACE_PATH)..."
	@databricks sync . $(WORKSPACE_PATH) --watch=false --profile $(PROFILE)

deploy-app: ## Deploy the app from workspace
	@echo "==> Deploying app '$(APP_NAME)'..."
	@databricks apps deploy $(APP_NAME) --source-code-path $(WORKSPACE_PATH) --profile $(PROFILE) --no-wait

# ── Monitoring ───────────────────────────────────────

status: ## Check app status
	@databricks apps get $(APP_NAME) --profile $(PROFILE)

open: ## Open the app in browser
	@databricks apps get $(APP_NAME) --profile $(PROFILE) --output json 2>/dev/null \
		| python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" \
		| xargs open

# ── Cleanup (destructive) ───────────────────────────

clean: ## Remove the app (destructive)
	@echo "==> Removing app '$(APP_NAME)'..."
	@databricks apps delete $(APP_NAME) --profile $(PROFILE) 2>/dev/null && \
		echo "    App '$(APP_NAME)' deleted." || \
		echo "    App '$(APP_NAME)' not found or already deleted."

