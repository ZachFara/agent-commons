"""Measure search relevance on the judged set (decision 0025).

Usage: uv run python tools/search_eval.py [--admin-url URL]  (or `make search-eval`)

Loads server/tests/search_relevance/judged.json into a throwaway database, runs every query
through the shipped search, and prints precision@5, recall@5, how often a query with a
relevant entry got nothing, and how often a query with none got something. Every change to
matching or ranking reports these numbers in its PR, and ships only if neither precision@5
nor recall@5 drops.
"""

import argparse
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

from commons.domain.search import SearchRequest
from commons.store.connection import Connection
from commons.store.migrate import apply_migrations
from commons.store.models import model_id
from commons.store.search import find_entries

JUDGED = Path(__file__).parents[1] / "server/tests/search_relevance/judged.json"
EVAL_DB = "commons_search_eval"
TOP = 5


@dataclass(frozen=True)
class Metrics:
    """Search quality on the judged set."""

    precision_at_5: float  # relevant share of what came back, over queries that got anything
    recall_at_5: float  # share of answerable queries whose entry was in the top 5
    empty_when_answerable: float  # share of answerable queries that got nothing
    noise_on_negatives: float  # share of unanswerable queries that got something
    queries: int

    def lines(self) -> list[str]:
        """Return the metrics as printable lines."""
        return [
            f"queries               {self.queries}",
            f"precision@5           {self.precision_at_5:.3f}",
            f"recall@5              {self.recall_at_5:.3f}",
            f"empty when answerable {self.empty_when_answerable:.3f}",
            f"noise on negatives    {self.noise_on_negatives:.3f}",
        ]


def load_judged(conn: Connection, path: Path = JUDGED) -> dict[str, uuid.UUID]:
    """Insert the judged entries as approved; return {judged id: entry id}."""
    data = json.loads(path.read_text(encoding="utf-8"))
    row = conn.execute(
        "INSERT INTO reporters (pseudonym) VALUES ('search-eval') "
        "ON CONFLICT (pseudonym) DO UPDATE SET pseudonym = EXCLUDED.pseudonym RETURNING id"
    ).fetchone()
    if row is None:
        msg = "could not create the search-eval reporter"
        raise RuntimeError(msg)
    model = model_id(conn, "search-eval")
    ids = {}
    for entry in data["entries"]:
        inserted = conn.execute(
            "INSERT INTO entries (symptom, environment, cause, fix, domains, status, created_by, "
            "created_by_model) VALUES (%s, %s, %s, %s, %s, 'approved', %s, %s) RETURNING id",
            (
                entry["symptom"],
                Jsonb(entry["environment"]),
                entry["cause"],
                entry["fix"],
                entry["domains"],
                row[0],
                model,
            ),
        ).fetchone()
        if inserted is None:
            msg = f"could not insert {entry['id']}"
            raise RuntimeError(msg)
        ids[entry["id"]] = inserted[0]
    return ids


def evaluate(conn: Connection, ids: dict[str, uuid.UUID], path: Path = JUDGED) -> Metrics:
    """Run every judged query through search and score the results."""
    data = json.loads(path.read_text(encoding="utf-8"))
    judged: list[tuple[str, uuid.UUID | None]] = [
        (q, ids[e["id"]]) for e in data["entries"] for q in e["queries"]
    ]
    judged += [(q, None) for q in data["negatives"]]
    ours = set(ids.values())
    precisions: list[float] = []
    found = empty = noisy = 0
    for query, relevant in judged:
        request = SearchRequest(query, uuid.uuid4(), None, TOP)
        hits = [h.entry_id for h in find_entries(conn, request) if h.entry_id in ours]
        if hits:
            precisions.append(sum(h == relevant for h in hits) / len(hits))
        if relevant is None:
            noisy += bool(hits)
        else:
            found += relevant in hits
            empty += not hits
    answerable = sum(1 for _, relevant in judged if relevant is not None)
    return Metrics(
        precision_at_5=sum(precisions) / len(precisions) if precisions else 0.0,
        recall_at_5=found / answerable,
        empty_when_answerable=empty / answerable,
        noise_on_negatives=noisy / len(data["negatives"]),
        queries=len(judged),
    )


def main() -> None:
    """Build the throwaway database, load the set, and print the metrics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-url", default="postgresql://commons:commons@localhost:5432/commons"
    )
    admin_url: str = parser.parse_args().admin_url
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(EVAL_DB)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(EVAL_DB)))
    with psycopg.connect(make_conninfo(admin_url, dbname=EVAL_DB)) as conn:
        apply_migrations(conn)
        ids = load_judged(conn)
        for line in evaluate(conn, ids).lines():
            print(line)


if __name__ == "__main__":
    main()
