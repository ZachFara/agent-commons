"""Counterfactual pairs from the arms ledger (tools/agent_eval.py, commons.store.counterfactuals).

The ledger here is synthetic: no session runs.
"""

import argparse
import json
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
import tools.agent_eval
from tools.eval import runner

import factories
from commons.store.counterfactuals import CounterfactualRun, known_entries, record_run
from commons.store.reporters import upsert_reporter
from conftest import ADMIN_URL

CONDITION = "github-arms"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Yield the name of a new store on the test server; drop it afterwards."""
    monkeypatch.setattr(runner, "ADMIN_URL", ADMIN_URL)
    name = f"commons_store_{uuid.uuid4().hex[:12]}"
    runner.open_store(runner.Store(name, seed_wave=True))
    yield name
    runner.drop_database(name)


def stored_entry(store: str) -> uuid.UUID:
    with psycopg.connect(runner.local_url(store)) as conn:
        return factories.entry(conn, upsert_reporter(conn, "eval-agent", "entry"))


def retrieval(entry: uuid.UUID | str, used: str | None = "yes") -> dict[str, Any]:
    return {
        "entry_id": str(entry),
        "rank": 1,
        "used": used,
        "observed_outcome": None,
        "reported": True,
    }


def line(arm: str, *, passed: bool, tokens: tuple[int, int], **fields: object) -> dict[str, Any]:
    """One ledger line of the arms run, as `append_ledger` writes it."""
    return {
        "condition": CONDITION,
        "scenario": "task-a",
        "model": "sonnet",
        "arm": arm,
        "repeat": 1,
        "run": 0,
        "tests_pass": passed,
        "tokens_in": tokens[0],
        "tokens_out": tokens[1],
        "wall_clock_seconds": 30.0,
        "retrievals": [],
        **fields,
    }


def write_ledger(path: Path, lines: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(item) + "\n" for item in lines), encoding="utf-8")


def stored_runs(store: str) -> list[tuple[Any, ...]]:
    with psycopg.connect(runner.local_url(store)) as conn:
        return conn.execute(
            "SELECT task_id, entry_id, with_entry, outcome::text, tokens_in, tokens_out, "
            "wall_clock_seconds FROM counterfactual_runs ORDER BY with_entry DESC"
        ).fetchall()


def test_four_sessions_two_pairs_one_without_a_retrieval_write_two_rows_once(
    store: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = stored_entry(store)
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    write_ledger(
        ledger,
        [
            line("both", passed=True, tokens=(1000, 100), retrievals=[retrieval(entry)]),
            line("context7", passed=False, tokens=(3000, 300), wall_clock_seconds=90.0),
            line("commons", passed=True, tokens=(1500, 150)),  # retrieved nothing
            line("none", passed=True, tokens=(2000, 200)),
            line("both", passed=True, tokens=(9, 9), condition="other", run=7),  # not this one
        ],
    )
    args = argparse.Namespace(condition=CONDITION, store=store)

    assert tools.agent_eval.counterfactuals(args) == 0
    first = stored_runs(store)
    assert tools.agent_eval.counterfactuals(args) == 0  # idempotent
    assert stored_runs(store) == first

    task = uuid.uuid5(
        tools.agent_eval.COUNTERFACTUAL_NAMESPACE, f"{CONDITION}/task-a/1/sonnet/both-context7"
    )
    assert first == [
        (task, entry, True, "tests_passed", 1000, 100, 30.0),
        (task, entry, False, "tests_failed", 3000, 300, 90.0),
    ]
    out = capsys.readouterr().out
    assert "1 pairs with a retrieval; 2 rows added" in out
    assert "1 pairs with a retrieval; 0 rows added" in out


def test_a_pair_names_the_entry_the_session_used_over_the_first_it_was_shown() -> None:
    shown, used = uuid.uuid4(), uuid.uuid4()
    rows = [
        line(
            "commons",
            passed=True,
            tokens=(1, 1),
            retrievals=[retrieval(shown, "no"), retrieval(used, "partial")],
        ),
        line("none", passed=True, tokens=(1, 1)),
    ]
    [(with_entry, without)] = tools.agent_eval.counterfactual_pairs(rows, CONDITION)
    assert with_entry.entry_id == without.entry_id == used
    assert with_entry.task_id == without.task_id
    unused = [line("commons", passed=True, tokens=(1, 1), retrievals=[retrieval(shown, None)])]
    [(only, _without)] = tools.agent_eval.counterfactual_pairs([*unused, rows[1]], CONDITION)
    assert only.entry_id == shown


def test_sessions_pair_only_with_the_same_task_repeat_and_model() -> None:
    sighted = line("both", passed=True, tokens=(1, 1), retrievals=[retrieval(uuid.uuid4())])
    others = [
        line("context7", passed=True, tokens=(1, 1), repeat=2),
        line("context7", passed=True, tokens=(1, 1), model="opus"),
        line("context7", passed=True, tokens=(1, 1), scenario="task-b"),
        line("none", passed=True, tokens=(1, 1)),  # the blind arm pairs with commons alone
    ]
    assert tools.agent_eval.counterfactual_pairs([sighted, *others], CONDITION) == []


def test_a_repeated_ledger_line_is_warned_about_and_only_the_last_is_paired(
    capsys: pytest.CaptureFixture[str],
) -> None:
    first, last = uuid.uuid4(), uuid.uuid4()
    rows = [
        line("both", passed=False, tokens=(1, 1), retrievals=[retrieval(first)]),
        line("both", passed=True, tokens=(2, 2), retrievals=[retrieval(last)]),  # same key
        line("context7", passed=True, tokens=(1, 1)),
    ]
    [(with_entry, _without)] = tools.agent_eval.counterfactual_pairs(rows, CONDITION)
    assert with_entry.entry_id == last
    assert "warning: 1 ledger lines repeat" in capsys.readouterr().err
    tools.agent_eval.counterfactual_pairs(rows[1:], CONDITION)
    assert capsys.readouterr().err == ""


def test_a_pair_whose_entry_the_store_lacks_is_skipped_and_said(
    store: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    elsewhere = retrieval(uuid.uuid4())
    write_ledger(
        ledger,
        [
            line("both", passed=True, tokens=(1, 1), retrievals=[elsewhere]),
            line("context7", passed=True, tokens=(1, 1)),
        ],
    )
    args = argparse.Namespace(condition=CONDITION, store=store)
    assert tools.agent_eval.counterfactuals(args) == 0
    assert stored_runs(store) == []
    assert "1 pairs skipped: their entry is not in store" in capsys.readouterr().out


def test_counterfactuals_refuse_a_store_that_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runner, "ADMIN_URL", ADMIN_URL)
    monkeypatch.setattr(tools.agent_eval, "LEDGER", tmp_path / "none.jsonl")
    args = argparse.Namespace(condition=CONDITION, store=f"commons_store_{uuid.uuid4().hex[:8]}")
    assert tools.agent_eval.counterfactuals(args) == 2


def test_a_run_written_twice_is_written_once(db: psycopg.Connection, reporter: uuid.UUID) -> None:
    entry = factories.entry(db, reporter)
    run = CounterfactualRun(
        task_id=uuid.uuid4(),
        entry_id=entry,
        with_entry=True,
        outcome="tests_passed",
        tokens_in=10,
        tokens_out=2,
        wall_clock_seconds=1.5,
    )
    assert record_run(db, run)
    assert not record_run(db, run)
    assert record_run(db, replace(run, with_entry=False))
    assert known_entries(db, {entry, uuid.uuid4()}) == {entry}
