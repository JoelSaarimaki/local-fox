"""
Keeps the mouse wheel from changing dropdowns and number fields. Qt lets the wheel turn a QComboBox or a QSpinBox
under the cursor, so scrolling through the settings changed whatever value the cursor passed over. With the guard,
the wheel scrolls the view the field is in instead; a value changes only by clicking, typing or the arrow keys.
An open dropdown's list still scrolls with the wheel, as it is a list view, not the dropdown itself.
"""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox

GUARDED = (QComboBox, QAbstractSpinBox)

class WheelGuard(QObject):
    """
    An application-wide event filter that passes wheel events over dropdowns and number fields to their parents.
    """
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Wheel and isinstance(watched, GUARDED):
            # Ignored and filtered: the widget never sees it, and Qt hands the event on to its parent (the scroll area)
            event.ignore()
            return True
        return False

def guard_wheel(app: QApplication) -> WheelGuard:
    """
    Installs the guard on the application once and returns it; the application owns it, which keeps it alive.
    """
    guard = app.findChild(WheelGuard)
    if guard is None:
        guard = WheelGuard(app)
        app.installEventFilter(guard)
    return guard
