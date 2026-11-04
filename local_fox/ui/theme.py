"""
The look of Local Fox: a dark console with a limited palette and a monospace font.
"""
# The palette: every color of the window and the feed is one of these
BG = "#161616"         # Window and feed background
PANEL = "#1d1d1d"      # Bars, the filemap and the input
BORDER = "#2e2e2e"
TEXT = "#d4d4d4"
BRIGHT = "#ececec"     # The user's messages
DIM = "#7c7c7c"        # Secondary text: collapsed entries, notices, thinking
DARK = "#4d4d4d"       # Forbidden files
ACCENT = "#e8913a"     # Fox orange: links, changed files, the prompt mark, what needs attention
CODE = "#e3c48f"       # Light yellow: inline code, and the headings of Markdown in the feed
CODE_BG = "#222222"
EXPANDED = "#1c1c1c"   # The box of an expanded tool call, instructions, filemap or tools in the feed
OK = "#8fbf7f"
ERROR = "#e06c6c"       # Removed lines of an edit; errors in the feed are ACCENT

FONT_FAMILIES = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"]

def stylesheet(font_size: int) -> str:
    """
    Returns the Qt style sheet of the window.
    """
    return f"""
    QWidget {{ background-color: {BG}; color: {TEXT}; font-family: {", ".join(repr(f).replace("'", '"') for f in FONT_FAMILIES)}, monospace; font-size: {font_size}pt; }}
    QLabel {{ background-color: transparent; }}
    QFrame#bar, QFrame#notice {{ background-color: {PANEL}; border: none; }}
    QFrame#notice {{ border-top: 1px solid {ACCENT}; }}
    QTreeView, QPlainTextEdit, QTextBrowser {{ background-color: {BG}; border: 1px solid {BORDER}; selection-background-color: #3a2a1a; }}
    QTreeView {{ background-color: {PANEL}; }}
    QTreeView::item:hover {{ background-color: #262626; }}
    /* A selected file keeps light text, also under the mouse (Qt's highlighted text color would be dark there) */
    QTreeView {{ selection-color: {TEXT}; }}
    QTreeView::item:selected, QTreeView::item:selected:hover {{ background-color: #3a2a1a; color: {TEXT}; }}
    QPlainTextEdit {{ background-color: {PANEL}; padding: 4px; }}
    QPushButton, QToolButton {{ background-color: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; padding: 2px 10px; }}
    QPushButton:hover, QToolButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
    QPushButton:disabled {{ color: {DARK}; }}
    QPushButton#flat, QToolButton#flat {{ border: none; padding: 2px 4px; }}
    QPushButton#primary {{ border-color: {ACCENT}; color: {ACCENT}; }}
    QPushButton#primary:disabled {{ border-color: {BORDER}; color: {DARK}; }}
    QListWidget {{ background-color: {PANEL}; border: 1px solid {BORDER}; }}
    QListWidget::item {{ padding: 3px 2px; }}
    QListWidget::item:selected {{ background-color: #3a2a1a; color: {BRIGHT}; }}
    QListWidget::item:hover {{ background-color: #262626; }}
    QListWidget::item:disabled, QListWidget::item:disabled:hover {{ background-color: transparent; color: {DARK}; }}
    QLabel#dim {{ color: {DIM}; }}
    QLabel#accent {{ color: {ACCENT}; font-weight: bold; }}
    QLabel#accent-text {{ color: {ACCENT}; }}
    QLabel#title {{ color: {ACCENT}; font-weight: bold; font-size: {font_size + 4}pt; }}
    QLabel#version {{ color: {DIM}; font-size: {font_size + 4}pt; }}
    QLabel#error {{ color: {ERROR}; }}
    QLineEdit, QSpinBox, QComboBox {{ background-color: {PANEL}; border: 1px solid {BORDER}; padding: 2px 4px; selection-background-color: #3a2a1a; }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
    QComboBox QAbstractItemView {{ background-color: {PANEL}; border: 1px solid {BORDER}; selection-background-color: #2a2a2a; selection-color: {ACCENT}; }}
    QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {DIM}; background-color: {PANEL}; }}
    QCheckBox::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}
    QTabWidget::pane {{ border: 1px solid {BORDER}; }}
    QTabBar::tab {{ background-color: {PANEL}; color: {DIM}; border: 1px solid {BORDER}; padding: 3px 12px; }}
    QTabBar::tab:selected {{ color: {ACCENT}; border-bottom-color: {ACCENT}; }}
    QMenu {{ background-color: {PANEL}; border: 1px solid {BORDER}; }}
    QMenu::item {{ padding: 4px 32px 4px 14px; }}
    QMenu::item:selected {{ background-color: #2a2a2a; color: {ACCENT}; }}
    QMenu::item:disabled {{ color: {DARK}; }}
    QToolButton::menu-indicator {{ image: none; width: 0px; }}
    QSplitter::handle {{ background-color: {BORDER}; }}
    QScrollBar:vertical {{ background: {BG}; width: 10px; }}
    QScrollBar::handle:vertical {{ background: #333333; min-height: 20px; }}
    QScrollBar:horizontal {{ background: {BG}; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: #333333; min-width: 20px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QToolTip {{ background-color: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; }}
    """

def feed_css() -> str:
    """
    Returns the CSS of the feed's HTML, as far as Qt's rich text supports CSS.
    """
    return f"""
    body {{ color: {TEXT}; }}
    a {{ color: {ACCENT}; text-decoration: none; }}
    h1, h2, h3, h4, h5, h6 {{ color: {CODE}; font-weight: bold; margin-top: 8px; margin-bottom: 4px; }}
    h1 {{ font-size: large; }} h2 {{ font-size: medium; }} h3, h4, h5, h6 {{ font-size: medium; }}
    p {{ margin-top: 2px; margin-bottom: 6px; }}
    code {{ color: {CODE}; background-color: {CODE_BG}; font-family: {", ".join(FONT_FAMILIES)}, monospace; }}
    pre {{ background-color: {CODE_BG}; color: {TEXT}; margin-top: 2px; margin-bottom: 6px; font-family: {", ".join(FONT_FAMILIES)}, monospace; white-space: pre-wrap; }}  /* Long lines wrap instead of running past the feed */
    blockquote {{ color: {DIM}; margin-left: 12px; }}
    table {{ border-collapse: collapse; margin-bottom: 6px; }}
    th {{ color: {CODE}; border: 1px solid {BORDER}; padding: 2px 6px; }}
    td {{ border: 1px solid {BORDER}; padding: 2px 6px; }}
    hr {{ color: {BORDER}; }}
    .user {{ color: {BRIGHT}; }}
    .mark {{ color: {ACCENT}; font-weight: bold; }}
    .dim {{ color: {DIM}; }}
    .thinking {{ color: {DIM}; }}
    .notice {{ color: {DIM}; }}
    .error {{ color: {ACCENT}; }}  /* Errors in the feed are orange, as the rest of what needs the user's attention */
    .ok {{ color: {OK}; }}
    .added {{ color: {OK}; }}
    .removed {{ color: {ERROR}; }}
    .accent {{ color: {ACCENT}; }}
    """
