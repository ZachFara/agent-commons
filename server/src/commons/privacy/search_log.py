"""What the store keeps about a search in each privacy mode (DESIGN.md §6, decision 0024)."""

from commons.domain.reporters import PrivacyDefault
from commons.domain.search import SearchHit, SearchRecord, SearchRequest
from commons.privacy.redaction import scrub, scrub_json


def search_record(
    request: SearchRequest, hits: list[SearchHit], mode: PrivacyDefault
) -> SearchRecord:
    """Return the redacted record of a search, keeping more only in full_trace mode.

    Every mode keeps the redacted query, the language filter, and how many entries came back,
    so searches that found nothing show what the store is missing. full_trace adds the
    redacted environment and which entries were returned. `local` only ever reaches a store
    on the reporter's own machine (decision 0014), so it keeps what `entry` keeps.
    """
    full = mode == "full_trace"
    return SearchRecord(
        task_id=request.task_id,
        privacy_mode=mode,
        query=scrub(request.query),
        language=request.language(),
        limit=request.limit,
        hit_count=len(hits),
        environment=scrub_json(request.environment or {}) if full else None,
        results=(
            [
                {"entry_id": str(hit.entry_id), "rank": rank, "score": hit.score}
                for rank, hit in enumerate(hits, start=1)
            ]
            if full
            else None
        ),
    )
