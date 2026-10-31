"""Move entries from a local store to a hosted one, through review again (decision 0031)."""

import uuid
from collections.abc import Mapping
from uuid import UUID

from commons.domain.entries import Proposal
from commons.service.proposals import ProposeResult, propose
from commons.store.connection import Connection
from commons.store.entries import set_imported_from
from commons.store.search import similar_entries


def import_entry(
    conn: Connection, proposal: Proposal, origin: Mapping[str, object], model: str | None
) -> ProposeResult:
    """Propose a local entry here: redaction, risk scanning, duplicates and review all apply.

    The original model is credited, and `imported_from` says where and when it came from.
    """
    result = propose(conn, proposal, model)
    set_imported_from(conn, result.entry_id, dict(origin))
    return result


def similar(conn: Connection, text: str) -> list[tuple[UUID, float]]:
    """Return the live entries most like `text`, for an import's "already there?" check."""
    return similar_entries(conn, text, exclude=[uuid.UUID(int=0)], limit=3)
