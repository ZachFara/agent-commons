from dataclasses import dataclass

from commons.domain.activity import RecentEntry, RecentRetrieval
from commons.domain.reviews import PendingEntry
from commons.domain.status import StoreStatus
from commons.service.reviews import pending
from commons.service.status import status
from commons.store.activity import recent_entries, recent_retrievals
from commons.store.connection import Connection

RECENT_LIMIT = 50


@dataclass(frozen=True)
class Dashboard:
    """Everything the admin dashboard's overview page shows."""

    status: StoreStatus
    pending: list[PendingEntry]
    recent_entries: list[RecentEntry]
    recent_retrievals: list[RecentRetrieval]


def dashboard(conn: Connection) -> Dashboard:
    """Collect the overview: store status, review queue, and recent activity."""
    return Dashboard(
        status=status(conn),
        pending=pending(conn),
        recent_entries=recent_entries(conn, RECENT_LIMIT),
        recent_retrievals=recent_retrievals(conn, RECENT_LIMIT),
    )
