"""Small fakes shared by the graph pipeline unit tests."""

from __future__ import annotations

from typing import Any


class Rows:
    """Stands in for a SQLAlchemy result: ``.mappings().all()``, ``.scalar()``, ``.first()``."""

    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar: Any = None) -> None:
        self._rows = rows or []
        self._scalar = scalar

    def mappings(self) -> Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def one(self) -> dict[str, Any]:
        return self._rows[0]

    def scalar(self) -> Any:
        return self._scalar


class RoutedSession:
    """An AsyncSession whose ``execute`` answers by matching a fragment of the SQL text."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes = routes or {}
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits = 0

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> Rows:
        sql = " ".join(str(statement).split())
        self.statements.append((sql, params or {}))
        for fragment, rows in self.routes.items():
            if fragment in sql:
                return rows(params or {}) if callable(rows) else rows
        return Rows()

    async def commit(self) -> None:
        self.commits += 1

    def begin_nested(self) -> _Savepoint:
        self.savepoints = getattr(self, "savepoints", 0) + 1
        return _Savepoint(self)

    async def rollback(self) -> None:
        return None

    def inserts(self) -> list[dict[str, Any]]:
        return [
            params for sql, params in self.statements if sql.startswith("INSERT INTO graph_outbox")
        ]


class _Savepoint:
    """``async with session.begin_nested():`` that records whether it was rolled back."""

    def __init__(self, session: RoutedSession) -> None:
        self._session = session

    async def __aenter__(self) -> _Savepoint:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            self._session.rolled_back = getattr(self._session, "rolled_back", 0) + 1
        return False  # never swallow: the caller decides
