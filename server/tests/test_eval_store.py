"""The eval's experiment store (tools/eval/runner.py): local only; shared, or cloned per session.

No session runs here: the store is built and cloned for real, and sessions' writes are made
directly, as the commons server would make them.
"""

import argparse
import json
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import psycopg
import pytest
import tools.agent_eval
from tools.eval import runner
from tools.eval.github_tasks import SMOKE_TASKS, TABLE_EXTRACTION
from tools.eval.scenarios import SCENARIOS

import factories
from commons.service import agent_text
from commons.store.reporters import upsert_reporter
from commons.store.tasks import start_task
from conftest import ADMIN_URL

TASK = SCENARIOS["control-typo"]


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[runner.Store]:
    """Yield a new seed-wave store on the test server; drop it afterwards."""
    monkeypatch.setattr(runner, "ADMIN_URL", ADMIN_URL)
    made = runner.Store(f"commons_store_{uuid.uuid4().hex[:12]}", seed_wave=True)
    runner.open_store(made)
    yield made
    runner.drop_database(made.name)


def condition() -> str:
    """Return a condition no other test run uses, so session databases never collide."""
    return f"t{uuid.uuid4().hex[:8]}"


def exists(name: str) -> bool:
    with psycopg.connect(ADMIN_URL) as admin:
        found = admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        return found.fetchone() is not None


@pytest.mark.parametrize(
    "admin",
    [
        "postgresql://commons:commons@db.example.com:5432/commons",
        "postgresql://commons:commons@localhost:5432/commons?hostaddr=203.0.113.9",
        "postgresql://commons:commons@localhost,db.example.com:5432/commons",
        "dbname=commons user=commons",  # a socket, or whatever PGHOST says
    ],
    ids=["remote-host", "remote-hostaddr", "a-remote-among-hosts", "no-host"],
)
def test_the_store_refuses_a_database_not_on_localhost(
    monkeypatch: pytest.MonkeyPatch, admin: str
) -> None:
    monkeypatch.setattr(runner, "ADMIN_URL", admin)
    with pytest.raises(ValueError, match="must be on localhost"):
        runner.open_store(runner.Store("commons_eval_store", seed_wave=True))
    with pytest.raises(ValueError, match="must be on localhost"):
        runner.clone_store(runner.Store("commons_eval_store"), "commons_eval_clone")
    with pytest.raises(ValueError, match="must be on localhost"):
        runner.local_url("commons_eval_store")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_the_store_accepts_this_machine(monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setattr(runner, "ADMIN_URL", f"postgresql://commons:commons@{host}:5432/commons")
    assert "dbname=commons_eval_store" in runner.local_url("commons_eval_store")


def test_a_measured_run_needs_a_store_its_seed_wave_made(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "ADMIN_URL", ADMIN_URL)
    missing = f"commons_store_{uuid.uuid4().hex[:12]}"
    with pytest.raises(ValueError, match="run its seed wave first"):
        runner.open_store(runner.Store(missing))
    assert not exists(missing)


def test_a_clone_holds_the_stores_approved_entry_and_is_dropped_while_the_store_remains(
    store: runner.Store,
) -> None:
    with psycopg.connect(runner.local_url(store.name)) as conn:
        entry = factories.entry(conn, upsert_reporter(conn, "eval-author", "entry"))
    job = runner.Job(TASK, 1, store=replace(store, seed_wave=False))
    name = runner.database_name(cond := condition(), job)

    with runner.session_database(cond, job) as url, psycopg.connect(url) as clone:
        row = clone.execute("SELECT status FROM entries WHERE id = %s", (entry,)).fetchone()
        assert row == ("approved",)
        assert exists(name)

    assert not exists(name)
    assert exists(store.name)


def graded_nothing(
    url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session_id: str
) -> runner.Result:
    """Grade a session that made no commons call, its tests' run stubbed as passing."""
    passed = subprocess.CompletedProcess(["pytest"], 0, "1 passed", "")
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: passed)
    session = runner.Session(
        TASK, "c", 1, tmp_path, tmp_path, url, agent_text.SHIPPED, "commons", None
    )
    events: list[dict[str, Any]] = [
        {"type": "system", "subtype": "init", "session_id": session_id, "mcp_servers": []},
        {"type": "result", "session_id": session_id, "is_error": False, "usage": {}},
    ]
    return runner.grade(session, events)


def test_a_clone_holding_eval_agent_entries_grades_a_session_that_proposed_nothing_as_none(
    store: runner.Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with psycopg.connect(runner.local_url(store.name)) as conn:
        agent = upsert_reporter(conn, "eval-agent", "entry")
        for wave in range(3):  # the seed wave's proposals, each from its own session
            factories.entry(conn, agent, symptom=f"seeded {wave}", task_id=uuid.uuid4())
    job = runner.Job(TASK, 1, store=replace(store, seed_wave=False))

    with runner.session_database(condition(), job) as url:
        graded = graded_nothing(url, tmp_path, monkeypatch, "measured-session")

    assert not graded.proposed
    assert graded.proposals == []
    assert not graded.reported


def test_in_a_seed_wave_two_sessions_proposals_both_land_in_the_store(
    store: runner.Store,
) -> None:
    cond = condition()
    for index in (1, 2):
        job = runner.Job(TASK, index, store=store)
        with runner.session_database(cond, job) as url, psycopg.connect(url) as conn:
            assert url == runner.local_url(store.name)
            agent = upsert_reporter(conn, "eval-agent", "entry")
            task = start_task(conn, f"seed-session-{index}", agent, None)
            factories.entry(conn, agent, symptom=f"proposal {index}", task_id=task)

    assert exists(store.name)
    with psycopg.connect(runner.local_url(store.name)) as conn:
        for index in (1, 2):  # each session's grading counts its own proposal only
            activity = runner.session_activity(conn, {f"seed-session-{index}"}, set())
            assert [row[0] for row in activity.proposals] == [f"proposal {index}"]


def test_a_seed_wave_seeds_a_scenarios_entries_once(store: runner.Store) -> None:
    known = SCENARIOS["known-silent-rename"]
    url = runner.local_url(store.name)
    runner.seed(url, known)
    runner.seed(url, known)  # a second session of the wave
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT count(*) FROM entries e JOIN reporters r ON r.id = e.created_by "
            "WHERE r.pseudonym = 'eval-author'"
        ).fetchone()
    assert row == (len(known.seeds),)


def arms_args(**fields: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "scenario": [TASK.name],
        "arm": None,
        "runs": 2,
        "model": None,
        "store": None,
        "seed_wave": False,
        "smoke": False,
    }
    return argparse.Namespace(**{**defaults, **fields})


def test_arms_runs_every_job_on_the_named_store_and_needs_one_for_a_seed_wave(
    store: runner.Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    planned: list[runner.Job] = []

    def run_jobs(jobs: list[runner.Job], _args: argparse.Namespace) -> int:
        planned.extend(jobs)
        return 0

    monkeypatch.setattr(tools.agent_eval, "run_jobs", run_jobs)
    assert tools.agent_eval.arms(arms_args(seed_wave=True)) == 2
    assert tools.agent_eval.arms(arms_args(store=store.name)) == 0
    assert len(planned) == 8
    assert {job.store for job in planned} == {runner.Store(store.name)}
    assert sorted({job.repeat for job in planned}) == [1, 2]


def test_arms_smoke_runs_the_ten_smoke_tasks_under_github_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    planned: list[tuple[str, runner.Job]] = []

    def run_jobs(jobs: list[runner.Job], args: argparse.Namespace) -> int:
        planned.extend((args.condition, job) for job in jobs)
        return 0

    monkeypatch.setattr(tools.agent_eval, "run_jobs", run_jobs)
    assert tools.agent_eval.arms(arms_args(smoke=True)) == 2  # with a --scenario too
    assert planned == []
    assert tools.agent_eval.arms(arms_args(scenario=None, smoke=True, condition="arms")) == 0
    assert len(SMOKE_TASKS) == 10
    assert {job.scenario.name for _condition, job in planned} == set(SMOKE_TASKS)
    assert {condition for condition, _job in planned} == {"github-smoke"}
    assert len(planned) == 10 * 4 * 2  # tasks x arms x runs


def write_ledger(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_the_review_sheet_shows_each_pending_entry_next_to_its_tasks_upstream_fix(
    store: runner.Store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session_task, orphan_task = uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(runner.local_url(store.name)) as conn:
        agent = upsert_reporter(conn, "eval-agent", "entry")
        linked = factories.entry(
            conn,
            agent,
            symptom="include_metadata reads False when omitted",
            cause="the form field arrives empty",
            fix="parse the field as a string and default it to true",
            task_id=session_task,
        )
        unknown = factories.entry(conn, agent, symptom="no line names my task", task_id=orphan_task)
        factories.entry(
            conn, agent, symptom="reviewed already", human_reviewed=True, task_id=session_task
        )
        before = conn.execute("SELECT count(*) FROM entries").fetchone()
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    task = TABLE_EXTRACTION
    write_ledger(
        ledger,
        [
            {"condition": "seed", "scenario": TABLE_EXTRACTION.name, "run": 1},  # no task ids
            {
                "condition": "seed",
                "scenario": TABLE_EXTRACTION.name,
                "run": 2,
                "task_ids": [str(uuid.uuid4()), str(session_task)],
            },
        ],
    )

    assert tools.agent_eval.review_sheet(argparse.Namespace(store=store.name)) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"2 pending entries in {store.name}.")
    assert "against its task's upstream fix" in out
    assert "reviewed already" not in out
    blocks = {block.splitlines()[0]: block for block in out.strip().split("\n\n")[1:]}
    assert blocks.keys() == {f"entry {linked}", f"entry {unknown}"}
    linked_block, unknown_block = blocks[f"entry {linked}"], blocks[f"entry {unknown}"]
    assert linked_block.splitlines() == [
        f"entry {linked}",
        f"  task     {TABLE_EXTRACTION.name}",
        "  symptom  include_metadata reads False when omitted",
        "  cause    the form field arrives empty",
        "  fix      parse the field as a string and default it to true",
        f"  upstream fix  {task.fix_url}",
        f"  issue         {task.issue_url}",
    ]
    assert unknown_block.splitlines()[:3] == [
        f"entry {unknown}",
        "  task     task unknown",
        "  symptom  no line names my task",
    ]
    assert "upstream fix" not in unknown_block
    with psycopg.connect(runner.local_url(store.name)) as conn:  # read only: nothing changed
        assert conn.execute("SELECT count(*) FROM entries").fetchone() == before


def test_a_graded_seed_wave_session_records_its_task_ids_so_the_sheet_links_its_entry(
    store: runner.Store,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url = runner.local_url(store.name)
    with psycopg.connect(url) as conn:  # what the session's hooks and propose call wrote
        agent = upsert_reporter(conn, "eval-agent", "entry")
        task = start_task(conn, "wave-session", agent, None)
        proposed = factories.entry(conn, agent, symptom="a wave's proposal", task_id=task)
    graded = graded_nothing(url, tmp_path, monkeypatch, "wave-session")
    assert graded.task_ids == [str(task)]
    ledger = tmp_path / "trials.jsonl"
    monkeypatch.setattr(tools.agent_eval, "LEDGER", ledger)
    tools.agent_eval.append_ledger([replace(graded, error=None)])
    assert json.loads(ledger.read_text(encoding="utf-8"))["task_ids"] == [str(task)]

    assert tools.agent_eval.review_sheet(argparse.Namespace(store=store.name)) == 0
    out = capsys.readouterr().out
    assert f"entry {proposed}\n  task     {TASK.name}\n" in out
    assert "  no upstream fix: not a GitHub task" in out  # a written scenario has no fix_url


def test_the_review_sheet_needs_an_existing_local_store(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(runner, "ADMIN_URL", ADMIN_URL)
    missing = f"commons_store_{uuid.uuid4().hex[:12]}"
    assert tools.agent_eval.review_sheet(argparse.Namespace(store=missing)) == 2
    assert f"no experiment store {missing}" in capsys.readouterr().err
    assert not exists(missing)
    monkeypatch.setattr(runner, "ADMIN_URL", "postgresql://commons:commons@db.example.com/commons")
    assert tools.agent_eval.review_sheet(argparse.Namespace(store=missing)) == 2
    assert "must be on localhost" in capsys.readouterr().err
