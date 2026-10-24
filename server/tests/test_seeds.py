import uuid
from pathlib import Path

import psycopg
import pytest
from fastmcp import FastMCP

from commons.cli.main import main
from mcp_client import call

NODE_SEED = "5eed0000-0000-4000-8000-000000000002"


@pytest.fixture
def initialized(database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run `commons init`, which commits the seeds to the session database."""
    monkeypatch.setenv("COMMONS_DATABASE_URL", database_url)
    monkeypatch.setenv("COMMONS_CONFIG", str(tmp_path / "config.json"))
    assert main(["init", "--pseudonym", f"seeder-{uuid.uuid4().hex[:8]}"]) == 0


@pytest.mark.usefixtures("initialized")
def test_init_is_idempotent_about_seeds(
    database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["init", "--pseudonym", "seeder-again"]) == 0
    assert "seed entries" not in capsys.readouterr().out
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(
            "SELECT e.status, e.human_reviewed, count(r.id) FROM entries e "
            "JOIN reviews r ON r.entry_id = e.id "
            "WHERE e.id::text LIKE '5eed0000-%%' GROUP BY e.id ORDER BY e.id"
        ).fetchall()
    assert rows == [("approved", True, 1)] * 3


@pytest.mark.usefixtures("initialized", "reporter")
def test_search_finds_a_seed_on_a_fresh_install(server: FastMCP) -> None:
    result = call(
        server,
        "search",
        {
            "query": "Error: error:0308010C:digital envelope routines::unsupported",
            "task_id": str(uuid.uuid4()),
            "environment": {"language": "javascript (node 20)"},
        },
    )
    top = result["data"][0]
    assert top["entry_id"] == NODE_SEED
    assert top["provenance"] == "proposed by seed; no outcomes yet; never confirmed"
