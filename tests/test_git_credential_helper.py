"""Tests for AC-9 and AC-10: Git Credential Helper.

AC-9: A git credential helper script is written to ~/.local/bin/git-credential-databricks.
      ~/.gitconfig includes [credential] helper = <path>.
      It reads DATABRICKS_TOKEN from env and returns it as password.

AC-10: The credential helper implements the git credential helper protocol:
       - "get": reads stdin key=value pairs, writes username/password to stdout
       - "store"/"erase": exits silently
"""

import os
import stat
import subprocess
from unittest.mock import patch, MagicMock
import pytest


def _mock_setup_git_config(tmp_path, monkeypatch):
    """Call _setup_git_config with WorkspaceClient mocked out."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DATABRICKS_HOST", "https://test.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")

    mock_me = MagicMock()
    mock_me.user_name = "test@example.com"
    mock_me.display_name = "Test User"
    mock_client = MagicMock()
    mock_client.current_user.me.return_value = mock_me

    with patch("databricks.sdk.WorkspaceClient", return_value=mock_client):
        from app import _setup_git_config

        _setup_git_config()


class TestCredentialHelperSetup:
    """AC-9: Git credential helper is created during setup."""

    def test_setup_git_config_creates_credential_helper(self, tmp_path, monkeypatch):
        """_setup_git_config creates the git-credential-databricks script."""
        _mock_setup_git_config(tmp_path, monkeypatch)
        helper_path = tmp_path / ".local" / "bin" / "git-credential-databricks"
        assert helper_path.exists(), f"Credential helper not found at {helper_path}"

    def test_credential_helper_is_executable(self, tmp_path, monkeypatch):
        """The credential helper script has executable permissions."""
        _mock_setup_git_config(tmp_path, monkeypatch)
        helper_path = tmp_path / ".local" / "bin" / "git-credential-databricks"
        file_stat = os.stat(helper_path)
        assert file_stat.st_mode & stat.S_IXUSR, "Script is not executable by owner"

    def test_gitconfig_references_credential_helper(self, tmp_path, monkeypatch):
        """~/.gitconfig contains [credential] section pointing to the helper."""
        _mock_setup_git_config(tmp_path, monkeypatch)
        gitconfig_path = tmp_path / ".gitconfig"
        content = gitconfig_path.read_text()
        assert "[credential]" in content, "Missing [credential] section in .gitconfig"
        assert "git-credential-databricks" in content, (
            "Missing helper reference in .gitconfig"
        )

    def test_credential_helper_reads_token_from_env(self, tmp_path, monkeypatch):
        """The helper script references DATABRICKS_TOKEN env var (not hardcoded)."""
        _mock_setup_git_config(tmp_path, monkeypatch)
        helper_path = tmp_path / ".local" / "bin" / "git-credential-databricks"
        script_content = helper_path.read_text()
        assert "DATABRICKS_TOKEN" in script_content, (
            "Script should read DATABRICKS_TOKEN from environment, not hardcode it"
        )


class TestCredentialHelperProtocol:
    """AC-10: The credential helper implements git credential helper protocol."""

    @pytest.fixture
    def helper_script(self, tmp_path, monkeypatch):
        """Set up the credential helper and return its path."""
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token_secret")
        _mock_setup_git_config(tmp_path, monkeypatch)
        return tmp_path / ".local" / "bin" / "git-credential-databricks"

    def test_get_returns_username_and_password(self, helper_script):
        """'get' action returns username=token and password=<DATABRICKS_TOKEN>."""
        result = subprocess.run(
            [str(helper_script), "get"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            env={**os.environ, "DATABRICKS_TOKEN": "dapi_test_token_secret"},
            timeout=5,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "username=" in output, f"No username in output: {output}"
        assert "password=dapi_test_token_secret" in output, (
            f"No password with correct token in output: {output}"
        )

    def test_get_works_for_any_host(self, helper_script):
        """Credential helper is not scoped -- works for any HTTPS host."""
        for host in ["github.com", "gitlab.com", "dev.azure.com", "bitbucket.org"]:
            result = subprocess.run(
                [str(helper_script), "get"],
                input=f"protocol=https\nhost={host}\n\n",
                capture_output=True,
                text=True,
                env={**os.environ, "DATABRICKS_TOKEN": "dapi_test_token_secret"},
                timeout=5,
            )
            assert result.returncode == 0
            assert "password=dapi_test_token_secret" in result.stdout, (
                f"Failed for host {host}"
            )

    def test_store_exits_silently(self, helper_script):
        """'store' action exits with 0 and no output."""
        result = subprocess.run(
            [str(helper_script), "store"],
            input="protocol=https\nhost=github.com\nusername=x\npassword=y\n\n",
            capture_output=True,
            text=True,
            env={**os.environ, "DATABRICKS_TOKEN": "dapi_test_token_secret"},
            timeout=5,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_erase_exits_silently(self, helper_script):
        """'erase' action exits with 0 and no output."""
        result = subprocess.run(
            [str(helper_script), "erase"],
            input="protocol=https\nhost=github.com\nusername=x\npassword=y\n\n",
            capture_output=True,
            text=True,
            env={**os.environ, "DATABRICKS_TOKEN": "dapi_test_token_secret"},
            timeout=5,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_git_token_preferred_for_matching_host(self, helper_script):
        """GIT_TOKEN is used when GIT_TOKEN_HOST matches the requested host."""
        result = subprocess.run(
            [str(helper_script), "get"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GIT_TOKEN": "ghp_enterprise",
                "GIT_TOKEN_HOST": "github.com",
                "DATABRICKS_TOKEN": "dapi_fallback",
            },
            timeout=5,
        )
        assert result.returncode == 0
        assert "password=ghp_enterprise" in result.stdout

    def test_git_token_not_used_for_non_matching_host(self, helper_script):
        """GIT_TOKEN is skipped when host doesn't match GIT_TOKEN_HOST; falls back to DATABRICKS_TOKEN."""
        result = subprocess.run(
            [str(helper_script), "get"],
            input="protocol=https\nhost=dev.azure.com\n\n",
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "GIT_TOKEN": "ghp_enterprise",
                "GIT_TOKEN_HOST": "github.com",
                "DATABRICKS_TOKEN": "dapi_fallback",
            },
            timeout=5,
        )
        assert result.returncode == 0
        assert "password=dapi_fallback" in result.stdout

    def test_git_token_without_host_filter_applies_to_all(self, helper_script):
        """GIT_TOKEN without GIT_TOKEN_HOST applies to all hosts."""
        for host in ["github.com", "gitlab.com", "dev.azure.com"]:
            result = subprocess.run(
                [str(helper_script), "get"],
                input=f"protocol=https\nhost={host}\n\n",
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "GIT_TOKEN": "ghp_universal",
                    "DATABRICKS_TOKEN": "dapi_should_not_use",
                },
                timeout=5,
            )
            assert result.returncode == 0
            assert "password=ghp_universal" in result.stdout, f"Failed for {host}"

    def test_get_with_no_token_fails_gracefully(self, helper_script):
        """If DATABRICKS_TOKEN is unset, 'get' exits non-zero or returns empty."""
        env = {k: v for k, v in os.environ.items() if k != "DATABRICKS_TOKEN"}
        result = subprocess.run(
            [str(helper_script), "get"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )
        # Either exits non-zero or returns no password line
        if result.returncode == 0:
            assert "password=" not in result.stdout or "password=\n" in result.stdout
