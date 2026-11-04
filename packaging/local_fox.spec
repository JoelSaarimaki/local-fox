# PyInstaller spec of the Local Fox executable. Build with build.ps1 (Windows) or build.sh (macOS, Linux) in the
# repository root, or:
#     .venv\Scripts\pyinstaller --noconfirm packaging\local_fox.spec
# The result is dist\LocalFox\LocalFox.exe, a windowed application without a console; on macOS also
# dist/LocalFox.app. It bundles the tools of ollmcp-generic-tools (installed into .venv by the build script), which
# it runs as LocalFox --tools-server.
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

# mcp.cli exits when imported without its extra dependencies. The server side is needed by the bundled tools.
hiddenimports = (collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))
                 + collect_submodules("ollama") + collect_submodules("markdown")
                 # The tools import their toolsets by name (importlib), so PyInstaller cannot find them itself
                 + collect_submodules("ollmcp_tools")
                 + collect_submodules("tree_sitter") + collect_submodules("tree_sitter_javascript")
                 + collect_submodules("tree_sitter_typescript"))
# The templates that the setup copies into a project, and the version shown in the feed
datas = collect_data_files("ollmcp_tools") + copy_metadata("ollmcp-tools")
# Local Fox's own project instructions template, used by the setup instead of the tools' one
datas += [("../local_fox/templates/instructions.md", "local_fox/templates")]

a = Analysis(
    ["../local_fox/__main__.py"],
    pathex=[".."],
    hiddenimports=hiddenimports,
    datas=datas,
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LocalFox",
    console=False,
    upx=False,
    # Written from the logo by the build script (python -m local_fox.ui.logo), so that it always matches it
    icon="../build/local_fox.icns" if sys.platform == "darwin" else "../build/local_fox.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="LocalFox")
if sys.platform == "darwin":
    # An application bundle, which macOS starts from the Finder and the Dock
    app = BUNDLE(coll, name="LocalFox.app", icon="../build/local_fox.icns", bundle_identifier=None,
                 info_plist={"CFBundleName": "Local Fox", "CFBundleDisplayName": "Local Fox",
                             "NSHighResolutionCapable": True})
