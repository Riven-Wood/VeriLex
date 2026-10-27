import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


_OPEN_RETRY_DELAYS_SECONDS = (0.01, 0.05)


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(
        self, *, foreign_keys: bool = True
    ) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        for attempt in range(len(_OPEN_RETRY_DELAYS_SECONDS) + 1):
            try:
                connection = sqlite3.connect(self.path)
                connection.row_factory = sqlite3.Row
                if foreign_keys:
                    connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA busy_timeout = 5000")
                break
            except sqlite3.OperationalError as error:
                if connection is not None:
                    with suppress(sqlite3.Error):
                        connection.close()
                retryable = str(error).lower() == "unable to open database file"
                if not retryable or attempt >= len(_OPEN_RETRY_DELAYS_SECONDS):
                    raise
                time.sleep(_OPEN_RETRY_DELAYS_SECONDS[attempt])

        if connection is None:
            raise RuntimeError("unreachable")

        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()
