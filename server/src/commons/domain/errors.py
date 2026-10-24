class InvalidInputError(ValueError):
    """Input that breaks a documented rule. The message is safe to show to the caller."""


class NotInitializedError(RuntimeError):
    """No reporter identity on this machine yet. The fix is to run `commons init`."""


class NotFoundError(LookupError):
    """The referenced thing does not exist for this caller. The message is safe to show."""


class NotPermittedError(PermissionError):
    """The caller may not do this. The message is safe to show."""


class StoreUnavailableError(ConnectionError):
    """The database can't be reached. The message says where and what to try."""
