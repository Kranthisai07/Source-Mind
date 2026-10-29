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

    async def rollback(self) -> None:
        return None

    def inserts(self) -> list[dict[str, Any]]:
        return [
            params for sql, params in self.statements if sql.startswith("INSERT INTO graph_outbox")
        ]
