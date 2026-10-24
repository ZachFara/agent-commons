class InvalidInputError(ValueError):
    """Input that breaks a documented rule. The message is safe to show to the caller."""


class NotInitializedError(RuntimeError):
    """No reporter identity on this machine yet. The fix is to run `commons init`."""
