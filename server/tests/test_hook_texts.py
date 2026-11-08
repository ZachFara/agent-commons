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
from commons.service import agent_text
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


def every_hook_text() -> list[agent_text.Variant]:
    """Every named variant, and the shipped wording with each SessionStart line."""
    wordings = (
        agent_text.resolve(agent_text.SHIPPED.replace("g1", f"g{option}"))
        for option in range(len(agent_text.START_LINES))
    )
    return [*agent_text.VARIANTS.values(), *wordings]


def test_a_server_recognizes_every_hook_version_this_code_ships() -> None:
    for shown in every_hook_text():
        version = agent_text.hook_version(shown)
        assert version.kind == "hooks"
        assert agent_text.reported_hook_version(version.hash[:12]) == version
        assert len(version.label) <= 120


def test_only_the_start_line_and_nudge_make_a_new_hook_version() -> None:
    versions = {
        name: agent_text.hook_version(agent_text.VARIANTS[name]) for name in START_VARIANTS[:4]
    }
    nudge = agent_text.hook_version(agent_text.VARIANTS["nudge"])
    assert agent_text.hook_version(agent_text.VARIANTS["examples"]) == versions["original"]
    assert versions["baseline"] == versions["previous"]
    hashes = {versions["baseline"].hash, versions["original"].hash, versions["start"].hash}
    assert len(hashes | {nudge.hash}) == 4
    assert nudge.surfaces == {
        "session_start": agent_text.SESSION_START,
        "report_reminder": agent_text.REPORT_REMINDER,
        "nudge": agent_text.NUDGE,
    }
    assert versions["original"].label == "original, examples, rule, invite"


def test_a_hash_no_release_shows_is_unrecognized() -> None:
    version = agent_text.reported_hook_version("0123456789ab")
    assert (version.label, version.surfaces, version.hash, version.kind) == (
        "unrecognized",
        None,
        "0123456789ab",
        "hooks",
    )
