"""
Tests of the version: it is a semantic version, kept in one place (local_fox.__version__, which pyproject.toml
reads), and the changelog has an entry for it.
"""
import re
import tomllib
from pathlib import Path

from local_fox import __version__

ROOT = Path(__file__).resolve().parent.parent

def test_the_version_is_semantic():
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?", __version__)

def test_pyproject_reads_the_version_from_the_package():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" not in project["project"] and "version" in project["project"]["dynamic"]
    assert project["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "local_fox.__version__"}

def test_the_changelog_has_the_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## {re.escape(__version__)} \(\d{{4}}-\d{{2}}-\d{{2}}\)", changelog, re.M)

def test_unreleased_changes_are_listed_above_the_releases():
    # Work on dev adds its lines under "## Unreleased"; a release renames the heading (see "Versions" in AGENTS.md)
    headings = re.findall(r"^## (.+)$", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.M)
    assert "Unreleased" not in headings[1:]
    assert all(re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)? \(\d{4}-\d{2}-\d{2}\)", h) for h in headings if h != "Unreleased")
