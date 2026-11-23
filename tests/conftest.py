"""
Shared fixtures of the tests.

The UI tests create widgets and drop them while the QApplication of their module is still alive. At interpreter
shutdown PySide6 then destroys the QApplication first, which frees every remaining widget's C++ object, and only
after that visits the still-alive Python wrappers, whose destructors free those objects a second time; macOS
aborts on the double free and the test process dies with SIGSEGV/SIGABRT after "QObject: shared QObject was
deleted directly". The session fixture below prevents that: when the session ends, it stops the session loops
of the windows and deletes every widget while it is still valid, deepest first so that no C++ object is freed
twice, which invalidates the wrappers before PySide6's own shutdown runs. Non-widget QObjects are left alone:
the application frees exactly the widgets in its destructor, and deleting e.g. the style here makes the
application's own destructor crash.
"""
import gc

import pytest


@pytest.fixture(scope="session", autouse=True)
def _delete_all_qobjects_before_shutdown():
    yield
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QWidget
    from shiboken6 import delete, isValid

    from local_fox.ui.bridge import AsyncRunner

    # The windows of the tests run an asyncio loop in a thread of their own; stop them before deleting anything
    for runner in [o for o in gc.get_objects() if isinstance(o, AsyncRunner)]:
        runner.stop()
        runner.thread.join(timeout=2)

    def depth(obj: QObject) -> int:
        d, parent = 0, obj.parent()
        while parent is not None:
            d += 1
            parent = parent.parent()
        return d

    # Only widgets: the QApplication's destructor frees exactly those, so their wrappers must be gone by then;
    # other QObjects (the style, models, timers) are either owned by a widget or not freed by the application
    objects = [o for o in gc.get_objects() if isinstance(o, QWidget) and isValid(o)]
    objects.sort(key=depth, reverse=True)  # leaves first: a deleted parent frees its children's C++ objects
    for obj in objects:
        if isValid(obj):  # a parent deleted above has already freed this one
            delete(obj)
    gc.collect()
