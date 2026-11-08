"""The text the hooks send the agent, byte for byte, against fixtures captured from the hooks.

`hook_texts.json` holds the hooks' stdout from before the texts moved into `agent_text`
(ticket `MIT-609`): moving or hashing them must not change one byte of what the agent reads.
"""

import io
import json
import uuid
from collections.abc import Mapping
from pathlib import Path

import pytest

from commons.cli.main import main
from commons.service.hooks import StopResult
from hook_env import HookEnv

FIXTURES = json.loads((Path(__file__).parent / "hook_texts.json").read_text(encoding="utf-8"))
TASK_ID = uuid.UUID("6f1d2c3b-4a59-4e68-8d7c-0b1a2f3e4d5c")
ENTRIES = [
    uuid.UUID("0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"),
    uuid.UUID("1b2c3d4e-5f6a-4b7c-9d8e-0f1a2b3c4d5e"),
]
START_VARIANTS = ("baseline", "previous", "original", "start", "w-a0b0c0d0e0f0g2h0n0")
STOP_CASES = {
    "unreported": ("baseline", StopResult(TASK_ID, ENTRIES)),
    "nudge": ("nudge", StopResult(TASK_ID, [], fixed_failing_tests=True)),
}


@pytest.fixture
def env(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HookEnv:
    hook_env = HookEnv(database_url, tmp_path, monkeypatch)
    hook_env.init()
    return hook_env


def hook_stdout(monkeypatch: pytest.MonkeyPatch, event: str, payload: Mapping[str, object]) -> str:
    stdout = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("sys.stdout", stdout)
    assert main(["hook", event]) == 0
    return stdout.getvalue()


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("variant", START_VARIANTS)
def test_session_start_context_is_unchanged(monkeypatch: pytest.MonkeyPatch, variant: str) -> None:
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", variant)
    monkeypatch.setattr(
        "commons.service.hook_client.on_session_start", lambda *_args, **_kwargs: TASK_ID
    )
    payload = {"session_id": str(uuid.uuid4()), "source": "startup"}
    assert hook_stdout(monkeypatch, "session-start", payload) == FIXTURES["session_start"][variant]


@pytest.mark.usefixtures("env")
@pytest.mark.parametrize("case", STOP_CASES)
def test_stop_reason_is_unchanged(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    variant, result = STOP_CASES[case]
    monkeypatch.setenv("COMMONS_INSTRUCTIONS_VARIANT", variant)
    monkeypatch.setattr("commons.service.hook_client.on_stop", lambda *_args: result)
    payload = {"session_id": str(uuid.uuid4()), "stop_hook_active": False}
    assert hook_stdout(monkeypatch, "stop", payload) == FIXTURES["stop"][case]
