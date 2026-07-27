"""A bounded single-writer queue for SQLite."""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class _Write:
    operation: Callable[[sqlite3.Connection], Any]
    future: concurrent.futures.Future


class SQLiteWriteWorker:
    def __init__(self, path: Path, max_queue_size: int = 2048) -> None:
        self.path = Path(path)
        self._queue = queue.Queue(maxsize=max(1, int(max_queue_size)))
        self._thread = threading.Thread(
            target=self._run, name="groupmate-sqlite-writer", daemon=True
        )
        self._closed = False
        self._thread.start()

    def submit(
        self, operation: Callable[[sqlite3.Connection], Any]
    ) -> concurrent.futures.Future:
        if self._closed:
            raise RuntimeError("SQLite writer is closed")
        future = concurrent.futures.Future()
        self._queue.put(_Write(operation, future))
        return future

    def execute(self, operation: Callable[[sqlite3.Connection], Any]) -> Any:
        return self.submit(operation).result()

    async def execute_async(
        self, operation: Callable[[sqlite3.Connection], Any]
    ) -> Any:
        loop = asyncio.get_event_loop()
        future = await loop.run_in_executor(None, self.submit, operation)
        return await asyncio.wrap_future(future)

    def flush(self) -> None:
        self.execute(lambda db: None)

    async def flush_async(self) -> None:
        await self.execute_async(lambda db: None)

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._closed = True
        self._queue.put(None)
        self._thread.join()

    def _run(self) -> None:
        db = sqlite3.connect(str(self.path))
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            while True:
                item: Optional[_Write] = self._queue.get()
                try:
                    if item is None:
                        return
                    if item.future.set_running_or_notify_cancel():
                        try:
                            with db:
                                result = item.operation(db)
                        except BaseException as exc:
                            item.future.set_exception(exc)
                        else:
                            item.future.set_result(result)
                finally:
                    self._queue.task_done()
        finally:
            db.close()
