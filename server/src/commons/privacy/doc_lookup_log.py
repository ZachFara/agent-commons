"""What the store keeps about a Context7 doc lookup in each privacy mode (DESIGN.md §6)."""

from uuid import UUID

from commons.domain.observations import DocLookup, DocLookupRecord
from commons.domain.reporters import PrivacyDefault
from commons.privacy.redaction import scrub


def doc_lookup_record(lookup: DocLookup, task_id: UUID, mode: PrivacyDefault) -> DocLookupRecord:
    """Return the record of a lookup, keeping the redacted query only in full_trace mode.

    Every mode keeps which library and version were looked up. The query is what the agent
    asked, so it is kept only where the reporter chose to share traces.
    """
    full = mode == "full_trace"
    return DocLookupRecord(
        task_id=task_id,
        privacy_mode=mode,
        library_id=lookup.library_id,
        version=lookup.version,
        query=scrub(lookup.query) if full and lookup.query is not None else None,
    )
