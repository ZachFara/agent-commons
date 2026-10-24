"""Literal types in `domain` mirror Postgres enums; these tests keep them in sync."""

import psycopg
from psycopg import sql

from commons.domain.reporters import PRIVACY_DEFAULTS

MIRRORS: dict[str, tuple[str, ...]] = {
    "privacy_default": PRIVACY_DEFAULTS,
}


def test_domain_literals_match_postgres_enums(db: psycopg.Connection) -> None:
    for enum, values in MIRRORS.items():
        query = sql.SQL("SELECT unnest(enum_range(NULL::{}))::text").format(sql.Identifier(enum))
        assert tuple(label for (label,) in db.execute(query).fetchall()) == values, enum
