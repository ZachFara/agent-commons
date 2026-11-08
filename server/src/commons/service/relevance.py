"""Real searches as candidates for the judged relevance set (decision 0025, step 1).

Each counted search becomes one line in the shape of a query in
`server/tests/search_relevance/judged.json`: its (redacted) query, and the entries it is
relevant to, here suggested from what hooks observed. A human checks the suggestions before
anything is added to the set. Nothing is written.
"""

from typing import Any

from commons.domain.errors import InvalidInputError
from commons.store import insights
from commons.store.connection import Connection

DEFAULT_DAYS = 30


def suggested_label(used: str | None) -> str | None:
    """Return a label from observed use: used is relevant, unused possibly not, else None."""
    if used in ("yes", "partial"):
        return "relevant"
    if used == "no":
        return "possibly_irrelevant"
    return None


def candidates(conn: Connection, days: int = DEFAULT_DAYS) -> list[dict[str, Any]]:
    """Return one candidate per counted search of the last `days` days, oldest first."""
    if days < 1:
        msg = "--days must be at least 1."
        raise InvalidInputError(msg)
    found = []
    for row in insights.search_results(conn, days):
        results = [{**hit, "label": suggested_label(hit["used"])} for hit in row["results"]]
        found.append(
            {
                "search_id": str(row["id"]),
                "searched_at": row["created_at"].isoformat(),
                "model": row["model"],
                "query": row["query"],
                "relevant": [hit["entry_id"] for hit in results if hit["label"] == "relevant"],
                "results": results,
            }
        )
    return found
