from commons.domain.status import StoreStatus
from commons.store.connection import Connection
from commons.store.status import store_status

TOP_ENTRIES = 5


def status(conn: Connection) -> StoreStatus:
    """Return store statistics."""
    return store_status(conn, TOP_ENTRIES)
