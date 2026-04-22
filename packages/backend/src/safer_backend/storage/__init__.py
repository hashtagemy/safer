"""SQLite storage layer (WAL mode, append-only events)."""

from .db import get_db, init_db

__all__ = ["get_db", "init_db"]
