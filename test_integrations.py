#!/usr/bin/env python
"""Integration tests for OpenCode and Gemini CLI setup scripts.

Tests config file generation, CLI installation, and endpoint configuration.
Run with: python test_integrations.py

For live API tests, set DATABRICKS_HOST and DATABRICKS_TOKEN environment variables.
"""
import os
import json
import subprocess
import sys
import tempfile
from pathlib import Path

# Test configuration
TEST_HOST = os.environ.get("DATABRICKS_HOST", "https://test-workspace.cloud.databricks.com")
TEST_TOKEN = os.environ.get("DATABRICKS_TOKEN", "dapi-test-token-12345")
LIVE_MODE = bool(os.environ.get("DATABRICKS_HOST") and os.environ.get("DATABRICKS_TOKEN"))

passed = 0
failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name}" + (f" - {detail}" if detail else ""))


def section(name):
    print(f"\n{'=' * 60}")
    print(f"  {name}")
    print(f"{'=' * 60}")


# ==========================================
# 1. CLI Installation Tests
# ==========================================
section("CLI Installation")

opencode_result = subprocess.run(["which", "opencode"], capture_output=True, text=True)
test("OpenCode CLI is installed", opencode_result.returncode == 0,
     f"not found (expected in PATH)")

gemini_result = subprocess.run(["which", "gemini"], capture_output=True, text=True)
test("Gemini CLI is installed", gemini_result.returncode == 0,
     f"not found (expected in PATH)")

opencode_ver = subprocess.run(["opencode", "--version"], capture_output=True, text=True)
test("OpenCode CLI runs", opencode_ver.returncode == 0 and opencode_ver.stdout.strip(),
     f"version: {opencode_ver.stdout.strip()}")

gemini_ver = subprocess.run(["gemini", "--version"], capture_output=True, text=True)
test("Gemini CLI runs", gemini_ver.returncode == 0 and gemini_ver.stdout.strip(),
     f"version: {gemini_ver.stdout.strip()}")


# ==========================================
# 2. Setup Script Tests
# ==========================================
section("Setup Script Execution")

# Run setup scripts with test/real credentials
env = {**os.environ, "DATABRICKS_HOST": TEST_HOST, "DATABRICKS_TOKEN": TEST_TOKEN}

opencode_setup = subprocess.run(
    [sys.executable, "setup_opencode.py"],
    capture_output=True, text=True, env=env,
    cwd="/home/user/claude-code-cli-bricks"
)
test("setup_opencode.py runs successfully", opencode_setup.returncode == 0,
     opencode_setup.stderr if opencode_setup.returncode != 0 else "")

gemini_setup = subprocess.run(
    [sys.executable, "setup_gemini.py"],
    capture_output=True, text=True, env=env,
    cwd="/home/user/claude-code-cli-bricks"
)
test("setup_gemini.py runs successfully", gemini_setup.returncode == 0,
     gemini_setup.stderr if gemini_setup.returncode != 0 else "")


# ==========================================
# 3. OpenCode Config Validation
# ==========================================
section("OpenCode Configuration")

home = Path(os.environ.get("HOME", "/root"))
opencode_config_path = home / ".config" / "opencode" / "opencode.json"

test("OpenCode config file exists", opencode_config_path.exists())

if opencode_config_path.exists():
    config = json.loads(opencode_config_path.read_text())

    # Provider config
    test("Databricks provider defined",
         "databricks" in config.get("provider", {}))

    db_provider = config.get("provider", {}).get("databricks", {})
    test("Uses @ai-sdk/openai-compatible",
         db_provider.get("npm") == "@ai-sdk/openai-compatible")

    base_url = db_provider.get("options", {}).get("baseURL", "")
    test("Base URL points to /serving-endpoints",
         base_url.endswith("/serving-endpoints"),
         f"got: {base_url}")
    test("Base URL contains workspace host",
         TEST_HOST.replace("https://", "") in base_url,
         f"got: {base_url}")

    api_key = db_provider.get("options", {}).get("apiKey", "")
    test("API key uses env var reference",
         api_key == "{env:DATABRICKS_TOKEN}",
         f"got: {api_key}")

    # Models
    models = db_provider.get("models", {})
    test("Claude model defined", "databricks-claude-sonnet-4-5" in models)
    test("Gemini Flash model defined", "databricks-gemini-2-5-flash" in models)
    test("Gemini Pro model defined", "databricks-gemini-2-5-pro" in models)
    test("Llama model defined", "databricks-meta-llama-3-3-70b-instruct" in models)

    # Default model
    test("Default model set",
         config.get("model", "").startswith("databricks/"),
         f"got: {config.get('model')}")

    # Verify models visible to opencode
    models_result = subprocess.run(
        ["opencode", "models", "databricks"],
        capture_output=True, text=True,
        env={**os.environ, "DATABRICKS_TOKEN": TEST_TOKEN}
    )
    if models_result.returncode == 0:
        output = models_result.stdout.strip()
        test("OpenCode lists Gemini Flash model",
             "databricks-gemini-2-5-flash" in output, output)
        test("OpenCode lists Claude model",
             "databricks-claude-sonnet-4-5" in output, output)


# ==========================================
# 4. Gemini CLI Config Validation
# ==========================================
section("Gemini CLI Configuration")

gemini_env_path = home / ".gemini" / ".env"
gemini_settings_path = home / ".gemini" / "settings.json"

test("Gemini .env file exists", gemini_env_path.exists())
test("Gemini settings.json exists", gemini_settings_path.exists())

if gemini_env_path.exists():
    env_content = gemini_env_path.read_text()
    test("GOOGLE_GEMINI_BASE_URL set",
         "GOOGLE_GEMINI_BASE_URL=" in env_content)
    test("Base URL contains /serving-endpoints/google",
         "/serving-endpoints/google" in env_content,
         f"content: {env_content.strip()}")
    test("GEMINI_API_KEY set",
         "GEMINI_API_KEY=" in env_content)
    test("Bearer auth mechanism configured",
         "GEMINI_API_KEY_AUTH_MECHANISM=bearer" in env_content)

    # Check permissions
    import stat
    mode = gemini_env_path.stat().st_mode
    test(".env file has restricted permissions",
         not (mode & stat.S_IROTH) and not (mode & stat.S_IWOTH),
         f"mode: {oct(mode)}")

if gemini_settings_path.exists():
    settings = json.loads(gemini_settings_path.read_text())
    test("Auth type set to api-key",
         settings.get("selectedAuthType") == "api-key")


# ==========================================
# 5. Live API Tests (only with real credentials)
# ==========================================
if LIVE_MODE:
    section("Live API Tests (Databricks)")

    # Test OpenAI-compatible endpoint with curl
    import urllib.request
    import urllib.error

    # Test OpenCode endpoint (OpenAI-compatible)
    openai_url = f"{TEST_HOST}/serving-endpoints/chat/completions"
    try:
        req = urllib.request.Request(
            openai_url,
            data=json.dumps({
                "model": "databricks-gemini-2-5-flash",
                "messages": [{"role": "user", "content": "Say hello in one word."}],
                "max_tokens": 10
            }).encode(),
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json"
            }
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        test("OpenAI-compatible endpoint works (Gemini Flash)",
             bool(content), f"response: {content[:100]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        test("OpenAI-compatible endpoint works (Gemini Flash)",
             False, f"HTTP {e.code}: {body[:200]}")
    except Exception as e:
        test("OpenAI-compatible endpoint works (Gemini Flash)",
             False, str(e))

    # Test Gemini-native endpoint
    gemini_url = f"{TEST_HOST}/serving-endpoints/google/v1beta/models/gemini-2.5-flash:generateContent"
    try:
        req = urllib.request.Request(
            gemini_url,
            data=json.dumps({
                "contents": [{"role": "user", "parts": [{"text": "Say hello in one word."}]}],
                "generationConfig": {"maxOutputTokens": 10}
            }).encode(),
            headers={
                "Authorization": f"Bearer {TEST_TOKEN}",
                "Content-Type": "application/json"
            }
        )
        resp = urllib.request.urlopen(req, timeout=30)
        data = json.loads(resp.read())
        content = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        test("Gemini-native endpoint works (/serving-endpoints/google)",
             bool(content), f"response: {content[:100]}")
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        test("Gemini-native endpoint works (/serving-endpoints/google)",
             False, f"HTTP {e.code}: {body[:200]}")
    except Exception as e:
        test("Gemini-native endpoint works (/serving-endpoints/google)",
             False, str(e))

else:
    section("Live API Tests (SKIPPED - no credentials)")
    print("  Set DATABRICKS_HOST and DATABRICKS_TOKEN to run live tests")


# ==========================================
# Summary
# ==========================================
print(f"\n{'=' * 60}")
print(f"  Results: {passed} passed, {failed} failed, {passed + failed} total")
print(f"{'=' * 60}")

sys.exit(1 if failed > 0 else 0)
