"""SQLite database executor for benchmark query testing."""

import sqlite3
from pathlib import Path

from model import QueryResult


class SQLiteDatabaseExecutor:
    """Executes SQL queries against a SQLite database (e.g., sakila.db)."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def execute(self, query: str) -> QueryResult:
        """Execute a SQL query and return rows/columns in QueryResult format."""
        try:
            resolved_path = Path(self.db_path)
            if not resolved_path.exists():
                return QueryResult(
                    rows=[],
                    columns=[],
                    succeeded=False,
                    error_message=f"Database file not found: {self.db_path}",
                )

            with sqlite3.connect(resolved_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query)

                # Non-SELECT statements can succeed without result rows.
                if cursor.description is None:
                    return QueryResult(rows=[], columns=[], succeeded=True)

                columns = [col[0] for col in cursor.description]
                rows = [dict(row) for row in cursor.fetchall()]

                return QueryResult(
                    rows=rows,
                    columns=columns,
                    succeeded=True,
                )

        except Exception as e:
            return QueryResult(
                rows=[],
                columns=[],
                succeeded=False,
                error_message=str(e),
            )


# Backward-compatible name used by existing imports.
class MockDatabaseExecutor(SQLiteDatabaseExecutor):
    """Compatibility wrapper to keep existing imports unchanged."""

    def __init__(self, db_path: str):
        super().__init__(db_path)
