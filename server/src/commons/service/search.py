from dataclasses import replace

from commons.domain.search import SearchHit, SearchRequest, validate_search
from commons.privacy.redaction import scrub
from commons.service.identity import current_model, current_reporter
from commons.store.connection import Connection
from commons.store.retrievals import record_retrievals
from commons.store.search import find_entries


def search(conn: Connection, request: SearchRequest, model: str | None) -> list[SearchHit]:
    """Find entries for the request and log a retrieval for each one returned.

    The query is matched as given but logged with secrets redacted.
    """
    validate_search(request)
    with conn.transaction():
        reporter = current_reporter(conn)
        model_row = current_model(conn, model)
        hits = find_entries(conn, request)
        logged = replace(request, query=scrub(request.query))
        record_retrievals(conn, logged, hits, reporter_id=reporter, model_id=model_row)
    return hits


def preview(conn: Connection, request: SearchRequest) -> list[SearchHit]:
    """Return what `search` would, without logging retrievals (for humans at the CLI)."""
    validate_search(request)
    return find_entries(conn, request)
