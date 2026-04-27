"""Tests that default model references have been updated from old to new names.

RED phase: these should FAIL before the changes are applied.
GREEN phase: these should PASS after all files are updated.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

OLD_MODEL = "databricks-gpt-5-3-codex"
NEW_MODEL = "databricks-gpt-5-5"

# Files that must reference the NEW model and must NOT reference the OLD model
FILES_TO_CHECK = [
    "setup_codex.py",
    "app.yaml",
    "app.yaml.template",
    "setup_opencode.py",
    "README.md",
    "docs/deployment.md",
]


def _file_content(rel_path: str) -> str:
    return (ROOT / rel_path).read_text()


class TestOldModelRemoved:
    """The old model name must not appear in any of the target files."""

    def test_setup_codex_no_old_model(self):
        content = _file_content("setup_codex.py")
        assert OLD_MODEL not in content, f"{OLD_MODEL} still found in setup_codex.py"

    def test_app_yaml_no_old_model(self):
        content = _file_content("app.yaml")
        assert OLD_MODEL not in content, f"{OLD_MODEL} still found in app.yaml"

    def test_app_yaml_template_no_old_model(self):
        content = _file_content("app.yaml.template")
        assert OLD_MODEL not in content, f"{OLD_MODEL} still found in app.yaml.template"

    def test_setup_opencode_no_old_model(self):
        content = _file_content("setup_opencode.py")
        assert OLD_MODEL not in content, f"{OLD_MODEL} still found in setup_opencode.py"

    def test_readme_no_old_model(self):
        content = _file_content("README.md")
        assert OLD_MODEL not in content, f"{OLD_MODEL} still found in README.md"

    def test_deployment_docs_no_old_model(self):
        content = _file_content("docs/deployment.md")
        assert OLD_MODEL not in content, f"{OLD_MODEL} still found in docs/deployment.md"


class TestNewModelPresent:
    """The new model name must appear in each target file."""

    def test_setup_codex_has_new_model(self):
        content = _file_content("setup_codex.py")
        assert NEW_MODEL in content, f"{NEW_MODEL} not found in setup_codex.py"

    def test_app_yaml_has_new_model(self):
        content = _file_content("app.yaml")
        assert NEW_MODEL in content, f"{NEW_MODEL} not found in app.yaml"

    def test_app_yaml_template_has_new_model(self):
        content = _file_content("app.yaml.template")
        assert NEW_MODEL in content, f"{NEW_MODEL} not found in app.yaml.template"

    def test_setup_opencode_has_new_model(self):
        content = _file_content("setup_opencode.py")
        assert NEW_MODEL in content, f"{NEW_MODEL} not found in setup_opencode.py"

    def test_readme_has_new_model(self):
        content = _file_content("README.md")
        assert NEW_MODEL in content, f"{NEW_MODEL} not found in README.md"

    def test_deployment_docs_has_new_model(self):
        content = _file_content("docs/deployment.md")
        assert NEW_MODEL in content, f"{NEW_MODEL} not found in docs/deployment.md"


class TestAppYamlTemplateEntries:
    """app.yaml.template must have both CODEX_MODEL and HERMES_MODEL entries."""

    def test_codex_model_entry(self):
        content = _file_content("app.yaml.template")
        assert re.search(r"name:\s*CODEX_MODEL", content), (
            "CODEX_MODEL env entry missing from app.yaml.template"
        )

    def test_hermes_model_entry(self):
        content = _file_content("app.yaml.template")
        assert re.search(r"name:\s*HERMES_MODEL", content), (
            "HERMES_MODEL env entry missing from app.yaml.template"
        )
