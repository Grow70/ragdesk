"""Explicit, interactive setup of one demo login; never runs on app startup."""

import argparse
from getpass import getpass

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import load_settings
from app.models import User
from app.services.auth import hash_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a demo login interactively")
    parser.add_argument("--login-name", required=True)
    parser.add_argument("--display-name", required=True)
    args = parser.parse_args(argv)
    login_name = args.login_name.strip()
    display_name = args.display_name.strip()
    if not login_name or len(login_name) > 100:
        parser.error("login name must contain 1-100 characters")
    if not display_name or len(display_name) > 200:
        parser.error("display name must contain 1-200 characters")

    password = getpass("Demo password (at least 12 characters): ")
    repeated = getpass("Repeat demo password: ")
    if password != repeated or len(password) < 12:
        parser.error("passwords must match and contain at least 12 characters")

    settings = load_settings()
    engine = create_engine(settings.database_url.get_secret_value())
    try:
        with Session(engine) as session:
            if session.scalar(select(User.id).where(User.login_name == login_name)):
                parser.error("login name already exists")
            session.add(
                User(
                    login_name=login_name,
                    display_name=display_name,
                    password_hash=hash_password(password),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                parser.error("login name already exists")
    finally:
        engine.dispose()
    print(f"Created demo user: {login_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
