from __future__ import annotations

import os

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from api.config import get_settings


def _psycopg_url(value: str) -> str:
    url = make_url(value)
    if not url.drivername.startswith("postgresql"):
        raise ValueError("PostgreSQL is required for the application RLS role")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def _role_password_statement(command: str, app_user: str, app_password: str) -> sql.Composed:
    normalized_command = command.upper()
    if normalized_command not in {"CREATE", "ALTER"}:
        raise ValueError("command must be CREATE or ALTER")
    return sql.SQL("{} ROLE {} LOGIN PASSWORD {} NOBYPASSRLS").format(
        sql.SQL(normalized_command),
        sql.Identifier(app_user),
        sql.Literal(app_password),
    )


def main() -> None:
    settings = get_settings()
    admin_url = settings.alembic_database_url
    app_user = os.environ.get("DATABASE_APP_USER", "omnisell_app")
    app_password = os.environ.get("DATABASE_APP_PASSWORD", "")
    if not app_password:
        raise ValueError("DATABASE_APP_PASSWORD is required")

    with psycopg.connect(_psycopg_url(admin_url), autocommit=True) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (app_user,))
        if cursor.fetchone() is None:
            cursor.execute(_role_password_statement("CREATE", app_user, app_password))
        else:
            cursor.execute(_role_password_statement("ALTER", app_user, app_password))
        cursor.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(connection.info.dbname), sql.Identifier(app_user)
            )
        )
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app_user)))
        cursor.execute(
            sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
                sql.Identifier(app_user)
            )
        )
        cursor.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(sql.Identifier(app_user))
        )
        cursor.execute(
            sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
            ).format(sql.Identifier(app_user))
        )
        cursor.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {}").format(
                sql.Identifier(app_user)
            )
        )

    print(f"configured PostgreSQL RLS application role: {app_user}")


if __name__ == "__main__":
    main()
