"""Create the database tables and storage folders.

    python -m app.database.init_db

The application also does this on start-up, so running it by hand is optional;
it is useful to verify the configuration before the first start.
"""

from __future__ import annotations

from sqlalchemy import inspect

from app.core.config import get_settings
from app.database.session import get_engine, init_db


def main() -> None:
    settings = get_settings()
    init_db()
    tables = sorted(inspect(get_engine()).get_table_names())
    url = settings.resolved_database_url
    print(f"Database ready: {url.split('@')[-1] if '@' in url else url}")
    print(f"Tables ({len(tables)}): {', '.join(tables)}")
    print(f"Data directory: {settings.data_dir}")


if __name__ == "__main__":
    main()
