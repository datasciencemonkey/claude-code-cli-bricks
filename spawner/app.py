"""Coding Agents Spawner App -- zero-PAT one-click provisioning for any developer.

Auth: Uses the app's own service principal (auto-provisioned by Databricks Apps)
via OAuth M2M. The SP must be a workspace admin to create apps and manage
service principal access. Users no longer need to provide a PAT to the spawner —
they authenticate via SSO (X-Forwarded-Email) and paste their PAT later
directly in their CODA instance.

Owner resolution: The spawner sets `owner:{email}` in the app description field.
CODA's get_token_owner() reads this to determine who owns the app.
"""

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

# OAuth M2M: use the app's own service principal credentials
# These are auto-injected by Databricks Apps runtime.
_SP_CLIENT_ID = os.environ.get("DATABRICKS_CLIENT_ID", "")
_SP_CLIENT_SECRET = os.environ.get("DATABRICKS_CLIENT_SECRET", "")

# Token cache
_oauth_token = None
_oauth_token_expiry = 0
_oauth_lock = threading.Lock()


def get_admin_token() -> str:
    """Get an OAuth token using the app's service principal credentials.

    Tokens are cached and refreshed 60s before expiry.
    Falls back to ADMIN_TOKEN env var if SP credentials are not available.
    """
    global _oauth_token, _oauth_token_expiry

    # Fallback to legacy ADMIN_TOKEN if SP creds not available
    if not _SP_CLIENT_ID or not _SP_CLIENT_SECRET:
        legacy = os.environ.get("ADMIN_TOKEN", "").strip()
        if legacy:
            return legacy
        raise RuntimeError(
            "No SP credentials (DATABRICKS_CLIENT_ID/SECRET) or ADMIN_TOKEN configured"
        )

    with _oauth_lock:
        if _oauth_token and time.time() < _oauth_token_expiry - 60:
            return _oauth_token

        resp = requests.post(
            f"{DATABRICKS_HOST}/oidc/v1/token",
            data={
                "grant_type": "client_credentials",
                "client_id": _SP_CLIENT_ID,
                "client_secret": _SP_CLIENT_SECRET,
                "scope": "all-apis",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _oauth_token = data["access_token"]
        _oauth_token_expiry = time.time() + data.get("expires_in", 3600)
        return _oauth_token


# In-memory provision progress, keyed by app_name
_provision_jobs: dict[str, dict] = {}
_provision_lock = threading.Lock()

# Limit concurrent provisioning to avoid overwhelming the Apps API.
# All threads start immediately but only N can proceed past the semaphore;
# the rest queue up and are released as earlier provisions complete each API call.
_PROVISION_CONCURRENCY = 3
_provision_semaphore = threading.Semaphore(_PROVISION_CONCURRENCY)

MAX_APP_NAME_LENGTH = 63

# UC Volume for offline Python wheel installation
WHEELS_VOLUME_CATALOG = os.environ.get("WHEELS_VOLUME_CATALOG", "main")
WHEELS_VOLUME_SCHEMA = os.environ.get("WHEELS_VOLUME_SCHEMA", "coda")
WHEELS_VOLUME_NAME = os.environ.get("WHEELS_VOLUME_NAME", "coda-wheels")


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

    hash_suffix = hashlib.sha256(slug.encode()).hexdigest()[:6]
    max_slug_len = MAX_APP_NAME_LENGTH - len(prefix) - len(hash_suffix) - 1
    truncated_slug = slug[:max_slug_len].rstrip("-")
    return f"{prefix}{truncated_slug}-{hash_suffix}"


def _ensure_owner_description(
    host: str, admin_token: str, app_name: str, owner_email: str
) -> None:
    """PATCH the app description to owner:{email} on re-provision.

    When an app already exists (409 on create), the description may be missing
    or stale. This ensures the child app can always resolve its owner.
    """
    resp = requests.patch(
        f"{host}/api/2.0/apps/{app_name}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"description": f"owner:{owner_email}"},
    )
    if resp.ok:
        print(f"  Updated app description to owner:{owner_email}")
    else:
        print(f"  Warning: could not update description ({resp.status_code}): {resp.text[:200]}")


def create_app(
    host: str,
    admin_token: str,
    app_name: str,
    owner_email: str,
    max_retries: int = 5,
    base_delay: float = 2.0,
) -> dict:
    """Create the Databricks App via POST /api/2.0/apps.

    The app is created with the admin SP token. Owner identity is stored in the
    description field as 'owner:{email}' so CODA's get_token_owner() can resolve
    it without requiring the user's PAT.

    Retries with exponential backoff on 429/400 responses that are common when
    creating many apps in rapid succession (bulk provisioning).
    """
    headers = {"Authorization": f"Bearer {admin_token}"}
    payload = {"name": app_name, "description": f"owner:{owner_email}"}

    for attempt in range(max_retries):
        resp = requests.post(f"{host}/api/2.0/apps", headers=headers, json=payload)

        # 409 means app already exists -- ensure description has owner:{email}
        if resp.status_code == 409:
            _ensure_owner_description(host, admin_token, app_name, owner_email)
            return check_existing_app(host, admin_token, app_name)

        if resp.ok:
            return resp.json()

        if resp.status_code in (429, 400):
            delay = min(base_delay * (2**attempt), 60)
            print(f"  Create {app_name}: {resp.status_code}, retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)
            continue

        # Non-retryable error — raise immediately
        resp.raise_for_status()

    resp.raise_for_status()
    return resp.json()  # unreachable but keeps linters happy


def wait_for_compute_active(
    host: str,
    oauth_token: str,
    app_name: str,
    timeout: int = 180,
    interval: int = 10,
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
    raise RuntimeError(
        f"Timed out waiting for compute to become ACTIVE after {timeout}s"
    )


def deploy_app(
    host: str,
    oauth_token: str,
    app_name: str,
    source_code_path: str,
    max_retries: int = 5,
    base_delay: float = 2.0,
) -> dict:
    """Deploy the app via POST /api/2.0/apps/{name}/deployments.

    Retries with exponential backoff on 429 (rate limit) and 400 (transient
    API errors common during bulk provisioning).
    """
    headers = {"Authorization": f"Bearer {oauth_token}"}
    last_resp = None
    for attempt in range(max_retries):
        resp = requests.post(
            f"{host}/api/2.0/apps/{app_name}/deployments",
            headers=headers,
            json={"source_code_path": source_code_path},
        )
        if resp.ok:
            return resp.json()
        last_resp = resp
        if resp.status_code in (429, 400):
            delay = min(base_delay * (2**attempt), 60)
            print(f"  Deploy {app_name}: {resp.status_code}, retrying in {delay:.0f}s "
                  f"(attempt {attempt + 1}/{max_retries})")
            time.sleep(delay)
            continue
        # Non-retryable error
        break
    raise RuntimeError(f"{last_resp.status_code} from deploy API: {last_resp.text}")


def _grant_with_retry(
    host: str,
    headers: dict,
    securable_type: str,
    full_name: str,
    privileges: list[str],
    sp_name: str,
    max_retries: int = 6,
    base_delay: float = 5.0,
) -> bool:
    """Grant UC permissions, retrying if the principal doesn't exist yet.

    After app creation the service principal can take 10-30s to propagate into
    Unity Catalog's identity store. Retries with exponential backoff (5s, 10s,
    20s, 40s, 60s, 60s) for up to ~3 minutes.
    """
    for attempt in range(max_retries):
        resp = requests.patch(
            f"{host}/api/2.1/unity-catalog/permissions/{securable_type}/{full_name}",
            headers=headers,
            json={"changes": [{"add": privileges, "principal": sp_name}]},
        )
        if resp.ok:
            print(f"  Granted {privileges} on {securable_type} {full_name} to {sp_name}")
            return True

        # Retry only on PRINCIPAL_DOES_NOT_EXIST — the SP hasn't propagated yet
        if resp.status_code == 404 and "PRINCIPAL_DOES_NOT_EXIST" in resp.text:
            delay = min(base_delay * (2**attempt), 60)
            print(
                f"  SP not propagated yet, retrying in {delay:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})..."
            )
            time.sleep(delay)
            continue

        # Any other error is not retryable
        print(f"  Warning: grant failed ({resp.status_code}): {resp.text[:200]}")
        return False

    print(f"  Error: SP {sp_name} not found after {max_retries} retries, skipping grant")
    return False


def grant_sp_volume_access(host: str, auth_token: str, app_result: dict) -> None:
    """Grant the app's SP read access to the coda-wheels UC Volume.

    Uses the Unity Catalog permissions API to grant USE CATALOG, USE SCHEMA,
    and READ_VOLUME to the child app's service principal.
    """
    sp_name = app_result.get("service_principal_name", "")
    if not sp_name:
        return

    catalog = WHEELS_VOLUME_CATALOG
    schema = WHEELS_VOLUME_SCHEMA
    volume = WHEELS_VOLUME_NAME
    headers = {
        "Authorization": f"Bearer {auth_token}",
        "Content-Type": "application/json",
    }

    grants = [
        ("catalog", catalog, ["USE_CATALOG"]),
        ("schema", f"{catalog}.{schema}", ["USE_SCHEMA"]),
        ("volume", f"{catalog}.{schema}.{volume}", ["READ_VOLUME"]),
    ]

    for securable_type, full_name, privileges in grants:
        _grant_with_retry(host, headers, securable_type, full_name, privileges, sp_name)


def list_spawned_apps(host: str, oauth_token: str) -> list:
    """List all coding-agents apps (excluding the spawner itself)."""
    resp = requests.get(
        f"{host}/api/2.0/apps",
        headers={"Authorization": f"Bearer {oauth_token}"},
    )
    resp.raise_for_status()
    all_apps = resp.json().get("apps", [])

    result = []
    seen_names = set()
    for a in all_apps:
        name = a["name"]
        if not name.startswith("coding-agents-") or name == "coding-agents-spawner":
            continue
        seen_names.add(name)
        job = _provision_jobs.get(name)
        if job and job["status"] == "in_progress":
            last_step = job["steps"][-1] if job["steps"] else {}
            state = f"PROVISIONING: {last_step.get('message', '...')}"
        else:
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
        result.append(
            {
                "name": name,
                "url": a.get("url", ""),
                "creator": a.get("creator", ""),
                "state": state,
                "compute": a.get("compute_status", {}).get("state", "UNKNOWN"),
                "created": a.get("create_time", ""),
            }
        )

    # Include in-flight jobs not yet in the API
    for name, job in _provision_jobs.items():
        if name not in seen_names and job["status"] == "in_progress":
            last_step = job["steps"][-1] if job["steps"] else {}
            result.append(
                {
                    "name": name,
                    "url": "",
                    "creator": job.get("email", ""),
                    "state": f"PROVISIONING: {last_step.get('message', '...')}",
                    "compute": "PENDING",
                    "created": "",
                }
            )

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


def provision_app_async(host: str, admin_token: str, email: str, app_name: str):
    """Run provisioning in a background thread, updating _provision_jobs as it goes.

    Zero-PAT flow: uses the admin SP token for all operations. The user's email
    (from SSO) is stored in the app description for owner resolution.

    Uses _provision_semaphore to limit concurrency — when called from bulk
    provisioning, threads wait their turn instead of all hitting the API at once.
    """
    source_code_path = "/Workspace/Shared/apps/coding-agents"

    with _provision_semaphore:
        try:
            # Step 1: Create app (with owner in description)
            _add_step(app_name, 1, "creating_app", f"Creating app '{app_name}'...")
            app_result = create_app(host, admin_token, app_name, email)
            sp_client_id = app_result.get("service_principal_client_id", "")

            # Step 2: Grant SP access to UC Volume (for offline wheel install)
            if sp_client_id:
                _add_step(
                    app_name, 2, "granting_access", "Granting service principal access..."
                )
                grant_sp_volume_access(host, admin_token, app_result)

            # Step 3: Wait for compute
            _add_step(
                app_name,
                3,
                "waiting_for_compute",
                "Waiting for compute to be ready (60-90s)...",
            )
            wait_for_compute_active(host, admin_token, app_name)

            # Step 4: Deploy
            _add_step(app_name, 4, "deploying", "Deploying app...")
            deploy_app(host, admin_token, app_name, source_code_path)

            # Step 5: Wait for app to be running
            _add_step(app_name, 5, "starting", "Waiting for app to start...")
            _wait_for_app_running(host, admin_token, app_name)

            app_url = app_result.get("url", app_result.get("app_url", ""))
            _add_step(app_name, 6, "complete", "App is running!")
            _update_job(app_name, status="complete", app_url=app_url)

        except Exception as exc:
            _add_step(app_name, -1, "error", str(exc))
            _update_job(app_name, status="error", error=str(exc))


def _wait_for_app_running(
    host: str, token: str, app_name: str, timeout: int = 300, interval: int = 10
):
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

    email = (request.headers.get("X-Forwarded-Email") or "unknown").lower()
    app_name = app_name_from_email(email) if email != "unknown" else "coding-agents-you"

    index_path = os.path.join(os.path.dirname(__file__), "static", "index.html")
    with open(index_path) as f:
        page = f.read()

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
    email = (request.headers.get("X-Forwarded-Email") or "").lower()
    host = DATABRICKS_HOST

    app_name = app_name_from_email(email)
    result = check_existing_app(host, get_admin_token(), app_name)
    return jsonify(result)


@app.route("/api/apps")
def api_list_apps():
    """List all spawned coding-agents apps (with in-flight provision status merged)."""
    host = DATABRICKS_HOST
    try:
        admin_token = get_admin_token()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500
    apps = list_spawned_apps(host, admin_token)
    return jsonify({"apps": apps})


@app.route("/api/provision", methods=["POST"])
def api_provision():
    """Start provisioning in background. No PAT required — uses SSO identity.

    Returns immediately with app_name to poll via /api/provision-status/<app_name>.
    """
    host = DATABRICKS_HOST

    try:
        admin_token = get_admin_token()
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 500

    # Identity: use email from POST body (admin provisioning for another user),
    # fall back to SSO header (self-provisioning)
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or request.headers.get("X-Forwarded-Email", "")).strip().lower()
    if not email:
        return jsonify({"success": False, "error": "No user identity provided"}), 400

    app_name = app_name_from_email(email)

    # Check if already running
    existing = check_existing_app(host, admin_token, app_name)
    if existing.get("deployed") and existing.get("state") == "RUNNING":
        return jsonify(
            {
                "success": True,
                "app_name": app_name,
                "app_url": existing.get("app_url", ""),
                "already_running": True,
            }
        )

    # Check if already provisioning
    with _provision_lock:
        existing_job = _provision_jobs.get(app_name)
        if existing_job and existing_job["status"] == "in_progress":
            return jsonify(
                {"success": True, "app_name": app_name, "already_in_progress": True}
            )

        _provision_jobs[app_name] = {
            "steps": [
                {
                    "step": 0,
                    "status": "starting",
                    "message": f"Provisioning for {email}...",
                }
            ],
            "status": "in_progress",
            "app_url": "",
            "app_name": app_name,
            "email": email,
        }

    thread = threading.Thread(
        target=provision_app_async,
        args=(host, admin_token, email, app_name),
        daemon=True,
    )
    thread.start()

    return jsonify({"success": True, "app_name": app_name})


@app.route("/api/provision-bulk", methods=["POST"])
def api_provision_bulk():
    """Provision apps for multiple users with throttled concurrency.

    Accepts {"emails": ["a@example.com", "b@example.com", ...]}.
    Registers all jobs immediately and returns the list to poll.
    Threads are spawned per user but gated by _provision_semaphore
    (max 3 concurrent), with exponential backoff on API errors.

    Unlike the single-user endpoint, we skip the per-email check_existing_app
    call to avoid 54+ sequential API calls blocking the HTTP response.
    Each provisioning thread handles existing apps via create_app (409 → re-use).
    """
    try:
        admin_token = get_admin_token()
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 500

    body = request.get_json(silent=True) or {}
    emails = body.get("emails", [])
    if not emails or not isinstance(emails, list):
        return jsonify({"success": False, "error": "Provide a list of emails"}), 400

    host = DATABRICKS_HOST
    results = []

    for email in emails:
        email = email.strip().lower()
        if not email:
            continue
        app_name = app_name_from_email(email)

        # Skip if already being provisioned in this session
        with _provision_lock:
            existing_job = _provision_jobs.get(app_name)
            if existing_job and existing_job["status"] == "in_progress":
                results.append({"email": email, "app_name": app_name, "status": "already_in_progress"})
                continue

            _provision_jobs[app_name] = {
                "steps": [{"step": 0, "status": "queued", "message": "Queued for provisioning..."}],
                "status": "in_progress",
                "app_url": "",
                "app_name": app_name,
                "email": email,
            }

        thread = threading.Thread(
            target=provision_app_async,
            args=(host, admin_token, email, app_name),
            daemon=True,
        )
        thread.start()
        results.append({"email": email, "app_name": app_name, "status": "started"})

    return jsonify({"success": True, "apps": results})


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


def _deploy_with_backoff(
    host: str,
    headers: dict,
    app_name: str,
    source_code_path: str,
    max_retries: int = 5,
    base_delay: float = 2.0,
) -> requests.Response:
    """Deploy a single app with exponential backoff on 429/400 responses."""
    for attempt in range(max_retries):
        resp = requests.post(
            f"{host}/api/2.0/apps/{app_name}/deployments",
            headers=headers,
            json={"source_code_path": source_code_path},
        )
        if resp.status_code not in (429, 400):
            return resp
        delay = min(base_delay * (2**attempt), 60)
        print(f"  {resp.status_code} deploying {app_name}, retrying in {delay:.0f}s "
              f"(attempt {attempt + 1}/{max_retries})")
        time.sleep(delay)
    return resp  # Return last error response if all retries exhausted


# Max concurrent deploy requests to avoid hitting API rate limits
_REDEPLOY_BATCH_SIZE = 3
_REDEPLOY_BATCH_DELAY = 2.0  # seconds between batches


def redeploy_all_apps(host: str, admin_token: str):
    """Redeploy all coding-agents-* apps from the shared template.

    Deploys in batches of 3 with a delay between batches to avoid API
    rate limiting (429). Individual deploys retry with exponential backoff.
    """
    global _redeploy_job
    source_code_path = "/Workspace/Shared/apps/coding-agents"
    headers = {"Authorization": f"Bearer {admin_token}"}

    try:
        resp = requests.get(f"{host}/api/2.0/apps", headers=headers)
        resp.raise_for_status()
        all_apps = resp.json().get("apps", [])
        targets = [
            a
            for a in all_apps
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
                deploy_resp = _deploy_with_backoff(
                    host, headers, name, source_code_path
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

            # Pause between batches to stay under rate limits
            if (i + 1) % _REDEPLOY_BATCH_SIZE == 0 and i + 1 < len(targets):
                time.sleep(_REDEPLOY_BATCH_DELAY)

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

    try:
        admin_token = get_admin_token()
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 500

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
        args=(DATABRICKS_HOST, admin_token),
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
