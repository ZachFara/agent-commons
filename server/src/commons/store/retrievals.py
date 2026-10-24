from uuid import UUID

from commons.domain.search import SearchHit, SearchRequest
from commons.store.connection import Connection


def record_retrievals(
    conn: Connection,
    request: SearchRequest,
    hits: list[SearchHit],
    reporter_id: UUID,
    model_id: UUID,
) -> None:
    """Log one retrievals row per returned entry, with its rank and score at the time."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, "
            "rank_position, score_at_retrieval) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    hit.entry_id,
                    reporter_id,
                    model_id,
                    request.task_id,
                    request.query,
                    rank,
                    hit.score,
                )
                for rank, hit in enumerate(hits, start=1)
            ],
        )
