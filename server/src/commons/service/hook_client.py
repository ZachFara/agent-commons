"""The hook side that runs on the user's machine (decision 0016).

It keeps the spool, matches test runners, reads the transcript, and computes `used`, then
sends only derived data to a backend: the local database, or a hosted server.
"""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx2

from commons.domain.errors import NotInitializedError, NotPermittedError
from commons.domain.observations import ToolUse, is_test_command
from commons.domain.outcomes import Usage
from commons.domain.usage import compute_used
from commons.privacy.redaction import scrub
from commons.service import hooks
from commons.service.hooks import StopResult
from commons.service.identity import configured_test_runners
from commons.service.local_state import append_spool, prune_spools, read_spool
from commons.store.connection import Connection
from commons.store.task_events import TaskRetrieval


class HookBackend(Protocol):
    """Where derived observations go."""

    def session_start(self, session_id: str, model: str | None) -> UUID:
        """Create or resume the session's task."""
        ...

    def tool_use(
        self,
        session_id: str,
        test_run: dict[str, object] | None,
        file_change: dict[str, object] | None,
    ) -> None:
        """Store one tool call's derived observations."""
        ...

    def retrievals(self, session_id: str) -> list[TaskRetrieval]:
        """Entries retrieved in the session's task."""
        ...

    def stop(
        self, session_id: str, model: str | None, used: dict[UUID, Usage | None]
    ) -> StopResult | None:
        """Record the turn's observations."""
        ...


class LocalBackend:
    """Write straight to the database (local mode)."""

    def __init__(self, connection: Callable[[], AbstractContextManager[Connection]]) -> None:
        """Open a connection per call with `connection`."""
        self._connection = connection

    def session_start(self, session_id: str, model: str | None) -> UUID:
        """Create or resume the session's task."""
        with self._connection() as conn:
            return hooks.session_start(conn, session_id, model)

    def tool_use(
        self,
        session_id: str,
        test_run: dict[str, object] | None,
        file_change: dict[str, object] | None,
    ) -> None:
        """Store one tool call's derived observations."""
        with self._connection() as conn:
            hooks.record_tool_use(conn, session_id, test_run, file_change)

    def retrievals(self, session_id: str) -> list[TaskRetrieval]:
        """Entries retrieved in the session's task."""
        with self._connection() as conn:
            return hooks.retrievals_to_judge(conn, session_id)

    def stop(
        self, session_id: str, model: str | None, used: dict[UUID, Usage | None]
    ) -> StopResult | None:
        """Record the turn's observations."""
        with self._connection() as conn:
            return hooks.finish_turn(conn, session_id, model, used)


class RemoteBackend:
    """Post derived observations to a hosted server's hook API (decision 0018)."""

    def __init__(self, client: httpx2.Client) -> None:
        """Use `client`, already pointed at the server with the API key."""
        self._client = client

    def _post(self, step: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(f"/api/hooks/{step}", json=body)
        if response.status_code == httpx2.codes.UNAUTHORIZED:
            msg = "The hosted server rejected the API key. Run `commons connect` again."
            raise NotInitializedError(msg)
        if response.status_code == httpx2.codes.FORBIDDEN:
            raise NotPermittedError(response.json()["error"]["message"])
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def ping(self) -> None:
        """Check the server accepts the key."""
        self._post("ping", {})

    def session_start(self, session_id: str, model: str | None) -> UUID:
        """Create or resume the session's task."""
        return UUID(
            self._post("session-start", {"session_id": session_id, "model": model})["task_id"]
        )

    def tool_use(
        self,
        session_id: str,
        test_run: dict[str, object] | None,
        file_change: dict[str, object] | None,
    ) -> None:
        """Store one tool call's derived observations."""
        body = {"session_id": session_id, "test_run": test_run, "file_change": file_change}
        self._post("tool-use", body)

    def retrievals(self, session_id: str) -> list[TaskRetrieval]:
        """Entries retrieved in the session's task."""
        items = self._post("retrievals", {"session_id": session_id})["retrievals"]
        return [
            TaskRetrieval(
                retrieval_id=UUID(item["retrieval_id"]),
                entry_id=UUID(item["entry_id"]),
                fix=item["fix"],
                retrieved_at=datetime.fromisoformat(item["retrieved_at"]),
                reported=item["reported"],
            )
            for item in items
        ]

    def stop(
        self, session_id: str, model: str | None, used: dict[UUID, Usage | None]
    ) -> StopResult | None:
        """Record the turn's observations."""
        body = {
            "session_id": session_id,
            "model": model,
            "used": {str(retrieval): value for retrieval, value in used.items()},
        }
        result = self._post("stop", body)
        if result["task_id"] is None:
            return None
        return StopResult(UUID(result["task_id"]), [UUID(e) for e in result["unreported"]])


def remote_client(url: str, key: str) -> httpx2.Client:
    """Return an HTTP client for a hosted server, authenticated with the API key."""
    return httpx2.Client(
        base_url=url, headers={"authorization": f"Bearer {key}"}, timeout=REMOTE_TIMEOUT
    )


REMOTE_TIMEOUT = 10.0


def on_session_start(backend: HookBackend, session_id: str, model: str | None) -> UUID:
    """Prune old spools, then create or resume the task."""
    prune_spools()
    return backend.session_start(session_id, model)


def on_tool_use(backend: HookBackend, use: ToolUse) -> None:
    """Spool scrubbed text locally; send only the test run and file hashes."""
    if use.command is not None:
        append_spool(use.session_id, {"tool": use.tool_name, "command": scrub(use.command)})
    if use.new_text is not None:
        append_spool(use.session_id, {"tool": use.tool_name, "text": scrub(use.new_text)})
    test_run: dict[str, object] | None = None
    if use.command is not None and use.exit_code is not None and _is_test(use.command):
        test_run = {"command": scrub(use.command), "exit_code": use.exit_code}
    file_change: dict[str, object] | None = None
    if use.file_path is not None and use.new_text is not None:
        file_change = {
            "path_sha256": _sha256(use.file_path),
            "content_sha256": _sha256(use.new_text),
        }
    if test_run is not None or file_change is not None:
        backend.tool_use(use.session_id, test_run, file_change)


def on_stop(backend: HookBackend, session_id: str, transcript: str | None) -> StopResult | None:
    """Compute `used` from the local spool and record the turn."""
    model = observed_model(Path(transcript)) if transcript else None
    text = _session_text(session_id)
    used = {
        item.retrieval_id: compute_used(item.fix, text) for item in backend.retrievals(session_id)
    }
    return backend.stop(session_id, model, used)


def _is_test(command: str) -> bool:
    return is_test_command(command, configured_test_runners())


def _session_text(session_id: str) -> str:
    parts = (record.get("command") or record.get("text") for record in read_spool(session_id))
    return "\n".join(str(part) for part in parts if part)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def observed_model(transcript: Path) -> str | None:
    """Return the model of the transcript's last assistant message, if it records one."""
    if not transcript.exists():
        return None
    model = None
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            message = json.loads(line).get("message")
        except (json.JSONDecodeError, AttributeError):
            continue
        if isinstance(message, dict) and isinstance(message.get("model"), str):
            model = message["model"]
    return model
