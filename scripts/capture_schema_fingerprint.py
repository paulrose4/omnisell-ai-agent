from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs" / "evidence" / "migrations" / "0023_sqlite_schema_fingerprint.json"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sorted_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=_canonical_json)


def _normalize_default(value: Any) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).split())


def _table_schema(connection: Connection, table_name: str) -> dict[str, Any]:
    inspector = inspect(connection)
    columns = []
    for column in inspector.get_columns(table_name):
        columns.append(
            {
                "name": column["name"],
                "type": str(column["type"]),
                "nullable": bool(column["nullable"]),
                "default": _normalize_default(column.get("default")),
                "primary_key": bool(column.get("primary_key")),
            }
        )

    foreign_keys = []
    for foreign_key in inspector.get_foreign_keys(table_name):
        options = foreign_key.get("options") or {}
        foreign_keys.append(
            {
                "name": foreign_key.get("name"),
                "columns": list(foreign_key.get("constrained_columns") or []),
                "referred_schema": foreign_key.get("referred_schema"),
                "referred_table": foreign_key.get("referred_table"),
                "referred_columns": list(foreign_key.get("referred_columns") or []),
                "ondelete": options.get("ondelete"),
                "onupdate": options.get("onupdate"),
            }
        )

    unique_constraints = [
        {
            "name": constraint.get("name"),
            "columns": list(constraint.get("column_names") or []),
        }
        for constraint in inspector.get_unique_constraints(table_name)
    ]
    check_constraints = [
        {
            "name": constraint.get("name"),
            "sqltext": _normalize_default(constraint.get("sqltext")),
        }
        for constraint in inspector.get_check_constraints(table_name)
    ]
    indexes = [
        {
            "name": index.get("name"),
            "columns": list(index.get("column_names") or []),
            "unique": bool(index.get("unique")),
        }
        for index in inspector.get_indexes(table_name)
    ]
    primary_key = inspector.get_pk_constraint(table_name)
    return {
        "name": table_name,
        "columns": columns,
        "primary_key": {
            "name": primary_key.get("name"),
            "columns": list(primary_key.get("constrained_columns") or []),
        },
        "foreign_keys": _sorted_records(foreign_keys),
        "unique_constraints": _sorted_records(unique_constraints),
        "check_constraints": _sorted_records(check_constraints),
        "indexes": _sorted_records(indexes),
    }


def _postgresql_rls_evidence(connection: Connection, table_names: list[str]) -> dict[str, Any]:
    if connection.dialect.name != "postgresql":
        return {
            "verified": False,
            "reason": "SQLite portability evidence does not verify PostgreSQL RLS or role isolation.",
            "tables": [],
            "policies": [],
        }

    table_rows = connection.execute(
        text(
            """
            SELECT c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
              FROM pg_class AS c
              JOIN pg_namespace AS n ON n.oid = c.relnamespace
             WHERE n.nspname = current_schema()
               AND c.relkind = 'r'
               AND c.relname = ANY(:table_names)
             ORDER BY c.relname
            """
        ),
        {"table_names": table_names},
    ).mappings()
    policy_rows = connection.execute(
        text(
            """
            SELECT tablename AS table_name,
                   policyname AS policy_name,
                   cmd,
                   roles,
                   qual,
                   with_check
              FROM pg_policies
             WHERE schemaname = current_schema()
               AND tablename = ANY(:table_names)
             ORDER BY tablename, policyname
            """
        ),
        {"table_names": table_names},
    ).mappings()
    return {
        "verified": True,
        "reason": None,
        "tables": [dict(row) for row in table_rows],
        "policies": [
            {
                **dict(row),
                "roles": list(row["roles"] or []),
            }
            for row in policy_rows
        ],
    }


def _migration_checksums(repository_root: Path) -> list[dict[str, str]]:
    versions_root = repository_root / "alembic" / "versions"
    return [
        {
            "path": path.relative_to(repository_root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(versions_root.glob("*.py"))
    ]


def build_schema_evidence(database_url: str, *, repository_root: Path = REPOSITORY_ROOT) -> dict[str, Any]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            table_names = sorted(
                table_name for table_name in inspector.get_table_names() if table_name != "alembic_version"
            )
            tables = [_table_schema(connection, table_name) for table_name in table_names]
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            rls = _postgresql_rls_evidence(connection, table_names)
            schema_payload = {
                "dialect": connection.dialect.name,
                "revision": revision,
                "tables": tables,
                "rls": rls,
            }
    finally:
        engine.dispose()

    return {
        "format_version": 1,
        "evidence_class": (
            "postgresql_schema_and_rls" if schema_payload["dialect"] == "postgresql" else "sqlite_portability_smoke"
        ),
        "authoritative_for_postgresql_rls": schema_payload["dialect"] == "postgresql",
        "migration_checksums": _migration_checksums(repository_root),
        "schema_sha256": hashlib.sha256(_canonical_json(schema_payload).encode("utf-8")).hexdigest(),
        "schema": schema_payload,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture or verify a deterministic migration schema fingerprint.")
    parser.add_argument(
        "--database-url", default=os.environ.get("MIGRATION_DATABASE_URL") or os.environ.get("DATABASE_URL")
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url, MIGRATION_DATABASE_URL, or DATABASE_URL is required")

    evidence = build_schema_evidence(args.database_url)
    output = args.output.resolve()
    if args.check:
        committed = json.loads(output.read_text(encoding="utf-8"))
        if committed != evidence:
            raise SystemExit(f"Schema fingerprint is stale: {output}")
        print(f"Schema fingerprint matches {output}")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(f"Wrote {evidence['evidence_class']} evidence to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
