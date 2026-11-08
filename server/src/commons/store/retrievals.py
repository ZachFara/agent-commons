from commons.domain.search import SearchHit, SearchRequest
from commons.store.connection import Connection
from commons.store.searches import Searcher


def record_retrievals(
    conn: Connection, request: SearchRequest, hits: list[SearchHit], searcher: Searcher
) -> None:
    """Log one retrievals row per returned entry, with its rank, score and score factors."""
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO retrievals (entry_id, reporter_id, model_id, task_id, query, "
            "rank_position, score_at_retrieval, relevance, version_factor, outcome_score, "
            "excluded, revision, prompt_version_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    hit.entry_id,
                    searcher.reporter_id,
                    searcher.model_id,
                    request.task_id,
                    request.query,
                    rank,
                    hit.score,
                    hit.relevance,
                    hit.version_factor,
                    hit.outcome_score,
                    searcher.excluded,
                    hit.revision,
                    searcher.prompt_version_id,
                )
                for rank, hit in enumerate(hits, start=1)
            ],
        )
