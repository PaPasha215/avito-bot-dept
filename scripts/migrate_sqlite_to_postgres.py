#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterable

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Connection

from app.models import Base


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate Bot DB data from SQLite to PostgreSQL using SQLAlchemy metadata.",
    )
    parser.add_argument(
        "--src",
        default="sqlite:///./data/app.db",
        help="Source SQLAlchemy URL (default: sqlite:///./data/app.db)",
    )
    parser.add_argument(
        "--dst",
        default=os.getenv("DATABASE_URL", ""),
        help="Destination SQLAlchemy URL (default: DATABASE_URL env)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Truncate destination tables before copying data.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Insert batch size (default: 1000).",
    )
    return parser.parse_args()


def chunked(items: list[dict], size: int) -> Iterable[list[dict]]:
    for idx in range(0, len(items), size):
        yield items[idx : idx + size]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def table_row_count(conn: Connection, table) -> int:
    return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def ensure_destination_ready(dst_engine) -> None:
    Base.metadata.create_all(bind=dst_engine)


def destination_has_rows(dst_conn: Connection) -> bool:
    for table in Base.metadata.sorted_tables:
        if table_row_count(dst_conn, table) > 0:
            return True
    return False


def truncate_destination(dst_conn: Connection, dialect_name: str) -> None:
    table_names = [table.name for table in Base.metadata.sorted_tables]
    if not table_names:
        return

    if dialect_name == "postgresql":
        joined = ", ".join(quote_ident(name) for name in table_names)
        dst_conn.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
        return

    # Generic fallback for non-PostgreSQL targets.
    for name in reversed(table_names):
        dst_conn.execute(text(f"DELETE FROM {quote_ident(name)}"))


def copy_rows(src_conn: Connection, dst_conn: Connection, batch_size: int) -> dict[str, int]:
    copied: dict[str, int] = {}
    for table in Base.metadata.sorted_tables:
        rows = src_conn.execute(select(table)).mappings().all()
        if not rows:
            copied[table.name] = 0
            continue

        payload = [dict(row) for row in rows]
        for chunk in chunked(payload, max(1, batch_size)):
            dst_conn.execute(table.insert(), chunk)
        copied[table.name] = len(payload)
    return copied


def sync_postgres_sequences(dst_conn: Connection) -> None:
    for table in Base.metadata.sorted_tables:
        if "id" not in table.c:
            continue
        table_name = quote_ident(table.name)
        sql = text(
            "SELECT setval("
            "pg_get_serial_sequence(:table_raw, 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table_name}), 1), "
            f"COALESCE((SELECT MAX(id) FROM {table_name}), 0) > 0"
            ")"
        )
        dst_conn.execute(sql, {"table_raw": table.name})


def main() -> int:
    args = parse_args()
    if not args.dst:
        raise SystemExit("Destination URL is empty. Set --dst or DATABASE_URL.")

    src_engine = create_engine(args.src, future=True)
    dst_engine = create_engine(args.dst, future=True)

    src_dialect = src_engine.dialect.name
    dst_dialect = dst_engine.dialect.name
    if src_dialect != "sqlite":
        raise SystemExit(f"Expected sqlite source, got: {src_dialect}")
    if dst_dialect != "postgresql":
        raise SystemExit(f"Expected postgresql destination, got: {dst_dialect}")

    ensure_destination_ready(dst_engine)

    with src_engine.connect() as src_conn, dst_engine.begin() as dst_conn:
        inspector = inspect(src_engine)
        src_tables = set(inspector.get_table_names())
        missing = [table.name for table in Base.metadata.sorted_tables if table.name not in src_tables]
        if missing:
            raise SystemExit(f"Source DB is missing expected tables: {', '.join(missing)}")

        if destination_has_rows(dst_conn):
            if not args.replace:
                raise SystemExit(
                    "Destination DB is not empty. Re-run with --replace to truncate and re-copy.",
                )
            truncate_destination(dst_conn, dst_dialect)

        copied = copy_rows(src_conn, dst_conn, batch_size=args.batch_size)
        sync_postgres_sequences(dst_conn)

    print(
        json.dumps(
            {
                "status": "ok",
                "source": args.src,
                "destination": args.dst,
                "replace": bool(args.replace),
                "copied": copied,
                "total_rows": int(sum(copied.values())),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
