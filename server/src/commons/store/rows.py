from uuid import UUID


def returned_id(row: tuple[object, ...] | None) -> UUID:
    """Return the id from an `INSERT ... RETURNING id` row."""
    if row is None or not isinstance(row[0], UUID):
        msg = f"expected a returned id, got {row!r}"
        raise RuntimeError(msg)
    return row[0]
