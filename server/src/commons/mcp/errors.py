"""Structured tool errors (DESIGN.md §4): every failure is `{"error": {"code", "message"}}`."""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from pydantic import ValidationError as PydanticValidationError
from typing_extensions import override

from commons.domain.errors import (
    InvalidInputError,
    NotFoundError,
    NotInitializedError,
    NotPermittedError,
)

ErrorCode = Literal["invalid_input", "not_initialized", "not_found", "forbidden", "internal"]
INVALID_INPUT: ErrorCode = "invalid_input"
INTERNAL: ErrorCode = "internal"
_CODES: dict[type[Exception], ErrorCode] = {
    InvalidInputError: INVALID_INPUT,
    NotInitializedError: "not_initialized",
    NotFoundError: "not_found",
    NotPermittedError: "forbidden",
}
_INTERNAL_MESSAGE = "Unexpected server error; see server logs."

logger = logging.getLogger(__name__)


def tool_error(code: ErrorCode, message: str) -> ToolError:
    """Build the ToolError whose text is the structured error payload."""
    return ToolError(json.dumps({"error": {"code": code, "message": message}}))


@contextmanager
def structured_errors() -> Iterator[None]:
    """Turn exceptions raised inside a tool into structured errors.

    Unexpected exceptions are logged and reported without details, which may hold internals.
    """
    try:
        yield
    except (InvalidInputError, NotInitializedError, NotFoundError, NotPermittedError) as error:
        raise tool_error(_CODES[type(error)], str(error)) from error
    except Exception as error:
        logger.exception("tool failed")
        raise tool_error(INTERNAL, _INTERNAL_MESSAGE) from error


class StructuredValidationErrors(Middleware):
    """Report FastMCP's argument validation failures in the structured error format."""

    @override
    async def __call__(self, context: MiddlewareContext[Any], call_next: CallNext[Any, Any]) -> Any:
        """Pass every message through, converting argument validation errors.

        Overrides `__call__` (a documented FastMCP pattern) rather than `on_call_tool`: only
        tool calls validate arguments, and the dead-code check cannot see framework hooks.
        """
        try:
            return await call_next(context)
        except ValidationError as error:
            cause = error.__cause__
            if not isinstance(cause, PydanticValidationError):
                raise
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                for e in cause.errors(include_url=False, include_input=False)
            )
            raise tool_error(INVALID_INPUT, problems) from error
