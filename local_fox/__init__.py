"""
Local Fox: a harness for local AI models run with Ollama and the MCP tools of ollmcp-generic-tools.

The core (config, session, MCP servers, filemap, export) has no UI dependencies, so that it can run from
the command line (local_fox.cli) and from the comparison scripts. The window is in local_fox.ui.
"""
APP_NAME = "Local Fox"
# The one place of the version (semantic versioning; see "Versions" in AGENTS.md): pyproject.toml, the
# executable's version resource and the macOS bundle read it from here
__version__ = "1.1.0"
