"""Coding Agents Spawner App -- one-click provisioning of coding-agents for any developer."""

import os

import requests
from flask import Flask, jsonify, request

app = Flask(__name__, static_folder="static")

DATABRICKS_HOST = os.environ.get("DATABRICKS_HOST", "")


def app_name_from_email(email: str) -> str:
    """Derive app name from user email: david.okeeffe@company.com -> coding-agents-david-okeeffe."""
    username = email.split("@")[0]
    slug = username.replace(".", "-").replace("_", "-").lower()
    return f"coding-agents-{slug}"


def mint_pat(host: str, oauth_token: str, app_name: str) -> str:
    """Mint a PAT via POST /api/2.0/token/create with 90-day lifetime."""
    resp = requests.post(
        f"{host}/api/2.0/token/create",
        headers={"Authorization": f"Bearer {oauth_token}"},
        json={
            "lifetime_seconds": 7_776_000,
            "comment": f"{app_name} (auto-provisioned)",
        },
    )
    resp.raise_for_status()
    return resp.json()["token_value"]


def store_pat_in_secret_scope(
    host: str, oauth_token: str, app_name: str, pat_value: str
) -> dict:
    """Create secret scope (handle 409) and store PAT."""
    scope_name = f"{app_name}-secrets"
    headers = {"Authorization": f"Bearer {oauth_token}"}

    # Create scope -- 409 means it already exists, which is fine
    scope_resp = requests.post(
        f"{host}/api/2.0/secrets/scopes/create",
        headers=headers,
        json={"scope": scope_name},
    )
    if scope_resp.status_code not in (200, 409):
        scope_resp.raise_for_status()

    # Store the PAT
    put_resp = requests.post(
        f"{host}/api/2.0/secrets/put",
        headers=headers,
        json={
            "scope": scope_name,
            "key": "databricks-token",
            "string_value": pat_value,
        },
    )
    put_resp.raise_for_status()

    return {"success": True, "scope": scope_name}


def create_app(
    host: str, oauth_token: str, app_name: str, source_code_path: str
) -> dict:
    """Create the Databricks App via POST /api/2.0/apps."""
    resp = requests.post(
        f"{host}/api/2.0/apps",
        headers={"Authorization": f"Bearer {oauth_token}"},
        json={"name": app_name},
    )
    resp.raise_for_status()
    return resp.json()


def link_secret_to_app(
    host: str,
    oauth_token: str,
    app_name: str,
    scope_name: str,
    secret_key: str,
) -> dict:
    """Link secret scope to app as DATABRICKS_TOKEN resource via PATCH."""
    resp = requests.patch(
        f"{host}/api/2.0/apps/{app_name}",
        headers={"Authorization": f"Bearer {oauth_token}"},
        json={
            "resources": [
                {
                    "name": "DATABRICKS_TOKEN",
                    "secret_scope": scope_name,
                    "secret_key": secret_key,
                }
            ]
        },
    )
    resp.raise_for_status()
    return resp.json()


def deploy_app(
    host: str, oauth_token: str, app_name: str, source_code_path: str
) -> dict:
    """Deploy the app via POST /api/2.0/apps/{name}/deployments."""
    resp = requests.post(
        f"{host}/api/2.0/apps/{app_name}/deployments",
        headers={"Authorization": f"Bearer {oauth_token}"},
        json={"source_code_path": source_code_path},
    )
    resp.raise_for_status()
    return resp.json()


def check_existing_app(host: str, oauth_token: str, app_name: str) -> dict:
    """Check if an app already exists."""
    resp = requests.get(
        f"{host}/api/2.0/apps/{app_name}",
        headers={"Authorization": f"Bearer {oauth_token}"},
    )
    if resp.status_code == 200:
        data = resp.json()
        return {
            "deployed": True,
            "app_name": app_name,
            "app_url": data.get("url", ""),
            "state": data.get("status", {}).get("state", "UNKNOWN"),
        }
    return {"deployed": False}


def provision_app(host: str, oauth_token: str, email: str) -> dict:
    """Orchestrate the full provisioning flow."""
    app_name = app_name_from_email(email)
    scope_name = f"{app_name}-secrets"
    source_code_path = f"/Workspace/Users/{email}/apps/{app_name}"
    steps = []

    try:
        # Step 1: Mint PAT
        steps.append({"step": 1, "status": "minting_pat", "message": "Creating personal access token..."})
        pat_value = mint_pat(host, oauth_token, app_name)

        # Step 2: Create secret scope
        steps.append({"step": 2, "status": "creating_scope", "message": "Creating secret scope..."})

        # Step 3: Store secret
        steps.append({"step": 3, "status": "storing_secret", "message": "Storing token in secret scope..."})
        store_pat_in_secret_scope(host, oauth_token, app_name, pat_value)

        # Step 4: Upload code
        steps.append({"step": 4, "status": "uploading_code", "message": "Uploading source code to workspace..."})

        # Step 5: Create app
        steps.append({"step": 5, "status": "creating_app", "message": "Creating Databricks App..."})
        app_result = create_app(host, oauth_token, app_name, source_code_path)

        # Step 6: Link secret
        steps.append({"step": 6, "status": "linking_secret", "message": "Linking secret to app..."})
        link_secret_to_app(host, oauth_token, app_name, scope_name, "databricks-token")

        # Step 7: Deploy
        steps.append({"step": 7, "status": "deploying", "message": "Deploying app..."})
        deploy_app(host, oauth_token, app_name, source_code_path)

        app_url = app_result.get("url", f"https://{app_name}.databricksapps.com")
        steps.append({"step": 8, "status": "complete", "app_url": app_url})

        return {"success": True, "steps": steps, "app_url": app_url, "app_name": app_name}

    except Exception as exc:
        current_step = steps[-1]["step"] if steps else 0
        current_status = steps[-1]["status"] if steps else "unknown"
        return {
            "success": False,
            "error": {
                "step": current_step,
                "status": current_status,
                "message": str(exc),
            },
        }


# --- Flask Routes ---


@app.route("/")
def index():
    """Serve the spawner UI showing user email and deploy button."""
    email = request.headers.get("X-Forwarded-Email", "unknown")
    return f"""<!DOCTYPE html>
<html>
<head><title>Coding Agents Spawner</title></head>
<body style="background:#1a1a2e;color:#eee;font-family:sans-serif;text-align:center;padding:60px;">
<h1>Coding Agents Spawner</h1>
<p>Logged in as: <strong>{email}</strong></p>
<button onclick="deploy()" style="padding:16px 48px;font-size:18px;cursor:pointer;background:#4CAF50;color:#fff;border:none;border-radius:8px;">Deploy</button>
<div id="status"></div>
<script>
async function deploy() {{
  const resp = await fetch('/api/provision', {{method:'POST'}});
  const data = await resp.json();
  document.getElementById('status').innerText = JSON.stringify(data, null, 2);
}}
</script>
</body>
</html>"""


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


@app.route("/api/status")
def api_status():
    """Check if user already has a deployed instance."""
    oauth_token = request.headers.get("X-Forwarded-Access-Token", "")
    email = request.headers.get("X-Forwarded-Email", "")
    host = DATABRICKS_HOST

    app_name = app_name_from_email(email)
    result = check_existing_app(host, oauth_token, app_name)
    return jsonify(result)


@app.route("/api/provision", methods=["POST"])
def api_provision():
    """Run the full provisioning flow."""
    oauth_token = request.headers.get("X-Forwarded-Access-Token", "")
    email = request.headers.get("X-Forwarded-Email", "")
    host = DATABRICKS_HOST

    result = provision_app(host, oauth_token, email)
    return jsonify(result)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
