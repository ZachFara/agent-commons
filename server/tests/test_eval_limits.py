"""The eval runner when the subscription's usage runs out (tools/eval/runner.py, decision 0023)."""

import json
from pathlib import Path
from typing import Any

import pytest
import tools.agent_eval
from tools.eval.runner import Job, Result, out_of_usage
from tools.eval.scenarios import SCENARIOS

# The real result event from a session started after the limit was hit (2026-10-25).
LIMIT_EVENT: dict[str, Any] = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": 429,
    "result": "You've hit your session limit · resets 2:40am (America/New_York)",
}


def result(error: str | None) -> Result:
    return Result(
        scenario="control-typo",
        trigger="control",
        condition="limits",
        index=1,
        searched=False,
        search_found=False,
        proposed=False,
        proposals=[],
        reported=False,
        used=None,
        observed_outcome=None,
        used_session_task_id=True,
        tests_pass=error is None,
        attempts=1,
        read_docs=False,
        injection_followed=False,
        turns=4,
        cost_usd=0.0,
        error=error,
    )


def test_a_session_limit_is_recognized_as_out_of_usage() -> None:
    """Found when a 153-session batch ran past the limit: 109 sessions "ran" and failed."""
    assert out_of_usage([LIMIT_EVENT], "")


def test_an_ordinary_session_or_a_short_rate_limit_is_not_out_of_usage() -> None:
    assert not out_of_usage([{"type": "result", "is_error": False, "result": "done"}], "")
    short = {"type": "result", "is_error": True, "api_error_status": 429, "result": "Overloaded"}
    assert not out_of_usage([short], "")  # retried with backoff instead


def test_errored_sessions_never_reach_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    tools.agent_eval.append_ledger([result(None), result(LIMIT_EVENT["result"])])
    lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    assert lines[0]["error"] is None


def test_resume_skips_only_clean_results_for_the_same_variant(tmp_path: Path) -> None:
    scenario = SCENARIOS["control-typo"]
    clean, errored, other = (
        Job(scenario, 1, "baseline"),
        Job(scenario, 2, "baseline"),
        Job(scenario, 3, "baseline"),
    )
    for job, error, variant in (
        (clean, None, "baseline"),
        (errored, "limit", "baseline"),
        (other, None, "start"),
    ):
        path = tmp_path / f"limits-control-typo-{job.index}.json"
        path.write_text(json.dumps({"error": error, "variant": variant}), encoding="utf-8")
    assert tools.agent_eval.finished(tmp_path, "limits", clean)
    assert not tools.agent_eval.finished(tmp_path, "limits", errored)
    assert not tools.agent_eval.finished(tmp_path, "limits", other)  # a different text ran there
    assert not tools.agent_eval.finished(tmp_path, "limits", Job(scenario, 4, "baseline"))
