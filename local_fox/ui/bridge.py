"""
The bridge between the window and the session: the session runs in an asyncio event loop in a thread of its
own, and tells the window what changed through Qt signals, which Qt delivers in the window's thread.
"""
import asyncio
import threading

from PySide6.QtCore import QObject, Signal

from ..conversation import Entry
from ..session import Frontend

class AsyncRunner:
    """
    An asyncio event loop in a background thread.
    """

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="local-fox-session", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        """
        Runs a coroutine in the loop and returns its concurrent.futures.Future.
        """
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, fn, *args) -> None:
        """
        Calls a function in the loop's thread.
        """
        self.loop.call_soon_threadsafe(fn, *args)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)

class Bridge(QObject):
    reset = Signal()
    entry_changed = Signal(int, bool)          # Entry id, new
    status = Signal()
    decision = Signal(str, list, int, object)  # Question, options, entry id (0 for none), the future to resolve
    decision_done = Signal(object)             # The future that was resolved or cancelled
    invoke = Signal(object)                    # A function to call in the window's thread

    def __init__(self):
        super().__init__()
        self.invoke.connect(lambda fn: fn())

class QtFrontend(Frontend):
    """
    The session's frontend in the window: every callback is turned into a signal.
    """

    def __init__(self, bridge: Bridge):
        self.bridge = bridge

    def reset(self) -> None:
        self.bridge.reset.emit()

    def changed(self, entry: Entry, new: bool) -> None:
        self.bridge.entry_changed.emit(entry.id, new)

    def status(self) -> None:
        self.bridge.status.emit()

    async def decide(self, question: str, options: list[str], entry: Entry | None) -> str:
        future = asyncio.get_running_loop().create_future()
        self.bridge.decision.emit(question, options, entry.id if entry else 0, future)
        try:
            return await future
        finally:
            self.bridge.decision_done.emit(future)
