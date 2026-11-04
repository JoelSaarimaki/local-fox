"""
Starts Local Fox: first the project is chosen (the start dialog with the recent projects, and the setup question for
a folder that is not a project yet), and only then the window opens, always with a project. Closing a dialog quits
before anything else starts. A folder given on the command line is opened at once if it is a project; otherwise it
gets the setup question first.
"""
import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import APP_NAME
from .. import config as config_module
from .. import session_log
from . import theme
from .logo import fox_icon
from .main_window import MainWindow
from .project_dialog import choose_project

def folder_argument(argv: list[str]) -> Path | None:
    """
    Returns the folder given on the command line, if any.
    """
    for arg in argv[1:]:
        if not arg.startswith("-") and Path(arg).is_dir():
            return Path(arg)
    return None

def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(fox_icon())  # The dialogs, the window and the taskbar
    session_log.reset(config_module.data_dir() / "logs")  # Every start begins with an empty log
    config_error = None
    try:
        settings = config_module.load()
    except config_module.ConfigError as error:
        settings, config_error = dict(config_module.DEFAULTS), str(error)
    # The dialogs before the window look like it
    font = QFont()
    font.setFamilies(theme.FONT_FAMILIES)
    font.setStyleHint(QFont.Monospace)
    font.setPointSize(settings.get("font_size", 10))
    app.setFont(font)
    theme.keep_selected_text_colors(app)
    app.setStyleSheet(theme.stylesheet(settings.get("font_size", 10)))
    if config_error:
        QMessageBox.warning(None, APP_NAME, f"config.json: {config_error}\nThe defaults are used until it is fixed.")
    chosen = choose_project(None, startup=True, folder=folder_argument(sys.argv))
    if chosen is None:
        return 0  # Closed before a project was chosen: nothing else starts
    project, report = chosen
    window = MainWindow(settings, config_error)
    window.show()
    window.open_project(project, report=report)
    return app.exec()
