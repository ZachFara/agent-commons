"""Corrections and exclusions (decision 0030): what may be corrected, and to what."""

from dataclasses import dataclass
from typing import Literal, get_args
from uuid import UUID

from commons.domain.errors import InvalidInputError
from commons.domain.outcomes import OutcomeKind, Usage

CorrectionTable = Literal["outcomes", "retrievals", "searches"]
CorrectionField = Literal["used", "outcome", "excluded"]
CORRECTION_TABLES: tuple[CorrectionTable, ...] = get_args(CorrectionTable)
CORRECTION_FIELDS: tuple[CorrectionField, ...] = get_args(CorrectionField)
CorrectionValue = str | bool | None

_FIELDS: dict[CorrectionTable, frozenset[CorrectionField]] = {
    "outcomes": frozenset({"used", "outcome", "excluded"}),
    "retrievals": frozenset({"excluded"}),
    "searches": frozenset({"excluded"}),
}
_USAGES: frozenset[str | None] = frozenset({*get_args(Usage), None})
_OUTCOMES: frozenset[str] = frozenset(get_args(OutcomeKind))


@dataclass(frozen=True)
class Correction:
    """One field of one row set to a new value, by someone, for a reason."""

    table: CorrectionTable
    row_id: UUID
    field: CorrectionField
    value: CorrectionValue
    reason: str
    by: str


def validate_correction(correction: Correction) -> None:
    """Raise InvalidInputError unless the field exists on the table and the value fits it."""
    if correction.field not in _FIELDS.get(correction.table, frozenset()):
        msg = f"{correction.table} has no correctable field {correction.field!r}."
        raise InvalidInputError(msg)
    if not correction.reason.strip():
        msg = "Every correction needs a reason."
        raise InvalidInputError(msg)
    if not correction.by.strip():
        msg = "Say who is making the correction."
        raise InvalidInputError(msg)
    value = correction.value
    valid = {
        "used": value in _USAGES,
        "outcome": isinstance(value, str) and value in _OUTCOMES,
        "excluded": isinstance(value, bool),
    }[correction.field]
    if not valid:
        msg = f"{value!r} is not a valid {correction.field}."
        raise InvalidInputError(msg)


def parse_value(field: CorrectionField, text: str) -> CorrectionValue:
    """Turn command-line or form text into a field value ('unknown' means NULL for used)."""
    if field == "excluded":
        if text not in {"true", "false"}:
            msg = "excluded is true or false."
            raise InvalidInputError(msg)
        return text == "true"
    if field == "used" and text in {"", "unknown", "null"}:
        return None
    return text
