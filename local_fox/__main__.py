"""
Starts the window: python -m local_fox [project folder]

The executable is also started with --tools-server to run the bundled tools' MCP server (see tools_setup.py).
That is checked before anything of the window is imported, so the server starts quickly and without Qt.
"""
import sys

if __name__ == "__main__":
    from local_fox.tools_setup import SERVER_FLAG, run_server
    if sys.argv[1:] == [SERVER_FLAG]:
        sys.exit(run_server())
    from local_fox.ui.app import main
    sys.exit(main())
