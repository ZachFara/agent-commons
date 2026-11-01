"""The hook side that runs on the user's machine (decision 0016).

It keeps the spool, matches test runners, reads the transcript, and computes `used`, then
sends only derived data to a backend: the local database, or a hosted server.
"""

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import httpx2

from commons.domain.errors import NotInitializedError, NotPermittedError, RateLimitedError
from commons.domain.observations import (
    ToolUse,
    command_tail,
    is_test_command,
    observed_exit_code,
)
from commons.domain.usage import ArtifactFrequencies, Evidence, UseObservation, observe_use
from commons.privacy.redaction import scrub
from commons.service import contributors, hooks
from commons.service.hooks import StopResult
from commons.service.identity import configured_test_runners
from commons.service.local_state import (
    append_spool,
    cached_frequencies,
    prune_spools,
    read_spool,
    save_frequencies,
)
from commons.store.connection import Connection
from commons.store.task_events import TaskRetrieval

CLOCK_SKEW = timedelta(seconds=5)


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
        self, session_id: str, model: str | None, used: dict[UUID, UseObservation]
    ) -> StopResult | None:
        """Record the turn's observations."""
        ...

    def artifact_frequencies(self) -> ArtifactFrequencies:
        """How common each fix artifact is across the store (decision 0036)."""
        ...

    def feedback(self) -> str | None:
        """Return a "since your last session" line, when the store turned that on (0035)."""
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
        self, session_id: str, model: str | None, used: dict[UUID, UseObservation]
    ) -> StopResult | None:
        """Record the turn's observations."""
        with self._connection() as conn:
            return hooks.finish_turn(conn, session_id, model, used)

    def artifact_frequencies(self) -> ArtifactFrequencies:
        """Count artifacts across the store's approved fixes."""
        with self._connection() as conn:
            return hooks.artifact_frequencies(conn)

    def feedback(self) -> str | None:
        """Ask the local store for a feedback line (off unless enabled here)."""
        with self._connection() as conn:
            return contributors.session_feedback(conn)


class RemoteBackend:
    """Post derived observations to a hosted server's hook API (decision 0018)."""

    def __init__(self, client: httpx2.Client) -> None:
        """Use `client`, already pointed at the server with the API key."""
        self._client = client

    def _post(self, step: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._client.post(f"/api/hooks/{step}", json=body)
        if response.status_code == httpx2.codes.UNAUTHORIZED:
            reason = _error_message(response) or "unknown API key"
            msg = f"The hosted server rejected the API key ({reason}). Run `commons join` again."
            raise NotInitializedError(msg)
        if response.status_code == httpx2.codes.FORBIDDEN:
            raise NotPermittedError(response.json()["error"]["message"])
        if response.status_code == httpx2.codes.TOO_MANY_REQUESTS:
            wait = int(response.headers.get("retry-after", "60"))
            raise RateLimitedError(response.json()["error"]["message"], wait)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    def ping(self) -> str | None:
        """Check the server accepts the key; return its scope (None from older servers)."""
        scope = self._post("ping", {}).get("scope")
        return scope if isinstance(scope, str) else None

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
        self, session_id: str, model: str | None, used: dict[UUID, UseObservation]
    ) -> StopResult | None:
        """Record the turn's observations, with when matched work happened (0036)."""
        body = {
            "session_id": session_id,
            "model": model,
            "used": {str(retrieval): seen.used for retrieval, seen in used.items()},
            "matched_at": {
                str(retrieval): seen.matched_at.isoformat() if seen.matched_at else None
                for retrieval, seen in used.items()
            },
        }
        result = self._post("stop", body)
        if result["task_id"] is None:
            return None
        return StopResult(
            UUID(result["task_id"]),
            [UUID(e) for e in result["unreported"]],
            fixed_failing_tests=bool(result.get("fixed_failing_tests")),
            searched=bool(result.get("searched")),
            nudge=result.get("nudge") if isinstance(result.get("nudge"), str) else None,
        )

    def feedback(self) -> str | None:
        """Ask the server for a feedback line; None from servers without it."""
        try:
            line = self._post("feedback", {}).get("line")
        except httpx2.HTTPError:
            return None
        return line if isinstance(line, str) else None

    def mine(self) -> list[dict[str, Any]]:
        """Return the key holder's entries, from the server (`commons mine`)."""
        entries: list[dict[str, Any]] = self._post("mine", {})["entries"]
        return entries

    def similar(self, text: str) -> list[tuple[str, float]]:
        """Return the server's live entries most like `text` (for imports, decision 0031)."""
        items = self._post("similar", {"text": text})["similar"]
        return [(str(item["entry_id"]), float(item["score"])) for item in items]

    def import_entry(self, entry: dict[str, Any], origin: dict[str, Any]) -> str:
        """Propose a local entry on the server; return the new entry's id."""
        result = self._post("import", {"entry": entry, "imported_from": origin})
        return str(result["entry_id"])

    def artifact_frequencies(self) -> ArtifactFrequencies:
        """Fetch artifact counts from the server, at most once a day; empty if unavailable."""
        if (cached := cached_frequencies()) is not None:
            return cached
        try:
            result = self._post("artifact-frequencies", {})
        except httpx2.HTTPError:
            return ArtifactFrequencies()  # older servers: equal weights, generic tokens still 0
        frequencies = ArtifactFrequencies(int(result["entries"]), dict(result["counts"]))
        save_frequencies(frequencies)
        return frequencies


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
    where = {"path": use.file_path} if use.file_path else {}
    if use.new_text is not None:
        append_spool(use.session_id, {"tool": use.tool_name, "text": scrub(use.new_text), **where})
    if use.old_text:
        append_spool(
            use.session_id, {"tool": use.tool_name, "removed": scrub(use.old_text), **where}
        )
    test_run: dict[str, object] | None = None
    if use.command is not None and _is_test(use.command):
        code = observed_exit_code(
            use.command, use.exit_code, use.output or "", configured_test_runners()
        )
        if code is not None:
            test_run = {"command": scrub(command_tail(use.command)), "exit_code": code}
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
    records = read_spool(session_id)
    retrieved = backend.retrievals(session_id)
    frequencies = backend.artifact_frequencies() if retrieved else ArtifactFrequencies()
    used = {
        item.retrieval_id: observe_use(item.fix, _evidence(records, item.retrieved_at), frequencies)
        for item in retrieved
    }
    return backend.stop(session_id, model, used)


def _is_test(command: str) -> bool:
    return is_test_command(command, configured_test_runners())


def _evidence(records: list[dict[str, object]], retrieved_at: datetime) -> list[Evidence]:
    """Commands run, text added, and text removed after the retrieval, oldest first.

    Removed text counts too: a fix often names what to take out.
    """
    after = [record for record in records if _spooled_after(record, retrieved_at)]
    return [
        Evidence(str(part), _time(record.get("at")), _text(record.get("path")))
        for record in after
        for key in ("command", "text", "removed")
        if (part := record.get(key))
    ]


def _time(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _spooled_after(record: dict[str, object], retrieved_at: datetime) -> bool:
    """Whether the agent did this after retrieving the entry: earlier work is not a use (T39).

    The spool's clock is this machine's and the retrieval's is the database's, so allow for
    skew. Records spooled before records carried a time have none, and count.
    """
    at = record.get("at")
    if not isinstance(at, str):
        return True
    return datetime.fromisoformat(at) >= retrieved_at - CLOCK_SKEW


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


def _error_message(response: httpx2.Response) -> str | None:
    try:
        message = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return None
    return message if isinstance(message, str) else None
