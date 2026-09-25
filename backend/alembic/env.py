"""Alembic environment: migrations run explicitly against DATABASE_URL."""

from sqlalchemy import create_engine, pool

from alembic import context
from app.config import load_settings
from app.models import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    url = load_settings().database_url.get_secret_value()
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not configured; use a test database")
run_migrations_online()
