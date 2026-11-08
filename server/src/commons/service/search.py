from dataclasses import replace

from commons.domain.prompts import PromptVersion
from commons.domain.search import SearchHit, SearchRequest, validate_search
from commons.privacy.redaction import scrub
from commons.privacy.search_log import search_record
from commons.service.identity import current_model, current_reporter, test_traffic
from commons.service.rates import limit
from commons.store.connection import Connection
from commons.store.prompt_versions import prompt_version_id
from commons.store.retrievals import record_retrievals
from commons.store.search import find_entries
from commons.store.searches import Searcher, privacy_mode, record_search


def search(
    conn: Connection,
    request: SearchRequest,
    model: str | None,
    *,
    prompt: PromptVersion | None = None,
) -> list[SearchHit]:
    """Find entries for the request, log the search, and log a retrieval for each one returned.

    The query is matched as given but logged with secrets redacted. Searches that find
    nothing are logged too, so the store's gaps show (decision 0024). `prompt` is the text the
    MCP server served; the search and its retrievals point at it (decision MIT-566).
    """
    validate_search(request)
    with conn.transaction():
        limit(conn, "search")
        reporter = current_reporter(conn)
        searcher = Searcher(
            reporter_id=reporter,
            model_id=current_model(conn, model),
            prompt_version_id=prompt_version_id(conn, prompt),
            excluded=test_traffic(),  # a test key's searches never count (decision 0030)
        )
        hits = find_entries(conn, request)
        mode = privacy_mode(conn, reporter, request.task_id)
        record_search(conn, search_record(request, hits, mode), searcher)
        logged = replace(request, query=scrub(request.query))
        record_retrievals(conn, logged, hits, searcher)
    return hits


def preview(conn: Connection, request: SearchRequest) -> list[SearchHit]:
    """Return what `search` would, without logging anything (for humans at the CLI)."""
    validate_search(request)
    return find_entries(conn, request)
