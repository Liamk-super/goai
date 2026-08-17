"""Fail closed when user-validation-designer 1.0.4 has undrained executions."""

from __future__ import annotations

import json

from sqlalchemy import text

from launchscope_api.infrastructure.db.session import DatabaseSettings, create_database_engine


def main() -> int:
    settings = DatabaseSettings.from_env()
    engine = create_database_engine(settings.url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            rows = connection.execute(text(
                """
                SELECT status, count(*)
                FROM skill_execution
                WHERE skill_code = 'user-validation-designer'
                  AND skill_version = '1.0.4'
                  AND status IN ('AWAITING_STEP', 'NEEDS_ATTENTION')
                GROUP BY status
                ORDER BY status
                """
            )).all()
    finally:
        engine.dispose()
    counts = {str(status): int(count) for status, count in rows}
    print(json.dumps({"skill_version": "1.0.4", "undrained": counts}, sort_keys=True))
    return 2 if counts else 0


if __name__ == "__main__":
    raise SystemExit(main())
