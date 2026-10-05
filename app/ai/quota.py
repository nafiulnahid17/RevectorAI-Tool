"""Persistent atomic rolling-window quota for all fallback AI calls."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import time

from app.core.exceptions import EngineError


class FallbackQuota:
    def __init__(
        self,
        path: Path,
        max_calls: int = 2,
        window_hours: int = 24,
        scope: str = "global",
        *,
        clock=time.time,
    ):
        if scope != "global":
            raise EngineError(
                "API_KEY_CONFIGURATION_ERROR",
                "Only global fallback quota scope is implemented",
            )
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_calls = int(max_calls)
        self.window_seconds = int(window_hours) * 3600
        self.window_hours = int(window_hours)
        self.scope = scope
        self.clock = clock
        with self._connect() as conn:
            conn.execute(
                "create table if not exists fallback_usage ("
                "id integer primary key autoincrement, created_at real not null)"
            )
            conn.execute(
                "create index if not exists fallback_usage_created_at "
                "on fallback_usage(created_at)"
            )

    def _connect(self):
        return sqlite3.connect(self.path, timeout=15, isolation_level=None)

    @staticmethod
    def _iso(value: float | None) -> str | None:
        if value is None:
            return None
        return datetime.fromtimestamp(value, timezone.utc).isoformat()

    def _snapshot(self, conn, now: float) -> dict:
        cutoff = now - self.window_seconds
        rows = conn.execute(
            "select created_at from fallback_usage where created_at > ? order by created_at",
            (cutoff,),
        ).fetchall()
        used = len(rows)
        oldest = rows[0][0] if rows else None
        return {
            "limit": self.max_calls,
            "used": used,
            "remaining": max(0, self.max_calls - used),
            "window_hours": self.window_hours,
            "scope": self.scope,
            "oldest_active_usage_timestamp": self._iso(oldest),
            "next_capacity_at": (
                self._iso(oldest + self.window_seconds)
                if oldest is not None and used >= self.max_calls
                else None
            ),
        }

    def status(self) -> dict:
        now = float(self.clock())
        with self._connect() as conn:
            conn.execute("begin immediate")
            conn.execute(
                "delete from fallback_usage where created_at <= ?",
                (now - self.window_seconds,),
            )
            result = self._snapshot(conn, now)
            conn.execute("commit")
        return result

    def reserve(self) -> dict:
        """Atomically consume one fallback allowance immediately before dispatch."""
        now = float(self.clock())
        with self._connect() as conn:
            conn.execute("begin immediate")
            conn.execute(
                "delete from fallback_usage where created_at <= ?",
                (now - self.window_seconds,),
            )
            status = self._snapshot(conn, now)
            if status["remaining"] <= 0:
                conn.execute("commit")
                raise EngineError(
                    "FALLBACK_DAILY_LIMIT_REACHED",
                    "Fallback AI rolling usage limit has been reached",
                    status=429,
                    diagnostics={"fallback_quota": status},
                )
            conn.execute(
                "insert into fallback_usage(created_at) values (?)",
                (now,),
            )
            status = self._snapshot(conn, now)
            conn.execute("commit")
        return status
