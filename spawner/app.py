"""Coding Agents Spawner App -- one-click provisioning of coding-agents for any developer."""

import hashlib
import os
import threading
import time

import requests
from flask import Flask, jsonify, request

app = Flask(__name__, static_folder="static")

_raw_host = os.environ.get("DATABRICKS_HOST", "")
DATABRICKS_HOST = (
    _raw_host if _raw_host.startswith("https://") else f"https://{_raw_host}"
).rstrip("/")

# Admin token for provisioning operations (secret scope, app creation, etc.)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()

# In-memory provision progress, keyed by app_name
# Each entry: {"steps": [...], "status": "in_progress"|"complete"|"error", "app_url": "", "app_name": ""}
_provision_jobs: dict[str, dict] = {}
_provision_lock = threading.Lock()


MAX_APP_NAME_LENGTH = 63

def app_name_from_email(email: str) -> str:
    """Derive app name from user email: david.okeeffe@company.com -> coding-agents-david-okeeffe.

    Databricks app names are limited to 63 characters. If the derived name exceeds
    this limit, the slug is truncated and a short hash suffix is appended to
    preserve uniqueness.
    """
    prefix = "coding-agents-"
    username = email.split("@")[0]
    slug = username.replace(".", "-").replace("_", "-").lower()
    full_name = f"{prefix}{slug}"

    if len(full_name) <= MAX_APP_NAME_LENGTH:
        return full_name

    # Truncate slug and append 6-char hash for uniqueness
    hash_suffix = hashlib.sha256(slug.encode()).hexdigest()[:6]
    max_slug_len = MAX_APP_NAME_LENGTH - len(prefix) - len(hash_suffix) - 1  # -1 for separator
    truncated_slug = slug[:max_slug_len].rstrip("-")
    return f"{prefix}{truncated_slug}-{hash_suffix}"


def resolve_pat_owner(host: str, pat: str) -> str:
    """Call /api/2.0/preview/scim/v2/Me to get the email of the PAT owner."""
    resp = requests.get(
        f"{host}/api/2.0/preview/scim/v2/Me",
        headers={"Authorization": f"Bearer {pat}"},
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("userName", "")


def store_pat_in_secret_scope(
    host: str, oauth_token: str, app_name: str, pat_value: str, secret_key: str
) -> dict:
    """Create secret scope (handle 409) and store PAT with unique key."""
    scope_name = f"{app_name}-secrets"
    headers = {"Authorization": f"Bearer {oauth_token}"}

    # Create scope -- 409 means it already exists, which is fine
    scope_resp = requests.post(
        f"{host}/api/2.0/secrets/scopes/create",
        headers=headers,
        json={"scope": scope_name},
    )
    if scope_resp.status_code not in (200, 409) and "ALREADY_EXISTS" not in scope_resp.text:
        raise RuntimeError(f"Failed to create secret scope: {scope_resp.status_code} {scope_resp.text}")

    # Store the PAT with unique key
    put_resp = requests.post(
        f"{host}/api/2.0/secrets/put",
        headers=headers,
        json={
            "scope": scope_name,
            "key": secret_key,
            "string_value": pat_value,
        },
    )
    if put_resp.status_code != 200:
        raise RuntimeError(f"Failed to store secret: {put_resp.status_code} {put_resp.text}")

    return {"success": True, "scope": scope_name, "key": secret_key}


def create_app(host: str, oauth_token: str, app_name: str, scope_name: str, secret_key: str) -> dict:
    """Create the Databricks App with secret resource via POST /api/2.0/apps."""
    resp = requests.post(
        f"{host}/api/2.0/apps",
        headers={"Authorization": f"Bearer {oauth_token}"},
        json={
            "name": app_name,
            "resources": [
                {
                    "name": "DATABRICKS_TOKEN",
                    "description": "PAT for model serving access",
                    "secret": {
                        "scope": scope_name,
                        "key": secret_key,
                        "permission": "READ",
                    },
                }
            ],
        },
    )
    # 409 means app already exists -- that's fine for re-provisioning
    if resp.status_code == 409:
        return check_existing_app(host, oauth_token, app_name)
    resp.raise_for_status()
    return resp.json()



def wait_for_compute_active(
    host: str, oauth_token: str, app_name: str, timeout: int = 180, interval: int = 10,
) -> None:
    """Poll until compute_status reaches ACTIVE (required before first deploy)."""
    headers = {"Authorization": f"Bearer {oauth_token}"}
    elapsed = 0
    while elapsed < timeout:
        resp = requests.get(f"{host}/api/2.0/apps/{app_name}", headers=headers)
        if resp.ok:
            compute = resp.json().get("compute_status", {}).get("state", "")
            if compute == "ACTIVE":
                return
        time.sleep(interval)
        elapsed += interval
    raise RuntimeError(f"Timed out waiting for compute to become ACTIVE after {timeout}s")


def deploy_app(
    host: str, oauth_token: str, app_name: str, source_code_path: str,
) -> dict:
    """Deploy the app via POST /api/2.0/apps/{name}/deployments."""
    resp = requests.post(
        f"{host}/api/2.0/apps/{app_name}/deployments",
        headers={"Authorization": f"Bearer {oauth_token}"},
        json={"source_code_path": source_code_path},
    )
    if not resp.ok:
        raise RuntimeError(f"{resp.status_code} from deploy API: {resp.text}")
    return resp.json()


def grant_sp_secret_access(
    host: str, auth_token: str, scope_name: str, sp_id: str
) -> None:
    """Grant the app's service principal READ access on the secret scope."""
    resp = requests.post(
        f"{host}/api/2.0/secrets/acls/put",
        headers={"Authorization": f"Bearer {auth_token}"},
        json={
            "scope": scope_name,
            "principal": sp_id,
            "permission": "READ",
        },
    )
    resp.raise_for_status()


def list_spawned_apps(host: str, oauth_token: str) -> list:
    """List all coding-agents apps (excluding the spawner itself)."""
    resp = requests.get(
        f"{host}/api/2.0/apps",
        headers={"Authorization": f"Bearer {oauth_token}"},
    )
    resp.raise_for_status()
    all_apps = resp.json().get("apps", [])

    # Merge live API state with any in-flight provision jobs
    result = []
    seen_names = set()
    for a in all_apps:
        name = a["name"]
        if not name.startswith("coding-agents-") or name == "coding-agents-spawner":
            continue
        seen_names.add(name)
        # If there's an in-flight job, overlay its status
        job = _provision_jobs.get(name)
        if job and job["status"] == "in_progress":
            last_step = job["steps"][-1] if job["steps"] else {}
            state = f"PROVISIONING: {last_step.get('message', '...')}"
        else:
            # List endpoint lacks app_status — derive from compute + deployment
            compute = a.get("compute_status", {}).get("state", "")
            deploy = a.get("active_deployment", {}).get("status", {}).get("state", "")
            if compute == "ACTIVE" and deploy == "SUCCEEDED":
                state = "RUNNING"
            elif deploy == "IN_PROGRESS":
                state = "DEPLOYING"
            elif compute == "ACTIVE":
                state = "DEPLOYED"
            elif not a.get("active_deployment"):
                state = "NOT DEPLOYED"
            else:
                state = compute or "UNKNOWN"
        result.append({
            "name": name,
            "url": a.get("url", ""),
            "creator": a.get("creator", ""),
            "state": state,
            "compute": a.get("compute_status", {}).get("state", "UNKNOWN"),
            "created": a.get("create_time", ""),
        })

    # Include in-flight jobs that haven't appeared in the API yet (app not created yet)
    for name, job in _provision_jobs.items():
        if name not in seen_names and job["status"] == "in_progress":
            last_step = job["steps"][-1] if job["steps"] else {}
            result.append({
                "name": name,
                "url": "",
                "creator": job.get("email", ""),
                "state": f"PROVISIONING: {last_step.get('message', '...')}",
                "compute": "PENDING",
                "created": "",
            })

    return result


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
            "state": data.get("app_status", {}).get("state", "UNKNOWN"),
            "service_principal_id": data.get("service_principal_id"),
            "service_principal_client_id": data.get("service_principal_client_id"),
            "service_principal_name": data.get("service_principal_name"),
        }
    return {"deployed": False}


def _update_job(app_name: str, **kwargs):
    """Thread-safe update of a provision job's state."""
    with _provision_lock:
        if app_name in _provision_jobs:
            _provision_jobs[app_name].update(kwargs)


def _add_step(app_name: str, step: int, status: str, message: str):
    """Thread-safe append of a step to a provision job."""
    entry = {"step": step, "status": status, "message": message}
    with _provision_lock:
        if app_name in _provision_jobs:
            _provision_jobs[app_name]["steps"].append(entry)


def provision_app_async(host: str, admin_token: str, pat_value: str, app_name: str):
    """Run provisioning in a background thread, updating _provision_jobs as it goes."""
    source_code_path = "/Workspace/Shared/apps/coding-agents"

    try:
        scope_name = f"{app_name}-secrets"
        secret_key = "databricks-token"

        # Step 1: Store secret
        _add_step(app_name, 1, "storing_secret", "Storing token in secret scope...")
        store_pat_in_secret_scope(host, admin_token, app_name, pat_value, secret_key)

        # Step 2: Create app
        _add_step(app_name, 2, "creating_app", f"Creating app '{app_name}'...")
        app_result = create_app(host, pat_value, app_name, scope_name, secret_key)
        sp_client_id = app_result.get("service_principal_client_id", "")

        # Step 3: Grant SP access
        if sp_client_id:
            _add_step(app_name, 3, "granting_access", "Granting service principal access...")
            grant_sp_secret_access(host, admin_token, scope_name, sp_client_id)

        # Step 4: Wait for compute
        _add_step(app_name, 4, "waiting_for_compute", "Waiting for compute to be ready (60-90s)...")
        wait_for_compute_active(host, admin_token, app_name)

        # Step 5: Deploy
        _add_step(app_name, 5, "deploying", "Deploying app...")
        deploy_app(host, admin_token, app_name, source_code_path)

        # Step 6: Wait for app to be running
        _add_step(app_name, 6, "starting", "Waiting for app to start...")
        _wait_for_app_running(host, admin_token, app_name)

        app_url = app_result.get("url", app_result.get("app_url", ""))
        _add_step(app_name, 7, "complete", "App is running!")
        _update_job(app_name, status="complete", app_url=app_url)

    except Exception as exc:
        _add_step(app_name, -1, "error", str(exc))
        _update_job(app_name, status="error", error=str(exc))


def _wait_for_app_running(host: str, token: str, app_name: str, timeout: int = 300, interval: int = 10):
    """Poll until app_status reaches RUNNING."""
    headers = {"Authorization": f"Bearer {token}"}
    elapsed = 0
    while elapsed < timeout:
        resp = requests.get(f"{host}/api/2.0/apps/{app_name}", headers=headers)
        if resp.ok:
            state = resp.json().get("app_status", {}).get("state", "")
            if state == "RUNNING":
                return
        time.sleep(interval)
        elapsed += interval
    raise RuntimeError(f"Timed out waiting for app to reach RUNNING after {timeout}s")


# --- Flask Routes ---


@app.route("/")
def index():
    """Serve the spawner UI with user context injected via data attributes."""
    import html as html_mod

    email = request.headers.get("X-Forwarded-Email", "unknown")
    app_name = app_name_from_email(email) if email != "unknown" else "coding-agents-you"

    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(index_path) as f:
        page = f.read()

    # Inject user context as data attributes on <body>
    page = page.replace(
        "<body>",
        f'<body data-email="{html_mod.escape(email)}" data-app-name="{html_mod.escape(app_name)}">',
    )
    return page


@app.route("/health")
def health():
    """Health check endpoint."""
    return jsonify({"status": "ok"})


@app.route("/api/status")
def api_status():
    """Check if user already has a deployed instance."""
    email = request.headers.get("X-Forwarded-Email", "")
    host = DATABRICKS_HOST

    app_name = app_name_from_email(email)
    result = check_existing_app(host, ADMIN_TOKEN, app_name)
    return jsonify(result)


@app.route("/api/apps")
def api_list_apps():
    """List all spawned coding-agents apps (with in-flight provision status merged)."""
    host = DATABRICKS_HOST
    if not ADMIN_TOKEN:
        return jsonify({"error": "Admin token not configured"}), 500
    apps = list_spawned_apps(host, ADMIN_TOKEN)
    return jsonify({"apps": apps})


@app.route("/api/provision", methods=["POST"])
def api_provision():
    """Start provisioning in background. Returns immediately with app_name to poll."""
    host = DATABRICKS_HOST

    if not ADMIN_TOKEN:
        return jsonify({"success": False, "error": "Spawner admin token not configured"}), 500

    body = request.get_json(silent=True) or {}
    pat_value = body.get("pat", "").strip()

    if not pat_value:
        return jsonify({"success": False, "error": "PAT is required"}), 400

    # Resolve identity synchronously (fast) so we can return the app_name
    try:
        email = resolve_pat_owner(host, pat_value)
        if not email:
            raise ValueError("Could not resolve PAT owner identity")
    except Exception as exc:
        return jsonify({"success": False, "error": f"Invalid PAT: {exc}"}), 400

    app_name = app_name_from_email(email)

    # Check if already running — just refresh token
    existing = check_existing_app(host, ADMIN_TOKEN, app_name)
    if existing.get("deployed") and existing.get("state") == "RUNNING":
        store_pat_in_secret_scope(host, ADMIN_TOKEN, app_name, pat_value, "databricks-token")
        return jsonify({
            "success": True,
            "app_name": app_name,
            "app_url": existing.get("app_url", ""),
            "already_running": True,
        })

    # Check if already provisioning
    with _provision_lock:
        existing_job = _provision_jobs.get(app_name)
        if existing_job and existing_job["status"] == "in_progress":
            return jsonify({"success": True, "app_name": app_name, "already_in_progress": True})

        # Initialize job tracker
        _provision_jobs[app_name] = {
            "steps": [{"step": 0, "status": "resolving_user", "message": "Identity verified, starting provision..."}],
            "status": "in_progress",
            "app_url": "",
            "app_name": app_name,
            "email": email,
        }

    # Kick off background thread
    thread = threading.Thread(
        target=provision_app_async,
        args=(host, ADMIN_TOKEN, pat_value, app_name),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True, "app_name": app_name})


@app.route("/api/provision-status/<app_name>")
def api_provision_status(app_name):
    """Poll endpoint for provision progress."""
    with _provision_lock:
        job = _provision_jobs.get(app_name)
    if not job:
        return jsonify({"found": False})
    return jsonify({"found": True, **job})


# In-memory redeploy-all job tracker
_redeploy_job: dict | None = None
_redeploy_lock = threading.Lock()


def redeploy_all_apps(host: str, admin_token: str):
    """Redeploy all coding-agents-* apps from the shared template."""
    global _redeploy_job
    source_code_path = "/Workspace/Shared/apps/coding-agents"
    headers = {"Authorization": f"Bearer {admin_token}"}

    try:
        # List all coding-agents apps
        resp = requests.get(f"{host}/api/2.0/apps", headers=headers)
        resp.raise_for_status()
        all_apps = resp.json().get("apps", [])
        targets = [
            a for a in all_apps
            if a["name"].startswith("coding-agents-")
            and a["name"] != "coding-agents-spawner"
        ]

        with _redeploy_lock:
            _redeploy_job["total"] = len(targets)
            _redeploy_job["apps"] = [
                {"name": a["name"], "status": "pending"} for a in targets
            ]

        for i, a in enumerate(targets):
            name = a["name"]
            with _redeploy_lock:
                _redeploy_job["apps"][i]["status"] = "deploying"
                _redeploy_job["completed"] = i

            try:
                deploy_resp = requests.post(
                    f"{host}/api/2.0/apps/{name}/deployments",
                    headers=headers,
                    json={"source_code_path": source_code_path},
                )
                if deploy_resp.ok:
                    with _redeploy_lock:
                        _redeploy_job["apps"][i]["status"] = "deployed"
                else:
                    with _redeploy_lock:
                        _redeploy_job["apps"][i]["status"] = "error"
                        _redeploy_job["apps"][i]["error"] = deploy_resp.text[:200]
            except Exception as exc:
                with _redeploy_lock:
                    _redeploy_job["apps"][i]["status"] = "error"
                    _redeploy_job["apps"][i]["error"] = str(exc)[:200]

        with _redeploy_lock:
            _redeploy_job["completed"] = len(targets)
            _redeploy_job["status"] = "complete"

    except Exception as exc:
        with _redeploy_lock:
            _redeploy_job["status"] = "error"
            _redeploy_job["error"] = str(exc)


@app.route("/api/redeploy-all", methods=["POST"])
def api_redeploy_all():
    """Trigger redeployment of all spawned coding-agents apps from the shared template."""
    global _redeploy_job

    if not ADMIN_TOKEN:
        return jsonify({"error": "Admin token not configured"}), 500

    with _redeploy_lock:
        if _redeploy_job and _redeploy_job.get("status") == "in_progress":
            return jsonify({"error": "Redeploy already in progress"}), 409

        _redeploy_job = {
            "status": "in_progress",
            "total": 0,
            "completed": 0,
            "apps": [],
            "error": None,
            "started_at": time.time(),
        }

    thread = threading.Thread(
        target=redeploy_all_apps,
        args=(DATABRICKS_HOST, ADMIN_TOKEN),
        daemon=True,
    )
    thread.start()
    return jsonify({"success": True})


@app.route("/api/redeploy-all/status")
def api_redeploy_all_status():
    """Poll endpoint for redeploy-all progress."""
    with _redeploy_lock:
        if not _redeploy_job:
            return jsonify({"active": False})
        return jsonify({"active": True, **_redeploy_job})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8001)
